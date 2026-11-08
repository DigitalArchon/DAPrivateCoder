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
PRIVATE = Profile("private", "private", "private/glm-5-3", "private/kimi-k3", "private/deepseek-v4-1-flash",
                  "private/deepseek-v4-1-flash", "low")
NANO = Profile("nano", "standard", "z-ai/glm-5.3", "moonshotai/kimi-k3", "z-ai/glm-5.3-flash", "", "low")
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


class FakePlain(FakePrivate):
    """Stands in for LLMClient: what went in the clear (or to a TEE model), to which endpoint, with which key."""

    def __init__(self, usage=None):
        super().__init__(usage)
        self.made = []

    def factory(self, base_url, key):
        self.made.append((base_url, key))
        return self


class Attester:
    """Stands in for llm/tee.py's attestation: what was attested, and what it found."""

    def __init__(self, level="full", fail=None):
        self.asked, self.level, self.fail = [], level, fail

    def __call__(self, base_url, key, model):
        self.asked.append((base_url, key, model))
        if self.fail is not None:
            raise self.fail
        return {"level": self.level, "summary": f"Intel TDX quote {self.level}", "shortfalls": [] if self.level == "full"
                else ["the model's own enclave unchecked (RedPill's gateway)"], "gpus": 8, "tcb_status": "UpToDate"}


def make(profile=PRIVATE, fake=None, handler=None, key="sk-nano", listed=LISTED, log=None, base=BASE, plain=None,
         attester=None, provider_url="", provider_key=None, provider_caps=None):
    fake = fake or FakePrivate()

    def nothing_in_the_clear(request):
        pytest.fail(f"nothing may go in the clear: {request.method} {request.url}")

    async def load_caps():
        return capabilities.parse(CAPS)

    async def lister():
        if isinstance(listed, Exception):
            raise listed
        return listed

    async def no_plain_caps():
        return provider_caps

    p = Proxy(profile, base, TOKEN, key=lambda: key, log=log,
              http=httpx.AsyncClient(transport=httpx.MockTransport(handler or nothing_in_the_clear)),
              private_client=fake.factory, capabilities_loader=load_caps, private_lister=lister,
              plain_client=(plain or FakePlain()).factory if plain is not False else None,
              tee_check=attester or Attester(), provider_url=provider_url,
              provider_key=provider_key, provider_capabilities_loader=no_plain_caps if provider_url else None)
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
    assert {c[3]["reasoning_effort"] for c in fake.calls} == {"low"}   # Claude Code said none: the profile's
    assert p.used_private


async def test_the_model_thinks_as_hard_as_claude_code_says(tmp_path):
    log = UsageLog(tmp_path / "requests.log", "s1")
    p, fake = make(log=log)
    # the editor's Effort, /effort and --effort all reach the proxy as output_config.effort
    for asked, run in (("low", "low"), ("medium", "medium"), ("high", "high"), ("xhigh", "max"), ("max", "max"),
                       ("HIGH", "high"), ("turbo", "low"), (3, "low")):
        r = await call(p, "POST", "/v1/messages", message("private/glm-5-3", output_config={"effort": asked}))
        assert r.status_code == 200 and fake.calls[-1][3]["reasoning_effort"] == run, asked
    assert [r["effort"] for r in read(tmp_path / "requests.log")][:5] == ["low", "medium", "high", "max", "max"]


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


async def test_whether_a_model_sees_is_never_guessed_when_nanogpts_list_cant_be_read(tmp_path):
    log = UsageLog(tmp_path / "requests.log", "s1")
    p, fake = make(log=log)
    up = {"now": False}

    async def flaky():
        if not up["now"]:
            raise httpx.ConnectError("NanoGPT is down")
        return capabilities.parse(CAPS)
    p._caps_loaders["nanogpt"] = flaky
    tool_image = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                               "content": [bridge.b64_image(PNG)]}]}
    body = message("private/kimi-k3", messages=[tool_image])
    assert bridge.has_images(body) and not bridge.has_images(message("private/kimi-k3"))
    r = await call(p, "POST", "/v1/messages", body)
    assert r.status_code == 503 and "x-should-retry" not in r.headers           # Claude Code asks again
    assert not fake.calls and not fake.described                               # nothing sent, nothing described
    assert (await call(p, "POST", "/v1/messages", message("private/kimi-k3"))).status_code == 200   # no image: sent
    up["now"] = True
    assert (await call(p, "POST", "/v1/messages", body)).status_code == 200
    assert fake.calls[-1][1][-1]["content"][1]["type"] == "image_url" and not fake.described   # Kimi K3 sees it
    assert [r.get("sees") for r in read(tmp_path / "requests.log")] == [None, False, True]


