"""The proxy Claude Code talks to: an Anthropic Messages endpoint on 127.0.0.1, for one session.

Claude Code is started with ANTHROPIC_BASE_URL pointing here and a random token of the session's
as its ANTHROPIC_AUTH_TOKEN (launcher.py); every request must carry the token, and the NanoGPT key
never leaves this process. After DA Vibe Manager's gateway (its model endpoint).

- A private/ model's request is translated to chat completions (llm/anthropic_bridge.py) and
  sealed on this computer to the attested Tinfoil enclave (llm/private_mode.py). Its tokens are
  counted here, roughly: nothing of it is sent anywhere to be counted.
- In a private profile that is all there is. A request for any other model (Claude Code asks for
  its own defaults now and then) goes sealed to the profile's quick model, never in the clear.
- Otherwise (the nano profile) a request for another model goes to NanoGPT's Anthropic endpoint as
  Claude Code wrote it, with the key added; a redirect is never followed. Once a private model has
  been used in the session, nothing more goes in the clear: what was said is in every request.

Only what Claude Code needs is served: POST /v1/messages, POST /v1/messages/count_tokens and
GET /v1/models.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import time
from typing import Awaitable, Callable

import httpx

from . import creds
from .config import Profile
from .llm import capabilities
from .llm.anthropic_bridge import VISION_PROMPT, ImageDescriber, estimate_tokens, message, sse, to_openai
from .llm.client import is_private_mode, private_id
from .llm.private_mode import Attestation, Enclave, PrivateModeClient, list_private_models, offers_private_mode, relay_url
from .netpolicy import check_url, is_secure
from .usagelog import UsageLog

MODEL_PATHS = frozenset({("POST", "/v1/messages"), ("POST", "/v1/messages/count_tokens"), ("GET", "/v1/models")})
# Claude Code's connection-warming probe (HEAD /api/hello): answered here, sent nowhere
PROBE = ("HEAD", "/api/hello")
# request headers passed on to NanoGPT; everything else (auth, host, cookies, Claude Code's session id) stays here
PASS_HEADERS = ("content-type", "accept", "anthropic-version", "anthropic-beta", "user-agent", "x-stainless-retry-count",
                "x-app")
# response headers passed back
RETURN_HEADERS = ("content-type", "request-id", "x-request-id", "retry-after", "x-should-retry",
                  "anthropic-ratelimit-requests-remaining", "anthropic-ratelimit-tokens-remaining")
VISION_EFFORT = "low"
# Claude Code gives up on its model list after 3 seconds (its gateway model discovery)
MODEL_LIST_SECONDS = 2.5


class Refused(Exception):
    """A request this proxy won't send, and the reason Claude Code shows for it. Unless `retry`,
    asking again won't help, and Claude Code is told so (it would otherwise ask up to ten times)."""

    def __init__(self, status: int, message: str, kind: str = "permission_error", retry: bool = False):
        super().__init__(message)
        self.status = status
        self.kind = kind
        self.retry = retry


def upstream_url(base: str, path: str, query: str = "") -> str:
    """NanoGPT's URL for a request path. Claude Code appends /v1/messages to its base URL; a base
    that already ends in /v1 (NanoGPT documents https://nano-gpt.com/api/v1) would give
    .../v1/v1/messages, which NanoGPT answers with a redirect, so the /v1 isn't doubled."""
    base = base.rstrip("/")
    if base.endswith("/v1") and path.startswith("/v1/"):
        path = path[3:]
    return base + path + (f"?{query}" if query else "")


