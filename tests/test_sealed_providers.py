"""Tinfoil direct and Chutes as providers (preliminary): which models are sealed, and by whom, follows from the
address their requests go to, however that address came into the settings (Tinfoil or Chutes chosen, or typed as a
provider of your own's); a session moves only to a model as private as the most private it has used; each key goes
to its own service's addresses alone."""

from dataclasses import replace

import httpx
import pytest

from daprivatecoder import config, creds, manager, providers
from daprivatecoder.config import ConfigError, Profile, parse
from daprivatecoder.llm import capabilities, privacy
from daprivatecoder.llm.client import TurnResult
from daprivatecoder.server import create_app

from test_proxy import FakePlain, FakePrivate, call, make, message
from test_window import TOKEN as WINDOW_TOKEN
from test_window import call as window_call

TINFOIL = providers.TINFOIL_BASE_URL
CHUTES = providers.CHUTES_BASE_URL
ON_TINFOIL = Profile("private", "private", "glm-5-3", "kimi-k3-1-pro", "deepseek-v4-1-flash", "", "low",
                     provider="tinfoil")
ON_CHUTES = Profile("tee", "tee", "zai-org/GLM-5.2-TEE", "moonshotai/Kimi-K3-TEE",
                    "deepseek-ai/DeepSeek-V4-Flash-0731-TEE", "", "low", provider="chutes")
CHUTES_STANDARD = Profile("chutes", "standard", "zai-org/GLM-5.3", "moonshotai/Kimi-K3-TEE", "zai-org/GLM-5.3", "",
                          "low", provider="chutes")


class FakeSealed:
    """Stands in for TinfoilDirectClient or ChutesE2EEClient: what it was made with and asked, sealed."""

    def __init__(self):
        self.made, self.calls = [], []

    def tinfoil(self, base_url, key, state):
        self.made.append((base_url, key))
        return self

    def chutes(self, base_url, key, key_origin, state, on_attested, client=None):
        self.made.append((base_url, key, key_origin))
        self.state = state
        return self

    def stream(self, model, messages, tools=None, params=None, *, opened=False):
        self.calls.append((model, params, opened))

        async def go():
            if opened:
                yield "open", None
            yield "text", f"sealed {model}"
            yield "done", TurnResult(content="x", finish_reason="stop", usage={"prompt_tokens": 1000,
                                                                                 "completion_tokens": 10})
        return go()


TINFOIL_CAPS = {"glm-5-3": {"vision": False, "tools": True, "context": 1048576, "efforts": ["low", "high", "max"],
                            "pricing": {"input": 1.8, "output": 6.0, "cache_read": 0.4}},
                "deepseek-v4-1-flash": {"vision": True, "tools": True, "efforts": ["none", "low", "high"],
                                        "pricing": None}}


def sealed_proxy(profile, provider_url, *, key="sk-svc-123456", caps=None, plain=None, fake=None, handler=None,
                 private=None):
    fake = fake or FakeSealed()

    def nothing_in_the_clear(request):
        pytest.fail(f"nothing may go in the clear: {request.method} {request.url}")
    p, private_fake = make(profile, fake=private, plain=plain or FakePlain(), provider_url=provider_url,
                           provider_key=(lambda: key), provider_caps=caps if caps is not None else TINFOIL_CAPS,
                           handler=handler or nothing_in_the_clear)
    p._tinfoil_client, p._chutes_client = fake.tinfoil, fake.chutes
    return p, fake, private_fake


# ---------------------------------------------------------------------- recognised by the address, every way in

@pytest.mark.parametrize("provider, address", [
    ("tinfoil", TINFOIL), ("custom", "https://inference.tinfoil.sh/v1"), ("custom", "https://INFERENCE.Tinfoil.sh./v1"),
    ("custom", "https://router.inference.tinfoil.sh/v1"), ("custom-anthropic", "https://inference.tinfoil.sh")])
