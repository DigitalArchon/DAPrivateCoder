"""The window: its commands in ~/.local/bin, opening Claude Code in a terminal, and the API the page
drives (server.py over manager.py), as the page calls it."""

import json
import os
import sys
from pathlib import Path

import httpx
import pytest

from daprivatecoder import cli, commands, config, editor, launch, manager
from daprivatecoder.server import create_app

TOKEN = "window-token"


# ---------------------------------------------------------------------- the commands

def test_the_commands_run_the_app_as_it_runs_now(monkeypatch):
    text = commands.script("claude-private")
    assert text.startswith("#!/bin/sh\n# Written by DA Private Coder")
    assert f'exec {sys.executable} -I -m daprivatecoder claude-private "$@"' in text
    monkeypatch.setenv("APPIMAGE", "/home/u/Apps/DA Private Coder.AppImage")
    assert "exec '/home/u/Apps/DA Private Coder.AppImage' claude-private \"$@\"" in commands.script("claude-private")
    assert commands.script("daprivatecoder").endswith("exec '/home/u/Apps/DA Private Coder.AppImage' \"$@\"\n")


def test_installing_replaces_privatecodes_and_ours_never_someone_elses(monkeypatch):
    bin_dir = commands.bin_dir()
    bin_dir.mkdir(parents=True)
    old = bin_dir.parent / "share/privatecode/venv/bin/claude-private"
    old.parent.mkdir(parents=True)
    old.write_text("#!/bin/sh\n")
    os.symlink(old, bin_dir / "claude-private")                    # PrivateCode's install.sh
    (bin_dir / "daprivatecoder").write_text("#!/bin/sh\necho mine\n")  # someone else's
    assert commands.state("claude-private") == "stale" and commands.state("daprivatecoder") == "taken"
    assert commands.install() == {"claude-private": "current", "daprivatecoder": "taken"}
    assert not (bin_dir / "claude-private").is_symlink() and os.access(bin_dir / "claude-private", os.X_OK)
    assert (bin_dir / "daprivatecoder").read_text() == "#!/bin/sh\necho mine\n"
    monkeypatch.setenv("APPIMAGE", "/moved/DAPrivateCoder.AppImage")    # the AppImage moved
    assert commands.state("claude-private") == "stale"
    commands.refresh()
    assert "/moved/DAPrivateCoder.AppImage" in (bin_dir / "claude-private").read_text()
    assert commands.state("daprivatecoder") == "taken"


def test_the_editor_runs_the_installed_command(monkeypatch, tmp_path):
    monkeypatch.setenv("APPIMAGE", "/apps/DAPrivateCoder.AppImage")
    path = editor.wrapper_path()                                    # installed now if it wasn't
    assert path == str(commands.path_of("claude-private")) and commands.state("claude-private") == "current"


# ---------------------------------------------------------------------- a terminal

def test_claude_code_opens_in_the_desktops_terminal_in_the_folder(tmp_path):
    found = {"gnome-terminal", "konsole", "xterm"}
    which = lambda name: f"/usr/bin/{name}" if name in found else None   # noqa: E731
    argv = launch.terminal_argv(["x"], {"XDG_CURRENT_DESKTOP": "KDE"}, which)
    assert argv[:2] == ["konsole", "-e"]
    assert launch.terminal_argv(["x"], {"XDG_CURRENT_DESKTOP": "X-Cinnamon"}, which)[:2] == ["gnome-terminal", "--"]
    assert launch.terminal_argv(["x"], {}, lambda n: None) is None
    started = []
    argv = launch.open_in_terminal(str(tmp_path), ["claude-private"], {"XDG_CURRENT_DESKTOP": "X-Cinnamon"}, which,
                                   popen=lambda a, **kw: started.append((a, kw)))
    assert argv[:4] == ["gnome-terminal", "--", "/bin/sh", "-c"] and argv[-2:] == [str(tmp_path), "claude-private"]
    assert started[0][1]["cwd"] == str(tmp_path) and started[0][1]["start_new_session"]
    with pytest.raises(launch.LaunchError, match="isn't a folder"):
        launch.open_in_terminal(str(tmp_path / "gone"), ["x"], {}, which, popen=lambda *a, **k: None)


