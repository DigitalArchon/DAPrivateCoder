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


async def test_a_private_session_seals_everything_and_runs_only_the_model_it_says():
    p, fake = make()
    for asked, sent in (("private/glm-5-3", "private/glm-5-3"), (" Private/Kimi-K3 ", "private/kimi-k3"),
                        ("claude-haiku-4-5-20251001", "private/deepseek-v4-1-flash"),     # Claude Code's quick work
                        (None, "private/glm-5-3")):
        r = await call(p, "POST", "/v1/messages?beta=true", message(asked))
        assert r.status_code == 200 and fake.calls[-1][0] == sent, asked
        assert said(r) == f"Hi from {sent}"
        assert SAID in json.dumps(fake.calls[-1][1])                   # what was said went to the sealed client
    assert fake.keys and set(fake.keys) == {"sk-nano"}
    assert {c[3]["reasoning_effort"] for c in fake.calls} == {"low"}   # Claude Code said none: the profile's
    assert p.used_private
    # a model the conversation would show but this session won't send to isn't swapped for another: refused
    for asked, why in (("claude-opus-5-5", "is one of Claude's own models"), ("z-ai/glm-5.3", "isn't end-to-end encrypted")):
        r = await call(p, "POST", "/v1/messages", message(asked))
        assert r.status_code == 400 and why in r.json()["error"]["message"] and r.headers["x-should-retry"] == "false"
        assert "private/kimi-k3" in r.json()["error"]["message"]          # what /model opus is here
    assert len(fake.calls) == 4


async def test_the_model_thinks_as_hard_as_claude_code_says_at_the_nearest_level_it_takes(tmp_path):
    log = UsageLog(tmp_path / "requests.log", "s1")
    p, fake = make(log=log)
    # the editor's Effort, /effort and --effort all reach the proxy as output_config.effort; a private
    # model takes low, high and max only (measured: Kimi K3 refuses medium, GLM-5.3 ignores it)
    for asked, run in (("low", "low"), ("medium", "high"), ("high", "high"), ("xhigh", "max"), ("max", "max"),
                       ("HIGH", "high"), ("turbo", "low"), (3, "low")):
        r = await call(p, "POST", "/v1/messages", message("private/glm-5-3", output_config={"effort": asked}))
        assert r.status_code == 200 and fake.calls[-1][3]["reasoning_effort"] == run, asked
    recs = read(tmp_path / "requests.log")
    assert [r["effort"] for r in recs][:5] == ["low", "high", "high", "max", "max"]
    assert recs[1]["effort_asked"] == "medium" and "effort_asked" not in recs[0]


def test_the_nearest_effort_a_model_takes():
    from daprivatecoder.proxy import effort_for, efforts_named
    glm = ["low", "high", "max"]
    assert [effort_for(e, glm) for e in ("low", "medium", "high", "xhigh", "max")] == ["low", "high", "high", "max", "max"]
    every = ["low", "medium", "high", "xhigh", "max"]
    assert [effort_for(e, every) for e in every] == every
    assert effort_for("max", ["minimal", "low", "medium", "high"]) == "high"
    assert effort_for("low", ["none", "high"]) == "high" and effort_for("medium", None) == "medium"
    assert effort_for("xhigh", ["none", "low", "high", "max"]) == "max"
    kimi = ('Private Mode request failed (HTTP 400): {"error":{"message":"Kimi K3 supports thinking_effort values: '
            'low, high, max (parameter=thinking_effort, value=medium)"}}')
    deepseek = "DeepSeek V4.1 reasoning_effort must be low, high, xhigh, max, or an integer within [1, 100]"
    assert efforts_named(kimi, "medium") == ("low", "high", "max")
    assert efforts_named(deepseek, "medium") == ("low", "high", "xhigh", "max")
    assert efforts_named("HTTP 400: max_tokens is too high", "low") == ()