def test_every_model_at_tinfoils_address_is_sealed_to_tinfoil_whichever_provider_names_it(provider, address):
    for model in ("glm-5-3", "z-ai/glm-5.3", "anthropic/claude-opus-5.5", "TEE/glm-5.3", "Qwen/Qwen3-32B-TEE"):
        found = providers.privacy_of(provider, address, model)
        assert found.service == privacy.TINFOIL and found.level == privacy.HIGHEST, (provider, address, model)
        assert providers.tier_of(provider, address, model) == "private"
    assert providers.service_at(address) == "tinfoil" and "private" in providers.kinds_at(provider, address)


@pytest.mark.parametrize("provider, address", [
    ("chutes", CHUTES), ("custom", "https://llm.chutes.ai/v1"), ("custom-anthropic", "https://claude.chutes.ai"),
    ("custom", "https://LLM.Chutes.AI./v1")])
def test_a_tee_model_at_chutes_address_is_sealed_to_chutes_and_its_others_go_in_the_clear(provider, address):
    for model in ("moonshotai/Kimi-K3-TEE", "Qwen/Qwen3-32B-tee", " zai-org/GLM-5.2-TEE:THINKING "):
        found = providers.privacy_of(provider, address, model)
        assert found.service == privacy.CHUTES and found.level == privacy.PARTIAL, (provider, model)
        assert providers.tier_of(provider, address, model) == "tee"
    for model in ("zai-org/GLM-5.3", "TEE/glm-5.3"):      # a NanoGPT TEE/ name is attested only on NanoGPT
        assert providers.tier_of(provider, address, model) == "standard", model
    assert providers.privacy_of(provider, address, "private/glm-5-3").service == privacy.NANOGPT_PRIVATE
    assert "tee" in providers.kinds_at(provider, address)


def test_a_chutes_tee_name_elsewhere_is_no_chutes_model_and_a_nanogpt_tee_one_is_attested_only_on_nanogpt():
    assert providers.tier_of("nanogpt", providers.NANOGPT_BASE_URL, "Qwen/Qwen3-32B-TEE") == "standard"
    assert providers.tier_of("nanogpt", providers.NANOGPT_BASE_URL, "TEE/glm-5.3") == "tee"
    assert providers.tier_of("openrouter", providers.OPENROUTER_BASE_URL, "TEE/glm-5.3") == "standard"


def test_the_settings_take_tinfoil_and_chutes_for_the_kinds_they_serve_and_say_what_doesnt_fit():
    cfg = parse('[profiles.private]\nprovider = "tinfoil"\nmodel = "glm-5-3"\n'
                '[profiles.tee]\nprovider = "chutes"\nmodel = "moonshotai/Kimi-K3-TEE"\n'
                'fast_model = "private/deepseek-v4-1-flash"\n')
    assert cfg.provider_url(cfg.profile("private")) == TINFOIL and cfg.provider_url(cfg.profile("tee")) == CHUTES
    assert parse(config.render(cfg)).profiles == cfg.profiles
    with pytest.raises(ConfigError, match="'zai-org/GLM-5.3', which isn't one of Chutes' end-to-end encrypted models"):
        parse('[profiles.tee]\nprovider = "chutes"\nmodel = "zai-org/GLM-5.3"\n')
    with pytest.raises(ConfigError, match="a standard profile's models are"):
        parse('[profiles.s]\nprovider = "tinfoil"\nmodel = "glm-5-3"\n')
    # typed as a provider of your own's address (or kept so in an old file): its service's kinds
    typed = parse('[profiles.private]\nprovider = "custom"\nbase_url = "https://inference.tinfoil.sh/v1"\n'
                  'model = "glm-5-3"\n[profiles.tee]\nprovider = "custom"\nbase_url = "https://llm.chutes.ai/v1"\n'
                  'model = "moonshotai/Kimi-K3-TEE"\n')
    assert typed.profile("private").provider == "custom" and typed.profile("tee").model == "moonshotai/Kimi-K3-TEE"
    with pytest.raises(ConfigError, match="isn't one of Chutes' end-to-end encrypted models"):
        parse('[profiles.tee]\nprovider = "custom"\nbase_url = "https://llm.chutes.ai/v1"\nmodel = "zai-org/GLM-5.3"\n')