def _n(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def anthropic_usage(u) -> dict:
    """An Anthropic usage block as tokens by kind (input here excludes the cache's)."""
    u = u if isinstance(u, dict) else {}
    return {"input": _n(u.get("input_tokens")), "output": _n(u.get("output_tokens")),
            "cache_read": _n(u.get("cache_read_input_tokens")), "cache_write": _n(u.get("cache_creation_input_tokens"))}


def openai_usage(u) -> dict:
    """A chat completions usage block as tokens by kind (its prompt_tokens include the cached ones),
    and what NanoGPT billed for it, when it says (USD)."""
    u = u if isinstance(u, dict) else {}
    cached = _n((u.get("prompt_tokens_details") or {}).get("cached_tokens"))
    out = {"input": max(_n(u.get("prompt_tokens")) - cached, 0), "output": _n(u.get("completion_tokens")),
           "cache_read": cached, "cache_write": 0}
    cost = u.get("cost")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0:
        out["cost"] = float(cost)
    return out


class UsageReader:
    """Picks the usage out of an Anthropic event stream as it passes: message_start has the input
    side, message_delta the output so far."""

    def __init__(self) -> None:
        self._buf = b""
        self.usage: dict | None = None

    def feed(self, chunk: bytes) -> None:
        *lines, self._buf = (self._buf + chunk).split(b"\n")
        if len(self._buf) > 1 << 20:
            self._buf = b""                     # a line this long is no usage line
        for line in lines:
            if line.startswith(b"data:") and b'"usage"' in line:
                try:
                    data = json.loads(line[5:])
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("type") == "message_start":
                    self.usage = anthropic_usage((data.get("message") or {}).get("usage"))
                elif data.get("type") == "message_delta" and isinstance(data.get("usage"), dict):
                    later = anthropic_usage(data["usage"])
                    self.usage = {k: later[k] or v for k, v in (self.usage or later).items()}


def _error_text(raw: bytes) -> str:
    try:
        data = json.loads(raw)
        err = data.get("error", data) if isinstance(data, dict) else data
        msg = err.get("message") if isinstance(err, dict) else err
        return str(msg or data)[:500]
    except ValueError:
        return raw.decode("utf-8", errors="replace")[:500]


PrivateClientFactory = Callable[[str, str, Enclave], object]   # (base_url, key, enclave) -> PrivateModeClient


class Proxy:
    """One session's proxy: `profile` says which models it uses and whether it is private."""

    def __init__(self, profile: Profile, base_url: str, token: str,
                 key: Callable[[], str | None] = creds.get_key, log: UsageLog | None = None,
                 http: httpx.AsyncClient | None = None,
                 private_client: PrivateClientFactory = PrivateModeClient,
                 capabilities_loader: Callable[[], Awaitable[dict]] | None = None,
                 private_lister: Callable[[], Awaitable[list[str]]] | None = None):
        self.profile = profile
        self.base_url = check_url(base_url, "NanoGPT's base URL").rstrip("/")
        self.token = token
        self.log = log
        self._key = key
        self._http = http
        self._private_client = private_client
        self._load_caps = capabilities_loader or (lambda: capabilities.fetch_nanogpt(self.base_url, self._client()))
        self._list_private = private_lister or (lambda: list_private_models(self.base_url, self._client()))
        self._caps: dict | None = None
        self._caps_lock = asyncio.Lock()
        # one enclave a session: its attestation, and the prompt cache's secret (llm/private_mode.py)
        self.enclave = Enclave(relay_url(self.base_url), on_attested=self._attested) \
            if offers_private_mode(self.base_url) else None
        vision = profile.vision_model
        # what an image shows is part of a sealed request: it is only ever described by a private model
        self.images = ImageDescriber(self._describe if is_private_mode(vision) else None, vision)
        self.used_private = False

    # ------------------------------------------------------------------ what is sent where

    def choose(self, asked: str) -> str:
        """The model a request for `asked` goes to; raises Refused when it can't go anywhere."""
        p = self.profile
        model = private_id(asked) if is_private_mode(asked) else (asked.strip() or p.model)
        if is_private_mode(model):
            return private_id(model)
        if p.private:
            # never in the clear: a model of Claude's (its own defaults, one picked by name) is
            # stood in for by the profile's, the quick one for a quick model's work
            return p.fast_model if "haiku" in model.lower() else p.model
        if self.used_private:
            raise Refused(403, f"This session has used a private model, so {model} can't be used in it: everything "
                               "said so far would go to it in the clear. Start a new session for it.")
        return model

    def _read_key(self) -> str:
        try:
            key = self._key()
        except creds.KeyringUnavailable as e:
            raise Refused(403, str(e)) from None
        if not key:
            raise Refused(403, "No NanoGPT API key is stored. Run `privatecode key set` in a terminal, then try again.")
        return key

    def _private(self, model: str):
        if self.enclave is None:
            raise Refused(403, f"{model} is a NanoGPT Private Mode model, and {self.base_url} isn't NanoGPT: "
                               "nothing was sent.")
        return self._private_client(self.base_url, self._read_key(), self.enclave)

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            async def refuse_insecure(response: httpx.Response) -> None:
                loc = response.headers.get("location")
                if response.is_redirect and loc and not is_secure(str(response.url.join(loc))):
                    raise httpx.HTTPError(f"redirect to insecure {loc[:100]!r} refused")

            self._http = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=30), follow_redirects=False,
                                           event_hooks={"response": [refuse_insecure]})
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()

    async def capabilities(self) -> dict:
        """NanoGPT's model list, loaded once (an empty one when it can't be read: asked again next time)."""
        async with self._caps_lock:
            if self._caps is None:
                try:
                    self._caps = await self._load_caps()
                except Exception:  # noqa: BLE001 - unknown capabilities: images are described, not shown
                    return {}
            return self._caps

    async def sees(self, model: str) -> bool:
        return bool((capabilities.lookup(await self.capabilities(), model) or {}).get("vision"))

    def _record(self, kind: str, **fields) -> None:
        if self.log is not None:
            self.log.write(kind, **fields)

    def _attested(self, att: Attestation) -> None:
        self._record("attested", enclave=att.enclave, hardware=att.hardware, release=att.release_tag,
                     digest=att.release_digest, latest=att.latest_release, latest_checked=att.latest_checked,
                     key_sha256=att.hpke_key_sha256, summary=att.summary)

    async def _priced(self, model: str, usage: dict, private: bool) -> dict:
        """The usage with what it cost: NanoGPT's own bill when it gives one, else the listed prices'."""
        if "cost" not in usage:
            estimate = capabilities.cost(capabilities.lookup(await self.capabilities(), model), usage, private)
            if estimate is not None:
                usage = {**usage, "cost": round(estimate, 6), "cost_estimated": True}
        return usage

    async def _describe(self, data_url: str, context: str) -> str:
        """The vision helper: a private model that can see describes an image for one that can't."""
        model = self.profile.vision_model
        messages = [{"role": "system", "content": VISION_PROMPT},
                    {"role": "user", "content": [
                        {"type": "text", "text": "What the assistant is doing: " + (context.strip() or "(not said)")},
                        {"type": "image_url", "image_url": {"url": data_url}}]}]
        started = time.monotonic()
        client = self._private(model)
        try:
            text = await client.complete(model, messages, params={"reasoning_effort": VISION_EFFORT})
        except Exception as e:
            self._record("vision", model=model, status=502, error=str(e)[:300])
            raise
        usage = getattr(client, "last_usage", None)
        self._record("vision", model=model, status=200, seconds=round(time.monotonic() - started, 2),
                     **({"usage": await self._priced(model, openai_usage(usage), True)} if usage else {}))
        return text.strip()

    async def _private_ids(self) -> list[str]:
        """NanoGPT's public list of Private Mode models, kept with the enclave's attestation ([]: unread)."""
        async def read() -> list[str]:
            try:
                return await self._list_private()
            except Exception:  # noqa: BLE001 - read again next time
                return []
        return await self.enclave.private_ids(read) if self.enclave is not None else []

    async def warm_up(self) -> None:
        """Attest the enclave and read NanoGPT's model lists before the first request needs them."""
        if self.enclave is not None and any(is_private_mode(m) for m in self.profile.models):
            await self._private_ids()
            try:
                await self.enclave.current()
            except Exception as e:  # noqa: BLE001 - the first request attests again, and says why it can't
                self._record("attested", error=str(e)[:300])
        await self.capabilities()

    # ------------------------------------------------------------------ the endpoint

    def app(self):
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        async def handle(request: Request):
            path = request.url.path
            supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip() \
                or request.headers.get("x-api-key", "").strip()
            if not secrets.compare_digest(supplied.encode(), self.token.encode()):
                return self._error(Refused(401, "This session's token doesn't match: only the Claude Code that "
                                                "PrivateCode started can use this proxy.", "authentication_error"))
            if (request.method, path) == PROBE:
                from starlette.responses import Response
                return Response(status_code=200)
            if (request.method, path) not in MODEL_PATHS:
                return self._error(Refused(404, f"PrivateCode passes on Claude Code's messages, not {request.method} {path}.",
                                           "not_found_error"))
            if path == "/v1/models":
                return JSONResponse(await self.model_list())
            body = await request.body()
            try:
                data = json.loads(body)
            except ValueError:
                return self._error(Refused(400, "The request body isn't JSON.", "invalid_request_error"))
            if not isinstance(data, dict):
                return self._error(Refused(400, "The request body isn't a JSON object.", "invalid_request_error"))
            asked = str(data.get("model") or "")
            try:
                model = self.choose(asked)
                if is_private_mode(model):
                    return await self._sealed(path, data, model, asked, len(body))
                return await self._forward(request, path, data, model, asked, body)
            except Refused as e:
                self._record("request", path=path, model=asked, status=e.status, error=str(e))
                return self._error(e)

        return Starlette(routes=[Route("/{path:path}", handle, methods=["GET", "POST", "HEAD"])])

    @staticmethod
    def _error(e: Refused):
        from starlette.responses import JSONResponse
        return JSONResponse({"type": "error", "error": {"type": e.kind, "message": str(e)}}, status_code=e.status,
                            headers={} if e.retry else {"x-should-retry": "false"})

    async def model_list(self) -> dict:
        """The models of this session's profile, as Anthropic lists them (Claude Code's /model picker);
        a private profile adds every model NanoGPT's Private Mode offers now (its public list)."""
        ids = list(self.profile.models)
        if self.profile.private and self.enclave is not None:
            try:
                listed = await asyncio.wait_for(self._private_ids(), MODEL_LIST_SECONDS)
            except asyncio.TimeoutError:
                listed = []                   # the profile's own models are listed all the same
            ids += [m for m in listed if m not in ids]
        data = [{"type": "model", "id": m, "display_name": m, "created_at": "2026-01-01T00:00:00Z"} for m in ids]
        return {"data": data, "has_more": False, "first_id": ids[0] if ids else None, "last_id": ids[-1] if ids else None}

    async def _sealed(self, path: str, data: dict, model: str, asked: str, size: int):
        """A request for a private model: Anthropic in, chat completions sealed to the enclave, Anthropic back."""
        from starlette.responses import JSONResponse, StreamingResponse

        if path == "/v1/messages/count_tokens":
            return JSONResponse({"input_tokens": estimate_tokens(data)})
        client = self._private(model)                       # raises Refused: no key, not NanoGPT
        started = time.monotonic()
        try:
            native = await self.sees(model)
            messages, tools, params = await to_openai(data, self.images, native=native)
        except Exception as e:  # noqa: BLE001 - Claude Code shows the reason
            raise Refused(502, f"Couldn't prepare the request for {model}: {e}", "api_error") from e
        params["reasoning_effort"] = self.profile.reasoning
        self.used_private = True
        estimate = estimate_tokens(data)
        rec = {"path": path, "model": model, "asked_for": asked if asked and private_id(asked) != model else "",
               "sealed": True, "sees": native, "bytes_out": size}
        events = client.stream(model, messages, tools, params)

        async def done(stats: dict, status: int, **extra) -> None:
            usage = stats.get("usage")
            self._record("request", **rec, status=status, seconds=round(time.monotonic() - started, 2), **extra,
                         **({"usage": await self._priced(model, openai_usage(usage), True)} if usage else {}))

        if not data.get("stream"):
            stats: dict = {}
            try:
                reply = await message(events, model, estimate, stats=stats)
            except Exception as e:  # noqa: BLE001
                await done(stats, 502, error=str(e)[:300])
                raise Refused(502, f"{model}: {e}", "api_error") from e
            await done(stats, 200)
            return JSONResponse(reply)

        async def body():
            stats: dict = {}
            n = 0
            try:
                async for chunk in sse(events, model, estimate, stats=stats):
                    n += len(chunk)
                    yield chunk
            finally:
                await done(stats, 502 if stats.get("error") else 200, bytes_in=n,
                           **({"error": stats["error"][:300]} if stats.get("error") else {}))
        return StreamingResponse(body(), media_type="text/event-stream")

    async def _forward(self, request, path: str, data: dict, model: str, asked: str, body: bytes):
        """A request for a model that isn't private (the nano profile): to NanoGPT's Anthropic endpoint."""
        from starlette.responses import Response, StreamingResponse

        key = self._read_key()
        # Claude Code's metadata is this computer's device id and the session's: NanoGPT knows the
        # account by its key, and needs neither
        if data.get("model") != model or "metadata" in data:
            data["model"] = model
            data.pop("metadata", None)
            body = json.dumps(data).encode()
        headers = {k: v for k, v in request.headers.items() if k.lower() in PASS_HEADERS}
        headers["authorization"] = f"Bearer {key}"
        url = upstream_url(self.base_url, path, request.url.query)
        client = self._client()
        started = time.monotonic()
        rec = {"path": path, "model": model, "asked_for": asked if asked and asked != model else "", "sealed": False,
               "bytes_out": len(body)}
        try:
            resp = await client.send(client.build_request(request.method, url, content=body, headers=headers), stream=True)
        except httpx.HTTPError as e:
            raise Refused(502, f"Could not reach NanoGPT: {e}", "api_error", retry=True) from e
        if resp.is_redirect:
            # never followed: the key would go wherever it points
            where = resp.headers.get("location", "")[:200]
            await resp.aclose()
            raise Refused(502, f"NanoGPT redirected the request to {where}; refusing to follow.", "api_error")
        out_headers = {k: v for k, v in resp.headers.items() if k.lower() in RETURN_HEADERS}
        if resp.status_code >= 400:
            # NanoGPT's own explanation (wrong key, unknown model, no credit), for the log and Claude Code
            raw = (await resp.aread())[:8000]
            await resp.aclose()
            self._record("request", **rec, status=resp.status_code, error=_error_text(raw),
                         seconds=round(time.monotonic() - started, 2))
            return Response(raw, status_code=resp.status_code, headers=out_headers)

        async def stream():
            n = 0
            usage = UsageReader()
            whole = b"" if "json" in resp.headers.get("content-type", "") else None
            try:
                async for chunk in resp.aiter_bytes():     # decoded here: Claude Code gets plain bytes
                    n += len(chunk)
                    if whole is None:
                        usage.feed(chunk)
                    elif len(whole) < 8 << 20:
                        whole += chunk
                    yield chunk
            finally:
                await resp.aclose()
                if whole:
                    try:
                        usage.usage = anthropic_usage(json.loads(whole).get("usage"))
                    except (ValueError, AttributeError):
                        pass
                self._record("request", **rec, status=resp.status_code, bytes_in=n,
                             seconds=round(time.monotonic() - started, 2),
                             **({"usage": await self._priced(model, usage.usage, False)} if usage.usage else {}))
        return StreamingResponse(stream(), status_code=resp.status_code, headers=out_headers)