async def test_a_model_that_refuses_an_effort_is_asked_again_with_one_it_names(tmp_path):
    from dataclasses import replace

    class Picky(FakePlain):
        def stream(self, model, messages, tools=None, params=None):
            if params.get("reasoning_effort") not in ("low", "high", "xhigh", "max"):
                self.calls.append((model, messages, tools, params))

                async def refuse():
                    raise RuntimeError("HTTP 400: DeepSeek V4.1 reasoning_effort must be low, high, xhigh, max, or an integer")
                    yield
                return refuse()
            return super().stream(model, messages, tools, params)
    log = UsageLog(tmp_path / "requests.log", "s1")
    plain = Picky()
    # its provider's listing says it takes medium (as NanoGPT lists TEE/deepseek-v4.1-flash), but it refuses it
    p, _ = make(replace(NANO, model="deepseek/deepseek-v4.1-flash"), plain=plain, log=log)
    p._caps["nanogpt"] = {"deepseek/deepseek-v4.1-flash": {"tools": True, "efforts": ["none", "low", "medium", "high", "max"]}}
    r = await call(p, "POST", "/v1/messages", message("deepseek/deepseek-v4.1-flash", output_config={"effort": "medium"}))
    assert r.status_code == 200 and "Hi from" in said(r)
    assert [c[3]["reasoning_effort"] for c in plain.calls] == ["medium", "high"]
    assert read(tmp_path / "requests.log")[-1]["effort"] == "high" and read(tmp_path / "requests.log")[-1]["effort_asked"] == "medium"
    await call(p, "POST", "/v1/messages", message("deepseek/deepseek-v4.1-flash", output_config={"effort": "medium"}))
    assert plain.calls[-1][3]["reasoning_effort"] == "high" and len(plain.calls) == 3      # learned: asked once
    r = await call(p, "POST", "/v1/messages", message("deepseek/deepseek-v4.1-flash", output_config={"effort": "xhigh"}))
    assert plain.calls[-1][3]["reasoning_effort"] == "xhigh"                               # which it does take


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


@pytest.mark.parametrize("profile, model", [(PRIVATE, "private/glm-5-3"), (NANO, "z-ai/glm-5.3")])
async def test_a_key_no_header_can_carry_is_never_sent(profile, model):
    """Sent, the HTTP library would refuse its header in words quoting it, key and all, and Claude Code
    would show them: refused here, saying what's wrong and nothing of the key."""
    plain = FakePlain()
    p, fake = make(profile, key="sk-abc\n123", plain=plain)
    r = await call(p, "POST", "/v1/messages", message(model))
    assert r.status_code == 403 and "no API key has" in r.json()["error"]["message"] and "abc" not in r.text
    assert fake.calls == [] and plain.calls == [] and plain.made == []


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


async def test_whether_a_model_sees_is_never_guessed_when_nanogpts_list_cant_be_read(tmp_path, monkeypatch):
    log = UsageLog(tmp_path / "requests.log", "s1")
    p, fake = make(log=log)
    up = {"now": False}

    async def flaky():
        if not up["now"]:
            raise httpx.ConnectError("NanoGPT is down")
        return capabilities.parse(CAPS)
    p._caps_loaders["nanogpt"] = flaky
    monkeypatch.setattr(proxy_mod, "CAPS_RETRY_SECONDS", 0)            # asked again at once (30 s, live)
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


@pytest.mark.parametrize("location, refused", [
    ("http://nano-gpt.com/api/v1/models", "insecure connection"), ("https://192.168.1.1/admin", "local network"),
    ("/api/v1/models", None)])
