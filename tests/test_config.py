"""The settings file, and the https rule for the addresses in it."""

from dataclasses import replace

import pytest

from daprivatecoder import config
from daprivatecoder.config import ConfigError, parse
from daprivatecoder.netpolicy import InsecureURL, check_url, is_secure


def test_the_defaults_are_written_on_first_use_and_read_back():
    cfg = config.load()
    assert cfg.path == config.config_path() and cfg.path.read_text() == config.DEFAULT_TEXT
    private, tee, standard = cfg.profile("private"), cfg.profile("tee"), cfg.profile("standard")
    assert private.private and private.models == ("private/glm-5-3", "private/kimi-k3", "private/deepseek-v4-1-flash")
    assert private.vision_model == "private/deepseek-v4-1-flash" and private.reasoning == "high"
    assert tee.kind == "tee" and tee.models == ("TEE/glm-5.3", "TEE/kimi-k3", "TEE/glm-5.3-flash")
    assert not standard.private and standard.model == "z-ai/glm-5.3" and standard.provider == "nanogpt"
    assert cfg.base_url == "https://nano-gpt.com/api/v1" and cfg.claude == ""


def test_a_private_profile_takes_private_models_only():
    for key in ("model", "opus_model", "fast_model", "vision_model"):
        models = {"model": "private/glm-5-3", key: "z-ai/glm-5.3"}
        text = "[profiles.p]\nprivate = true\n" + "".join(f'{k} = "{v}"\n' for k, v in models.items())
        with pytest.raises(ConfigError, match=f"{key} is 'z-ai/glm-5.3', which isn't a NanoGPT Private Mode model"):
            parse(text)
    p = parse('[profiles.p]\nprivate = true\nmodel = " Private/GLM-5-3 "\n').profile("p")
    assert p.model == p.opus_model == p.fast_model == "private/glm-5-3"     # the others default to the model
    assert p.models == ("private/glm-5-3",)


def test_mistakes_in_the_file_are_said_plainly():
    with pytest.raises(ConfigError, match="isn't valid TOML"):
        parse("[profiles.p\n")
    with pytest.raises(ConfigError, match="model is missing"):
        parse("[profiles.p]\nprivate = true\n")
    with pytest.raises(ConfigError, match="reasoning must be one of"):
        parse('[profiles.p]\nmodel = "x"\nreasoning = "none"\n')
    with pytest.raises(ConfigError, match="private must be true or false"):
        parse('[profiles.p]\nmodel = "x"\nprivate = "yes"\n')
    with pytest.raises(ConfigError, match="lower-case letters"):
        parse('[profiles.Big]\nmodel = "x"\n')
    with pytest.raises(ConfigError, match=r"no profile called 'gone'.*there are: p"):
        parse('[profiles.p]\nmodel = "x"\n').profile("gone")


def test_the_base_url_must_be_https_unless_local_or_an_onion():
    with pytest.raises(ConfigError, match="plain http to nano-gpt.com"):
        parse('base_url = "http://nano-gpt.com/api/v1"\n')
    assert parse('base_url = "http://127.0.0.1:8000/v1/"\n').base_url == "http://127.0.0.1:8000/v1"
    onion = "http://" + "a" * 56 + ".onion/api/v1"
    assert parse(f'base_url = "{onion}"\n').base_url == onion
    with pytest.raises(ConfigError, match="full https:// URL"):
        parse('base_url = "nano-gpt.com"\n')


def test_the_https_rule():
    for ok in ("https://nano-gpt.com/api/v1", "http://localhost:8080", "http://192.168.1.5/v1", "http://[::1]:9/x",
               "http://box.local/v1", "http://10.0.0.2", "http://" + "b" * 56 + ".onion/"):
        assert is_secure(ok), ok
        assert check_url(ok) == ok
    for bad in ("http://nano-gpt.com", "http://8.8.8.8", "http://192.168.1.50./v1", "ftp://nano-gpt.com", "http://.onion",
                "http://evil.example/x.onion"):
        assert not is_secure(bad), bad
        with pytest.raises(InsecureURL):
            check_url(bad)