async def test_a_standard_session_sends_claude_models_to_nanogpts_anthropic_endpoint_with_the_key():
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
                   message("anthropic/claude-opus-5.5", metadata={"user_id": '{"device_id":"0c51a8"}'}),
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
    assert body["model"] == "anthropic/claude-opus-5.5" and "metadata" not in body   # no device id for NanoGPT
    assert not fake.calls


async def test_a_standard_session_never_follows_a_redirect_and_passes_errors_back():
    replies = [httpx.Response(302, headers={"location": "http://0.0.0.0/steal"}),
               httpx.Response(402, json={"type": "error", "error": {"type": "billing_error", "message": "no credit"}})]
    p, _ = make(NANO, handler=lambda request: replies.pop(0))
    r = await call(p, "POST", "/v1/messages", message("anthropic/claude-opus-5.5"))
    assert r.status_code == 502 and "refusing to follow" in r.text
    r = await call(p, "POST", "/v1/messages", message("anthropic/claude-opus-5.5"))
    assert r.status_code == 402 and r.json()["error"]["message"] == "no credit"


async def test_a_standard_session_sends_other_models_in_the_clear_with_the_route_chosen_for_them(tmp_path):
    from dataclasses import replace
    log = UsageLog(tmp_path / "requests.log", "s1")
    route = {"priority": "host", "host": "parasail", "host_name": "Parasail", "fp8": True}
    plain = FakePlain()
    p, fake = make(replace(NANO, routes={"z-ai/glm-5.3": route}), plain=plain, log=log)
    for asked, sent in (("z-ai/glm-5.3", "z-ai/glm-5.3"), ("moonshotai/kimi-k3", "moonshotai/kimi-k3"),
                        ("claude-haiku-4-5", "z-ai/glm-5.3-flash"),       # Claude Code's own default: the profile's
                        (None, "z-ai/glm-5.3")):
        r = await call(p, "POST", "/v1/messages", message(asked))
        assert r.status_code == 200 and plain.calls[-1][0] == sent, asked
    assert set(plain.made) == {(BASE, "sk-nano")} and not fake.calls
    assert plain.calls[0][3]["provider"] == {"require_parameters": True, "order": ["parasail"], "min_quantization": "fp8"}
    assert "provider" not in plain.calls[1][3]                                   # NanoGPT's own choice
    recs = read(tmp_path / "requests.log")
    assert [(r["tier"], r["sealed"]) for r in recs] == [("standard", False)] * 4
    assert recs[0]["route"] == "Parasail · FP8 or better" and "route" not in recs[1]


async def test_a_standard_session_on_openrouter_uses_its_key_and_its_way_of_routing():
    from dataclasses import replace
    route = {"priority": "throughput", "fp8": True}
    profile = replace(NANO, provider="openrouter", routes={"z-ai/glm-5.3": route})
    plain = FakePlain()
    p, fake = make(profile, plain=plain, provider_url="https://openrouter.ai/api/v1", provider_key=lambda: "sk-or",
                   provider_caps=capabilities.parse_openrouter([{"id": "z-ai/glm-5.3", "architecture": {
                       "input_modalities": ["text"]}, "supported_parameters": ["tools"]}]))
    r = await call(p, "POST", "/v1/messages", message("z-ai/glm-5.3"))
    assert r.status_code == 200 and plain.made == [("https://openrouter.ai/api/v1", "sk-or")]
    assert plain.calls[-1][3]["provider"] == {"require_parameters": True, "sort": "throughput",
                                              "quantizations": ["int8", "fp8", "mxfp8", "fp16", "bf16", "fp32"]}
    r = await call(p, "POST", "/v1/messages", message("private/kimi-k3"))   # sealed through NanoGPT, its key
    assert r.status_code == 200 and fake.calls[-1][0] == "private/kimi-k3" and fake.keys == ["sk-nano"]
    p, _ = make(profile, plain=FakePlain(), provider_url="https://openrouter.ai/api/v1", provider_key=lambda: None)
    r = await call(p, "POST", "/v1/messages", message("z-ai/glm-5.3"))
    assert r.status_code == 403 and "No OpenRouter API key" in r.text


TEE = Profile("tee", "tee", "TEE/glm-5.3", "TEE/kimi-k3", "TEE/glm-5.3-flash", "TEE/glm-5.3-flash", "low")