async def test_the_proxys_own_client_refuses_a_redirect_off_https_or_into_the_local_network(location, refused):
    """As the verifier's client does (llm/secure_http.py); a relative one isn't taken for either."""
    request = httpx.Request("GET", "https://nano-gpt.com/api/v1/models")
    response = httpx.Response(302, headers={"location": location}, request=request)
    if refused is None:
        await proxy_mod.refuse_insecure(response)
        return
    with pytest.raises(httpx.HTTPError, match=refused):
        await proxy_mod.refuse_insecure(response)


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
                        ("claude-haiku-4-5", "TEE/glm-5.3-flash")):
        r = await call(p, "POST", "/v1/messages", message(asked))
        assert r.status_code == 200 and plain.calls[-1][0] == sent, asked
    r = await call(p, "POST", "/v1/messages", message("z-ai/glm-5.3"))      # never below TEE: refused, not swapped
    assert r.status_code == 400 and "isn't attested in an enclave" in r.json()["error"]["message"]
    assert [m for _, _, m in attester.asked] == ["TEE/glm-5.3", "TEE/kimi-k3", "TEE/glm-5.3-flash"]   # once each
    assert attester.asked[0][:2] == (BASE, "sk-nano") and set(plain.made) == {(BASE, "sk-nano")}
    assert "provider" not in plain.calls[0][3]                         # never routed: the enclave is NanoGPT's
    attested = [r for r in read(tmp_path / "requests.log") if r["kind"] == "attested_tee"]
    assert attested[0]["level"] == "partial" and attested[0]["shortfalls"]
    assert {r.get("tier") for r in read(tmp_path / "requests.log") if r["kind"] == "request"} == {"tee", None}  # None: refused
    monkeypatch.setattr(proxy_mod, "TEE_REATTEST_SECONDS", 0)
    await call(p, "POST", "/v1/messages", message("TEE/glm-5.3"))
    assert len(attester.asked) == 4                                    # attested again when it's old


async def test_a_tee_models_enclave_is_attested_again_after_a_sleep(suspend):
    """15 minutes on time.monotonic, which stops while the computer is suspended: a session woken after a
    night took the attestation made before it for minutes old."""
    attester = Attester()
    p, _ = make(TEE, plain=FakePlain(), attester=attester)
    for _ in range(2):
        assert (await call(p, "POST", "/v1/messages", message("TEE/glm-5.3"))).status_code == 200
    assert len(attester.asked) == 1
    suspend(1)
    assert (await call(p, "POST", "/v1/messages", message("TEE/glm-5.3"))).status_code == 200
    assert len(attester.asked) == 2


async def test_nothing_goes_to_a_tee_model_whose_enclave_doesnt_attest():
    from daprivatecoder.llm.secure_http import ProviderError
    from daprivatecoder.llm.tee import TeeRefused
    plain = FakePlain()
    refused = TeeRefused("TEE/glm-5.3's quote doesn't bind our nonce")
    p, _ = make(TEE, plain=plain, attester=Attester(fail=refused))
    r = await call(p, "POST", "/v1/messages", message("TEE/glm-5.3"))
    assert r.status_code == 502 and "didn't attest, so nothing was sent" in r.text and not plain.calls
    assert r.headers["x-should-retry"] == "false"                      # it won't hold next time either
    # as llm/tee.py says each: what failed decides, not the words (its 5xx is "couldn't fetch")
    for said in ("TEE/glm-5.3's attestation couldn't be fetched: down",
                 "nano-gpt couldn't fetch TEE/glm-5.3's attestation just now (503); this is on the provider's side"):
        unreachable = TeeRefused(said)
        unreachable.__cause__ = ProviderError("nano-gpt answered HTTP 503", status_code=503)
        p, _ = make(TEE, plain=plain, attester=Attester(fail=unreachable))
        r = await call(p, "POST", "/v1/messages", message("TEE/glm-5.3"))
        assert r.status_code == 502 and "x-should-retry" not in r.headers and not plain.calls, said   # asked again
    no_attestation = TeeRefused("TEE/deepseek-v4.1-flash offers no attestation")
    no_attestation.__cause__ = ProviderError("nano-gpt answered HTTP 400", status_code=400)
    p, _ = make(TEE, plain=plain, attester=Attester(fail=no_attestation))
    r = await call(p, "POST", "/v1/messages", message("TEE/glm-5.3"))
    assert r.headers["x-should-retry"] == "false"


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