def test_web_search_is_linkup_in_a_private_profile_unless_chosen_otherwise():
    cfg = config.load()
    assert cfg.profile("private").web_search == "zero-retention" and cfg.profile("standard").web_search == "best"
    assert parse('[profiles.p]\nprivate = true\nmodel = "private/glm-5-3"\n').profile("p").web_search == "zero-retention"
    assert parse('[profiles.p]\nmodel = "z-ai/glm-5.3"\n').profile("p").web_search == "best"
    p = parse('[profiles.p]\nprivate = true\nmodel = "private/glm-5-3"\nweb_search = "best"\n').profile("p")
    assert p.private and p.web_search == "best"
    with pytest.raises(ConfigError, match="web_search must be one of zero-retention, best, off"):
        parse('[profiles.p]\nmodel = "x"\nweb_search = "kagi"\n')


def test_settings_saved_from_the_window_keep_their_notes_and_read_back_the_same():
    cfg = config.load()
    changed = cfg.with_profile(replace(cfg.profile("private"), model="private/kimi-k3", web_search="best"))
    saved = config.save(changed)
    assert saved.profile("private").model == "private/kimi-k3" and saved.profile("private").web_search == "best"
    text = config.config_path().read_text()
    assert "# Web search, through NanoGPT" in text and 'model = "private/kimi-k3"' in text
    assert config.load().profiles == saved.profiles
    with pytest.raises(ConfigError, match="isn't a NanoGPT Private Mode model"):
        config.save(cfg.with_profile(replace(cfg.profile("private"), opus_model="z-ai/glm-5.3")))
    assert config.load().profiles == saved.profiles                     # nothing written


def test_a_tee_profile_takes_tee_or_private_models_and_only_nanogpts():
    tee = '[profiles.t]\nkind = "tee"\nmodel = "TEE/kimi-k3"\n'
    p = parse(tee + 'fast_model = "private/deepseek-v4-1-flash"\n').profile("t")
    assert p.kind == "tee" and p.models == ("TEE/kimi-k3", "private/deepseek-v4-1-flash")
    for key in ("model", "opus_model", "fast_model", "vision_model"):
        models = {"model": "TEE/kimi-k3", key: "z-ai/glm-5.3"}
        with pytest.raises(ConfigError, match=f"{key} is 'z-ai/glm-5.3', which isn't one of NanoGPT's TEE models"):
            parse('[profiles.t]\nkind = "tee"\n' + "".join(f'{k} = "{v}"\n' for k, v in models.items()))
    with pytest.raises(ConfigError, match="a tee profile's models are NanoGPT's"):
        parse(tee + 'provider = "openrouter"\n')
    with pytest.raises(ConfigError, match="kind must be one of private, tee, standard"):
        parse('[profiles.t]\nkind = "secret"\nmodel = "x"\n')


def test_a_standard_profile_takes_any_provider_and_another_only_over_https():
    p = parse('[profiles.s]\nkind = "standard"\nprovider = "openrouter"\nmodel = "z-ai/glm-5.3"\n')
    cfg = parse('[profiles.s]\nkind = "standard"\nprovider = "openrouter"\nmodel = "z-ai/glm-5.3"\n')
    assert p.profile("s").provider == "openrouter" and cfg.provider_url(cfg.profile("s")) == "https://openrouter.ai/api/v1"
    custom = '[profiles.s]\nkind = "standard"\nprovider = "custom"\nmodel = "m"\n'
    with pytest.raises(ConfigError, match="base_url is missing"):
        parse(custom)
    with pytest.raises(ConfigError, match="https"):
        parse(custom + 'base_url = "http://llm.example.com/v1"\n')         # not local: refused, never rewritten
    for ok in ("https://llm.example.com/v1/", "http://127.0.0.1:8080/v1", "http://192.168.1.20:11434/v1"):
        cfg = parse(custom + f'base_url = "{ok}"\n')
        assert cfg.provider_url(cfg.profile("s")) == ok.rstrip("/")
    with pytest.raises(ConfigError, match="provider must be one of"):
        parse('[profiles.s]\nprovider = "acme"\nmodel = "m"\n')