# ---------------------------------------------------------------------- the proxy

async def test_a_private_session_on_tinfoil_sends_every_request_sealed_to_tinfoil_with_its_key(tmp_path):
    from daprivatecoder.usagelog import UsageLog, read
    p, fake, private = sealed_proxy(ON_TINFOIL, TINFOIL)
    p.log = UsageLog(tmp_path / "requests.log", "s1")
    for asked, sent in (("glm-5-3", "glm-5-3"), ("kimi-k3-1-pro", "kimi-k3-1-pro"),
                        ("claude-haiku-4-5", "deepseek-v4-1-flash"), (None, "glm-5-3")):
        r = await call(p, "POST", "/v1/messages", message(asked))
        assert r.status_code == 200 and fake.calls[-1][0] == sent and fake.calls[-1][2] is True, asked
    assert set(fake.made) == {(TINFOIL, "sk-svc-123456")} and not private.calls
    recs = [r for r in read(tmp_path / "requests.log") if r["kind"] == "request"]
    assert {(r["tier"], r["sealed"], r["service"], r["provider"]) for r in recs} == {("private", True, "tinfoil", "tinfoil")}
    assert recs[0]["usage"]["cost"] == pytest.approx((1000 * 1.8 + 10 * 6.0) / 1e6)    # its listed prices
    r = await call(p, "POST", "/v1/messages", message("claude-opus-5-5"))                # Claude's own: refused
    assert r.status_code == 400 and len(fake.calls) == 4
    # at Tinfoil's address every model is Tinfoil's, a private/ name too (when in doubt, sealed): never NanoGPT's
    r = await call(p, "POST", "/v1/messages", message("private/glm-5-3"))
    assert r.status_code == 200 and fake.calls[-1][0] == "private/glm-5-3" and not private.calls


async def test_a_provider_of_your_own_at_tinfoils_address_is_tinfoil_never_in_the_clear():
    own = replace(ON_TINFOIL, name="own", kind="standard", provider="custom", base_url="https://inference.tinfoil.sh/v1")
    p, fake, _ = sealed_proxy(own, own.base_url)
    r = await call(p, "POST", "/v1/messages", message("glm-5-3"))
    assert r.status_code == 200 and fake.calls[-1][0] == "glm-5-3" and fake.made == [(own.base_url, "sk-svc-123456")]
    assert p.tier("z-ai/glm-5.3") == "private"


async def test_a_tee_session_on_chutes_seals_its_tee_models_and_never_asks_nanogpt_to_attest_them(tmp_path):
    from daprivatecoder.usagelog import UsageLog, read
    from test_proxy import Attester
    attester = Attester()
    p, fake, _ = sealed_proxy(ON_CHUTES, CHUTES, caps={})
    p._tee_check = attester
    p.log = UsageLog(tmp_path / "requests.log", "s1")
    for asked in ("zai-org/GLM-5.2-TEE", "moonshotai/Kimi-K3-TEE", "deepseek-ai/DeepSeek-V4-Flash-0731-TEE"):
        r = await call(p, "POST", "/v1/messages", message(asked))
        assert r.status_code == 200 and fake.calls[-1][0] == asked
    assert not attester.asked                                   # Chutes' own client attests each instance itself
    assert fake.made[0] == (CHUTES, "sk-svc-123456", "https://llm.chutes.ai:443")
    r = await call(p, "POST", "/v1/messages", message("zai-org/GLM-5.3"))                 # in the clear: refused
    assert r.status_code == 400 and len(fake.calls) == 3
    recs = [r for r in read(tmp_path / "requests.log") if r["kind"] == "request" and r.get("status") == 200]
    assert {(r["tier"], r["sealed"], r["service"]) for r in recs} == {("tee", True, "chutes")}
    assert manager._sent(recs[0]) == "chutes"


