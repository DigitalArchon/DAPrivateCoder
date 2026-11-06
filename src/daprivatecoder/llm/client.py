"""The chat-completions side of a private model: its stream as deltas and a final TurnResult.

From DA Vibe Manager's llm/client.py (its StreamAccumulator unchanged). There is no plain client
here: a private model is only ever sent sealed (llm/private_mode.py), and every other model goes
to NanoGPT's Anthropic endpoint as Claude Code wrote the request (proxy.py)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field


def is_private_mode(model_id: str) -> bool:
    """NanoGPT Private Mode: only ever sent sealed (llm/private_mode.py). However it's written
    ("Private/…", a space before it): when in doubt, sealed."""
    return str(model_id or "").strip().lower().startswith("private/")


def private_id(model_id: str) -> str:
    """A private model's id as NanoGPT lists it (lower case, no spaces around it)."""
    return str(model_id or "").strip().lower()


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
