"""Setup takes any provider of the table (NanoGPT, the default, Chutes, Tinfoil, OpenRouter, Anthropic, one of your
own) and asks for that provider's key alone: a key of Anthropic's, Tinfoil's or Chutes' gets a session started
without NanoGPT's. Web search goes through NanoGPT, so without its key a session starts with search off, and says so."""

import json
from dataclasses import replace

import pytest

from daprivatecoder import config, creds, launcher, manager
from daprivatecoder.server import create_app

from test_proxy import NANO, FakePlain, call, make, message
from test_window import TOKEN as WINDOW_TOKEN
from test_window import call as window_call


@pytest.fixture
def window(monkeypatch, tmp_path):
    monkeypatch.setattr("daprivatecoder.launcher.EXTENSION_DIRS", ())
    monkeypatch.setattr("daprivatecoder.launcher.find_claude", lambda configured="": "/usr/bin/claude")
    monkeypatch.setattr(manager.editor, "EDITORS", ())
    return create_app(WINDOW_TOKEN, manager.Manager())


async def test_setup_is_nanogpts_unless_another_is_chosen_and_then_that_providers_key_alone_is_asked(window, ring):
    st = (await window_call(window, "GET", "/api/state"))[1]
    assert st["setup"] == "nanogpt" and st["setup_ready"] is False and st["setup_key"]["provider"] == "nanogpt"
    for provider, kind, key in (("anthropic", "standard", "sk-ant-123456"), ("tinfoil", "private", "tk_123456"),
                                ("chutes", "tee", "cpk_123456")):
        st = (await window_call(window, "POST", "/api/setup", {"provider": provider}))[1]
        assert st["setup"] == provider and st["start"] == kind, provider
        assert st["config"]["profiles"][kind]["provider"] == provider and st["setup_ready"] is False
        st = (await window_call(window, "POST", "/api/key", {"key": key, "provider": provider}))[1]
        assert st["setup_ready"] is True and st["ready"][kind] is True and st["setup_key"]["ending"] == key[-4:], provider
        assert not st["key"]["stored"]                                     # no NanoGPT key anywhere in this
    assert st["config"]["profiles"]["tee"]["model"] == "zai-org/GLM-5.2-TEE"     # Chutes' own models, for the kind
    assert st["ready"]["standard"] is True                                # Anthropic's, still set up
    status, said = await window_call(window, "POST", "/api/setup", {"provider": "custom"})
    assert status == 400 and "address" in said["error"]
    st = (await window_call(window, "POST", "/api/setup", {"provider": "custom-anthropic",
                                                           "base_url": "https://api.example.com/anthropic"}))[1]
    assert st["setup_ready"] is True                                      # a server of your own may need no key
    assert st["config"]["profiles"]["standard"]["base_url"] == "https://api.example.com/anthropic"
    st = (await window_call(window, "POST", "/api/setup", {"provider": "nanogpt"}))[1]
    assert st["setup"] == "nanogpt" and st["setup_ready"] is False         # NanoGPT's own key, as before
    st = (await window_call(window, "POST", "/api/key", {"key": "sk-nano-123456"}))[1]
    assert st["setup_ready"] is True


def test_without_nanogpts_key_a_session_starts_with_web_search_off_and_says_so(ring):
    cfg = config.load()
    profile = replace(cfg.profile("standard"), provider="anthropic", web_search="best")
    off = launcher.searching(cfg, profile)
    assert off.web_search == "off" and launcher.mcp_config(off, None, {}) is None
    assert "WebSearch" in launcher.session_settings(off, {})["permissions"]["deny"]
    assert "no NanoGPT API key is stored" in launcher.NO_SEARCH_KEY
    creds.set_key("sk-nano-123456", "nanogpt", cfg.base_url)
    assert launcher.searching(cfg, profile).web_search == "best"


async def test_a_standard_session_on_nanogpt_or_openrouter_treats_claude_names_as_before():
    """As in 0.1.0b7: a Claude model picked by its provider's name (anthropic/…) goes to the provider's Anthropic
    endpoint as Claude Code wrote it; Claude Code's own quick work under a Haiku name goes to the profile's quick
    model; another of Claude Code's own names (claude-opus-…) is refused, so nothing goes to Claude unless picked."""
    import httpx
    seen = []

    def anthropic_endpoint(request):
        seen.append(request)
        return httpx.Response(200, json={"type": "message", "content": [], "usage": {"input_tokens": 1}})
    for provider, url in (("nanogpt", "https://nano-gpt.com/api/v1"), ("openrouter", "https://openrouter.ai/api/v1")):
        plain = FakePlain()
        p, _ = make(replace(NANO, provider=provider), handler=anthropic_endpoint, plain=plain, provider_url=url,
                    provider_key=lambda: "sk-1")
        r = await call(p, "POST", "/v1/messages", message("anthropic/claude-opus-5.5", stream=False))
        assert r.status_code == 200 and str(seen[-1].url) == f"{url}/messages", provider
        r = await call(p, "POST", "/v1/messages", message("claude-haiku-4-5", stream=provider == "openrouter"))
        if provider == "nanogpt":           # NanoGPT's Anthropic endpoint takes its other models too (messages_any)
            assert r.status_code == 200 and json.loads(seen[-1].content)["model"] == NANO.fast_model, provider
        else:
            assert r.status_code == 200 and plain.calls[-1][0] == NANO.fast_model, provider
        r = await call(p, "POST", "/v1/messages", message("claude-opus-5-5"))
        assert r.status_code == 400 and "anthropic/… for Claude" in r.json()["error"]["message"], provider