async def test_a_standard_session_on_chutes_sends_its_others_in_the_clear_and_moves_only_to_as_private_or_more():
    plain = FakePlain()
    p, fake, _ = sealed_proxy(CHUTES_STANDARD, CHUTES, caps={}, plain=plain)
    r = await call(p, "POST", "/v1/messages", message("zai-org/GLM-5.3"))
    assert r.status_code == 200 and plain.made == [(CHUTES, "sk-svc-123456")] and not fake.calls
    r = await call(p, "POST", "/v1/messages", message("moonshotai/Kimi-K3-TEE"))
    assert r.status_code == 200 and fake.calls[-1][0] == "moonshotai/Kimi-K3-TEE"
    r = await call(p, "POST", "/v1/messages", message("zai-org/GLM-5.3"))
    said = r.json()["error"]["message"]
    assert r.status_code == 403 and "zai-org/GLM-5.3 isn't a TEE or end-to-end encrypted model" in said
    assert "kept to partial privacy" in said and len(plain.calls) == 1
    # a session with the highest privacy never moves to Chutes (partial), in words
    p, fake, private = sealed_proxy(CHUTES_STANDARD, CHUTES, caps={}, private=FakePrivate())
    assert (await call(p, "POST", "/v1/messages", message("private/glm-5-3"))).status_code == 200
    r = await call(p, "POST", "/v1/messages", message("moonshotai/Kimi-K3-TEE"))
    said = r.json()["error"]["message"]
    assert r.status_code == 403 and not fake.calls
    assert "is end-to-end encrypted through Chutes, but has partial privacy" in said
    assert "kept to the highest privacy" in said


async def test_chutes_proxy_of_anthropics_api_gets_its_others_as_written_with_a_bearer_key_and_never_a_tee_one():
    seen = []

    def chutes_proxy(request):
        seen.append(request)
        return httpx.Response(200, json={"type": "message", "content": [], "usage": {"input_tokens": 3}})
    own = Profile("own", "standard", "zai-org/GLM-5.3", "zai-org/GLM-5.3", "zai-org/GLM-5.3", "", "low",
                  provider="custom-anthropic", base_url="https://claude.chutes.ai")
    p, fake, _ = sealed_proxy(own, own.base_url, caps={}, handler=chutes_proxy)
    r = await call(p, "POST", "/v1/messages", message("zai-org/GLM-5.3", stream=False))
    assert r.status_code == 200 and str(seen[-1].url) == "https://claude.chutes.ai/v1/messages"
    assert seen[-1].headers["authorization"] == "Bearer sk-svc-123456" and "x-api-key" not in seen[-1].headers
    r = await call(p, "POST", "/v1/messages", message("moonshotai/Kimi-K3-TEE"))
    assert r.status_code == 200 and fake.calls[-1][0] == "moonshotai/Kimi-K3-TEE" and len(seen) == 1
    assert fake.made[0][2] == "https://claude.chutes.ai:443"


async def test_a_provider_that_speaks_anthropics_api_at_tinfoils_address_is_sealed_never_passed_on():
    own = Profile("own", "standard", "glm-5-3", "glm-5-3", "glm-5-3", "", "low", provider="custom-anthropic",
                  base_url="https://inference.tinfoil.sh")
    p, fake, _ = sealed_proxy(own, own.base_url)          # any request in the clear fails the test
    r = await call(p, "POST", "/v1/messages", message("glm-5-3"))
    assert r.status_code == 200 and fake.calls[-1][0] == "glm-5-3"


# ---------------------------------------------------------------------- keys

