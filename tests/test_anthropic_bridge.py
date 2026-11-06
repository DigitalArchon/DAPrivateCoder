"""Claude Code's Anthropic requests as chat completions for a private model, and its replies back.
From DA Vibe Manager's tests/test_anthropic_bridge.py, with tests of what is new here."""

import asyncio
import json

from openai.types.chat import ChatCompletionChunk

from privatecode.llm import anthropic_bridge as bridge
from privatecode.llm.client import StreamAccumulator, ToolCall, TurnResult

PNG = b"\x89PNG\r\n\x1a\n fake"


class Seen:
    def __init__(self):
        self.calls = []

    async def describe(self, url, context):
        self.calls.append((url, context))
        return "A window titled gThumb with a zoom box drawn on the photo."


async def test_a_conversation_with_tools_and_images_becomes_chat_messages():
    seen = Seen()
    images = bridge.ImageDescriber(seen.describe, "private/deepseek-v4-1-flash")
    body = {
        "system": [{"type": "text", "text": "You are Claude Code."}],
        "max_tokens": 32000, "stop_sequences": ["END"], "tool_choice": {"type": "any"}, "thinking": {"type": "adaptive"},
        "tools": [{"name": "Bash", "description": "Run a command", "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}}}],
        "messages": [
            {"role": "user", "content": "Add drag-to-zoom to gThumb"},
            {"role": "assistant", "content": [{"type": "text", "text": "Let me look at the screen."},
                                              {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "import -window root s.png"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": [
                {"type": "text", "text": "saved"}, bridge.b64_image(PNG)]}, {"type": "text", "text": "and?"}]},
            {"role": "user", "content": [bridge.b64_image(PNG)]},
        ]}
    messages, tools, params = await bridge.to_openai(body, images)
    assert messages[0] == {"role": "system", "content": "You are Claude Code."}
    assert messages[2]["tool_calls"][0]["function"] == {"name": "Bash", "arguments": json.dumps({"command": "import -window root s.png"})}
    assert messages[3]["role"] == "tool" and messages[3]["tool_call_id"] == "toolu_1"
    assert "saved" in messages[3]["content"] and "zoom box" in messages[3]["content"]
    assert "private/deepseek-v4-1-flash" in messages[3]["content"]
    assert messages[4] == {"role": "user", "content": "and?"}
    assert "zoom box" in messages[5]["content"]
    assert len(seen.calls) == 1                                    # the same image is described once
    assert seen.calls[0][0].startswith("data:image/png;base64,") and "look at the screen" in seen.calls[0][1]
    assert not any(isinstance(m.get("content"), list) for m in messages)   # no image reaches the text model
    assert tools[0]["function"]["parameters"]["properties"]["command"]["type"] == "string"
    assert params == {"max_tokens": 32000, "stop": ["END"], "tool_choice": "required"}   # no "thinking"


async def test_without_a_vision_helper_the_model_is_told_it_cant_see():
    messages, _, _ = await bridge.to_openai({"messages": [{"role": "user", "content": [bridge.b64_image(PNG)]}]},
                                            bridge.ImageDescriber(None))
    assert "can't see images" in messages[0]["content"]


async def test_a_model_that_sees_gets_the_images_themselves():
    seen = Seen()
    body = {"messages": [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "shot"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": [
            {"type": "text", "text": "saved"}, bridge.b64_image(PNG)]}, {"type": "text", "text": "and?"}]},
        {"role": "user", "content": [bridge.b64_image(PNG)]},
        {"role": "user", "content": [{"type": "image", "source": {"type": "url", "url": "https://example.com/a.png"}}]},
    ]}
    messages, _, _ = await bridge.to_openai(body, bridge.ImageDescriber(seen.describe, "private/deepseek-v4-1-flash"), native=True)
    tool, after, alone, by_url = messages[1:]
    # a tool message holds text: its image comes right after the tool results, labelled with the call
    assert tool["role"] == "tool" and "saved" in tool["content"] and "next message" in tool["content"]
    assert after["role"] == "user" and [p["type"] for p in after["content"]] == ["text", "image_url", "text"]
    assert "toolu_1" in after["content"][0]["text"] and after["content"][2]["text"] == "and?"
    assert after["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert alone["content"][0]["type"] == "image_url"
    assert by_url["content"] == "[An image was here, but it couldn't be read.]"     # never fetched
    assert not seen.calls                                                          # nothing described


async def test_anthropics_server_tools_are_left_out():
    """Its web search runs on Anthropic's servers: a private model given it as a function would call it,
    and nothing would run it."""
    body = {"messages": [{"role": "user", "content": "search"}], "tools": [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": 8},
        {"name": "Read", "description": "Read a file", "input_schema": {"type": "object"}},
        {"type": "custom", "name": "Mine", "input_schema": {"type": "object"}}]}
    _, tools, _ = await bridge.to_openai(body, bridge.ImageDescriber(None))
    assert [t["function"]["name"] for t in tools] == ["Read", "Mine"]
    _, tools, _ = await bridge.to_openai({**body, "tools": body["tools"][:1]}, bridge.ImageDescriber(None))
    assert tools is None


async def test_the_billing_block_is_left_out_of_the_system_prompt():
    """Claude Code's first system block is for Anthropic's billing, with a fingerprint that differs
    between conversations (seen: cc_version=2.1.296.cf4, then .9ec)."""
    body = {"system": [{"type": "text", "text": "x-anthropic-billing-header: cc_version=2.1.296.cf4; cc_entrypoint=cli;"},
                       {"type": "text", "text": "You are a Claude agent.", "cache_control": {"type": "ephemeral"}},
                       {"type": "text", "text": "Be brief."}],
            "messages": [{"role": "user", "content": "hi"}]}
    messages, _, _ = await bridge.to_openai(body, bridge.ImageDescriber(None))
    assert messages[0] == {"role": "system", "content": "You are a Claude agent.\nBe brief."}


async def test_system_messages_within_the_conversation_go_in_the_users_turn():
    """As Claude Code 2.1.296 sends them: its environment, after the user's message. A chat template
    may refuse a system message anywhere but first."""
    body = {"system": "You are Claude Code.", "messages": [
        {"role": "user", "content": [{"type": "text", "text": "<system-reminder>CLAUDE.md</system-reminder>"},
                                     {"type": "text", "text": "Say hi"}]},
        {"role": "system", "content": "# Environment\n - Primary working directory: /work"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
        {"role": "system", "content": [{"type": "text", "text": "The date changed."}]},
    ]}
    messages, _, _ = await bridge.to_openai(body, bridge.ImageDescriber(None))
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "tool", "user"]
    assert messages[1]["content"].endswith("Say hi\n\n<system-reminder>\n# Environment\n - Primary working directory: /work\n"
                                           "</system-reminder>")
    assert messages[4]["content"] == "<system-reminder>\nThe date changed.\n</system-reminder>"


async def events(text="", calls=(), reasoning=0, usage=None):
    for _ in range(reasoning):
        yield "reasoning", "hmm"
    if text:
        yield "text", text[:3]
        yield "text", text[3:]
    yield "done", TurnResult(content=text, tool_calls=list(calls), finish_reason="tool_calls" if calls else "stop",
                             usage=usage or {"prompt_tokens": 120, "completion_tokens": 7})


def parse(raw: bytes) -> list[tuple[str, dict]]:
    out = []
    for chunk in raw.decode().strip().split("\n\n"):
        name, data = chunk.split("\n", 1)
        out.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


async def test_the_reply_streams_back_as_anthropic_events():
    raw = b"".join([c async for c in bridge.sse(events("Hello!", [ToolCall("call_9", "Bash", '{"command": "ls"}')], 3),
                                                "private/glm-5-3", 100, ping_every=0)])
    evs = parse(raw)
    names = [n for n, _ in evs]
    assert names[0] == "message_start" and names[-1] == "message_stop" and "ping" in names
    text = "".join(d["delta"]["text"] for n, d in evs if n == "content_block_delta" and d["delta"]["type"] == "text_delta")
    assert text == "Hello!"
    tool = next(d["content_block"] for n, d in evs if n == "content_block_start" and d["content_block"]["type"] == "tool_use")
    assert tool["id"] == "call_9" and tool["name"] == "Bash"
    args = next(d["delta"]["partial_json"] for n, d in evs if d.get("delta", {}).get("type") == "input_json_delta")
    assert json.loads(args) == {"command": "ls"}
    delta = next(d for n, d in evs if n == "message_delta")
    assert delta["delta"]["stop_reason"] == "tool_use" and delta["usage"]["output_tokens"] == 7


async def test_the_usage_says_what_came_from_the_prompt_cache():
    """As Anthropic's does: input_tokens without the cached part, which is cache_read_input_tokens
    (Claude Code adds the two up for the context it shows, and prices them apart)."""
    usage = {"prompt_tokens": 100_000, "completion_tokens": 50, "prompt_tokens_details": {"cached_tokens": 90_000}}
    raw = b"".join([c async for c in bridge.sse(events("ok", usage=usage), "private/glm-5-3", 5)])
    delta = next(d for n, d in parse(raw) if n == "message_delta")
    assert delta["usage"] == {"input_tokens": 10_000, "cache_creation_input_tokens": 0,
                              "cache_read_input_tokens": 90_000, "output_tokens": 50}
    msg = await bridge.message(events("ok", usage=usage), "private/glm-5-3", 5)
    assert msg["usage"]["cache_read_input_tokens"] == 90_000 and msg["usage"]["input_tokens"] == 10_000
    assert bridge.anthropic_usage(None, 42) == {"input_tokens": 42, "output_tokens": 0}       # no usage: the estimate
    assert bridge.anthropic_usage({"prompt_tokens": 5, "prompt_tokens_details": None}, 42)["input_tokens"] == 5


async def test_a_quiet_model_is_kept_alive_with_pings():
    """Claude Code gives up on a stream after 300 seconds without an event; attestation and a model
    that reasons without streaming it are quiet."""
    async def slow():
        await asyncio.sleep(1.2)
        yield "text", "late"
        yield "done", TurnResult(content="late", finish_reason="stop")
    evs = parse(b"".join([c async for c in bridge.sse(slow(), "private/glm-5-3", 1, ping_every=0.5)]))
    names = [n for n, _ in evs]
    assert names.count("ping") >= 2 and names.index("ping") < names.index("content_block_start")
    assert "late" in json.dumps(evs)


async def test_a_reply_abandoned_midway_closes_the_models_stream():
    """Claude Code gone mid-reply (the user pressed Esc): the sealed request is dropped at once."""
    closed = asyncio.Event()

    async def endless():
        try:
            while True:
                yield "text", "more "
                await asyncio.sleep(0.01)
        finally:
            closed.set()
    stream = bridge.sse(endless(), "private/glm-5-3", 1)
    async for chunk in stream:
        if b"more" in chunk:
            break
    await stream.aclose()
    await asyncio.wait_for(closed.wait(), 1)


async def test_an_error_midway_is_told_as_anthropic_tells_one():
    async def failing():
        yield "text", "partial"
        raise RuntimeError("the enclave refused our key twice")
    stats = {}
    evs = parse(b"".join([c async for c in bridge.sse(failing(), "private/glm-5-3", 1, stats=stats)]))
    assert evs[-1][0] == "error" and "refused our key twice" in evs[-1][1]["error"]["message"]
    assert "refused our key" in stats["error"]


async def test_a_reply_that_does_not_stream_is_one_message():
    msg = await bridge.message(events("Done."), "private/glm-5-3", 50)
    assert msg["content"] == [{"type": "text", "text": "Done."}] and msg["stop_reason"] == "end_turn"


async def test_a_reply_cut_off_at_its_length_says_so_and_hands_over_no_call_it_cut_short():
    """finish_reason "length" in the middle of a Write: not a tool call with no input (run with nothing it
    fails, and the model, never told why, writes it all again, paid each time), but max_tokens."""
    async def cut():
        yield "text", "Writing it."
        yield "done", TurnResult(content="Writing it.", finish_reason="length", tool_calls=[
            ToolCall("call_1", "Read", '{"file_path": "/work/a.c"}'),
            ToolCall("call_2", "Write", '{"file_path": "/work/zoom.c", "content": "#include <gtk/gtk.h>\\nstatic void on_')])
    msg = await bridge.message(cut(), "private/glm-5-3", 100)
    assert msg["stop_reason"] == "max_tokens"
    assert [(c["name"], c["input"]) for c in msg["content"] if c["type"] == "tool_use"] == [("Read", {"file_path": "/work/a.c"})]
    streamed = b"".join([c async for c in bridge.sse(cut(), "private/glm-5-3", 100)]).decode()
    assert '"stop_reason": "max_tokens"' in streamed and '"Write"' not in streamed


def test_tool_calls_a_server_gives_no_index_or_the_same_one_stay_apart():
    """Deltas keyed by index only: two calls without one became one "ReadRead" with '{…}{…}' for input."""
    def chunk(calls, finish=None):
        return ChatCompletionChunk.model_validate({"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m",
                                                   "choices": [{"index": 0, "delta": {"tool_calls": calls}, "finish_reason": finish}]})
    acc = StreamAccumulator()                                   # each call under index 0 (no index: the same)
    for cid, path in (("a", "/a"), ("b", "/b")):
        acc.feed(chunk([{"index": 0, "id": cid, "type": "function", "function": {"name": "Read", "arguments": f'{{"file_path": "{path}"}}'}}]))
    acc.feed(chunk([], "tool_calls"))
    calls = acc.finish().tool_calls
    assert [(c.id, c.name, c.arguments) for c in calls] == [("a", "Read", '{"file_path": "/a"}'), ("b", "Read", '{"file_path": "/b"}')]
    acc = StreamAccumulator()                                   # the usual: one call in pieces, under its index
    for part in ('{"file_', 'path": "/a"}'):
        acc.feed(chunk([{"index": 0, **({"id": "a", "type": "function"} if part.startswith("{") else {}),
                         "function": {**({"name": "Read"} if part.startswith("{") else {}), "arguments": part}}]))
    assert [(c.id, c.arguments) for c in acc.finish().tool_calls] == [("a", '{"file_path": "/a"}')]
