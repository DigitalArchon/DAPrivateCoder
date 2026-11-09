"""A user name and password written into an address (a provider of your own behind a password,
NanoGPT's base_url) are kept in the keyring, as an API key is, never in config.toml, and never said.

From DA Assistant's tests/test_address_passwords.py (Nov 2026). Every test has its own settings file
and the in-memory keyring of conftest.py, or one standing in for a locked keyring; nothing here reads
or writes the real one."""

from dataclasses import replace

import keyring
import pytest
from keyring.backend import KeyringBackend

from daprivatecoder import config, creds
from daprivatecoder.netpolicy import InsecureURL, addresses_shown, check_url, shown, without_password

ENDPOINT = "https://bob:hunter2@llm.example/v1"
PLAIN = "https://llm.example/v1"
NANO = "https://carol:pw-123@nano.example/api/v1"
STANDARD = "profiles.standard.base_url"


class Locked(KeyringBackend):
    """A keyring that is there but refuses: locked, or its unlock prompt dismissed."""
    priority = 1

    def get_password(self, service, username):
        raise RuntimeError("locked")

    def set_password(self, service, username, password):
        raise RuntimeError("locked")

    def delete_password(self, service, username):
        raise RuntimeError("locked")


def custom(cfg: config.Config, url: str) -> config.Config:
    return cfg.with_profile(replace(cfg.profile("standard"), provider="custom", base_url=url))


def text() -> str:
    return config.config_path().read_text()


def entry(ring, setting=STANDARD):
    return ring.store.get(("daprivatecoder", creds.ADDRESS.format(setting)))


# ---------------------------------------------------------------------- the address, said

def test_the_user_name_and_password_go_and_the_address_stays():
    for url, plain in ((ENDPOINT, PLAIN),
                       ("https://token@host.example/v1?a=b#c", "https://host.example/v1?a=b#c"),
                       ("https://u:p@ss@host.example/", "https://host.example/"),
                       ("http://u:p@[fd00::5]:11434", "http://[fd00::5]:11434"),
                       ("u:p@proxy:3128", "proxy:3128"),
                       ("https://u:p@host:port/", "https://host:port/")):
        assert without_password(url) == plain, url
    for url in ("", "http://localhost:11434", "HTTPS://Llm.Example:8443/", "https://host.example/a@b",
                "https://host.example/?to=a@b"):
        assert without_password(url) == url, url


def test_an_address_is_shown_without_its_password():
    assert shown(ENDPOINT) == "https://…@llm.example/v1"
    assert shown("http://192.168.1.20:11434/v1") == "http://192.168.1.20:11434/v1"
    assert shown("https://llm.example/v1/@cf/x") == "https://llm.example/v1/@cf/x"
    # one that can't be read as an address loses all it has up to its last "@"
    for typed, said in (("http://alice:s3cret@[fd00::5", "http://…@[fd00::5"),
                        ("alice:s3cret@proxy.example:3128", "…@proxy.example:3128"),
                        ("https://alice:s3cret@llm.example:99999/v1", "https://…@llm.example:99999/v1"),
                        ("https://alice:s3/cret@llm.example/v1", "https://…@llm.example/v1")):
        assert shown(typed) == said, typed
    assert addresses_shown("Client error '401' for url 'https://bob:hunter2@llm.example/v1/models'") == \
        "Client error '401' for url 'https://…@llm.example/v1/models'"


@pytest.mark.parametrize("url", ["http://alice:s3cret@llm.example/v1", "http://alice:s3cret@[fd00::5",
                                 "alice:s3cret@llm.example/v1", "ftp://alice:s3cret@llm.example/v1",
                                 "https://alice:s3cret@/v1"])
def test_no_refusal_of_an_address_repeats_its_password(url):
    with pytest.raises(InsecureURL) as refused:
        check_url(url, "Your provider's address")
    assert "s3cret" not in str(refused.value)
    cfg = config.load()
    with pytest.raises(config.ConfigError) as refused:
        config.save(custom(cfg, url))
    assert "s3cret" not in str(refused.value) and "s3cret" not in text()


# ---------------------------------------------------------------------- kept in the keyring

def test_an_address_typed_with_a_password_is_written_without_it_and_used_as_typed(ring):
    saved = config.save(custom(config.load(), ENDPOINT))
    assert f'base_url = "{PLAIN}"' in text() and "hunter2" not in text() and "bob" not in text()
    assert entry(ring) == ENDPOINT
    assert saved.profile("standard").base_url == ENDPOINT                 # in use as typed, now
    creds.forget()
    again = config.load()                                                 # and in the next process
    assert again.profile("standard").base_url == again.provider_url(again.profile("standard")) == ENDPOINT


def test_nanogpts_address_is_kept_the_same_way(ring):
    config.save(replace(config.load(), base_url=NANO))
    assert "pw-123" not in text() and 'base_url = "https://nano.example/api/v1"' in text()
    creds.forget()
    assert config.load().base_url == NANO and entry(ring, "base_url") == NANO


