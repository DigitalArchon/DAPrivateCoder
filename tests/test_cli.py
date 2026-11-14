"""The commands: the key, the editor switch, the log and the status."""

import io
import json
import os
import sys

import httpx
import pytest

from daprivatecoder import cli, creds, editor
from daprivatecoder.usagelog import UsageLog


class Stdin(io.StringIO):
    def isatty(self):
        return False


def test_the_key_is_kept_in_the_keyring(ring, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", Stdin("sk-nano-123456\n"))
    assert cli.main(["key", "set"]) == 0
    assert ring.store[("daprivatecoder", "nanogpt")] == "sk-nano-123456"
    assert cli.main(["key", "status"]) == 0 and "ending 3456" in capsys.readouterr().out
    assert cli.main(["key", "clear"]) == 0 and creds.get_key() is None
    monkeypatch.setattr("sys.stdin", Stdin("not a key\n"))
    assert cli.main(["key", "set"]) == 1 and "nothing was stored" in capsys.readouterr().err


# what a key picks up where it was copied from: a line break, a zero-width space, a letter that isn't
# ASCII, a tab. h11 refuses such a header in words that quote it, key and all
BAD_KEYS = ("sk-abc123\nsk-more", "sk-abc\u200b123", "sk-abcé123", "sk-abc\t123")


@pytest.mark.parametrize("key", BAD_KEYS)
def test_a_key_no_header_can_carry_is_refused_and_never_said(ring, monkeypatch, capsys, key):
    if "\n" not in key:                                  # the command reads one line
        monkeypatch.setattr("sys.stdin", Stdin(key + "\n"))
        assert cli.main(["key", "set"]) == 1
        said = capsys.readouterr().err
        assert "nothing was stored" in said and "abc" not in said and ring.store == {}
    with pytest.raises(creds.KeyUnusable, match="no API key has"):
        creds.set_key(key)
    assert ring.store == {}
    # one stored before, or by another program: refused whenever it's read, saying so, and nothing of it
    ring.store[("daprivatecoder", "nanogpt")] = key
    creds.forget()
    with pytest.raises(creds.KeyUnusable) as refused:
        creds.get_key()
    assert "NanoGPT" in str(refused.value) and "abc" not in str(refused.value)
    assert isinstance(refused.value, creds.KeyringUnavailable)       # said wherever a key can't be had
    assert cli.main(["key", "status"]) == 1 and "abc" not in capsys.readouterr().err
    # the web search, which reads NanoGPT's key as any request does, says so and searches nothing
    import asyncio

    from daprivatecoder import search
    said, failed = asyncio.run(search.SearchServer("zero-retention", "https://nano-gpt.com/api/v1").search(
        {"query": "python asyncio timeout"}))
    assert failed and said.startswith("Not searched:") and "no API key has" in said and "abc" not in said


def test_the_key_can_come_from_vibe_manager(ring, capsys):
    assert cli.main(["key", "set", "--from-vibe-manager"]) == 1 and "no NanoGPT key" in capsys.readouterr().err
    ring.store[("davibemanager", "provider:NanoGPT")] = "sk-from-dvm"
    creds.forget()
    assert cli.main(["key", "set", "--from-vibe-manager"]) == 0 and creds.get_key() == "sk-from-dvm"


def test_the_editor_switch(tmp_path, monkeypatch, capsys):
    settings = tmp_path / "VSCodium" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{\n    "claudeCode.preferredLocation": "panel" // mine\n}\n')
    monkeypatch.setattr(editor, "EDITORS", (("VSCodium", str(settings)), ("VS Code", str(tmp_path / "none" / "s.json"))))
    monkeypatch.setattr(editor, "wrapper_path", lambda profile="private": "/opt/pc/bin/claude-private")
    assert cli.main(["editor", "on"]) == 0
    assert editor.current(settings) == "/opt/pc/bin/claude-private" and "// mine" in settings.read_text()
    assert "daprivatecoder editor off" in capsys.readouterr().out
    assert cli.main(["editor", "status"]) == 0 and "VSCodium: on" in capsys.readouterr().out
    assert cli.main(["editor", "off"]) == 0 and editor.current(settings) is None
    settings.write_text('{"claudeCode.claudeProcessWrapper": "/corp/launcher"}')        # someone else's wrapper
    assert cli.main(["editor", "on"]) == 0 and "left as it is" in capsys.readouterr().out
    assert cli.main(["editor", "off"]) == 0 and editor.current(settings) == "/corp/launcher"
    assert cli.main(["editor", "on", "code"]) == 1 and "No settings of code" in capsys.readouterr().err


def test_the_log_shows_tokens_and_cost(capsys):
    from daprivatecoder.config import state_dir
    log = UsageLog(state_dir() / "requests.log", "ab12")
    log.write("session", profile="private")
    log.write("request", model="private/glm-5-3", sealed=True, status=200, seconds=3.2,
              usage={"input": 10_000, "cache_read": 90_000, "output": 50, "cost": 0.0395, "cost_estimated": True})
    log.write("request", model="private/kimi-k3", sealed=True, status=502, error="the enclave refused our key twice")
    assert cli.main(["log"]) == 0
    out = capsys.readouterr().out
    assert "sealed" in out and "100000 in (90000 cached)" in out and "$0.0395~" in out and "refused our key" in out
    assert "2 requests, $0.0395" in out


def test_check_says_what_a_tee_models_attestation_was_of(ring, monkeypatch, capsys):
    """`daprivatecoder check --profile tee` said "Verified in part" of RedPill's gateway as of anything else: it
    names what was attested now, and what that leaves to someone's word (SealedLore's, Nov 11 2026)."""
    from daprivatecoder import proxy
    from daprivatecoder.llm import capabilities
    from daprivatecoder.llm.tee import GPUS_LOOSE, OWN_ENCLAVE_UNCHECKED

    async def listed(*_a, **_k):
        return {}
    gateway = {"level": "partial", "attested": "gateway", "shortfalls": [OWN_ENCLAVE_UNCHECKED, GPUS_LOOSE],
               "summary": f"partially verified: {OWN_ENCLAVE_UNCHECKED}, {GPUS_LOOSE} (8 GPUs verified by NVIDIA)"}
    found = {"TEE/glm-5.3": gateway, "TEE/glm-5.3-flash": gateway,
             "TEE/kimi-k3": {"level": "full", "attested": "instances", "instances": 4,
                             "summary": "Intel TDX quote verified (4 instances, 32 GPUs verified by NVIDIA)"}}
    monkeypatch.setattr(capabilities, "fetch_nanogpt", listed)
    monkeypatch.setattr(proxy, "attest_tee", lambda base, key, model: found[model])
    creds.set_key("sk-nano-123456")
    assert cli.main(["check", "--profile", "tee"]) == 0
    out = capsys.readouterr().out
    assert "Verified" not in out
    assert "  TEE gateway attested, only in part. Attested: RedPill's TEE gateway" in out
    assert "the gateway's word, in each reply's signed receipt" in out and "GPUs not tied to the quote" in out
    assert "  TEE attested. Attested: each of the 4 instances this model's provider listed." in out


def test_status_says_what_is_set_up(monkeypatch, capsys):
    monkeypatch.setattr(editor, "EDITORS", ())
    monkeypatch.setenv("PATH", "/nonexistent")
    monkeypatch.setattr("daprivatecoder.launcher.EXTENSION_DIRS", ())
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "[private] private: private/glm-5-3" in out and "NanoGPT key: none" in out
    assert "Claude Code: Claude Code isn't installed" in out


def test_a_broken_settings_file_is_said_plainly(capsys):
    from daprivatecoder.config import config_path
    config_path().parent.mkdir(parents=True)
    config_path().write_text('[profiles.private]\nprivate = true\nmodel = "z-ai/glm-5.3"\n')
    assert cli.main(["status"]) == 1
    assert "isn't a NanoGPT Private Mode model" in capsys.readouterr().err
    assert cli._launch("claude-private", "private", ["-p", "hi"]) == 1
    assert "nothing would be sent in the clear" in capsys.readouterr().err


def test_a_key_found_missing_is_asked_for_again_and_each_custom_server_has_its_own(ring):
    from daprivatecoder import creds
    assert creds.get_key() is None
    ring.store[("daprivatecoder", "nanogpt")] = "sk-stored-from-a-terminal"
    assert creds.get_key() == "sk-stored-from-a-terminal"            # not "none" from before
    assert creds.account("custom", "http://localhost:8443/v1") == "custom:localhost:8443"
    assert creds.account("custom", "http://localhost:9000/v1") == "custom:localhost:9000"
    assert creds.account("custom", "https://api.example.com/v1") == "custom:api.example.com"


# --- a key never follows an edited address (DA Private Coder's creds.py by host and port; the DA apps' rule) -----

NANOGPT = "https://nano-gpt.com/api/v1"


def test_a_key_goes_only_to_the_address_it_was_stored_for(ring, monkeypatch, capsys):
    """NanoGPT's key was the keyring's "nanogpt", whatever base_url said: edited in the settings file to another
    server, it went there. Its origin is kept beside it now, and it goes nowhere else; it is never deleted for it."""
    from daprivatecoder import config

    monkeypatch.setattr("sys.stdin", Stdin("sk-nano-123456\n"))
    assert cli.main(["key", "set"]) == 0
    assert ring.store[("daprivatecoder", "nanogpt_origin")] == "https://nano-gpt.com:443"
    assert creds.get_key("nanogpt", NANOGPT) == "sk-nano-123456"
    assert creds.get_key("nanogpt", "https://NANO-GPT.com:443/api/v2") == "sk-nano-123456"     # the same origin
    # base_url edited by hand to another server
    config.config_path().write_text('base_url = "https://evil.example/api/v1"\n')
    with pytest.raises(creds.KeyElsewhere) as refused:
        creds.get_key("nanogpt", "https://evil.example/api/v1")
    said = str(refused.value)
    assert "stored for https://nano-gpt.com," in said and "evil.example" in said and "123456" not in said
    assert "key set" in said and isinstance(refused.value, creds.KeyringUnavailable)
    assert cli.main(["status"]) == 0 and "NanoGPT key: The NanoGPT API key in your keyring was stored for" in \
        capsys.readouterr().out
    assert ring.store[("daprivatecoder", "nanogpt")] == "sk-nano-123456"                   # never deleted
    # stored again, for the address it should go to now: there, and no longer to the old one
    monkeypatch.setattr("sys.stdin", Stdin("sk-nano-123456\n"))
    assert cli.main(["key", "set"]) == 0
    assert creds.get_key("nanogpt", "https://evil.example/api/v1") == "sk-nano-123456"
    with pytest.raises(creds.KeyElsewhere):
        creds.get_key("nanogpt", NANOGPT)
    assert cli.main(["key", "clear"]) == 0 and ("daprivatecoder", "nanogpt_origin") not in ring.store


def test_a_key_stored_before_origins_were_kept_takes_the_first_address_it_is_read_for(ring):
    ring.store[("daprivatecoder", "nanogpt")] = "sk-before"
    assert creds.get_key("nanogpt", NANOGPT) == "sk-before"                 # no question asked
    assert ring.store[("daprivatecoder", "nanogpt_origin")] == "https://nano-gpt.com:443"
    with pytest.raises(creds.KeyElsewhere):
        creds.get_key("nanogpt", "https://api.nano-gpt.com/api/v1")
    assert creds.get_key() == "sk-before"                                    # what is stored, said where nothing is sent


def test_a_servers_key_of_your_own_doesnt_follow_its_address_from_https_to_http(ring):
    """It was already its server's own by host and port (account), but https and http to the same host shared one:
    the key would have crossed the network in the clear."""
    creds.set_key("sk-box", "custom", "https://192.168.1.5/v1")
    assert creds.get_key("custom", "https://192.168.1.5/v1") == "sk-box"
    with pytest.raises(creds.KeyElsewhere, match="stored for https://192.168.1.5,"):
        creds.get_key("custom", "http://192.168.1.5/v1")
    assert creds.get_key("custom", "https://192.168.1.6/v1") is None        # another server: none of its own


def test_nanogpts_key_reaches_no_search_at_an_address_edited_since_it_was_stored(ring):
    import asyncio

    from daprivatecoder import search

    creds.set_key("sk-nano-123456", "nanogpt", NANOGPT)
    asked = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: asked.append(r) or httpx.Response(500)))
    said, failed = asyncio.run(search.SearchServer("zero-retention", "https://search.example/api/v1", http=http).search(
        {"query": "python asyncio timeout"}))
    assert failed and said.startswith("Not searched: The NanoGPT API key") and "stored for https://nano-gpt.com" in said
    assert asked == []


