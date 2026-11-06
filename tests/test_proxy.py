"""The proxy Claude Code talks to: who may use it, what goes sealed, what goes in the clear (only in
a profile that isn't private), and what is logged."""

import asyncio
import json

import httpx
import pytest

from daprivatecoder import proxy as proxy_mod
from daprivatecoder.config import Profile
from daprivatecoder.llm import anthropic_bridge as bridge
from daprivatecoder.llm import capabilities
from daprivatecoder.llm.client import TurnResult
from daprivatecoder.proxy import Proxy, upstream_url
from daprivatecoder.usagelog import UsageLog, read

BASE = "https://nano-gpt.com/api/v1"
TOKEN = "session-token-" + "x" * 30
PRIVATE = Profile("private", True, "private/glm-5-3", "private/kimi-k3", "private/deepseek-v4-1-flash",
                  "private/deepseek-v4-1-flash", "low")
NANO = Profile("nano", False, "z-ai/glm-5.3", "moonshotai/kimi-k3", "z-ai/glm-5.3-flash", "", "low")
CAPS = [
    {"id": "TEE/kimi-k3", "capabilities": {"vision": True, "tool_calling": True}, "context_length": 1048576,
     "pricing": {"prompt": 3, "completion": 15, "cacheReadInputPer1kTokens": 0.0015, "currency": "USD", "unit": "per_million_tokens"}},
    {"id": "TEE/glm-5.3", "capabilities": {"vision": False, "tool_calling": True}, "context_length": 1048576,
     "pricing": {"prompt": 1.4, "completion": 4.4, "cacheReadInputPer1kTokens": 0.00026, "currency": "USD", "unit": "per_million_tokens"}},
    {"id": "TEE/deepseek-v4.1-flash", "capabilities": {"vision": True, "tool_calling": True}},
]
LISTED = ["private/deepseek-v4-1-flash", "private/gemma4-31b", "private/glm-5-3", "private/kimi-k3"]
SAID = "what we said in private"
CACHED = {"prompt_tokens": 100_000, "completion_tokens": 50, "prompt_tokens_details": {"cached_tokens": 90_000}}
PNG = b"\x89PNG\r\n\x1a\n fake"


class FakePrivate:
    """Stands in for PrivateModeClient: what it was asked, sealed."""

    def __init__(self, usage=None):
        self.calls, self.keys, self.described = [], [], []
        self.usage = usage or {"prompt_tokens": 120, "completion_tokens": 7}

    def factory(self, base_url, key, enclave):
        self.keys.append(key)
        return self

    def stream(self, model, messages, tools=None, params=None):
        self.calls.append((model, messages, tools, params))

        async def go():
            yield "text", "Hi from "
            yield "text", model
            yield "done", TurnResult(content="Hi", finish_reason="stop", usage=self.usage)
        return go()

    async def complete(self, model, messages, params=None):
        self.described.append((model, messages, params))
        self.last_usage = {"prompt_tokens": 900, "completion_tokens": 40}
        return "A terminal with a failing test."


def make(profile=PRIVATE, fake=None, handler=None, key="sk-nano", listed=LISTED, log=None, base=BASE):
    fake = fake or FakePrivate()

    def nothing_in_the_clear(request):
        pytest.fail(f"nothing may go in the clear: {request.method} {request.url}")

    async def load_caps():
        return capabilities.parse(CAPS)

    async def lister():
        if isinstance(listed, Exception):
            raise listed
        return listed

    p = Proxy(profile, base, TOKEN, key=lambda: key, log=log,
              http=httpx.AsyncClient(transport=httpx.MockTransport(handler or nothing_in_the_clear)),
              private_client=fake.factory, capabilities_loader=load_caps, private_lister=lister)
    return p, fake


async def call(p, method, path, body=None, token=TOKEN, headers=None, raw=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=p.app()), base_url="http://127.0.0.1") as c:
        h = {"authorization": f"Bearer {token}", "content-type": "application/json", **(headers or {})}
        content = raw if raw is not None else (json.dumps(body) if body is not None else None)
        return await c.request(method, path, headers=h, content=content)


def message(model=None, text=SAID, stream=True, **extra):
    body = {"max_tokens": 32000, "stream": stream, "messages": [{"role": "user", "content": text}], **extra}
    if model is not None:
        body["model"] = model
    return body


def said(r) -> str:
    return "".join(d["delta"].get("text", "") for n, d in parse(r.content) if n == "content_block_delta")


def parse(raw: bytes):
    out = []
    for chunk in raw.decode().strip().split("\n\n"):
        name, data = chunk.split("\n", 1)
        out.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


async def test_only_the_sessions_claude_code_gets_in():
    p, fake = make()
    r = await call(p, "POST", "/v1/messages", message("private/glm-5-3"), token="wrong")
    assert r.status_code == 401 and r.headers["x-should-retry"] == "false" and not fake.calls
    r = await call(p, "POST", "/v1/messages", message("private/glm-5-3"), token="", headers={"x-api-key": TOKEN})
    assert r.status_code == 200                                       # the token as Claude Code may send it
    r = await call(p, "HEAD", "/api/hello")
    assert r.status_code == 200                                       # its connection-warming probe
    r = await call(p, "GET", "/v1/organizations")
    assert r.status_code == 404 and "not GET /v1/organizations" in r.json()["error"]["message"]