class Refusing(FakePrivate):
    """A provider that refuses a request before it answers (as its HTTP status says), or stops partway."""

    def __init__(self, error, partway=False):
        super().__init__()
        self.error, self.partway = error, partway

    def stream(self, model, messages, tools=None, params=None):
        self.calls.append((model, messages, tools, params))
        error, partway = self.error, self.partway

        async def go():
            if partway:
                yield "open", None
                yield "text", "Half an answer"
            raise error
            yield
        return go()


def nanogpt_refused(status, said='{"error":"Insufficient balance"}'):
    from daprivatecoder.llm.private_mode import PrivateModeError
    return PrivateModeError(f"NanoGPT refused the Private Mode request (HTTP {status}): {said}", status, said)


async def test_a_request_the_provider_refuses_is_told_by_its_status_and_asked_again_only_when_that_helps():
    """An empty balance isn't a temporary server-side issue to be asked about ten times (Nov 8 2026: it
    was a 502): each refusal reaches Claude Code before any reply begins, by what it is."""
    for status, code, kind, retry in ((402, 402, "billing_error", False), (401, 403, "permission_error", False),
                                      (400, 400, "invalid_request_error", False), (429, 429, "rate_limit_error", True),
                                      (503, 502, "api_error", True), (None, 502, "api_error", False)):
        for stream in (True, False):
            p, fake = make(fake=Refusing(nanogpt_refused(status)))
            r = await call(p, "POST", "/v1/messages", message("private/kimi-k3", stream=stream))
            assert (r.status_code, r.json()["error"]["type"]) == (code, kind), (status, stream)
            assert ("x-should-retry" in r.headers) == (not retry), (status, stream)
    p, fake = make(fake=Refusing(nanogpt_refused(402)))
    said = (await call(p, "POST", "/v1/messages", message("private/kimi-k3"))).json()["error"]["message"]
    assert "NanoGPT balance is too low for this request" in said and "Top it up" in said


async def test_a_request_the_provider_refuses_is_logged_once_as_sent(tmp_path):
    """It was logged as the request sent, with how it went, and again as one refused here, before anything was
    sent: Usage showed a second row, "not sent", for a request NanoGPT had refused. One refused here is logged once
    too, as not sent."""
    from daprivatecoder.manager import _sent
    for stream, partway in ((True, False), (False, False), (False, True)):
        log = UsageLog(tmp_path / f"requests-{stream}-{partway}.log", "s1")
        p, fake = make(fake=Refusing(nanogpt_refused(402), partway=partway), log=log)
        r = await call(p, "POST", "/v1/messages", message("private/kimi-k3", stream=stream))
        assert r.status_code == (200 if stream and partway else 402), (stream, partway)
        lines = [e for e in read(log.path) if e["kind"] == "request"]
        assert [(e["tier"], e["status"], _sent(e)) for e in lines] == [("private", 402, "sealed")], (stream, partway)
    log = UsageLog(tmp_path / "refused-here.log", "s1")
    p, fake = make(log=log)
    assert (await call(p, "POST", "/v1/messages", message("z-ai/glm-5.3"))).status_code == 400
    (line,) = [e for e in read(log.path) if e["kind"] == "request"]
    assert _sent(line) == "" and not fake.calls


async def test_a_model_that_stops_partway_says_so_in_the_stream_by_its_name():
    from daprivatecoder.llm.private_mode import PrivateModeError
    p, fake = make(fake=Refusing(PrivateModeError("the model stopped: context length exceeded"), partway=True))
    r = await call(p, "POST", "/v1/messages", message("private/kimi-k3"))
    assert r.status_code == 200 and said(r) == "Half an answer"
    error = [d for n, d in parse(r.content) if n == "error"][0]["error"]
    assert error["message"] == "private/kimi-k3: the model stopped: context length exceeded"


