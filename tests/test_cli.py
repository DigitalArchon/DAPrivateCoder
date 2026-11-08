"""The commands: the key, the editor switch, the log and the status."""

import io
import json

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