async def test_a_private_session_seals_everything_whatever_model_is_asked_for():
    p, fake = make()
    for asked, sent in (("private/glm-5-3", "private/glm-5-3"), (" Private/Kimi-K3 ", "private/kimi-k3"),
                        ("claude-haiku-4-5-20251001", "private/deepseek-v4-1-flash"),     # Claude Code's quick default
                        ("claude-opus-5-5", "private/glm-5-3"),                          # a Claude model picked by name
                        (None, "private/glm-5-3")):
        r = await call(p, "POST", "/v1/messages?beta=true", message(asked))
        assert r.status_code == 200 and fake.calls[-1][0] == sent, asked
        assert said(r) == f"Hi from {sent}"
        assert SAID in json.dumps(fake.calls[-1][1])                   # what was said went to the sealed client
    assert fake.keys and set(fake.keys) == {"sk-nano"}
    assert {c[3]["reasoning_effort"] for c in fake.calls} == {"low"}   # the profile's, not Claude Code's "high"
    assert p.used_private


async def test_tokens_of_a_private_request_are_counted_here():
    p, fake = make()
    r = await call(p, "POST", "/v1/messages/count_tokens", message("private/glm-5-3", stream=False))
    assert r.status_code == 200 and r.json()["input_tokens"] > 0
    assert not fake.calls and not fake.keys                             # sent nowhere, not even sealed


async def test_a_reply_that_isnt_streamed_comes_whole():
    p, fake = make(fake=FakePrivate(usage=CACHED))
    r = await call(p, "POST", "/v1/messages", message("private/glm-5-3", stream=False))
    body = r.json()
    assert body["content"] == [{"type": "text", "text": "Hi from private/glm-5-3"}]
    assert body["usage"]["cache_read_input_tokens"] == 90_000 and body["usage"]["input_tokens"] == 10_000


async def test_without_a_key_claude_code_is_told_how_to_store_one():
    p, fake = make(key=None)
    r = await call(p, "POST", "/v1/messages", message("private/glm-5-3"))
    assert r.status_code == 403 and "daprivatecoder key set" in r.json()["error"]["message"]
    assert r.headers["x-should-retry"] == "false" and not fake.calls


async def test_a_private_model_needs_nanogpt():
    p, fake = make(base="https://llm.example.com/v1")
    r = await call(p, "POST", "/v1/messages", message("private/glm-5-3"))
    assert r.status_code == 403 and "isn't NanoGPT" in r.text and not fake.calls


async def test_a_model_that_sees_gets_images_and_one_that_doesnt_has_them_described_privately(tmp_path):
    log = UsageLog(tmp_path / "requests.log", "s1")
    p, fake = make(log=log)
    body = message(None, stream=True)
    body["messages"] = [{"role": "user", "content": [{"type": "text", "text": "what is on screen?"}, bridge.b64_image(PNG)]}]
    await call(p, "POST", "/v1/messages", {**body, "model": "private/kimi-k3"})
    assert fake.calls[-1][1][-1]["content"][1]["type"] == "image_url" and not fake.described
    await call(p, "POST", "/v1/messages", {**body, "model": "private/glm-5-3"})
    assert "failing test" in fake.calls[-1][1][-1]["content"]
    model, messages, params = fake.described[0]
    assert model == "private/deepseek-v4-1-flash" and messages[1]["content"][1]["type"] == "image_url"
    records = read(tmp_path / "requests.log")
    assert [r["kind"] for r in records] == ["request", "vision", "request"]
    assert records[1]["model"] == "private/deepseek-v4-1-flash" and records[1]["usage"]["input"] == 900


async def test_a_nano_session_sends_other_models_to_nanogpt_with_the_key():
    seen = []

    def nanogpt(request):
        seen.append(request)
        sse = ('event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":12,'
               '"cache_read_input_tokens":3000}}}\n\nevent: message_delta\ndata: {"type":"message_delta",'
               '"usage":{"output_tokens":9}}\n\nevent: message_stop\ndata: {"type":"message_stop"}\n\n')
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream", "request-id": "req_1",
                                                      "set-cookie": "a=b"})
    p, fake = make(NANO, handler=nanogpt)
    r = await call(p, "POST", "/v1/messages?beta=true",
                   message("z-ai/glm-5.3", metadata={"user_id": '{"device_id":"0c51a8"}'}),
                   headers={"anthropic-beta": "claude-code-20250219", "anthropic-version": "2023-06-01",
                            "x-claude-code-session-id": "38826834", "cookie": "c=d"})
    assert r.status_code == 200 and "message_stop" in r.text and r.headers["request-id"] == "req_1"
    assert "set-cookie" not in r.headers
    sent = seen[0]
    assert str(sent.url) == "https://nano-gpt.com/api/v1/messages?beta=true"       # /v1 not doubled
    assert sent.headers["authorization"] == "Bearer sk-nano" and sent.headers["anthropic-beta"] == "claude-code-20250219"
    for kept_here in ("x-claude-code-session-id", "cookie", "x-api-key"):
        assert kept_here not in sent.headers
    body = json.loads(sent.content)
    assert body["model"] == "z-ai/glm-5.3" and "metadata" not in body          # no device id for NanoGPT
    assert not fake.calls