# ---------------------------------------------------------------------- running it

def listen(port: int = 0) -> socket.socket:
    """A listening socket on 127.0.0.1 (port 0: any free one)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(128)
        sock.setblocking(False)
    except OSError:
        sock.close()
        raise
    return sock


async def serve(proxy: Proxy, sock: socket.socket, ready: Callable[[int], None] | None = None,
                parent: int | None = None) -> None:
    """Serve until stopped (SIGTERM, SIGINT) or, given `parent`, until that process is gone."""
    import uvicorn

    config = uvicorn.Config(proxy.app(), log_level="warning", lifespan="off", access_log=False,
                            timeout_keep_alive=75, timeout_graceful_shutdown=3)
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        if task.done():
            task.result()                       # it failed to start: say why
            return
        await asyncio.sleep(0.01)
    if ready is not None:
        ready(sock.getsockname()[1])
    warm = asyncio.create_task(proxy.warm_up())

    async def watch_parent() -> None:
        while parent is not None and not server.should_exit:
            if os.getppid() != parent:
                server.should_exit = True       # Claude Code is gone: so is its proxy
            await asyncio.sleep(2)

    watcher = asyncio.create_task(watch_parent())
    try:
        await task
    finally:
        watcher.cancel()
        warm.cancel()
        await proxy.aclose()