def test_the_window_script_keeps_a_failed_session_open(tmp_path):
    import subprocess
    done = subprocess.run(launch.in_folder(str(tmp_path), ["sh", "-c", "pwd; exit 3"]), input="\n",
                          capture_output=True, text=True, timeout=10)
    assert done.stdout.startswith(str(tmp_path)) and "Claude Code ended (exit 3). Press Enter to close." in done.stdout


# ---------------------------------------------------------------------- the API the page drives

@pytest.fixture
def window(monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(launch.subprocess, "Popen", lambda argv, **kw: started.append((argv, kw)))
    monkeypatch.setattr(launch, "terminal_argv", lambda command, env=None, which=None: ["xterm", "-e", *command])
    monkeypatch.setattr("daprivatecoder.launcher.EXTENSION_DIRS", ())
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    settings = tmp_path / "VSCodium" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{\n    "editor.fontSize": 14 // mine\n}\n')
    monkeypatch.setattr(editor, "EDITORS", (("VSCodium", str(settings)),))
    quit_called = []
    app = create_app(TOKEN, manager.Manager(), on_quit=lambda: quit_called.append(1))
    return app, started, settings, quit_called


async def call(app, method, path, body=None, token=TOKEN):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
        r = await c.request(method, path, headers={"X-Token": token} if token else {},
                            content=json.dumps(body) if body is not None else None)
        return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)


async def test_only_the_window_gets_in(window):
    app, *_ = window
    assert (await call(app, "GET", "/api/state", token="guess"))[0] == 403
    assert (await call(app, "GET", "/api/state", token=None))[0] == 403
    status, page = await call(app, "GET", "/")
    assert status == 200 and "Content-Security-Policy" in page
    assert (await call(app, "GET", "/static/app.js"))[0] == 200


async def test_setting_it_up_from_the_window(window, ring, tmp_path):
    app, started, settings, quit_called = window
    status, st = await call(app, "GET", "/api/state")
    assert status == 200 and st["ready"] is False and st["key"]["stored"] is False
    assert st["config"]["profiles"]["private"]["model"] == "private/glm-5-3" and st["claude"]["error"]
    assert st["editors"] == [{"name": "VSCodium", "path": str(settings), "value": "", "state": "off"}]
    status, st = await call(app, "POST", "/api/key", {"key": "not a key"})
    assert status == 400 and "isn't an API key" in st["error"]
    ring.store[("davibemanager", "provider:NanoGPT")] = "sk-dvm"
    st = (await call(app, "POST", "/api/key", {"from": "vibe-manager"}))[1]
    assert st["key"]["stored"] and st["key"]["ending"] == "-dvm"
    st = (await call(app, "POST", "/api/commands", {}))[1]
    assert {c["state"] for c in st["commands"].values()} == {"current"}
    st = (await call(app, "POST", "/api/editor", {"name": "VSCodium", "on": True}))[1]
    assert st["editors"][0]["state"] == "on" and "// mine" in settings.read_text()
    st = (await call(app, "POST", "/api/editor", {"name": "VSCodium", "on": False}))[1]
    assert st["editors"][0]["state"] == "off"
    st = (await call(app, "POST", "/api/key", {"clear": True}))[1]
    assert not st["key"]["stored"]
    await call(app, "POST", "/api/quit", {})
    assert quit_called == [1]


async def test_the_private_profile_is_changed_from_the_window_and_never_made_unprivate(window):
    app, *_ = window
    st = (await call(app, "POST", "/api/profile", {"profile": "private", "fields": {"model": "private/kimi-k3",
                                                                                     "web_search": "best"}}))[1]
    assert st["config"]["profiles"]["private"]["model"] == "private/kimi-k3"
    assert config.load().profile("private").web_search == "best"
    status, said = await call(app, "POST", "/api/profile", {"profile": "private", "fields": {"fast_model": "z-ai/glm-5.3"}})
    assert status == 400 and "isn't a NanoGPT Private Mode model" in said["error"]
    assert config.load().profile("private").fast_model == "private/deepseek-v4-1-flash"
    status, said = await call(app, "POST", "/api/profile", {"profile": "private", "fields": {"web_search": "kagi"}})
    assert status == 400 and "web_search must be one of" in said["error"]
    assert (await call(app, "POST", "/api/theme", {"theme": "light"}))[0] == 200
    assert (await call(app, "GET", "/api/state"))[1]["theme"] == "light"


async def test_claude_code_is_opened_in_a_folder_and_the_folder_remembered(window, tmp_path):
    app, started, *_ = window
    project = tmp_path / "project"
    project.mkdir()
    st = (await call(app, "POST", "/api/open", {"folder": str(project)}))[1]
    argv, kw = started[-1]
    assert argv[:2] == ["xterm", "-e"] and argv[-6:] == [str(project), sys.executable, "-I", "-m", "daprivatecoder",
                                                         "claude-private"]
    assert kw["cwd"] == str(project) and "APPIMAGE" not in kw["env"]
    assert st["recent"] == [str(project)]
    status, said = await call(app, "POST", "/api/open", {"folder": str(tmp_path / "gone")})
    assert status == 400 and "isn't a folder" in said["error"]
    st = (await call(app, "POST", "/api/forget", {"folder": str(project)}))[1]
    assert st["recent"] == []
    status, said = await call(app, "POST", "/api/choose-folder", {})
    assert status == 400 and "Type the folder" in said["error"]           # no folder chooser in the browser


async def test_models_and_the_enclave_from_nanogpts_public_lists(window, monkeypatch):
    app, *_ = window

    async def listed(base_url, http=None):
        return ["private/glm-5-3", "private/kimi-k3"]

    async def caps(base_url, http=None):
        from daprivatecoder.llm import capabilities
        return capabilities.parse([{"id": "TEE/kimi-k3", "capabilities": {"vision": True, "tool_calling": True},
                                    "context_length": 1048576, "pricing": {"prompt": 3, "completion": 15,
                                    "cacheReadInputPer1kTokens": 0.0015, "currency": "USD", "unit": "per_million_tokens"}}])
    monkeypatch.setattr(manager, "list_private_models", listed)
    monkeypatch.setattr(manager.capabilities, "fetch_nanogpt", caps)
    models = (await call(app, "GET", "/api/models"))[1]["models"]
    kimi = next(m for m in models if m["id"] == "private/kimi-k3")
    assert kimi["vision"] and kimi["price"] == {"input": 3.15, "cache_read": 1.575, "output": 15.75}

    class Verified:
        class attestation:
            enclave, hardware, release_tag, release_digest = "inference.tinfoil.sh", "AMD SEV-SNP", "v0.0.170", "d" * 64
            latest_release, latest_checked, hpke_key_sha256 = "", True, "ab" * 32
            summary = "inference.tinfoil.sh runs tinfoilsh/confidential-model-router v0.0.170 (latest release)"

    class Enclave:
        def __init__(self, relay):
            assert relay == "https://nano-gpt.com/api/v1/private/tinfoil"

        async def current(self):
            return Verified
    monkeypatch.setattr(manager, "Enclave", Enclave)
    att = (await call(app, "POST", "/api/check", {}))[1]["attestation"]
    assert att["hardware"] == "AMD SEV-SNP" and att["key_sha256"] == "ab" * 32
    assert (await call(app, "GET", "/api/state"))[1]["attestation"]["release"] == "v0.0.170"


def test_the_command_opens_the_window_or_starts_claude_code(monkeypatch):
    opened, launched = [], []
    monkeypatch.setattr("daprivatecoder.app.main", lambda argv: opened.append(argv))
    monkeypatch.setattr(cli, "_launch", lambda prog, profile, args: launched.append((prog, profile, args)) or 0)
    assert cli.main([]) == 0 and cli.main(["--browser", "--port", "8790"]) == 0
    assert opened == [[], ["--browser", "--port", "8790"]]
    assert cli.main(["claude-private", "-p", "hi"]) == 0
    assert launched == [("claude-private", "private", ["-p", "hi"])]