async def test_nothing_is_sent_or_fetched_while_the_gate_says_so():
    """DA Private Coder closed, or the editor that started the session switched off (gate.py): every
    request is refused, saying so, and goes once it may again."""
    p, fake = make()
    why = {"now": "DA Private Coder is closed, so nothing was sent. Open it to go on with this conversation."}
    p._gate = lambda: why["now"]
    r = await call(p, "POST", "/v1/messages", message("private/glm-5-3"))
    assert r.status_code == 403 and r.headers["x-should-retry"] == "false" and not fake.calls
    assert r.json()["error"]["message"] == why["now"]
    listed = (await call(p, "GET", "/v1/models")).json()["data"]
    assert [m["id"] for m in listed] == list(PRIVATE.models)         # NanoGPT's list wasn't read
    assert (await call(p, "POST", "/v1/messages/count_tokens", message("private/glm-5-3"))).status_code == 403
    why["now"] = ""
    assert (await call(p, "POST", "/v1/messages", message("private/glm-5-3"))).status_code == 200


def test_a_refusal_lists_the_efforts_a_model_takes_only_as_words_of_their_own():
    from daprivatecoder.proxy import effort_unsupported, efforts_named
    assert efforts_named("reasoning_effort requires max_tokens >= 1024", "high") == ()
    assert efforts_named("reasoning effort is too high for this model", "max") == ()
    assert efforts_named("Kimi K3 supports thinking_effort values: low, high, max (value=medium)", "medium") == \
        ("low", "high", "max")
    assert effort_unsupported("Unsupported parameter: 'reasoning_effort' is not supported with this model.")
    assert not effort_unsupported("reasoning_effort must be low, high, xhigh, max")


class NoEffort(FakePrivate):
    """A model that takes no reasoning effort at all (OpenAI's that don't reason)."""

    def stream(self, model, messages, tools=None, params=None):
        if "reasoning_effort" in (params or {}):
            self.calls.append((model, messages, tools, params))
            from daprivatecoder.llm.private_mode import PrivateModeError

            async def no():
                raise PrivateModeError("Unsupported parameter: 'reasoning_effort' is not supported with this model.", 400)
                yield
            return no()
        return super().stream(model, messages, tools, params)


async def test_a_model_that_takes_no_effort_is_asked_again_without_one_and_from_then_on():
    p, fake = make(fake=NoEffort())
    for _ in range(2):
        r = await call(p, "POST", "/v1/messages", message("private/glm-5-3"))
        assert r.status_code == 200 and "reasoning_effort" not in fake.calls[-1][3]
    assert len(fake.calls) == 3                                      # refused once, then never sent one


async def test_a_private_model_asked_for_holds_the_session_to_it_before_anything_is_awaited():
    p, fake = make(NANO, provider_url="https://nano-gpt.com/api/v1")
    p.choose("private/kimi-k3")
    assert p.used_private
    with pytest.raises(proxy_mod.Refused, match="has used a private model"):
        p.choose("z-ai/glm-5.3")


async def test_a_model_list_that_comes_back_empty_isnt_kept(monkeypatch):
    p, fake = make()
    answers = [[], CAPS]

    async def load():
        return capabilities.parse(answers.pop(0))
    p._caps_loaders["nanogpt"] = load
    monkeypatch.setattr(proxy_mod, "CAPS_RETRY_SECONDS", 0)
    assert await p.capabilities() == {}
    assert "TEE/kimi-k3" in await p.capabilities()                   # read again, and kept now


class Reasons(FakePrivate):
    """A model that reasons before it answers; `refuse_field`: a provider that won't take reasoning handed back."""

    def __init__(self, refuse_field=False):
        super().__init__()
        self.refuse_field = refuse_field

    def stream(self, model, messages, tools=None, params=None):
        self.calls.append((model, messages, tools, params))
        refuse = self.refuse_field and any("reasoning_content" in m for m in messages)

        async def go():
            if refuse:
                from daprivatecoder.llm.private_mode import PrivateModeError
                raise PrivateModeError("Unrecognized request argument: messages[1].reasoning_content", 400)
            yield "open", None
            yield "reasoning", "Let me think."
            yield "text", "ok"
            yield "done", TurnResult(content="ok", finish_reason="stop", usage=self.usage)
        return go()