async def test_a_tee_session_attests_each_model_before_anything_goes_to_it(tmp_path, monkeypatch):
    log = UsageLog(tmp_path / "requests.log", "s1")
    plain, attester = FakePlain(), Attester(level="partial")
    p, fake = make(TEE, plain=plain, attester=attester, log=log)
    for asked, sent in (("TEE/glm-5.3", "TEE/glm-5.3"), ("TEE/kimi-k3", "TEE/kimi-k3"), ("TEE/glm-5.3", "TEE/glm-5.3"),
                        ("z-ai/glm-5.3", "TEE/glm-5.3"),                   # never below TEE: its own model stands in
                        ("claude-haiku-4-5", "TEE/glm-5.3-flash")):
        r = await call(p, "POST", "/v1/messages", message(asked))
        assert r.status_code == 200 and plain.calls[-1][0] == sent, asked
    assert [m for _, _, m in attester.asked] == ["TEE/glm-5.3", "TEE/kimi-k3", "TEE/glm-5.3-flash"]   # once each
    assert attester.asked[0][:2] == (BASE, "sk-nano") and set(plain.made) == {(BASE, "sk-nano")}
    assert "provider" not in plain.calls[0][3]                         # never routed: the enclave is NanoGPT's
    attested = [r for r in read(tmp_path / "requests.log") if r["kind"] == "attested_tee"]
    assert attested[0]["level"] == "partial" and attested[0]["shortfalls"]
    assert {r["tier"] for r in read(tmp_path / "requests.log") if r["kind"] == "request"} == {"tee"}
    monkeypatch.setattr(proxy_mod, "TEE_REATTEST_SECONDS", 0)
    await call(p, "POST", "/v1/messages", message("TEE/glm-5.3"))
    assert len(attester.asked) == 4                                    # attested again when it's old


async def test_nothing_goes_to_a_tee_model_whose_enclave_doesnt_attest():
    from daprivatecoder.llm.secure_http import ProviderError
    from daprivatecoder.llm.tee import TeeRefused
    plain = FakePlain()
    refused = TeeRefused("TEE/glm-5.3's quote doesn't bind our nonce")
    p, _ = make(TEE, plain=plain, attester=Attester(fail=refused))
    r = await call(p, "POST", "/v1/messages", message("TEE/glm-5.3"))
    assert r.status_code == 502 and "didn't attest, so nothing was sent" in r.text and not plain.calls
    assert r.headers["x-should-retry"] == "false"                      # it won't hold next time either
    unreachable = TeeRefused("TEE/glm-5.3's attestation couldn't be fetched: down")
    unreachable.__cause__ = ProviderError("nano-gpt answered HTTP 503", status_code=503)
    p, _ = make(TEE, plain=plain, attester=Attester(fail=unreachable))
    r = await call(p, "POST", "/v1/messages", message("TEE/glm-5.3"))
    assert r.status_code == 502 and "x-should-retry" not in r.headers and not plain.calls     # asked again


async def test_images_are_described_only_by_a_helper_as_protected_as_the_request():
    from dataclasses import replace
    body = message(None)
    body["messages"] = [{"role": "user", "content": [{"type": "text", "text": "what is on screen?"}, bridge.b64_image(PNG)]}]
    # a standard session whose helper is in the clear: a private model's images aren't described by it
    plain = FakePlain()
    p, fake = make(replace(NANO, vision_model="z-ai/glm-5.3-flash"), plain=plain)
    await call(p, "POST", "/v1/messages", {**body, "model": "private/glm-5-3"})
    assert "no vision helper is set up" in fake.calls[-1][1][-1]["content"] and not plain.described
    await call(p, "POST", "/v1/messages", {**body, "model": "moonshotai/kimi-k3"})
    assert not plain.described                                         # Kimi K3 sees it itself
    # a TEE session: its TEE helper describes for GLM-5.3, which can't see
    plain = FakePlain()
    p, _ = make(replace(TEE, vision_model="TEE/kimi-k3"), plain=plain)
    await call(p, "POST", "/v1/messages", {**body, "model": "TEE/glm-5.3"})
    assert plain.described[0][0] == "TEE/kimi-k3" and "failing test" in plain.calls[-1][1][-1]["content"]


async def test_once_a_private_model_is_used_nothing_more_goes_in_the_clear():
    p, fake = make(NANO, handler=lambda request: pytest.fail("in the clear after a private model"))
    r = await call(p, "POST", "/v1/messages", message("private/kimi-k3"))
    assert r.status_code == 200 and fake.calls[-1][0] == "private/kimi-k3"          # sealed, in a nano session too
    for asked in ("z-ai/glm-5.3", None):
        r = await call(p, "POST", "/v1/messages", message(asked))
        assert r.status_code == 403 and "Start a new session" in r.text and r.headers["x-should-retry"] == "false"
    p, fake = make(TEE, plain=FakePlain())                               # nor to a TEE model, read by NanoGPT
    await call(p, "POST", "/v1/messages", message("private/kimi-k3"))
    r = await call(p, "POST", "/v1/messages", message("TEE/glm-5.3"))
    assert r.status_code == 403 and "Start a new session" in r.text


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