def test_a_settings_file_that_is_a_link_stays_one(tmp_path):
    from daprivatecoder import editor
    real = tmp_path / "dotfiles" / "settings.json"
    real.parent.mkdir()
    real.write_text('{\n    "editor.fontSize": 14\n}\n')
    link = tmp_path / "User" / "settings.json"
    link.parent.mkdir()
    link.symlink_to(real)
    assert editor.switch(link, "/x/claude-editor")
    assert link.is_symlink() and editor.current(real) == "/x/claude-editor"


# --- no core dumps (SealedLore's gui/app.py _no_core_dumps) ----------------------------------------------

_DUMP_STATE = """
import ctypes, resource, sys
libc = ctypes.CDLL(None, use_errno=True)
_, hard = resource.getrlimit(resource.RLIMIT_CORE)
resource.setrlimit(resource.RLIMIT_CORE, (hard, hard))      # cores allowed, as a shell may start it
assert libc.prctl(3, 0, 0, 0, 0) == 1                        # PR_GET_DUMPABLE: dumpable to begin with
{run}
print("\\n" + str(libc.prctl(3, 0, 0, 0, 0)), resource.getrlimit(resource.RLIMIT_CORE) == (0, hard), flush=True)
"""


@pytest.mark.parametrize("run", [
    # every command, the window, the proxy (serve) and the search server go through cli.main
    "from daprivatecoder import cli\ncli.main(['log', '-n', '1'])",
    # claude-private, before anything is started
    "from daprivatecoder import cli\ncli._launch = lambda *a: 0\ntry:\n    cli.claude_private()\nexcept SystemExit:\n    pass",
    # WebFetch's check, which Claude Code runs as python -m daprivatecoder.identity
    ("import io, runpy\nsys.stdin = io.StringIO('{}')\ntry:\n    runpy.run_module('daprivatecoder.identity', run_name='__main__')\n"
     "except SystemExit:\n    pass"),
])
def test_every_entry_point_writes_no_core_dump(run, tmp_path):
    """A crash must not write the process's memory to disk (the proxy holds every session's words in the clear):
    not dumpable, and a core size of 0, its hard limit left for the programs the user runs."""
    import subprocess

    env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path / "config"), "XDG_STATE_HOME": str(tmp_path / "state")}
    done = subprocess.run([sys.executable, "-c", _DUMP_STATE.format(run=run)], capture_output=True, text=True,
                          env=env, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[-1] == "0 True"