THOUGHT = {"role": "assistant", "content": [{"type": "thinking", "thinking": "Earlier thought.", "signature": bridge.SIGNATURE},
                                            {"type": "text", "text": "Earlier answer."}]}


async def test_the_models_reasoning_is_shown_and_handed_back_as_claude_codes_is():
    """As with Claude: the reasoning shows as Claude Code's thinking (not when its thinking is off), and
    what Claude Code sends back of it reaches the model with the conversation."""
    p, fake = make(fake=Reasons())
    r = await call(p, "POST", "/v1/messages", message("private/glm-5-3"))
    blocks = [d["content_block"]["type"] for n, d in parse(r.content) if n == "content_block_start"]
    assert blocks == ["thinking", "text"]
    r = await call(p, "POST", "/v1/messages", message("private/glm-5-3", thinking={"type": "disabled"}))
    assert [d["content_block"]["type"] for n, d in parse(r.content) if n == "content_block_start"] == ["text"]
    body = message("private/glm-5-3", stream=False)
    body["messages"] = [{"role": "user", "content": "first"}, THOUGHT, {"role": "user", "content": "next"}]
    reply = (await call(p, "POST", "/v1/messages", body)).json()
    assert reply["content"][0] == {"type": "thinking", "thinking": "Let me think.", "signature": bridge.SIGNATURE}
    sent = fake.calls[-1][1]
    assert [m.get("reasoning_content") for m in sent if m["role"] == "assistant"] == ["Earlier thought."]


async def test_a_provider_that_wont_take_reasoning_handed_back_is_asked_again_without_it_from_then_on(tmp_path):
    log = UsageLog(tmp_path / "requests.log", "s1")
    p, fake = make(fake=Reasons(refuse_field=True), log=log)
    body = message("private/glm-5-3")
    body["messages"] = [{"role": "user", "content": "first"}, THOUGHT, {"role": "user", "content": "next"}]
    for _ in range(2):
        assert (await call(p, "POST", "/v1/messages", body)).status_code == 200
    assert len(fake.calls) == 3                                   # refused once; then never sent again
    assert not any("reasoning_content" in m for m in fake.calls[-1][1])
    assert [r.get("reasoning_back") for r in read(tmp_path / "requests.log")] == [False, False]   # the log says so


async def test_our_thinking_never_reaches_a_claude_model():
    """Another model's reasoning, signed as DA Private Coder's, would be refused by Anthropic's API: a
    standard session that turns to Claude takes it out; Claude's own thinking goes as it is."""
    seen = []

    def nanogpt(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, text='event: message_stop\ndata: {"type":"message_stop"}\n\n',
                              headers={"content-type": "text/event-stream"})
    p, fake = make(NANO, handler=nanogpt)
    claudes = {"type": "thinking", "thinking": "Claude's own.", "signature": "EqQBCkgIBRAB"}
    body = message("anthropic/claude-opus-5.5")
    body["messages"] = [{"role": "user", "content": "first"}, THOUGHT,
                        {"role": "user", "content": "next"}, {"role": "assistant", "content": [claudes, {"type": "text", "text": "x"}]},
                        {"role": "user", "content": "more"}]
    assert (await call(p, "POST", "/v1/messages", body)).status_code == 200
    history = seen[0]["messages"]
    assert history[1]["content"] == [{"type": "text", "text": "Earlier answer."}]
    assert history[3]["content"][0] == claudes


# --- an answer with no stream in it is said, never taken for an empty reply (SealedLore's e947938, as DA Vibe
# Manager's d6f1ca8) -------------------------------------------------------------------------------------------

def _answering(status: int, content: bytes = b"", headers: dict | None = None):
    """A chat client whose provider answers every request so, and the requests it was sent."""
    from daprivatecoder.llm.client import LLMClient

    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, content=content, headers=headers or {})
    return LLMClient("https://example.test/v1", "k", httpx.AsyncClient(transport=httpx.MockTransport(handler))), seen


