"""The settings file, and the https rule for the addresses in it."""

import pytest

from daprivatecoder import config
from daprivatecoder.config import ConfigError, parse
from daprivatecoder.netpolicy import InsecureURL, check_url, is_secure


def test_the_defaults_are_written_on_first_use_and_read_back():
    cfg = config.load()
    assert cfg.path == config.config_path() and cfg.path.read_text() == config.DEFAULT_TEXT
    private, nano = cfg.profile("private"), cfg.profile("nano")
    assert private.private and private.models == ("private/glm-5-3", "private/kimi-k3", "private/deepseek-v4-1-flash")
    assert private.vision_model == "private/deepseek-v4-1-flash" and private.reasoning == "low"
    assert not nano.private and nano.model == "z-ai/glm-5.3" and nano.vision_model == ""
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
    assert cfg.profile("private").web_search == "zero-retention" and cfg.profile("nano").web_search == "best"
    assert parse('[profiles.p]\nprivate = true\nmodel = "private/glm-5-3"\n').profile("p").web_search == "zero-retention"
    assert parse('[profiles.p]\nmodel = "z-ai/glm-5.3"\n').profile("p").web_search == "best"
    p = parse('[profiles.p]\nprivate = true\nmodel = "private/glm-5-3"\nweb_search = "best"\n').profile("p")
    assert p.private and p.web_search == "best"
    with pytest.raises(ConfigError, match="web_search must be one of zero-retention, best, off"):
        parse('[profiles.p]\nmodel = "x"\nweb_search = "kagi"\n')