async def test_a_nano_session_never_follows_a_redirect_and_passes_errors_back():
    replies = [httpx.Response(302, headers={"location": "http://0.0.0.0/steal"}),
               httpx.Response(402, json={"type": "error", "error": {"type": "billing_error", "message": "no credit"}})]
    p, _ = make(NANO, handler=lambda request: replies.pop(0))
    r = await call(p, "POST", "/v1/messages", message("z-ai/glm-5.3"))
    assert r.status_code == 502 and "refusing to follow" in r.text
    r = await call(p, "POST", "/v1/messages", message("z-ai/glm-5.3"))
    assert r.status_code == 402 and r.json()["error"]["message"] == "no credit"


async def test_once_a_private_model_is_used_nothing_more_goes_in_the_clear():
    p, fake = make(NANO, handler=lambda request: pytest.fail("in the clear after a private model"))
    r = await call(p, "POST", "/v1/messages", message("private/kimi-k3"))
    assert r.status_code == 200 and fake.calls[-1][0] == "private/kimi-k3"          # sealed, in a nano session too
    for asked in ("z-ai/glm-5.3", None):
        r = await call(p, "POST", "/v1/messages", message(asked))
        assert r.status_code == 403 and "Start a new session" in r.text and r.headers["x-should-retry"] == "false"


async def test_the_model_list_offers_every_private_model_in_a_private_session():
    p, _ = make()
    r = await call(p, "GET", "/v1/models?limit=1000")
    ids = [m["id"] for m in r.json()["data"]]
    assert ids[:3] == ["private/glm-5-3", "private/kimi-k3", "private/deepseek-v4-1-flash"]
    assert "private/gemma4-31b" in ids and len(ids) == len(set(ids))
    assert all(m["type"] == "model" for m in r.json()["data"])
    p, _ = make(NANO)
    r = await call(p, "GET", "/v1/models")
    assert [m["id"] for m in r.json()["data"]] == ["z-ai/glm-5.3", "moonshotai/kimi-k3", "z-ai/glm-5.3-flash"]


async def test_the_model_list_answers_in_time_when_nanogpt_is_slow(monkeypatch):
    monkeypatch.setattr(proxy_mod, "MODEL_LIST_SECONDS", 0.1)
    p, _ = make()

    async def slow():
        await asyncio.sleep(5)
        return LISTED
    p._list_private = slow
    r = await call(p, "GET", "/v1/models")
    assert [m["id"] for m in r.json()["data"]] == list(PRIVATE.models)
    p, _ = make(listed=RuntimeError("down"))
    r = await call(p, "GET", "/v1/models")
    assert [m["id"] for m in r.json()["data"]] == list(PRIVATE.models)


async def test_what_is_logged_is_tokens_and_cost_never_what_was_said(tmp_path):
    path = tmp_path / "requests.log"
    p, _ = make(fake=FakePrivate(usage=CACHED), log=UsageLog(path, "s1"))
    r = await call(p, "POST", "/v1/messages", message("claude-haiku-4-5"))
    assert r.status_code == 200
    (rec,) = read(path)
    assert rec["kind"] == "request" and rec["sealed"] is True and rec["status"] == 200
    assert rec["model"] == "private/deepseek-v4-1-flash" and rec["asked_for"] == "claude-haiku-4-5"
    assert rec["usage"]["input"] == 10_000 and rec["usage"]["cache_read"] == 90_000 and rec["usage"]["output"] == 50
    p, _ = make(fake=FakePrivate(usage=CACHED), log=UsageLog(path, "s2"))
    await call(p, "POST", "/v1/messages", message("private/glm-5-3"))
    glm = read(path)[-1]["usage"]
    assert glm["cost_estimated"] and glm["cost"] == pytest.approx((10_000 * 1.4 + 90_000 * 0.26 + 50 * 4.4) / 1e6 * 1.05)
    assert SAID not in path.read_text()


async def test_requests_that_arent_json_objects_are_refused():
    p, fake = make()
    assert (await call(p, "POST", "/v1/messages", raw=b"{not json")).status_code == 400
    assert (await call(p, "POST", "/v1/messages", raw=b'["not", "a", "request"]')).status_code == 400
    assert not fake.calls


def test_the_upstream_url_doesnt_double_v1():
    assert upstream_url("https://nano-gpt.com/api/v1", "/v1/messages", "beta=true") == "https://nano-gpt.com/api/v1/messages?beta=true"
    assert upstream_url("https://nano-gpt.com/api", "/v1/messages") == "https://nano-gpt.com/api/v1/messages"


def test_a_plain_http_base_url_is_refused():
    with pytest.raises(ValueError, match="plain http"):
        Proxy(PRIVATE, "http://nano-gpt.com/api/v1", TOKEN)