async def _streamed(client, exact: bool = False) -> list:
    return [e async for e in client.stream("z-ai/glm-5.3", [{"role": "user", "content": "hi"}], exact=exact)]


@pytest.mark.parametrize(("body", "kind"), [
    (b"<html><body>Sign in to the Wi-Fi</body></html>", "text/html"),
    (b"", "text/event-stream"),
    (b'{"id": "x", "choices": []}', "application/json"),
])
@pytest.mark.parametrize("exact", [False, True])
async def test_an_answer_with_no_stream_in_it_is_said_never_an_empty_reply(body, kind, exact):
    """A web page answering 200 where the API was expected (an address without its /v1, a sign-in page on a local
    network), or a body with no event in it, ended Claude Code's turn with an empty reply and no reason: the SDK read
    it as a stream with nothing in it. A TEE model's reply (exact) went the same way."""
    from daprivatecoder.llm.secure_http import ProviderError

    client, seen = _answering(200, body, {"content-type": kind})
    with pytest.raises(ProviderError) as info:
        await _streamed(client, exact)
    said = str(info.value)
    assert f"https://example.test/v1/chat/completions answered HTTP 200 ({kind}) with no reply in it" in said
    assert "an address without its /v1" in said and info.value.status_code == 200
    assert len(seen) == 1, "nothing asked again"


async def test_a_stream_is_read_whatever_its_content_type_says():
    """Real servers label their streams every way (llama.cpp, proxies): a body with `data:` lines is the reply."""
    stream = b'data: {"id": "c", "choices": [{"delta": {"content": "fine"}, "finish_reason": "stop"}]}\n\ndata: [DONE]\n\n'
    for kind in ("text/html", "application/json", "text/plain", None):
        client, _ = _answering(200, stream, {"content-type": kind} if kind else {})
        events = await _streamed(client)
        assert "".join(v for k, v in events if k == "text") == "fine", kind
        assert events[-1][1].finish_reason == "stop" and events[-1][1].body_hash is None, "hashed only for a TEE reply"
    # and one that says nothing but [DONE] is an empty reply, as the endpoint sent it
    client, _ = _answering(200, b"data: [DONE]\n\n", {"content-type": "text/event-stream"})
    events = await _streamed(client)
    assert events[-1][0] == "done" and events[-1][1].content == ""


async def test_claude_code_is_told_of_an_answer_with_no_stream_in_it_not_given_an_empty_reply(tmp_path):
    """Through the proxy: streamed, an error event in place of an empty turn; asked whole, a 502 that says what came,
    not asked again (the address is wrong, not busy). The address is said without a password written into it."""
    from daprivatecoder.llm.client import LLMClient

    page = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(
        200, content=b"<html>Not here</html>", headers={"content-type": "text/html; charset=utf-8"})))

    class Plain:
        def factory(self, url, key):
            return LLMClient(url, key, page)
    log = UsageLog(tmp_path / "requests.log", "s1")
    p, _ = make(NANO, plain=Plain(), log=log, base="https://user:secret@nano-gpt.com/api/v1")
    r = await call(p, "POST", "/v1/messages", message("z-ai/glm-5.3"))
    whole = await call(p, "POST", "/v1/messages", message("z-ai/glm-5.3", stream=False))
    events = parse(r.content)
    assert not [d for n, d in events if n == "content_block_delta"]
    told = [d for n, d in events if n == "error"][0]["error"]["message"]
    assert told.startswith("z-ai/glm-5.3: https://…@nano-gpt.com/api/v1/chat/completions answered HTTP 200 (text/html)")
    assert whole.status_code == 502 and "with no reply in it" in whole.json()["error"]["message"]
    assert whole.headers.get("x-should-retry") == "false"
    logged = [e for e in read(tmp_path / "requests.log") if e["kind"] == "request" and e.get("tier")]
    assert [e["status"] for e in logged] == [502, 502] and all("with no reply in it" in e["error"] for e in logged)
    assert "secret" not in (tmp_path / "requests.log").read_text() + told + whole.text