def test_routes_are_kept_for_models_they_apply_to_and_saved_with_their_notes():
    text = '[profiles.s]\nkind = "standard"\nmodel = "z-ai/glm-5.3"\n[profiles.s.routes]\n' \
           '"z-ai/glm-5.3" = { priority = "host", host = "parasail", host_name = "Parasail", fp8 = true }\n' \
           '"moonshotai/kimi-k3" = { priority = "auto" }\n"TEE/kimi-k3" = { priority = "speed" }\n'
    p = parse(text).profile("s")
    assert p.routes == {"z-ai/glm-5.3": {"priority": "host", "fp8": True, "host": "parasail", "host_name": "Parasail"}}
    cfg = config.load()
    saved = config.save(cfg.with_profile(p))
    assert saved.profile("s").routes == p.routes and "[profiles.s.routes]" in config.config_path().read_text()
    with pytest.raises(ConfigError, match="the route for z-ai/glm-5.3: A route here is one of"):
        parse(text.replace('"host", host', '"speed", host').replace("speed", "fastest", 1))
    with pytest.raises(ConfigError, match="one of: auto, latency"):                        # OpenRouter has no speed
        parse('[profiles.s]\nprovider = "openrouter"\nmodel = "z-ai/glm-5.3"\n[profiles.s.routes]\n'
              '"z-ai/glm-5.3" = { priority = "speed" }\n')


def test_the_three_kinds_of_session_are_always_there_and_the_first_files_still_load():
    cfg = parse('[profiles.private]\nprivate = true\nmodel = "private/kimi-k3"\n'
                '[profiles.nano]\nprivate = false\nmodel = "z-ai/glm-5.3"\n')
    assert cfg.profile("private").model == "private/kimi-k3" and cfg.profile("nano").kind == "standard"
    assert cfg.profile("tee") == config.DEFAULTS.profile("tee") and cfg.profile("standard").kind == "standard"


def test_the_private_and_tee_profiles_are_those_kinds_whatever_a_hand_written_file_leaves_out():
    """claude-private, the window's Private session and the editor's choices run [profiles.private] by
    name: one written without a kind is private (its models then must be), never standard in the clear."""
    with pytest.raises(ConfigError, match="A private profile only uses those"):
        parse('[profiles.private]\nmodel = "private/glm-5-3"\nfast_model = "z-ai/glm-5.3"\n')
    assert parse('[profiles.private]\nmodel = "private/glm-5-3"\n').profile("private").kind == "private"
    assert parse('[profiles.tee]\nmodel = "TEE/glm-5.3"\n').profile("tee").kind == "tee"
    for name, kind in (("private", "standard"), ("tee", "private")):
        with pytest.raises(ConfigError, match=f"the {name} profile is what {name} sessions run on"):
            parse(f'[profiles.{name}]\nkind = "{kind}"\nmodel = "private/glm-5-3"\n')
    with pytest.raises(ConfigError, match="a profile's name"):
        parse('[profiles."dev\\n"]\nmodel = "z-ai/glm-5.3"\n')


def test_the_context_window_is_200k_unless_said_and_read_back_as_written():
    assert config.DEFAULTS.profile("private").context == 200_000
    cfg = parse('[profiles.private]\nmodel = "private/glm-5-3"\ncontext = 1000000\n')
    assert cfg.profile("private").context == 1_000_000
    assert parse(config.render(cfg)).profile("private").context == 1_000_000
    assert "where Claude Code compacts it" in config.render(cfg)
    for wrong in ("50000", '"1M"', "true", "2000000"):
        with pytest.raises(ConfigError, match="context must be a number of tokens"):
            parse(f'[profiles.private]\nmodel = "private/glm-5-3"\ncontext = {wrong}\n')
