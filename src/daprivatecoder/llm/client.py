"""The chat-completions side of a model: tiers, the plain client, and the stream as deltas and a
final TurnResult.

From DA Vibe Manager's llm/client.py (its LLMClient, and StreamAccumulator unchanged). A private
model is only ever sent sealed (llm/private_mode.py): the plain client refuses one. A TEE model
goes through it once its enclave is attested (llm/tee.py, proxy.py); any other in the clear."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import AsyncIterator

from ..netpolicy import check_url

# private: sealed on this computer to an attested enclave (NanoGPT Private Mode). tee: runs in an
# enclave this app attests first, but the prompt passes NanoGPT's gateway in the clear (NanoGPT's
# TEE/ models). standard: in the clear to the provider and the model's host
TIERS = ("private", "tee", "standard")
RANK = {"standard": 0, "tee": 1, "private": 2}


def is_private_mode(model_id: str) -> bool:
    """NanoGPT Private Mode: only ever sent sealed (llm/private_mode.py). However it's written
    ("Private/…", a space before it): when in doubt, sealed."""
    return str(model_id or "").strip().lower().startswith("private/")


def private_id(model_id: str) -> str:
    """A private model's id as NanoGPT lists it (lower case, no spaces around it)."""
    return str(model_id or "").strip().lower()


def is_tee(model_id: str) -> bool:
    """NanoGPT's TEE models: "TEE/kimi-k3" (as DA Vibe Manager's llm/tee.py has it)."""
    return str(model_id or "").strip().lower().startswith("tee/")


def is_claude(model_id: str) -> bool:
    """Anthropic's own models, which go to a provider's Anthropic endpoint as Claude Code wrote them."""
    return str(model_id or "").strip().lower().startswith(("anthropic/", "claude-", "claude/"))


def tier_of(model_id: str, nanogpt: bool) -> str:
    """How a model's requests travel. TEE only on NanoGPT (`nanogpt`), the provider whose enclaves
    this app can attest: elsewhere a model so named is sent like any other."""
    if is_private_mode(model_id):
        return "private"
    return "tee" if nanogpt and is_tee(model_id) else "standard"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text as streamed

    def parsed(self) -> dict:
        return json.loads(self.arguments or "{}")


@dataclass
class TurnResult:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict | None = None
    id: str | None = None


class LLMClient:
    """A plain chat-completions client (NanoGPT, OpenRouter, any compatible endpoint): https, or
    http only to this computer or the local network; a redirect is never followed (the user's words
    would go wherever the provider points)."""

    def __init__(self, base_url: str, api_key: str | None, http_client=None):
        from openai import AsyncOpenAI, DefaultAsyncHttpxClient
        check_url(base_url, "The provider's base URL")
        # a local server often needs no key, but the SDK insists on a value
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key or "not-needed", max_retries=1, timeout=600,
                                   http_client=http_client or DefaultAsyncHttpxClient(follow_redirects=False))
        self.last_usage: dict | None = None

    async def stream(self, model: str, messages: list[dict], tools: list[dict] | None = None,
                     params: dict | None = None) -> AsyncIterator[tuple[str, object]]:
        """Yield ("text"|"reasoning", str) deltas and ("tool", name) as each tool call starts,
        then ("done", TurnResult)."""
        if is_private_mode(model):
            raise RuntimeError(f"{model} is a Private Mode model and is only ever sent sealed; nothing was sent")
        kwargs: dict = {"model": model, "messages": messages, "stream": True, "stream_options": {"include_usage": True}}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        kwargs.update(_split_params(params))
        acc = StreamAccumulator()
        stream = await self._client.chat.completions.create(**kwargs)
        yield "open", None                 # the provider took it, and its reply has begun
        try:
            async for chunk in stream:
                for event in acc.feed(chunk):
                    yield event
        finally:
            await stream.close()     # stopped or cancelled: drop the connection, so the provider stops generating
        yield "done", acc.finish()

    async def complete(self, model: str, messages: list[dict], params: dict | None = None) -> str:
        if is_private_mode(model):
            raise RuntimeError(f"{model} is a Private Mode model and is only ever sent sealed; nothing was sent")
        resp = await self._client.chat.completions.create(model=model, messages=messages, **_split_params(params))
        self.last_usage = resp.usage.model_dump() if resp.usage else None
        return resp.choices[0].message.content or ""

    async def aclose(self) -> None:
        await self._client.close()


# provider extensions sent in the request body, whatever the SDK version knows about: reasoning_effort,
# and provider (which host runs the model: llm/routes.py)
_EXTRA_BODY = ("reasoning_effort", "provider")


def _split_params(params: dict | None) -> dict:
    """Standard sampling fields as SDK arguments; provider extensions in the extra body."""
    params = dict(params or {})
    extra = {k: params.pop(k) for k in _EXTRA_BODY if params.get(k)}
    for k in _EXTRA_BODY:
        params.pop(k, None)
    if extra:
        params["extra_body"] = extra
    return params


class StreamAccumulator:
    """Turns chat.completion.chunk objects into deltas and a final TurnResult."""

    def __init__(self) -> None:
        self.result = TurnResult()
        # each call in the order it came, and the call each of the server's indexes is now: a server that gives
        # no index, or the same one to every call, would otherwise merge them ("ReadRead", '{…}{…}')
        self._calls: list[ToolCall] = []
        self._at: dict[int | None, int] = {}

    def feed(self, chunk) -> list[tuple[str, str]]:
        events: list[tuple[str, str]] = []
        if getattr(chunk, "id", None) and not self.result.id:
            self.result.id = chunk.id
        if chunk.usage:
            self.result.usage = chunk.usage.model_dump()
        if not chunk.choices:
            return events
        choice = chunk.choices[0]
        if choice.finish_reason:
            self.result.finish_reason = choice.finish_reason
        delta = choice.delta
        if delta is None:
            return events
        extra = delta.model_extra or {}
        reasoning = extra.get("reasoning_content") or extra.get("reasoning")
        if isinstance(reasoning, str) and reasoning:
            self.result.reasoning += reasoning
            events.append(("reasoning", reasoning))
        if delta.content:
            self.result.content += delta.content
            events.append(("text", delta.content))
        for tc in delta.tool_calls or []:
            slot = self._at.get(tc.index)
            if slot is None or (tc.id and self._calls[slot].id and tc.id != self._calls[slot].id):
                self._calls.append(ToolCall(id="", name="", arguments=""))
                slot = self._at[tc.index] = len(self._calls) - 1
            call = self._calls[slot]
            if tc.id:
                call.id = tc.id
            if tc.function:
                if tc.function.name:
                    if not call.name:
                        # arguments stream silently; this says the model is still working
                        events.append(("tool", tc.function.name))
                    call.name += tc.function.name
                if tc.function.arguments:
                    call.arguments += tc.function.arguments
        return events

    def finish(self) -> TurnResult:
        self.result.tool_calls = list(self._calls)
        for i, call in enumerate(self.result.tool_calls):
            if not call.id:
                call.id = f"call_{i}"
        return self.result