def test_a_chutes_key_goes_to_chutes_three_hosts_alone_and_tinfoils_to_tinfoils(ring):
    creds.set_key("cpk_123456", "chutes", CHUTES)
    for url in (CHUTES, "https://claude.chutes.ai", "https://api.chutes.ai"):
        assert creds.get_key("chutes", url) == "cpk_123456"
        assert creds.held_origin("chutes", url) == creds.origin(url)
    for url in ("https://chutes.ai.example.net/v1", "http://llm.chutes.ai/v1", "https://evil.chutes.dev/v1"):
        with pytest.raises(creds.KeyElsewhere):
            creds.get_key("chutes", url)
    creds.set_key("tk_123456", "tinfoil", TINFOIL)
    assert creds.get_key("tinfoil", TINFOIL) == "tk_123456"
    with pytest.raises(creds.KeyElsewhere, match="goes only to https://inference.tinfoil.sh"):
        creds.get_key("tinfoil", "https://router.inference.tinfoil.sh/v1")
    assert ring.store[("daprivatecoder", "tinfoil")] == "tk_123456"


# ---------------------------------------------------------------------- the window

@pytest.fixture
def window(monkeypatch, tmp_path):
    monkeypatch.setattr("daprivatecoder.launcher.EXTENSION_DIRS", ())
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    monkeypatch.setattr(manager.editor, "EDITORS", ())

    async def listed(service, url):
        assert service in ("tinfoil", "chutes")
        if service == "tinfoil":
            return capabilities.from_listing({"glm-5-3": {"tools": True, "vision": False,
                                                           "pricing": {"prompt": 1.8, "completion": 6.0}}})
        return capabilities.from_listing({"moonshotai/Kimi-K3-TEE": {"tools": True, "confidential": True},
                                          "zai-org/GLM-5.3": {"tools": True}})
    monkeypatch.setattr(manager.Manager, "_sealed_listing", staticmethod(listed))
    return create_app(WINDOW_TOKEN, manager.Manager())


async def test_the_window_switches_a_private_session_to_tinfoil_and_a_tee_one_to_chutes_with_their_models(window, ring):
    st = (await window_call(window, "POST", "/api/profile", {"profile": "private", "fields": {"provider": "tinfoil"}}))[1]
    p = st["config"]["profiles"]["private"]
    assert (p["provider"], p["model"], p["opus_model"]) == ("tinfoil", "glm-5-3", "kimi-k3-1-pro")
    assert st["services"]["private"] == "tinfoil" and st["provider_keys"]["private"]["provider"] == "tinfoil"
    assert st["ready"]["private"] is False                       # no Claude Code here, nor a Tinfoil key
    models = (await window_call(window, "GET", "/api/models?profile=private&fresh=1"))[1]["models"]
    assert [m["id"] for m in models] == ["glm-5-3"] and models[0]["privacy"]["level"] == "highest"
    assert models[0]["price"] == {"input": 1.8, "cache_read": 1.8, "output": 6.0}
    st = (await window_call(window, "POST", "/api/key", {"key": "tk_123456", "provider": "tinfoil"}))[1]
    assert st["provider_keys"]["private"]["stored"] and ring.store[("daprivatecoder", "tinfoil_origin")] == \
        "https://inference.tinfoil.sh:443"
    st = (await window_call(window, "POST", "/api/profile", {"profile": "tee", "fields": {"provider": "chutes"}}))[1]
    assert st["config"]["profiles"]["tee"]["model"] == "zai-org/GLM-5.2-TEE" and st["services"]["tee"] == "chutes"
    models = (await window_call(window, "GET", "/api/models?profile=tee&fresh=1"))[1]["models"]
    assert [m["id"] for m in models] == ["moonshotai/Kimi-K3-TEE"]                  # its -TEE ones alone
    status, said = await window_call(window, "POST", "/api/profile", {"profile": "tee", "fields": {
        "model": "zai-org/GLM-5.3"}})
    assert status == 400 and "isn't one of Chutes' end-to-end encrypted models" in said["error"]
    # back to NanoGPT: its own models for the kind, never Tinfoil's ids on NanoGPT
    st = (await window_call(window, "POST", "/api/profile", {"profile": "private", "fields": {"provider": "nanogpt"}}))[1]
    assert st["config"]["profiles"]["private"]["model"] == config.DEFAULTS.profile("private").model
    assert ring.store[("daprivatecoder", "tinfoil")] == "tk_123456"                  # kept for when it's chosen again


