"""The proxy Claude Code talks to: an Anthropic Messages endpoint on 127.0.0.1, for one session.

Claude Code is started with ANTHROPIC_BASE_URL pointing here and a random token of the session's
as its ANTHROPIC_AUTH_TOKEN (launcher.py); every request must carry the token, and the NanoGPT key
never leaves this process. After DA Vibe Manager's gateway (its model endpoint).

- A private/ model's request is translated to chat completions (llm/anthropic_bridge.py) and
  sealed on this computer to the attested Tinfoil enclave (llm/private_mode.py). Its tokens are
  counted here, roughly: nothing of it is sent anywhere to be counted.
- In a private profile that is all there is. A request for any other model (Claude Code asks for
  its own defaults now and then) goes sealed to the profile's quick model, never in the clear.
- A TEE/ model's request (a TEE profile's, or one picked on NanoGPT) is translated too, and sent
  through NanoGPT once its enclave has attested (llm/tee.py): never before, and again every 15
  minutes. NanoGPT's gateway reads it: it isn't sealed. Each reply is read as it came, every byte
  hashed, and once it has gone on to Claude Code (never held up or changed), checked against the
  record the attested key signed for it (check_reply): the request log says what that found, and a
  reply that fails has its model attested again before its next request. A TEE vision helper's
  description of an image is a reply like any other, read and checked the same way (_describe). A
  TEE session never sends to a model below TEE: its own model stands in.
- In a standard profile, any other model's request is translated and sent to its provider (NanoGPT,
  OpenRouter, another OpenAI-compatible one) in the clear, with the host the user chose for it
  (llm/routes.py); a Claude model's goes to the provider's Anthropic endpoint as Claude Code wrote
  it. A redirect is never followed. Once a private model has been used in a session, nothing more
  goes in the clear: what was said is in every request.

Only what Claude Code needs is served: POST /v1/messages, POST /v1/messages/count_tokens and
GET /v1/models.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import sys
import time
from typing import Awaitable, Callable

import httpx

from . import creds
from .config import Profile
from .llm import capabilities, routes
from .llm.dcap import since_boot
from .llm.anthropic_bridge import VISION_PROMPT, ImageDescriber, estimate_tokens, has_images, message, sse, to_openai
from .llm.client import RANK, LLMClient, is_claude, is_private_mode, is_tee, private_id, tier_of
from .llm.private_mode import Attestation, Enclave, PrivateModeClient, list_private_models, offers_private_mode, relay_url
from .netpolicy import check_url, key_problem, scrubbed, shown
from .usagelog import UsageLog

MODEL_PATHS = frozenset({("POST", "/v1/messages"), ("POST", "/v1/messages/count_tokens"), ("GET", "/v1/models")})
# Claude Code's connection-warming probe (HEAD /api/hello): answered here, sent nowhere
PROBE = ("HEAD", "/api/hello")
# request headers passed on to the provider; everything else (auth, host, cookies, Claude Code's session id) stays here
PASS_HEADERS = ("content-type", "accept", "anthropic-version", "anthropic-beta", "user-agent", "x-stainless-retry-count",
                "x-app")
# response headers passed back
RETURN_HEADERS = ("content-type", "request-id", "x-request-id", "retry-after", "x-should-retry",
                  "anthropic-ratelimit-requests-remaining", "anthropic-ratelimit-tokens-remaining")
VISION_EFFORT = "low"
# Claude Code's efforts (output_config.effort: the editor's Effort, /effort, --effort, or where the
# session started, launcher.py), least to most. A model is sent the nearest one it takes (effort_for):
# measured live (Nov 8 2026), a private model refuses any other (Kimi K3 and DeepSeek V4.1 Flash
# answer HTTP 400 to "medium"), and through NanoGPT's gateway one is taken but ignored (GLM-5.3 then
# reasons at its default, max: "medium" made it think harder than "high")
LEVELS = ("low", "medium", "high", "xhigh", "max")
# what every private model takes (Kimi K3 only these; GLM-5.3 ignores others; DeepSeek V4.1 Flash
# also xhigh): NanoGPT's listing of their TEE twins doesn't hold for the private build
PRIVATE_EFFORTS = ("low", "high", "max")
# an OpenAI-compatible provider of your own: OpenAI's own levels
CUSTOM_EFFORTS = ("low", "medium", "high")
# Claude Code gives up on its model list after 3 seconds (its gateway model discovery)
MODEL_LIST_SECONDS = 2.5
# a provider's model list: how long it may take, and how soon it's asked for again after it couldn't be read
CAPS_SECONDS = 20
CAPS_RETRY_SECONDS = 30


def handed_back(messages: list[dict]) -> bool:
    return any("reasoning_content" in m for m in messages)


def without_reasoning(messages: list[dict]) -> list[dict]:
    """The messages without the reasoning handed back (for a provider that won't take the field)."""
    return [{k: v for k, v in m.items() if k != "reasoning_content"} for m in messages]


def drop_our_thinking(data: dict) -> bool:
    """Take DA Private Coder's thinking blocks out of an Anthropic request's history (in place); whether
    there were any."""
    from .llm.anthropic_bridge import SIGNATURE
    found = False
    for m in data.get("messages") or []:
        content = m.get("content") if isinstance(m, dict) else None
        if m.get("role") != "assistant" or not isinstance(content, list):
            continue
        kept = [b for b in content if not (isinstance(b, dict) and b.get("type") == "thinking"
                                           and b.get("signature") == SIGNATURE)]
        if len(kept) != len(content):
            found = True
            m["content"] = kept or [{"type": "text", "text": "…"}]
    return found


def with_effort(params: dict, effort: str) -> dict:
    """`params` with the reasoning effort to send ("": none, for a model that takes none)."""
    out = {k: v for k, v in params.items() if k != "reasoning_effort"}
    if effort:
        out["reasoning_effort"] = effort
    return out


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


def effort_of(data: dict, default: str) -> str:
    """How hard the model should reason for this request: what Claude Code asks for, else `default`
    (the profile's)."""
    config = data.get("output_config")
    asked = config.get("effort") if isinstance(config, dict) else None
    asked = asked.strip().lower() if isinstance(asked, str) else ""
    return asked if asked in LEVELS else default


def effort_for(asked: str, takes) -> str:
    """The effort a model that takes `takes` (None: not known) is sent for `asked`: itself if it takes
    it, else the nearest it does, the higher of two as near (medium as high where there's no medium,
    xhigh as max). Never "none" or "minimal": Claude Code doesn't ask for them (and GLM with its
    thinking off reasons all the more, in its answer)."""
    choices = [e for e in (takes or ()) if e in LEVELS]
    if not choices or asked in choices or asked not in LEVELS:
        return asked
    at = LEVELS.index(asked)
    return min(choices, key=lambda e: (abs(LEVELS.index(e) - at), -LEVELS.index(e)))


def efforts_named(error: str, sent: str | None) -> tuple[str, ...]:
    """The efforts a model's refusal says it takes ("Kimi K3 supports thinking_effort values: low,
    high, max (… value=medium)"; "reasoning_effort must be low, high, xhigh, max, or …"), or (). A
    list names two or more, as words of their own: "max_tokens", or "effort too high", lists none."""
    import re
    low = error.lower()
    if "effort" not in low:
        return ()
    named = set(re.findall(r"(?<![a-z_])(low|medium|high|xhigh|max)(?![a-z_])", low)) - {sent}
    return tuple(e for e in LEVELS if e in named) if len(named) >= 2 else ()


def effort_unsupported(error: str) -> bool:
    """Whether a refusal says the model takes no reasoning effort at all (OpenAI's models that don't
    reason: "Unsupported parameter: 'reasoning_effort' is not supported with this model.")."""
    low = error.lower()
    return "reasoning_effort" in low and any(w in low for w in (
        "not supported", "unsupported", "unrecognized", "unknown parameter", "not allowed", "extra inputs"))


def upstream_refusal(model: str, e: BaseException, provider: str, answering: bool = False) -> Refused:
    """What Claude Code is told of a request for `model` that failed on its way to the provider (or,
    `answering`, while it answered), by the provider's HTTP status. Asked again only when that may
    help (a busy or failing provider, the network): Claude Code would otherwise ask up to ten times,
    calling it a temporary server-side issue, of a balance that's run out or a key refused."""
    from .llm.private_mode import ModelRetired
    status = getattr(e, "status_code", None)
    status = status if isinstance(status, int) else None
    name = NAMES.get(provider, provider)
    said = f"{model}: {e}"
    if isinstance(e, ModelRetired):
        return Refused(400, said, "invalid_request_error")
    if status == 402:
        return Refused(402, f"{model}: your {name} balance is too low for this request, so it wasn't run. Top it up, "
                            f"then try again. ({name} said: {e})", "billing_error")
    if status in (401, 403):
        # never a 401 to Claude Code: it would take its own login for the one refused
        return Refused(403, f"{model}: {name} refused the request (HTTP {status}): {e}")
    if status == 429:
        return Refused(429, said, "rate_limit_error", retry=True)
    if status is not None and 400 <= status < 500:
        return Refused(400, said, "invalid_request_error")
    import openai
    network = isinstance(e, (httpx.TransportError, openai.APIConnectionError, asyncio.TimeoutError))
    return Refused(502, said, "api_error", retry=network or (status or 0) >= 500 or (answering and status is None))


async def answered(events):
    """`events` once the provider has answered (its first event, or "open" when its reply began), so
    that a request it refuses is told Claude Code by status, before any reply has started; the
    "open" markers aren't passed on."""
    it = aiter(events)
    try:
        first = await anext(it)
    except StopAsyncIteration:
        first = None

    async def rest():
        try:
            if first is not None and first[0] != "open":
                yield first
            if first is not None:
                async for event in it:
                    if event[0] != "open":
                        yield event
        finally:
            close = getattr(it, "aclose", None)
            if close is not None:
                await close()
    return rest()


def _error_text(raw: bytes) -> str:
    try:
        data = json.loads(raw)
        err = data.get("error", data) if isinstance(data, dict) else data
        msg = err.get("message") if isinstance(err, dict) else err
        return str(msg or data)[:500]
    except ValueError:
        return raw.decode("utf-8", errors="replace")[:500]


PrivateClientFactory = Callable[[str, str, Enclave], object]   # (base_url, key, enclave) -> PrivateModeClient
PlainClientFactory = Callable[[str, "str | None"], object]     # (base_url, key) -> LLMClient
# (NanoGPT's base_url, key, model) -> the attestation (AttestedTee, or what it found: no reply is checked then)
TeeCheck = Callable[[str, str, str], "AttestedTee | dict"]
# a TEE model's enclave is attested again after this, before its next request (as DA Vibe Manager does), on a
# clock that runs on while the computer sleeps (dcap.since_boot: time.monotonic stops during a suspend)
TEE_REATTEST_SECONDS = 900
# providers with an Anthropic endpoint, where a Claude model goes as Claude Code wrote the request
ANTHROPIC_ENDPOINTS = ("nanogpt", "openrouter")
NAMES = {"nanogpt": "NanoGPT", "openrouter": "OpenRouter", "custom": "your provider"}


def attest_tee(base_url: str, key: str, model: str) -> dict:
    """A TEE model's enclave, attested now (llm/tee.py; blocking, so run in a thread): raises when it
    doesn't hold."""
    from .llm import tee
    client = tee.TeeClient(base_url, key, model)
    try:
        return client.attest().to_dict()
    finally:
        client.close()


class AttestedTee:
    """A TEE model's enclave as it attested: what it found, and the client that attested it, kept for
    its replies' checks against it (as DA Vibe Manager keeps it)."""

    def __init__(self, client, found: dict):
        self.client = client
        self.found = found

    def check(self, reply) -> str:
        """A reply read with exact=True (its TurnResult), against the record the attested key signed
        (llm/tee.py check_reply; blocking): "verified", "signed" or "failed". Raises when it can't be
        checked ("unchecked")."""
        return self.client.check_reply(getattr(reply, "id", None), receipt_id=getattr(reply, "receipt_id", None),
                                       body_hash=getattr(reply, "body_hash", None),
                                       route=getattr(reply, "tee_route", None))


def attest_tee_for_replies(base_url: str, key: str, model: str) -> AttestedTee:
    """A TEE model's enclave, attested now as attest_tee does it, its client kept to check the replies."""
    from .llm import tee
    client = tee.TeeClient(base_url, key, model)
    try:
        return AttestedTee(client, client.attest().to_dict())
    except BaseException:
        client.close()
        raise


async def refuse_insecure(response: httpx.Response) -> None:
    """The proxy's own client's response hook: a redirect is never followed, and one off https, or from
    the internet into this computer or its network, is refused as the verifier's client refuses it
    (llm/secure_http.py), as an HTTP error, which is what the callers here take."""
    from .llm.secure_http import ProviderError, refuse_insecure_redirect
    try:
        refuse_insecure_redirect(response)
    except ProviderError as e:
        raise httpx.HTTPError(str(e)) from None


def _worth_retrying(e: BaseException) -> bool:
    """An attestation that couldn't be fetched (the network, NanoGPT's 5xx) may well be fetched next
    time; one that was fetched and didn't hold, or a key NanoGPT refused, won't."""
    from .llm.secure_http import ProviderError
    cause = e.__cause__
    return isinstance(cause, httpx.TransportError) or (
        isinstance(cause, ProviderError) and (cause.status_code is None or cause.status_code >= 500))


class Proxy:
    """One session's proxy: `profile` says which models it uses, and how their requests travel."""

    def __init__(self, profile: Profile, base_url: str, token: str,
                 key: Callable[[], str | None] = creds.get_key, log: UsageLog | None = None,
                 http: httpx.AsyncClient | None = None,
                 private_client: PrivateClientFactory = PrivateModeClient,
                 capabilities_loader: Callable[[], Awaitable[dict]] | None = None,
                 private_lister: Callable[[], Awaitable[list[str]]] | None = None,
                 provider_url: str = "", provider_key: Callable[[], str | None] | None = None,
                 plain_client: PlainClientFactory | None = None, tee_check: TeeCheck = attest_tee_for_replies,
                 provider_capabilities_loader: Callable[[], Awaitable[dict | None]] | None = None,
                 gate: Callable[[], str] | None = None):
        """`base_url` and `key` are NanoGPT's (private and TEE models); a standard profile's provider
        is at `provider_url`, with `provider_key`. `gate` says why nothing may be sent now ("": it may;
        gate.py: DA Private Coder closed, the editor switched off)."""
        self._gate = gate
        self.profile = profile
        self.base_url = check_url(base_url, "NanoGPT's base URL").rstrip("/")
        self.token = token
        self.log = log
        self._key = key
        self._http = http
        self._private_client = private_client
        # a standard profile's models are its provider's; a private or TEE profile's, NanoGPT's
        self.provider = profile.provider if profile.kind == "standard" else "nanogpt"
        self.provider_url = check_url(provider_url or self.base_url, f"{NAMES[self.provider]}'s base URL").rstrip("/")
        self._provider_key = provider_key or (key if self.provider == "nanogpt"
                                              else lambda: creds.get_key(self.provider, self.provider_url))
        self._plain_client = plain_client or LLMClient
        self._plain: dict[tuple[str, str], object] = {}
        self._tee_check = tee_check
        # model -> when it attested, what it found, and the check of its replies against that (None: none)
        self._tee: dict[str, tuple[float, dict, Callable[[object], str] | None]] = {}
        self._doubted: set[str] = set()                     # TEE models a reply of failed its check: attested again
        self._checks: set[asyncio.Task] = set()              # replies being checked, after they went on
        self._tee_lock = asyncio.Lock()
        self._caps_loaders: dict[str, Callable[[], Awaitable[dict | None]]] = {
            "nanogpt": capabilities_loader or (lambda: capabilities.fetch_nanogpt(self.base_url, self._client()))}
        if self.provider != "nanogpt":
            self._caps_loaders[self.provider] = provider_capabilities_loader or (
                lambda: capabilities.fetch(self.provider, self.provider_url, self._client()))
        self._list_private = private_lister or (lambda: list_private_models(self.base_url, self._client()))
        self._caps: dict[str, dict | None] = {}
        self._caps_failed: dict[str, float] = {}       # provider -> when its list last couldn't be read
        self._caps_lock = asyncio.Lock()
        # one enclave a session: its attestation, and the prompt cache's secret (llm/private_mode.py)
        self.enclave = Enclave(relay_url(self.base_url), on_attested=self._attested) \
            if offers_private_mode(self.base_url) else None
        self._describers: dict[str, ImageDescriber] = {}
        self._efforts: dict[str, tuple[str, ...]] = {}      # model -> the efforts its refusal said it takes
        self._no_reasoning_back: set[str] = set()          # providers that refused the reasoning handed back
        self.used_private = False

    # ------------------------------------------------------------------ what is sent where

    def tier(self, model: str) -> str:
        """How a request for `model` travels: private (sealed), tee (attested, through NanoGPT) or standard."""
        return tier_of(model, self.provider == "nanogpt")

    def _provider_of(self, tier: str) -> str:
        return self.provider if tier == "standard" else "nanogpt"

    def gated(self) -> str:
        """Why nothing may be sent now ("": it may)."""
        return self._gate() if self._gate is not None else ""

    def choose(self, asked: str) -> str:
        """The model a request for `asked` goes to; raises Refused when it can't go anywhere. A model is
        never swapped for another the conversation doesn't show: one this session won't send to is
        refused, saying why. Only Claude Code's own quick work under a Haiku name of Claude's (its
        background tasks, unseen) goes to the profile's quick model."""
        p = self.profile
        model = private_id(asked) if is_private_mode(asked) else (asked.strip() or p.model)
        tier = self.tier(model)
        if tier == "private":
            # from now on nothing of this session goes in the clear: set before anything is awaited, so
            # a request alongside this one (Claude Code's quick model's) is held to it too
            self.used_private = True
            return private_id(model)
        name = model.lower()
        own = model in p.models
        if not own and name.startswith("claude-") and "haiku" in name:
            return self.choose(p.fast_model)
        if not own and (name.startswith("claude-") or RANK[tier] < RANK[p.kind]):
            raise Refused(400, self._not_here(model, tier), "invalid_request_error")
        if self.used_private:
            raise Refused(403, f"This session has used a private model, so {model} can't be used in it: everything "
                               "said so far would go to it in the clear. Start a new session for it.")
        return model

    def _not_here(self, model: str, tier: str) -> str:
        p = self.profile
        label = {"private": "Private", "tee": "TEE", "standard": "Standard"}[p.kind]
        pick = f"Pick one of its models with /model (Opus here is {p.opus_model})"
        if model.lower().startswith("claude-"):
            claude = (f", or anthropic/… for Claude through {NAMES[self.provider]}"
                      if p.kind == "standard" and self.provider in ANTHROPIC_ENDPOINTS else "")
            return (f"{model} is one of Claude's own models, and this conversation runs on DA Private Coder's {label} "
                    f"models, so nothing was sent. {pick}{claude}; or start a conversation as usual for Claude.")
        what = "end-to-end encrypted" if p.kind == "private" else "attested in an enclave"
        return f"{model} isn't {what}, and this is a {label} conversation, so nothing was sent. {pick}."

    def _read_key(self, provider: str = "nanogpt") -> str | None:
        try:
            key = self._key() if provider == "nanogpt" else self._provider_key()
        except creds.KeyUnusable as e:
            raise Refused(403, str(e)) from None
        except creds.KeyringUnavailable as e:
            if provider == "custom":
                return None                                  # a server of your own may need none: it says if it does
            raise Refused(403, str(e)) from None
        # a key no header can carry is never sent: the HTTP library's error would quote it to Claude Code
        problem = key_problem(key, NAMES[provider])
        if problem:
            raise Refused(403, problem)
        if key or provider == "custom":                      # a server of your own may need none
            return key
        if provider == "nanogpt":
            raise Refused(403, "No NanoGPT API key is stored. Run `daprivatecoder key set` in a terminal, then try again.")
        raise Refused(403, f"No {NAMES[provider]} API key is stored: add it in DA Private Coder's window (Models, "
                           f"Standard), or run `daprivatecoder key set --provider {provider}`, then try again.")

    def _private(self, model: str):
        if self.enclave is None:
            raise Refused(403, f"{model} is a NanoGPT Private Mode model, and {shown(self.base_url)} isn't NanoGPT: "
                               "nothing was sent.")
        return self._private_client(self.base_url, self._read_key(), self.enclave)

    def _chat_client(self, model: str, tier: str):
        """The client for a model's chat completions: sealed for a private one, plain otherwise (a TEE
        one's enclave attested first, by the caller)."""
        if tier == "private":
            return self._private(model)
        url, key = (self.base_url, self._read_key()) if tier == "tee" \
            else (self.provider_url, self._read_key(self.provider))
        client = self._plain.get((url, key or ""))
        if client is None:
            client = self._plain[(url, key or "")] = self._plain_client(url, key)
        return client

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=30), follow_redirects=False,
                                           event_hooks={"response": [refuse_insecure]})
        return self._http

    async def aclose(self) -> None:
        for task in list(self._checks):
            task.cancel()
        if self._http is not None:
            await self._http.aclose()
        for client in self._plain.values():
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()

    async def capabilities(self, provider: str = "nanogpt") -> dict | None:
        """The provider's model list, loaded once: None when it lists none (another provider), {} when
        it can't be read now (asked again next time)."""
        async with self._caps_lock:
            if provider not in self._caps:
                if time.monotonic() - self._caps_failed.get(provider, -CAPS_RETRY_SECONDS) < CAPS_RETRY_SECONDS:
                    return {}
                try:
                    caps = await asyncio.wait_for(self._caps_loaders[provider](), CAPS_SECONDS)
                except Exception:  # noqa: BLE001 - unknown for now: asked again a little later
                    caps = {}
                if caps == {}:                       # read, but nothing in it: not kept either
                    self._caps_failed[provider] = time.monotonic()
                    return {}
                self._caps[provider] = caps
            return self._caps[provider]

    async def sees(self, model: str, tier: str = "private") -> bool:
        """Whether `model` reads images itself (they go to it as they are) or has them described by
        the vision helper. Without its provider's list it isn't known, and a model that sees mustn't be
        given descriptions, nor one that doesn't images: Claude Code is told to ask again. A provider
        that lists nothing of what its models do: described."""
        caps = await self.capabilities(self._provider_of(tier))
        if caps is None:
            return False
        if not caps:
            raise Refused(503, f"{NAMES[self._provider_of(tier)]}'s list of models couldn't be read, so it isn't known "
                               f"whether {model} sees images: nothing was sent. Claude Code asks again.", "api_error",
                          retry=True)
        return bool((capabilities.lookup(caps, model) or {}).get("vision"))

    def _record(self, kind: str, **fields) -> None:
        if self.log is not None:
            self.log.write(kind, **fields)

    def _attested(self, att: Attestation) -> None:
        self._record("attested", enclave=att.enclave, hardware=att.hardware, release=att.release_tag,
                     digest=att.release_digest, latest=att.latest_release, latest_checked=att.latest_checked,
                     key_sha256=att.hpke_key_sha256, summary=att.summary)

    async def _attest_tee(self, model: str) -> dict:
        """Nothing goes to a TEE model whose enclave hasn't attested (llm/tee.py); an attestation older
        than TEE_REATTEST_SECONDS, or one a reply failed its check against, is made again first (DA
        Assistant's rule for a reply that fails, a50e414). Raises Refused when it doesn't hold."""
        async with self._tee_lock:
            hit = self._tee.get(model)
            doubted = model in self._doubted
            if hit and not doubted and since_boot() - hit[0] < TEE_REATTEST_SECONDS:
                return hit[1]
            key = self._read_key() or ""
            why = {"after": "a reply that failed its check"} if doubted else {}
            try:
                attested = await asyncio.to_thread(self._tee_check, self.base_url, key, model)
            except Exception as e:  # noqa: BLE001 - said as it is, and nothing sent
                self._record("attested_tee", model=model, error=str(e)[:300], **why)
                raise Refused(502, f"{model}'s enclave didn't attest, so nothing was sent to it: {e}", "api_error",
                              retry=_worth_retrying(e)) from e
            att = getattr(attested, "found", attested)
            self._tee[model] = (since_boot(), att, getattr(attested, "check", None))
            self._doubted.discard(model)
            self._record("attested_tee", model=model, **why, **{k: att.get(k) for k in (
                "level", "summary", "shortfalls", "tcb_status", "gpus", "instances", "signing_address")})
            return att

    def _reply_check(self, model: str) -> Callable[[object], str] | None:
        """How a reply of TEE `model` is checked, against the attestation in force now (_attest_tee made
        it just before the request); None when there is none to make."""
        hit = self._tee.get(model)
        return hit[2] if hit else None

    def _check_later(self, model: str, check, ended: dict, rec: dict) -> None:
        """A TEE model's reply, which has gone on to Claude Code as it came, checked against the record
        its attested key signed, aside: the request log says what that found, as a line naming the
        request (rec["ref"]). Only a reply its stream carried to the end is checked; one stopped or cut
        short has no record to match. One that fails is said there, and on the proxy's error output,
        and its model attested again before its next request; one that couldn't be checked
        ("unchecked": a provider that signs no replies, a record not found) is no reason to."""
        reply = ended.get("reply")
        if check is None or reply is None:
            return

        async def run():
            try:
                outcome, detail = await asyncio.to_thread(check, reply), ""
            except Exception as e:  # noqa: BLE001 - a check that couldn't be made is "unchecked", never a failure
                outcome, detail = "unchecked", str(e)[:300]
            if outcome == "failed":
                self._doubted.add(model)
                print(f"daprivatecoder: {model}'s reply didn't match the record its attested enclave signed: its "
                      "enclave is attested again before anything more is sent to it.", file=sys.stderr, flush=True)
            self._record("tee_reply", of=rec.get("ref"), model=model, reply=outcome, **({"detail": detail} if detail else {}))
        task = asyncio.get_running_loop().create_task(run())
        self._checks.add(task)
        task.add_done_callback(self._checks.discard)

    async def _priced(self, model: str, usage: dict, tier: str) -> dict:
        """The usage with what it cost: the provider's own bill when it gives one, else the listed prices'."""
        if "cost" not in usage:
            caps = await self.capabilities(self._provider_of(tier)) or {}
            estimate = capabilities.cost(capabilities.lookup(caps, model), usage, tier == "private")
            if estimate is not None:
                usage = {**usage, "cost": round(estimate, 6), "cost_estimated": True}
        return usage

    async def efforts(self, model: str, tier: str):
        """The efforts `model` takes: what it said when it refused one, else for a private model those
        every private model takes, else its provider's listing (another provider: OpenAI's levels); None
        when not known (sent as asked)."""
        if model in self._efforts:
            return self._efforts[model]
        if tier == "private":
            return PRIVATE_EFFORTS
        provider = self._provider_of(tier)
        if provider == "custom":
            return CUSTOM_EFFORTS
        caps = await self.capabilities(provider) or {}
        return (capabilities.lookup(caps, model) or {}).get("efforts")

    async def _effort(self, model: str, tier: str, asked: str) -> str:
        """The effort `model` is sent for `asked` ("": none, a model that takes none)."""
        takes = await self.efforts(model, tier)
        return "" if takes == () else effort_for(asked, takes)

    def _learned(self, model: str, error: BaseException, sent: str | None) -> str | None:
        """When `model` refused the effort it was sent and said which it takes (or that it takes none):
        remember them, and the one to send instead ("": none; None: not that kind of refusal)."""
        if not sent:
            return None
        takes = efforts_named(str(error), sent)
        if takes:
            self._efforts[model] = takes
            return effort_for(sent, takes)
        if effort_unsupported(str(error)):
            self._efforts[model] = ()
            return ""
        return None

    def _stream(self, client, model: str, messages, tools, params: dict, rec: dict, where: str = "",
                exact: bool = False, ended: dict | None = None):
        """The model's events. Refused before it answers, it is asked again (twice at most): without the
        reasoning handed back, if `where` (its provider) won't take that field, as from then on; with an
        effort it takes, if it named those it takes (rec["effort"] says which went). With `exact` (a TEE
        model's reply, to be checked), its stream's every byte is hashed as it passes (LLMClient.stream);
        the whole reply, once the stream has ended, goes in `ended`["reply"]."""
        async def events():
            msgs, prm = messages, params
            for attempt in range(3):
                sent = prm.get("reasoning_effort")
                started = False
                try:
                    async for event in client.stream(model, msgs, tools, prm, **({"exact": True} if exact else {})):
                        started = True
                        if event[0] == "done" and ended is not None:
                            ended["reply"] = event[1]
                        yield event
                    return
                except Exception as e:
                    if started or attempt == 2:
                        raise
                    if "reasoning_content" in str(e).lower() and handed_back(msgs):
                        self._no_reasoning_back.add(where)
                        msgs = without_reasoning(msgs)
                        rec["reasoning_back"] = False          # in the log: the provider wouldn't take it
                        continue
                    instead = self._learned(model, e, sent)
                    if instead is None or instead == sent:
                        raise
                    rec.setdefault("effort_asked", sent)
                    rec["effort"] = instead
                    prm = with_effort(prm, instead)
        return events()

    def _images(self, tier: str) -> ImageDescriber:
        """The vision helper for a request of `tier`: what an image shows is part of the request, so
        only a helper no less protected than it describes one (else the model is told it can't see)."""
        if tier not in self._describers:
            vision = self.profile.vision_model
            allowed = bool(vision) and RANK[self.tier(vision)] >= RANK[tier]
            self._describers[tier] = ImageDescriber(self._describe if allowed else None, vision)
        return self._describers[tier]

    async def _describe(self, data_url: str, context: str) -> str:
        """The vision helper: a model that can see describes an image for one that can't. A TEE model's
        description is a reply like any other: read as it came, every byte hashed, and once it is in
        hand, checked against the record its attested enclave signed, as the chat's replies are
        (_check_later): the request log says what that found, and one that fails has the model
        attested again before its next request."""
        model = self.profile.vision_model
        tier = self.tier(model)
        messages = [{"role": "system", "content": VISION_PROMPT},
                    {"role": "user", "content": [
                        {"type": "text", "text": "What the assistant is doing: " + (context.strip() or "(not said)")},
                        {"type": "image_url", "image_url": {"url": data_url}}]}]
        started = time.monotonic()
        if tier == "tee":
            await self._attest_tee(model)
        # checked against the record its attested key signs, as the attestation just made has it
        check = self._reply_check(model) if tier == "tee" else None
        client = self._chat_client(model, tier)
        effort = await self._effort(model, tier, VISION_EFFORT)
        rec = {"model": model, "tier": tier}
        # a TEE model's reply is read as a stream, every byte hashed (LLMClient.complete, exact): the record
        # names the hash of a stream's bytes, and NanoGPT's records of replies not streamed can't be compared
        exact = {"exact": True} if check is not None else {}
        if check is not None:
            rec.update(ref=secrets.token_hex(6), reply="checking")    # its check comes later, as the chat's
        ended: dict = {}

        async def asked(params: dict) -> str:
            text = await client.complete(model, messages, params=params, **exact)
            if check is not None:
                ended["reply"] = getattr(client, "last_reply", None)     # read at once: the client is shared
            return text
        try:
            try:
                text = await asked(with_effort({}, effort))
            except Exception as e:
                instead = self._learned(model, e, effort)
                if instead is None or instead == effort:
                    raise
                text = await asked(with_effort({}, instead))
        except Exception as e:
            self._record("vision", model=model, tier=tier, status=502, error=str(e)[:300])
            raise
        usage = getattr(client, "last_usage", None)
        self._record("vision", **rec, sealed=tier == "private", status=200, seconds=round(time.monotonic() - started, 2),
                     **({"usage": await self._priced(model, openai_usage(usage), tier)} if usage else {}))
        self._check_later(model, check, ended, rec)
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
        """Attest the enclaves and read the model lists before the first request needs them (not while
        nothing may be sent: DA Private Coder is closed, say)."""
        if self.gated():
            return
        if self.enclave is not None and any(is_private_mode(m) for m in self.profile.models):
            await self._private_ids()
            try:
                await self.enclave.current()
            except Exception as e:  # noqa: BLE001 - the first request attests again, and says why it can't
                self._record("attested", error=str(e)[:300])
        if self.tier(self.profile.model) == "tee":
            try:
                await self._attest_tee(self.profile.model)
            except Refused:
                pass                                    # the first request attests again, and says why it can't
        for provider in self._caps_loaders:
            await self.capabilities(provider)

    # ------------------------------------------------------------------ the endpoint

    def app(self):
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.routing import Route

        async def handle(request: Request):
            path = request.url.path
            supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip() \
                or request.headers.get("x-api-key", "").strip()
            if not secrets.compare_digest(supplied.encode(), self.token.encode()):
                return self._error(Refused(401, "This session's token doesn't match: only the Claude Code that "
                                                "DA Private Coder started can use this proxy.", "authentication_error"))
            if (request.method, path) == PROBE:
                from starlette.responses import Response
                return Response(status_code=200)
            if (request.method, path) not in MODEL_PATHS:
                return self._error(Refused(404, f"DA Private Coder passes on Claude Code's messages, not {request.method} {path}.",
                                           "not_found_error"))
            if path == "/v1/models":
                from starlette.responses import JSONResponse
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
                why = self.gated()
                if why:
                    raise Refused(403, why)
                model = self.choose(asked)
                tier = self.tier(model)
                if tier == "standard" and is_claude(model) and self.provider in ANTHROPIC_ENDPOINTS:
                    return await self._forward(request, path, data, model, asked, body)
                return await self._translated(path, data, model, asked, len(body), tier)
            except Refused as e:
                self._record("request", path=path, model=asked, status=e.status, error=str(e))
                return self._error(e)

        return Starlette(routes=[Route("/{path:path}", handle, methods=["GET", "POST", "HEAD"])])

    @staticmethod
    def _error(e: Refused):
        from starlette.responses import JSONResponse
        # an address in it never with its password (an error from the network can quote one)
        return JSONResponse({"type": "error", "error": {"type": e.kind, "message": scrubbed(str(e))}},
                            status_code=e.status,
                            headers={} if e.retry else {"x-should-retry": "false"})

    async def model_list(self) -> dict:
        """The models of this session's profile, as Anthropic lists them (Claude Code's /model picker);
        a private profile adds every model NanoGPT's Private Mode offers now (its public list), and a
        TEE one every TEE model that takes tools."""
        ids = list(self.profile.models)
        listed: list[str] = []
        try:
            if self.gated():
                pass                          # nothing fetched: the profile's own models only
            elif self.profile.private and self.enclave is not None:
                listed = await asyncio.wait_for(self._private_ids(), MODEL_LIST_SECONDS)
            elif self.profile.kind == "tee":
                caps = await asyncio.wait_for(self.capabilities("nanogpt"), MODEL_LIST_SECONDS) or {}
                listed = sorted(m for m, c in caps.items() if is_tee(m) and c.get("tools"))
        except asyncio.TimeoutError:
            listed = []                       # the profile's own models are listed all the same
        ids += [m for m in listed if m not in ids]
        data = [{"type": "model", "id": m, "display_name": m, "created_at": "2026-01-01T00:00:00Z"} for m in ids]
        return {"data": data, "has_more": False, "first_id": ids[0] if ids else None, "last_id": ids[-1] if ids else None}

    async def _translated(self, path: str, data: dict, model: str, asked: str, size: int, tier: str):
        """A request for a model other than Claude: Anthropic in, chat completions out (sealed to the
        enclave for a private model; to an attested enclave through NanoGPT for a TEE one; to the
        provider for any other), Anthropic back."""
        from starlette.responses import JSONResponse, StreamingResponse

        if path == "/v1/messages/count_tokens":
            return JSONResponse({"input_tokens": estimate_tokens(data)})
        if tier == "tee":
            await self._attest_tee(model)                  # raises Refused: nothing goes to it unattested
        # a TEE model's reply is checked against the record its attested key signs, once it has gone on
        check = self._reply_check(model) if tier == "tee" else None
        client = self._chat_client(model, tier)            # raises Refused: no key, not NanoGPT
        started = time.monotonic()
        # raises Refused (asked again) when it isn't known whether the model sees: never guessed
        native = await self.sees(model, tier) if has_images(data) else False
        try:
            messages, tools, params = await to_openai(data, self._images(tier), native=native)
        except Refused:
            raise
        except Exception as e:  # noqa: BLE001 - Claude Code shows the reason (an image's description failed, say)
            raise upstream_refusal(f"Couldn't prepare the request for {model}: {self.profile.vision_model}", e,
                                   self._provider_of(self.tier(self.profile.vision_model or model))) from e
        wanted = effort_of(data, self.profile.reasoning)
        effort = await self._effort(model, tier, wanted)
        params = with_effort(params, effort)
        route = self.profile.routes.get(model) if tier == "standard" else None
        params.update(routes.body(self.provider, route))      # which of the provider's hosts runs it, if chosen
        estimate = estimate_tokens(data)
        # the model's reasoning is shown as Claude Code shows Claude's, unless its thinking is off
        show = not (isinstance(data.get("thinking"), dict) and data["thinking"].get("type") == "disabled")
        rec = {"path": path, "model": model, "asked_for": asked if asked and asked.strip().lower() != model.lower() else "",
               "sealed": tier == "private", "tier": tier, "provider": self._provider_of(tier), "sees": native,
               "effort": effort, **({"effort_asked": wanted} if wanted != effort else {}), "bytes_out": size,
               **({"route": routes.describe(self.provider, route)} if route else {})}
        if check is not None:
            # its reply's check comes later, as a line of its own naming this one (the window marks the request)
            rec.update(ref=secrets.token_hex(6), reply="checking")
        ended: dict = {}                                    # the provider's whole reply, once its stream has ended

        async def done(stats: dict, status: int, **extra) -> None:
            usage = stats.get("usage")
            self._record("request", **rec, status=status, seconds=round(time.monotonic() - started, 2), **extra,
                         **({"usage": await self._priced(model, openai_usage(usage), tier)} if usage else {}))

        try:
            # the provider's answer first: a request it refuses (no credit, its key, too long) is told by status
            where = f"{tier}:{self._provider_of(tier)}"
            if where in self._no_reasoning_back and handed_back(messages):
                messages = without_reasoning(messages)
                rec["reasoning_back"] = False
            events = await answered(self._stream(client, model, messages, tools, params, rec, where,
                                                 exact=check is not None, ended=ended))
        except Exception as e:  # noqa: BLE001
            refusal = upstream_refusal(model, e, self._provider_of(tier))
            await done({}, refusal.status, error=str(e)[:300])
            raise refusal from e

        if not data.get("stream"):
            stats: dict = {}
            try:
                reply = await message(events, model, estimate, stats=stats, thinking=show)
            except Exception as e:  # noqa: BLE001
                refusal = upstream_refusal(model, e, self._provider_of(tier), answering=True)
                await done(stats, refusal.status, error=str(e)[:300])
                raise refusal from e
            await done(stats, 200)
            self._check_later(model, check, ended, rec)
            return JSONResponse(reply)

        async def body():
            stats: dict = {}
            n = 0
            try:
                async for chunk in sse(events, model, estimate, stats=stats, thinking=show):
                    n += len(chunk)
                    yield chunk
            finally:
                await done(stats, 502 if stats.get("error") else 200, bytes_in=n,
                           **({"error": stats["error"][:300]} if stats.get("error") else {}))
                self._check_later(model, check, ended, rec)
        return StreamingResponse(body(), media_type="text/event-stream")

    async def _forward(self, request, path: str, data: dict, model: str, asked: str, body: bytes):
        """A request for a Claude model in a standard session: to the provider's Anthropic endpoint, as
        Claude Code wrote it, with the provider's key."""
        from starlette.responses import Response, StreamingResponse

        key = self._read_key(self.provider)
        # Claude Code's metadata is this computer's device id and the session's: the provider knows the
        # account by its key, and needs neither
        # the thinking blocks of DA Private Coder's (another model's reasoning) aren't Claude's: Anthropic's
        # API refuses a signature it didn't make, so they go no further
        ours = drop_our_thinking(data)
        if data.get("model") != model or "metadata" in data or ours:
            data["model"] = model
            data.pop("metadata", None)
            body = json.dumps(data).encode()
        headers = {k: v for k, v in request.headers.items() if k.lower() in PASS_HEADERS}
        headers["authorization"] = f"Bearer {key}"
        url = upstream_url(self.provider_url, path, request.url.query)
        client = self._client()
        started = time.monotonic()
        rec = {"path": path, "model": model, "asked_for": asked if asked and asked != model else "", "sealed": False,
               "tier": "standard", "provider": self.provider, "bytes_out": len(body)}
        name = NAMES[self.provider]
        try:
            resp = await client.send(client.build_request(request.method, url, content=body, headers=headers), stream=True)
        except httpx.HTTPError as e:
            raise Refused(502, f"Could not reach {name}: {e}", "api_error", retry=True) from e
        if resp.is_redirect:
            # never followed: the key would go wherever it points
            where = shown(resp.headers.get("location", ""))[:200]
            await resp.aclose()
            raise Refused(502, f"{name} redirected the request to {where}; refusing to follow.", "api_error")
        out_headers = {k: v for k, v in resp.headers.items() if k.lower() in RETURN_HEADERS}
        if resp.status_code >= 400:
            # the provider's own explanation (wrong key, unknown model, no credit), for the log and Claude Code
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
                             **({"usage": await self._priced(model, usage.usage, "standard")} if usage.usage else {}))
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

    # its parts named, not whatever happens to import (a system's older websockets broke "auto")
    config = uvicorn.Config(proxy.app(), log_level="warning", lifespan="off", access_log=False,
                            timeout_keep_alive=75, timeout_graceful_shutdown=3, http="h11", ws="none",
                            loop="asyncio")
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
