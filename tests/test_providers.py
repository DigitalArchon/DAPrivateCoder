"""The providers a profile's models come from are one table (providers.py): the settings, the keyring, the proxy,
the window's script and the commands all read it, so a provider is added in one place, and each one's key goes to
its own addresses alone."""

import io
import json
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from daprivatecoder import cli, config, creds, providers
from daprivatecoder.config import ConfigError, parse

APP_JS = Path(__file__).resolve().parents[1] / "src" / "daprivatecoder" / "web" / "app.js"
NODE = shutil.which("node")


def window_providers() -> dict:
    """app.js's PROVIDERS, as node reads it."""
    text = APP_JS.read_text(encoding="utf-8")
    found = re.search(r"^const PROVIDERS = \{$.*?^\};$", text, re.M | re.S)
    assert found, "app.js has no PROVIDERS table"
    done = subprocess.run([NODE, "-e", found.group(0) + "\nconsole.log(JSON.stringify(PROVIDERS));"],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.skipif(NODE is None, reason="no node here to run the window's script")
def test_the_window_offers_the_tables_providers_in_its_order_for_the_kinds_they_serve():
    shown = window_providers()
    assert list(shown) == list(providers.PROVIDERS)
    for pid, spec in providers.PROVIDERS.items():
        assert shown[pid]["label"] == spec.label and shown[pid]["name"] == spec.name, pid
        assert shown[pid]["kinds"] == list(spec.kinds), pid
        assert bool(shown[pid].get("own")) == spec.own_address, pid
    assert list(config.PROVIDERS) == list(providers.PROVIDERS)
    assert cli._parser().parse_args(["key", "set", "--provider", "openrouter"]).provider == "openrouter"


def test_a_profile_takes_only_a_provider_that_serves_its_kind():
    """A private profile is NanoGPT's private/ models or Tinfoil's, a TEE one NanoGPT's TEE/ ones or Chutes' -TEE
    ones (the highest privacy, and partial)."""
    assert providers.for_kind("private") == ["nanogpt", "tinfoil"]
    assert providers.for_kind("tee") == ["nanogpt", "chutes"]
    with pytest.raises(ConfigError, match="a private profile's models are NanoGPT's or Tinfoil's, so its provider is "
                                          "nanogpt or tinfoil"):
        parse('[profiles.private]\nkind = "private"\nprovider = "openrouter"\nmodel = "private/x"\n')
    with pytest.raises(ConfigError, match="a tee profile's models are NanoGPT's or Chutes', so its provider is "
                                          "nanogpt or chutes"):
        parse('[profiles.tee]\nkind = "tee"\nprovider = "tinfoil"\nmodel = "TEE/x"\n')
    assert set(providers.for_kind("standard")) == set(providers.PROVIDERS) - {"tinfoil"}


def test_a_key_for_a_provider_whose_address_is_fixed_goes_there_alone_whatever_is_stored_beside_it(ring):
    """OpenRouter's key was held to whatever origin was stored beside it, so a key stored (or an origin written by
    another program) for another address went there."""
    elsewhere = "https://openrouter.example.net/api/v1"
    creds.set_key("sk-or-123456", "openrouter", elsewhere)
    with pytest.raises(creds.KeyElsewhere, match="goes only to https://openrouter.ai, and would go to "
                                                 "https://openrouter.example.net/api/v1 now"):
        creds.get_key("openrouter", elsewhere)
    assert creds.get_key("openrouter", providers.OPENROUTER_BASE_URL) == "sk-or-123456"


def test_key_set_for_a_provider_of_your_own_needs_the_standard_profile_on_one(ring, monkeypatch, capsys):
    """It stored the key as the provider of your own's at NanoGPT's address when the standard profile was on
    NanoGPT: the base URL it looked for was never empty."""
    monkeypatch.setattr("sys.stdin", io.StringIO("sk-box-123456\n"))
    assert cli.main(["key", "set", "--provider", "custom"]) == 1
    assert "isn't one of your own" in capsys.readouterr().err
    assert not any(user.startswith("custom:") for _, user in ring.store)
    cfg = config.load()
    config.save(cfg.with_profile(replace(cfg.profile("standard"), provider="custom",
                                         base_url="https://llm.example.com/v1")))
    monkeypatch.setattr("sys.stdin", io.StringIO("sk-box-123456\n"))
    assert cli.main(["key", "set", "--provider", "custom"]) == 0
    assert ring.store[("daprivatecoder", "custom:llm.example.com")] == "sk-box-123456"


def test_each_providers_key_goes_in_its_own_header():
    assert providers.auth_headers("nanogpt", "k") == {"authorization": "Bearer k"}
    assert providers.auth_headers("custom", None) == {}


async def test_a_claude_endpoint_a_provider_keeps_elsewhere_gets_claude_models_with_the_key_held_to_it(ring, monkeypatch):
    """How Chutes' claude.chutes.ai is to slot in: a provider whose Anthropic endpoint isn't at its own address gets a
    Claude model's request there, as Claude Code wrote it, with its key only if the key may go to that address."""
    import httpx

    from test_proxy import NANO, call, make, message
    claude_at = "https://claude.openrouter.example"
    spec = providers.PROVIDERS["openrouter"]
    monkeypatch.setitem(providers.PROVIDERS, "openrouter", replace(spec, claude=claude_at))
    seen = []

    def server(request):
        seen.append(request)
        return httpx.Response(200, json={"type": "message", "content": [], "usage": {"input_tokens": 1}})
    profile = replace(NANO, provider="openrouter")
    creds.set_key("sk-or-123456", "openrouter", providers.OPENROUTER_BASE_URL)
    p, _ = make(profile, handler=server, provider_url=providers.OPENROUTER_BASE_URL)
    r = await call(p, "POST", "/v1/messages", message("anthropic/claude-opus-5.5", stream=False))
    assert r.status_code == 403 and "goes only to https://openrouter.ai" in r.text and not seen
    monkeypatch.setitem(providers.PROVIDERS, "openrouter", replace(
        spec, claude=claude_at, key_origins=spec.key_origins | {"https://claude.openrouter.example:443"}))
    p, _ = make(profile, handler=server, provider_url=providers.OPENROUTER_BASE_URL)
    r = await call(p, "POST", "/v1/messages", message("anthropic/claude-opus-5.5", stream=False))
    assert r.status_code == 200 and str(seen[-1].url) == "https://claude.openrouter.example/v1/messages"
    assert seen[-1].headers["authorization"] == "Bearer sk-or-123456"