def test_the_settings_file_says_how_private_each_session_is_on_its_service():
    from daprivatecoder import launcher
    assert launcher._describe(ON_TINFOIL, "glm-5-3").startswith("Private: sealed on this computer to Tinfoil's")
    assert launcher._describe(ON_CHUTES, "moonshotai/Kimi-K3-TEE").startswith("Partial privacy: sealed")
    assert launcher._model_ok(ON_CHUTES, "moonshotai/Kimi-K3-TEE") and not launcher._model_ok(ON_CHUTES, "zai-org/GLM-5.3")


# ---------------------------------------------------------------------- every model that gets a session's content

def never(request):
    pytest.fail(f"nothing of a sealed session may go in the clear: {request.method} {request.url}")


async def test_every_request_of_a_private_session_on_tinfoil_is_sealed_to_tinfoil_or_refused():
    """Claude Code asks for models of its own accord: its quick model (titles, summaries), its subagents', names
    of Claude's. In a session on Tinfoil each goes sealed to Tinfoil, or is refused in words: nothing in the clear."""
    plain = FakePlain()
    p, fake, private = sealed_proxy(ON_TINFOIL, TINFOIL, plain=plain, handler=never)
    for asked, sent in (("claude-haiku-4-5-20251001", "deepseek-v4-1-flash"), ("claude-3-5-haiku-latest",
                                                                               "deepseek-v4-1-flash"),
                        ("gpt-oss-120b", "gpt-oss-120b")):          # a model of Tinfoil's not in the profile: sealed
        r = await call(p, "POST", "/v1/messages", message(asked))
        assert r.status_code == 200 and fake.calls[-1][0] == sent, asked
    for asked in ("claude-sonnet-4-5", "claude-opus-5-5[1m]", "claude-fable-1"):
        r = await call(p, "POST", "/v1/messages", message(asked))
        assert r.status_code == 400 and "nothing was sent" in r.json()["error"]["message"], asked
    r = await call(p, "POST", "/v1/messages/count_tokens", {"model": "claude-haiku-4-5", "messages": []})
    assert r.status_code == 200 and "input_tokens" in r.json()                      # counted here, sent nowhere
    assert len(fake.calls) == 3 and not plain.calls and not plain.made and not private.calls


async def test_every_request_of_a_tee_session_on_chutes_is_sealed_at_partial_privacy_or_more_or_refused():
    plain = FakePlain()
    p, fake, private = sealed_proxy(ON_CHUTES, CHUTES, caps={}, plain=plain, handler=never, private=FakePrivate())
    r = await call(p, "POST", "/v1/messages", message("claude-haiku-4-5-20251001"))     # Claude Code's quick work
    assert r.status_code == 200 and fake.calls[-1][0] == ON_CHUTES.fast_model
    for asked in ("claude-sonnet-4-5", "zai-org/GLM-5.3", "TEE/glm-5.3", "deepseek-ai/DeepSeek-V4.1-Flash-1017"):
        r = await call(p, "POST", "/v1/messages", message(asked))
        assert r.status_code == 400 and "nothing was sent" in r.json()["error"]["message"], asked
    assert len(fake.calls) == 1 and not plain.calls and not plain.made
    # a private/ model is more private: sealed through NanoGPT, and from then on nothing goes to Chutes (partial)
    assert (await call(p, "POST", "/v1/messages", message("private/glm-5-3"))).status_code == 200
    assert private.calls[-1][0] == "private/glm-5-3"
    r = await call(p, "POST", "/v1/messages", message("claude-haiku-4-5"))
    assert r.status_code == 403 and "kept to the highest privacy" in r.json()["error"]["message"]
    assert len(fake.calls) == 1