def test_one_left_in_the_file_moves_to_the_keyring(ring):
    config.load()
    config.config_path().write_text(text().replace('provider = "nanogpt"', f'provider = "custom"\nbase_url = "{ENDPOINT}"', 1))
    assert "hunter2" in text()                                            # as an older version wrote it, or by hand
    assert config.load().profile("standard").base_url == ENDPOINT
    assert entry(ring) == ENDPOINT and "hunter2" not in text() and PLAIN in text()


def test_the_password_goes_only_to_the_address_it_was_typed_for(ring):
    config.save(custom(config.load(), ENDPOINT))
    # config.toml edited by hand, or by another copy of the app, to another server
    config.config_path().write_text(text().replace(PLAIN, "https://other.example/v1"))
    creds.forget()
    assert config.load().profile("standard").base_url == "https://other.example/v1"


def test_taking_the_password_out_takes_it_out_of_the_keyring(ring):
    config.save(custom(config.load(), ENDPOINT))
    config.save(custom(config.load(), PLAIN))
    assert entry(ring) is None
    creds.forget()
    assert config.load().profile("standard").base_url == PLAIN
    config.save(custom(config.load(), ENDPOINT))                          # and a provider that isn't custom now
    config.save(config.load().with_profile(replace(config.load().profile("standard"), provider="openrouter")))
    assert entry(ring) is None


def test_saving_other_settings_leaves_the_keyring_alone(ring, monkeypatch):
    config.save(custom(config.load(), ENDPOINT))
    writes = []
    monkeypatch.setattr(ring, "set_password", lambda *a: writes.append(a))
    monkeypatch.setattr(ring, "delete_password", lambda *a: writes.append(a))
    cfg = config.load()
    config.save(cfg.with_profile(replace(cfg.profile("standard"), reasoning="max")))
    assert writes == [] and "hunter2" not in text()
    assert config.load().profile("standard").base_url == ENDPOINT


def test_an_address_without_a_password_is_written_as_it_is_and_nothing_is_kept(ring):
    config.save(custom(config.load(), "http://192.168.1.201:11434/v1"))
    assert 'base_url = "http://192.168.1.201:11434/v1"' in text()
    assert not any(user.endswith("_with_password") for _service, user in ring.store)


# ---------------------------------------------------------------------- with no keyring to take it

@pytest.fixture
def locked():
    keyring.set_keyring(Locked())
    creds.forget()


def test_one_in_the_file_is_used_and_stays_there(locked):
    config.load()
    config.config_path().write_text(text().replace('provider = "nanogpt"', f'provider = "custom"\nbase_url = "{ENDPOINT}"', 1))
    cfg = config.load()
    assert cfg.profile("standard").base_url == ENDPOINT and "hunter2" in text()     # not lost
    config.save(cfg.with_profile(replace(cfg.profile("standard"), reasoning="max")))  # a save about something else
    assert "hunter2" in text() and 'reasoning = "max"' in text()


def test_one_typed_is_written_nowhere_and_nothing_is_saved(locked):
    cfg = config.load()
    before = text()
    with pytest.raises(config.ConfigError, match="Nothing was saved") as refused:
        config.save(custom(cfg, ENDPOINT))
    assert "hunter2" not in str(refused.value) and "https://…@llm.example/v1" in str(refused.value)
    assert text() == before


def test_a_save_after_the_keyring_couldnt_be_read_keeps_the_password(ring):
    """Read while the keyring was locked, the address came without its password: saved so, it must not
    look like an address the password was taken out of."""
    config.save(custom(config.load(), ENDPOINT))
    keyring.set_keyring(Locked())
    creds.forget()
    cfg = config.load()
    assert cfg.profile("standard").base_url == PLAIN
    keyring.set_keyring(ring)                                   # unlocked again before the save
    config.save(cfg.with_profile(replace(cfg.profile("standard"), reasoning="max")))
    assert entry(ring) == ENDPOINT
    creds.forget()
    assert config.load().profile("standard").base_url == ENDPOINT


def test_it_moves_once_the_keyring_is_back(ring):
    keyring.set_keyring(Locked())
    creds.forget()
    config.load()
    config.config_path().write_text(text().replace('provider = "nanogpt"', f'provider = "custom"\nbase_url = "{ENDPOINT}"', 1))
    config.load()
    assert "hunter2" in text()
    keyring.set_keyring(ring)
    creds.forget()
    assert config.load().profile("standard").base_url == ENDPOINT and "hunter2" not in text()


# ---------------------------------------------------------------------- never said

def test_what_is_said_of_the_address_never_has_its_password(ring, capsys, tmp_path):
    from daprivatecoder import cli, usagelog
    config.save(custom(config.load(), ENDPOINT))
    assert cli.main(["key", "status", "--provider", "custom"]) == 0
    out = capsys.readouterr().out
    assert "https://…@llm.example/v1" in out and "hunter2" not in out
    log = usagelog.UsageLog(tmp_path / "requests.log", "s")
    log.write("request", error=f"Client error '401 Unauthorized' for url '{ENDPOINT}/models'")
    assert "hunter2" not in (tmp_path / "requests.log").read_text()
