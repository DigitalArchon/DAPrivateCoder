"""Anthropic as a standard profile's provider (providers.py): every request Claude Code makes goes to
api.anthropic.com as Claude Code wrote it, never translated, with the Anthropic key in x-api-key, and that key goes
to Anthropic's address alone."""

import json
from dataclasses import replace

import httpx
import pytest

from daprivatecoder import config, creds, manager, providers
from daprivatecoder.config import ConfigError, Profile, parse
from daprivatecoder.server import create_app

from test_proxy import TOKEN, FakePlain, call, make, message
from test_window import TOKEN as WINDOW_TOKEN
from test_window import call as window_call

ANTHROPIC = Profile("claude", "standard", "claude-sonnet-5", "claude-opus-5-5", "claude-haiku-5", "", "high",
                    provider="anthropic")
SSE = ('event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":12}}}\n\n'
       'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":9}}\n\n'
       'event: message_stop\ndata: {"type":"message_stop"}\n\n')


def anthropic_server(seen):
    def answer(request):
        seen.append(request)
        if request.url.path == "/v1/messages/count_tokens":
            return httpx.Response(200, json={"input_tokens": 4321})
        return httpx.Response(200, text=SSE, headers={"content-type": "text/event-stream", "request-id": "req_a"})
    return answer


async def test_every_request_goes_to_anthropic_as_claude_code_wrote_it_with_the_key_in_x_api_key():
    seen, plain = [], FakePlain()
    p, fake = make(ANTHROPIC, handler=anthropic_server(seen), plain=plain,
                   provider_url=providers.ANTHROPIC_BASE_URL, provider_key=lambda: "sk-ant-123456")
    tools = [{"name": "Bash", "description": "run", "input_schema": {"type": "object"}}]
    for asked in ("claude-sonnet-5", "claude-haiku-4-5-20251001", "claude-opus-5-5[1m]", None):
        body = message(asked, tools=tools, thinking={"type": "adaptive"}, system=[{"type": "text", "text": "You are"}])
        r = await call(p, "POST", "/v1/messages?beta=true", body,
                       headers={"anthropic-version": "2023-06-01", "anthropic-beta": "claude-code-20250219",
                                "x-claude-code-session-id": "38826834"})
        assert r.status_code == 200 and "message_stop" in r.text and r.headers["request-id"] == "req_a", asked
        sent = seen[-1]
        assert str(sent.url) == "https://api.anthropic.com/v1/messages?beta=true"
        assert sent.headers["x-api-key"] == "sk-ant-123456" and "authorization" not in sent.headers
        assert sent.headers["anthropic-beta"] == "claude-code-20250219" and "x-claude-code-session-id" not in sent.headers
        assert TOKEN not in str(sent.headers)                     # the session's token stays here
        # the request as Claude Code wrote it: Claude Code's own model names included (its quick model's too)
        assert json.loads(sent.content) == {**body, "model": asked or ANTHROPIC.model}
    r = await call(p, "POST", "/v1/messages/count_tokens", {"model": "claude-sonnet-5", "messages": []})
    assert r.json() == {"input_tokens": 4321} and seen[-1].url.path == "/v1/messages/count_tokens"   # Anthropic's count
    assert not plain.calls and not plain.made and not fake.calls                                       # nothing translated


async def test_a_private_model_picked_on_anthropic_is_still_sealed_and_nothing_after_it_goes_to_anthropic():
    seen = []
    p, fake = make(ANTHROPIC, handler=anthropic_server(seen), provider_url=providers.ANTHROPIC_BASE_URL,
                   provider_key=lambda: "sk-ant-123456")
    r = await call(p, "POST", "/v1/messages", message("private/glm-5-3"))
    assert r.status_code == 200 and fake.calls[-1][0] == "private/glm-5-3" and not seen
    r = await call(p, "POST", "/v1/messages", message("claude-sonnet-5"))
    assert r.status_code == 403 and "has used a private model" in r.text and not seen