async def test_a_tee_session_through_chutes_proxy_of_anthropics_api_never_passes_a_request_on_in_the_clear():
    own = replace(ON_CHUTES, provider="custom-anthropic", base_url="https://claude.chutes.ai")
    p, fake, _ = sealed_proxy(own, own.base_url, caps={}, handler=never)
    r = await call(p, "POST", "/v1/messages", message("claude-haiku-4-5"))
    assert r.status_code == 200 and fake.calls[-1][0] == own.fast_model
    for asked in ("claude-sonnet-4-5", "zai-org/GLM-5.3"):
        r = await call(p, "POST", "/v1/messages", message(asked))
        assert r.status_code == 400, asked
    r = await call(p, "POST", "/v1/messages/count_tokens", {"model": "zai-org/GLM-5.3", "messages": []})
    assert r.status_code == 400 and len(fake.calls) == 1


# ---------------------------------------------------------------------- auto mode and effort on every provider

EVERY_MODEL = {m: {"vision": False, "tools": True, "efforts": ["low", "high", "max"]} for m in (
    "glm-5-3", "zai-org/GLM-5.2-TEE", "zai-org/GLM-5.3", "z-ai/glm-5.3")}
CHECK_SYSTEM = "You are a security monitor for autonomous AI coding agents.\n\n## Context"


@pytest.mark.parametrize("profile, url, model, way", [
    (ON_TINFOIL, TINFOIL, "glm-5-3", "sealed"),                          # sealed straight to Tinfoil's router
    (ON_CHUTES, CHUTES, "zai-org/GLM-5.2-TEE", "sealed"),                # sealed to an attested Chutes instance
    (CHUTES_STANDARD, CHUTES, "zai-org/GLM-5.3", "translated"),          # Chutes' others: chat completions
    (replace(CHUTES_STANDARD, name="or", provider="openrouter", model="z-ai/glm-5.3", opus_model="z-ai/glm-5.3",
             fast_model="z-ai/glm-5.3"), providers.OPENROUTER_BASE_URL, "z-ai/glm-5.3", "translated")])
async def test_auto_modes_checks_and_the_effort_hold_on_tinfoil_chutes_and_openrouter(profile, url, model, way):
    """Every provider, not only NanoGPT: a session's auto mode checks on its own model, here (launcher.auto_mode_env);
    a check runs at the profile's check_effort and anything else at Claude Code's effort or the profile's; each by its
    provider's own way (sealed, or translated), never NanoGPT's Anthropic endpoint (only NanoGPT's takes any model)."""
    from daprivatecoder import launcher
    env = launcher.claude_env(profile, 1234, "tok")
    assert env["CLAUDE_CODE_AUTO_MODE_SERVER"] == "0" and env["CLAUDE_CODE_AUTO_MODE_MODEL"] == profile.model
    profile = replace(profile, reasoning="low", check_effort="high")
    plain = FakePlain()
    p, fake, _ = sealed_proxy(profile, url, caps=EVERY_MODEL, plain=plain)
    assert not p._messages_any(model)
    for body, run in ((message(model), "low"), (message(model, output_config={"effort": "max"}), "max"),
                      (message(model, stream=False, system=CHECK_SYSTEM, stop_sequences=["</block>"]), "high")):
        r = await call(p, "POST", "/v1/messages", body)
        assert r.status_code == 200, r.text
        sent = fake.calls[-1][1] if way == "sealed" else plain.calls[-1][3]
        assert sent["reasoning_effort"] == run, body
    assert len(fake.calls if way == "sealed" else plain.calls) == 3
    assert not (plain.calls if way == "sealed" else fake.calls)
