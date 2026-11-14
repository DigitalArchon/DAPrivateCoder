"""A key that would go over plain http to another machine on your network (where anyone on it can read it) is asked
about first, No the default, and a No is kept for that address (the cross-app standard, Nov 2026, as SealedLore's
keys_in_the_clear): until you say Yes it is refused before anything is sent, in words naming the address and the
ways out. The window asks where your provider's address is typed (Models, Standard); `daprivatecoder key set` asks
in a terminal. https, this computer itself and an onion service need no question."""

import json
from dataclasses import replace

import httpx
import pytest

from daprivatecoder import cli, creds
from daprivatecoder.netpolicy import in_the_clear, is_loopback_host

BOX = "http://192.168.1.5:8080/v1"


def test_which_addresses_a_key_would_cross_a_network_unencrypted_to():
    assert in_the_clear(BOX) and in_the_clear("http://printer.local/v1") and in_the_clear("http://[fd00::5]:11434/")
    for safe in ("https://192.168.1.5/v1", "http://localhost:11434/v1", "http://127.0.0.2/v1", "http://[::1]:9/",
                 "http://ollama.localhost/v1", "http://abcdefghijklmnop.onion/v1", ""):
        assert not in_the_clear(safe), safe
    assert is_loopback_host("127.10.0.1") and not is_loopback_host("127.0.0.1.") and not is_loopback_host("10.0.0.1")


def test_a_key_isnt_sent_in_the_clear_until_you_say_yes_and_a_no_is_kept(ring):
    creds.set_key("sk-box-123456", "custom", BOX)
    assert creds.in_the_clear_state("custom", BOX) == "ask"
    with pytest.raises(creds.KeyInTheClear) as refused:
        creds.get_key("custom", BOX)
    said = str(refused.value)
    assert "192.168.1.5:8080" in said and "https://" in said and "key set --provider custom" in said
    assert "123456" not in said and isinstance(refused.value, creds.KeyringUnavailable)
    assert creds.get_key("custom", BOX, sending=False) == "sk-box-123456"       # what is stored, sent nowhere
    creds.answer_in_the_clear("custom", BOX, False)
    assert creds.in_the_clear_state("custom", BOX) == "declined"                  # not asked again
    with pytest.raises(creds.KeyInTheClear):
        creds.get_key("custom", BOX)
    creds.answer_in_the_clear("custom", BOX, None)                                # Ask me again
    assert creds.in_the_clear_state("custom", BOX) == "ask"
    creds.answer_in_the_clear("custom", BOX, True)
    assert creds.in_the_clear_state("custom", BOX) == "allowed" and creds.get_key("custom", BOX) == "sk-box-123456"
    assert ring.store[("daprivatecoder", "custom:192.168.1.5:8080")] == "sk-box-123456"     # never deleted
    creds.delete_key("custom", BOX)
    assert not [k for k in ring.store if k[1].startswith("custom:192.168.1.5:8080")]


def test_https_and_this_computer_need_no_question(ring):
    creds.set_key("sk-a", "custom", "https://192.168.1.5/v1")
    creds.set_key("sk-b", "custom", "http://localhost:11434/v1")
    assert creds.get_key("custom", "https://192.168.1.5/v1") == "sk-a"
    assert creds.get_key("custom", "http://localhost:11434/v1") == "sk-b"
    assert creds.in_the_clear_state("custom", "http://localhost:11434/v1") == ""


def test_a_key_already_going_there_before_this_version_is_held_back_until_you_say_yes(ring):
    """Stored before origins were kept: it takes the address it is first read for, and one that was going there in
    the clear is not taken as agreed (the author, Nov 14 2026: nothing is grandfathered): refused in words naming the
    address and where to say Yes, and the window asks (state "ask"), No first."""
    ring.store[("daprivatecoder", "custom:192.168.1.5:8080")] = "sk-before"
    with pytest.raises(creds.KeyInTheClear, match="192.168.1.5:8080.*say Yes where DA Private Coder's window asks"):
        creds.get_key("custom", BOX)
    assert creds.in_the_clear_state("custom", BOX) == "ask"
    assert creds.get_key("custom", BOX, sending=False) == "sk-before", "kept, never deleted"
    creds.answer_in_the_clear("custom", BOX, True)
    assert creds.get_key("custom", BOX) == "sk-before"