async def test_the_anthropic_key_is_read_from_the_keyring_for_anthropics_address_alone(ring):
    seen = []
    creds.set_key("sk-ant-123456", "anthropic", providers.ANTHROPIC_BASE_URL)
    assert ring.store[("daprivatecoder", "anthropic")] == "sk-ant-123456"
    assert ring.store[("daprivatecoder", "anthropic_origin")] == "https://api.anthropic.com:443"
    p, _ = make(ANTHROPIC, handler=anthropic_server(seen), provider_url=providers.ANTHROPIC_BASE_URL)
    assert (await call(p, "POST", "/v1/messages", message("claude-sonnet-5"))).status_code == 200
    assert seen[-1].headers["x-api-key"] == "sk-ant-123456"
    with pytest.raises(creds.KeyElsewhere, match="goes only to https://api.anthropic.com"):
        creds.get_key("anthropic", "https://api.anthropic.com.example.net")
    creds.delete_key("anthropic", providers.ANTHROPIC_BASE_URL)
    p, _ = make(ANTHROPIC, handler=anthropic_server(seen), provider_url=providers.ANTHROPIC_BASE_URL)
    r = await call(p, "POST", "/v1/messages", message("claude-sonnet-5"))
    assert r.status_code == 403 and "No Anthropic API key is stored" in r.text and len(seen) == 1


def test_an_anthropic_profile_is_standard_and_its_address_is_anthropics_whatever_the_file_says():
    cfg = parse('[profiles.s]\nprovider = "anthropic"\nbase_url = "https://anthropic.example.net"\n'
                'model = "claude-sonnet-5"\n')
    assert cfg.provider_url(cfg.profile("s")) == "https://api.anthropic.com" and cfg.profile("s").base_url == ""
    assert "\nbase_url = " not in config.render(cfg).split("[profiles.s]")[1]
    assert '"anthropic" (Anthropic\'s own API' in config.render(cfg)
    for kind in ("private", "tee"):
        with pytest.raises(ConfigError, match=f"a {kind} profile's models are NanoGPT's"):
            parse(f'[profiles.{kind}]\nkind = "{kind}"\nprovider = "anthropic"\nmodel = "{kind}/x"\n')


@pytest.fixture
def window(monkeypatch, tmp_path):
    monkeypatch.setattr("daprivatecoder.launcher.EXTENSION_DIRS", ())
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    monkeypatch.setattr(manager.editor, "EDITORS", ())
    return create_app(WINDOW_TOKEN, manager.Manager())


async def test_the_window_takes_anthropic_and_its_key_and_lists_its_models_with_it(window, ring, monkeypatch):
    st = (await window_call(window, "POST", "/api/profile", {"profile": "standard",
                                                             "fields": {"provider": "anthropic"}}))[1]
    assert st["config"]["profiles"]["standard"]["provider"] == "anthropic"
    assert st["provider_keys"]["standard"]["provider"] == "anthropic" and not st["provider_key"]["stored"]
    status, said = await window_call(window, "GET", "/api/models?profile=standard&fresh=1")
    assert status == 400 and "needs your Anthropic API key" in said["error"]
    status, said = await window_call(window, "POST", "/api/key", {"key": " ", "provider": "anthropic"})
    assert status == 400 and "console.anthropic.com" in said["error"]
    st = (await window_call(window, "POST", "/api/key", {"key": "sk-ant-123456", "provider": "anthropic"}))[1]
    assert st["provider_key"]["stored"] and st["provider_key"]["ending"] == "3456"
    assert ring.store[("daprivatecoder", "anthropic_origin")] == "https://api.anthropic.com:443"
    asked = []

    def anthropic(request):
        asked.append(request)
        return httpx.Response(200, json={"data": [{"type": "model", "id": "claude-sonnet-5"},
                                                  {"type": "model", "id": "claude-opus-5-5"}], "has_more": False})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: real(*a, **{"transport": httpx.MockTransport(anthropic),
                                                                            **kw}))
    status, listed = await window_call(window, "GET", "/api/models?profile=standard&fresh=1")
    assert status == 200 and [m["id"] for m in listed["models"]] == ["claude-opus-5-5", "claude-sonnet-5"]
    assert str(asked[0].url) == "https://api.anthropic.com/v1/models?limit=1000"
    assert asked[0].headers["x-api-key"] == "sk-ant-123456" and asked[0].headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in asked[0].headers
    cfg = config.load()
    assert cfg.provider_url(cfg.profile("standard")) == providers.ANTHROPIC_BASE_URL
    config.save(cfg.with_profile(replace(cfg.profile("standard"), provider="nanogpt")))
    assert ring.store[("daprivatecoder", "anthropic")] == "sk-ant-123456"     # kept, for when it's chosen again