def test_the_proxy_refuses_it_before_anything_is_sent(ring):
    import asyncio

    from test_proxy import NANO, FakePlain, call, make, message

    creds.set_key("sk-box-123456", "custom", BOX)
    plain = FakePlain()
    profile = replace(NANO, provider="custom", base_url=BOX)
    p, _ = make(profile, plain=plain, provider_url=BOX)
    r = asyncio.run(call(p, "POST", "/v1/messages", message("z-ai/glm-5.3")))
    assert r.status_code == 403 and "unencrypted" in r.json()["error"]["message"] and "123456" not in r.text
    assert plain.made == [] and plain.calls == []


async def test_the_window_asks_once_and_keeps_your_answer(ring):
    from daprivatecoder import config, manager
    from daprivatecoder.server import create_app

    cfg = config.load()
    config.save(cfg.with_profile(replace(cfg.profile("standard"), provider="custom", base_url=BOX)))
    app = create_app("tok", manager.Manager(), on_quit=lambda: None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
        async def post(body):
            r = await c.post("/api/key", headers={"X-Token": "tok"}, content=json.dumps(body))
            assert r.status_code == 200, r.text
            return r.json()["provider_key"]
        state = await post({"provider": "custom", "key": "sk-box-123456"})
        assert state["stored"] and state["in_the_clear"] == "ask" and "192.168.1.5:8080" in state["clear_host"]
        assert (await post({"provider": "custom", "in_the_clear": "yes"}))["in_the_clear"] == "declined"  # true only
        assert (await post({"provider": "custom", "in_the_clear": None}))["in_the_clear"] == "ask"
        assert (await post({"provider": "custom", "in_the_clear": True}))["in_the_clear"] == "allowed"


def test_key_set_asks_in_a_terminal_and_keeps_a_no_without_one(ring, monkeypatch, capsys):
    from daprivatecoder import config

    cfg = config.load()
    config.save(cfg.with_profile(replace(cfg.profile("standard"), provider="custom", base_url=BOX)))

    class Stdin:
        def __init__(self, text, tty):
            self.text, self.tty = text, tty

        def isatty(self):
            return self.tty

        def readline(self):
            return self.text

    monkeypatch.setattr("sys.stdin", Stdin("sk-box-123456\n", False))
    assert cli.main(["key", "set", "--provider", "custom"]) == 0
    assert "isn't sent there" in capsys.readouterr().out and creds.in_the_clear_state("custom", BOX) == "declined"
    monkeypatch.setattr("sys.stdin", Stdin("", True))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "sk-box-123456")
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert cli.main(["key", "set", "--provider", "custom"]) == 0
    assert creds.in_the_clear_state("custom", BOX) == "allowed" and creds.get_key("custom", BOX) == "sk-box-123456"


def test_a_password_in_the_address_is_held_to_the_same_rule(ring):
    """A password written into a provider's address goes beside the request as its login: over plain http to another
    machine, as unencrypted as a key."""
    with_password = "http://jo:hunter2@192.168.1.5:8080/v1"
    with pytest.raises(creds.KeyInTheClear, match="password in its address"):
        creds.get_key("custom", with_password)
    assert creds.in_the_clear_state("custom", with_password) == "ask"
    creds.answer_in_the_clear("custom", with_password, True)
    assert creds.get_key("custom", with_password) is None                   # no key: the server needs none but that
    assert creds.get_key("custom", BOX) is None                             # neither key nor password: no question
