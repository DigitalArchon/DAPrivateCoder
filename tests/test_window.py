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


def test_installing_writes_ours_and_never_touches_someone_elses(monkeypatch, tmp_path):
    bin_dir = commands.bin_dir()
    bin_dir.mkdir(parents=True)
    theirs = tmp_path / "elsewhere/claude-private"
    theirs.parent.mkdir()
    theirs.write_text("#!/bin/sh\necho theirs\n")
    os.symlink(theirs, bin_dir / "claude-private")                   # a link to someone else's
    (bin_dir / "daprivatecoder").write_text("#!/bin/sh\necho mine\n")  # someone else's
    assert commands.state("claude-private") == "taken" and commands.state("daprivatecoder") == "taken"
    assert commands.install() == {"claude-private": "taken", "daprivatecoder": "taken"}
    assert (bin_dir / "claude-private").is_symlink() and theirs.read_text() == "#!/bin/sh\necho theirs\n"
    assert (bin_dir / "daprivatecoder").read_text() == "#!/bin/sh\necho mine\n"
    (bin_dir / "claude-private").unlink()
    assert commands.state("claude-private") == "missing"
    assert commands.install(["claude-private"]) == {"claude-private": "current"}
    assert os.access(bin_dir / "claude-private", os.X_OK)
    monkeypatch.setenv("APPIMAGE", "/moved/DAPrivateCoder.AppImage")    # the AppImage moved
    assert commands.state("claude-private") == "stale"
    commands.refresh()
    assert "/moved/DAPrivateCoder.AppImage" in (bin_dir / "claude-private").read_text()
    assert commands.state("daprivatecoder") == "taken"


def test_the_editor_runs_its_script_out_of_the_path(monkeypatch, tmp_path):
    monkeypatch.setenv("APPIMAGE", "/apps/DAPrivateCoder.AppImage")
    path = editor.wrapper_path()                                    # written now if it wasn't
    assert path == str(commands.wrapper_dir() / "claude-editor") and editor.profile_of(path) == editor.PER_FOLDER
    assert 'exec /apps/DAPrivateCoder.AppImage claude --folders "$@"' in Path(path).read_text()
    assert commands.state("claude-private") == "missing"            # the terminal's commands are another matter


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


async def test_a_browser_is_started_with_a_one_time_address_only_this_user_can_use(window, monkeypatch):
    """Whatever a browser is started with is on its command line, which every user of the computer can
    read: it gets an address that opens the window once, for a minute, for this user's own programs, and
    sends it on to the window with the token in the fragment (which the browser sends nowhere)."""
    from daprivatecoder import app as app_mod
    from daprivatecoder.server import Tickets

    now = [100.0]
    tickets = Tickets(clock=lambda: now[0])
    owners = {"who": os.getuid()}
    page = create_app(TOKEN, manager.Manager(), tickets=tickets, owner=lambda client, server: owners["who"])
    opened = []
    address = app_mod.open_in_browser("http://127.0.0.1:8790", tickets, opened.append)
    assert opened == [address] and TOKEN not in address and address.startswith("http://127.0.0.1:8790/open/")
    path = address.removeprefix("http://127.0.0.1:8790")
    owners["who"] = os.getuid() + 1                                # another user's program, which read it
    status, said = await call(page, "GET", path, token=None)
    assert status == 403 and TOKEN not in said
    owners["who"] = os.getuid()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=page), base_url="http://127.0.0.1") as c:
        r = await c.get(path)
    assert r.status_code == 303 and r.headers["location"] == f"/#t={TOKEN}"   # not spent by the other's try
    assert (await call(page, "GET", path, token=None))[0] == 403    # once only
    late = app_mod.open_in_browser("http://127.0.0.1:8790", tickets, opened.append).removeprefix("http://127.0.0.1:8790")
    now[0] += 61
    assert (await call(page, "GET", late, token=None))[0] == 403    # a minute at most
    assert (await call(page, "GET", "/open/made-up", token=None))[0] == 403


def test_the_owner_of_a_connection_is_read_from_the_kernels_table(tmp_path):
    import socket

    from daprivatecoder.server import _socket_owner
    with socket.socket() as listening:
        listening.bind(("127.0.0.1", 0))
        listening.listen(1)
        with socket.create_connection(listening.getsockname()) as client:
            accepted, peer = listening.accept()
            with accepted:
                assert peer == client.getsockname()
                assert _socket_owner(peer, listening.getsockname()) == os.getuid()
    # 127.0.0.1:54884 -> 127.0.0.1:42509, as /proc/net/tcp lists it, owned by user 1234
    table = tmp_path / "tcp"
    table.write_text("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
                     "   0: 0100007F:D664 0100007F:A60D 01 00000000:00000000 00:00000000 00000000  1234        0 1 1\n")
    assert _socket_owner(("127.0.0.1", 54884), ("127.0.0.1", 42509), (str(table),)) == 1234
    assert _socket_owner(("127.0.0.1", 54885), ("127.0.0.1", 42509), (str(table),)) is None


def test_the_page_takes_the_token_out_of_the_address_bar():
    script = (Path(manager.__file__).parent / "web" / "app.js").read_text()
    assert 'sessionStorage.setItem("dpc-token"' in script and 'history.replaceState(null, "", "/")' in script
    assert "location.hash" in script


async def test_setting_it_up_from_the_window(window, ring, tmp_path):
    app, started, settings, quit_called = window
    status, st = await call(app, "GET", "/api/state")
    assert status == 200 and not any(st["ready"].values()) and st["key"]["stored"] is False
    assert st["config"]["profiles"]["private"]["model"] == "private/glm-5-3" and st["claude"]["error"]
    assert st["editors"] == [{"name": "VSCodium", "path": str(settings), "value": "", "state": "off", "mode": "",
                              "opens": False}]
    assert st["folders"] == {"list": [], "other": "", "error": ""} and st["sessions"] == []
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


async def test_a_key_no_header_can_carry_is_refused_and_never_said(window, ring):
    """A key pasted with a line break or a zero-width space would be refused by the HTTP library in words
    quoting it whole: it isn't stored, and one stored before says what's wrong, not what it is."""
    app, *_ = window
    for key in ("sk-abc123\nsk-more", "sk-abc\u200b123", "sk-abcé123"):
        status, st = await call(app, "POST", "/api/key", {"key": key})
        assert status == 400 and "isn't an API key" in st["error"] and "abc" not in st["error"]
    assert ring.store == {}
    ring.store[("daprivatecoder", "nanogpt")] = "sk-abc\u200b123"
    st = (await call(app, "GET", "/api/state"))[1]
    assert not st["key"]["stored"] and "no API key has" in st["key"]["error"] and "abc" not in st["key"]["error"]


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


# ---------------------------------------------------------------------- what the window says

async def test_usage_says_how_each_request_left_and_blanks_what_wasnt_counted(window, capsys):
    from daprivatecoder import usagelog
    log = usagelog.UsageLog(config.state_dir() / "requests.log", "s1")
    log.write("request", model="private/glm-5-3", sealed=True, status=200, usage={"input": 10, "cache_read": 90,
                                                                                    "output": 5, "cost": 0.001})
    log.write("request", model="z-ai/glm-5.3", sealed=False, status=200, usage={"input": 7, "output": 3})   # nano
    log.write("request", model="claude-opus-4", status=403, error="refused here")                         # never sent
    log.write("vision", model="private/deepseek-v4-1-flash", status=502, error="enclave down")
    log.write("search", mode="zero-retention", status="refused", reason="names this computer or holds a secret")
    log.write("search", mode="best", provider="kagi", status=200, results=8, cost=0.006)
    app, *_ = window
    rows = (await call(app, "GET", "/api/state"))[1]["usage"]["last"][::-1]
    assert [r["sent"] for r in rows] == ["sealed", "clear", "", "sealed", "", "clear"]
    assert [r["sealed"] for r in rows] == [True, False, False, True, False, False]
    assert (rows[0]["input"], rows[0]["cached"], rows[0]["output"]) == (100, 90, 5)
    assert all(rows[i][k] is None for i in (2, 3, 4, 5) for k in ("input", "cached", "output"))
    assert rows[5]["what"] == "Kagi" and rows[5]["results"] == 8 and rows[4]["what"] == ""
    assert rows[2]["error"] == "refused here" and rows[4]["error"].startswith("names this computer")

    import argparse
    assert cli._log(argparse.Namespace(n=10)) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split()[2] for line in lines[:6]] == ["sealed", "clear", "unsent", "sealed", "unsent", "clear"]


async def test_an_editor_on_another_programs_wrapper_is_left_as_it_is(window, capsys):
    app, started, settings, _ = window
    assert [editor.owner(v) for v in (None, "", "/home/u/.local/bin/claude-private", "/opt/wrap", 3)] == \
        ["", "", "ours", "other", "other"]
    st = (await call(app, "POST", "/api/editor", {"name": "VSCodium", "on": True}))[1]
    assert st["editors"][0]["state"] == "on" and "// mine" in settings.read_text()
    assert editor.current(settings) == str(commands.wrapper_dir() / "claude-editor")
    assert cli.main(["editor", "status"]) == 0
    assert "VSCodium: on (DA Private Coder: each folder as chosen" in capsys.readouterr().out
    settings.write_text('{"claudeCode.claudeProcessWrapper": "/opt/wrap"}')
    status, said = await call(app, "POST", "/api/editor", {"name": "VSCodium", "on": True})
    assert status == 400 and "another program" in said["error"] and editor.current(settings) == "/opt/wrap"


async def test_tee_and_standard_sessions_from_the_window(window, ring, monkeypatch, tmp_path):
    app, started, settings, _ = window
    ring.store[("daprivatecoder", "nanogpt")] = "sk-nano"
    project = tmp_path / "project"
    project.mkdir()
    # Start opens what was chosen last
    st = (await call(app, "POST", "/api/start", {"session": "tee"}))[1]
    assert st["start"] == "tee" and (await call(app, "POST", "/api/start", {"session": "secret"}))[0] == 400
    await call(app, "POST", "/api/open", {"folder": str(project), "profile": "tee"})
    assert started[-1][0][-3:] == ["claude", "--profile", "tee"]
    # the editor runs DA Private Coder's script, kept out of the PATH, as each folder says
    st = (await call(app, "POST", "/api/editor", {"name": "VSCodium", "on": True}))[1]
    wrapper = commands.wrapper_dir() / "claude-editor"
    assert st["editors"][0]["state"] == "on" and st["editors"][0]["mode"] == editor.PER_FOLDER
    assert editor.current(settings) == str(wrapper) and 'claude --folders "$@"' in wrapper.read_text()
    monkeypatch.setenv("APPIMAGE", "/moved/DAPrivateCoder.AppImage")             # the AppImage moved: it follows
    commands.refresh()
    assert "exec /moved/DAPrivateCoder.AppImage claude --folders" in wrapper.read_text()
    # a standard profile on OpenRouter: its own key, a host for a model
    await call(app, "POST", "/api/profile", {"profile": "standard", "fields": {"provider": "openrouter"}})
    st = (await call(app, "GET", "/api/state"))[1]
    assert st["provider_key"]["provider"] == "openrouter" and not st["provider_key"]["stored"]
    assert st["ready"]["tee"] is False and st["ready"]["standard"] is False             # no Claude Code here
    st = (await call(app, "POST", "/api/key", {"key": "sk-or-1234", "provider": "openrouter"}))[1]
    assert st["provider_key"]["stored"] and ring.store[("daprivatecoder", "openrouter")] == "sk-or-1234"
    assert ring.store[("daprivatecoder", "nanogpt")] == "sk-nano"
    route = {"priority": "host", "host": "parasail/fp8", "host_name": "Parasail", "fp8": True}
    st = (await call(app, "POST", "/api/route", {"profile": "standard", "model": "z-ai/glm-5.3", "route": route}))[1]
    assert st["config"]["profiles"]["standard"]["routes"]["z-ai/glm-5.3"]["host"] == "parasail/fp8"
    assert config.load().profile("standard").routes["z-ai/glm-5.3"]["priority"] == "host"
    status, said = await call(app, "POST", "/api/route", {"profile": "standard", "model": "z-ai/glm-5.3",
                                                         "route": {"priority": "speed"}})
    assert status == 400 and "one of: auto, latency" in said["error"]          # OpenRouter has no "speed"
    st = (await call(app, "POST", "/api/route", {"profile": "standard", "model": "z-ai/glm-5.3", "route": None}))[1]
    assert st["config"]["profiles"]["standard"]["routes"] == {}
    status, said = await call(app, "POST", "/api/route", {"profile": "tee", "model": "TEE/glm-5.3", "route": route})
    assert status == 400 and "can't be given a route" in said["error"]
    await call(app, "POST", "/api/route", {"profile": "standard", "model": "z-ai/glm-5.3", "route": route})
    st = (await call(app, "POST", "/api/profile", {"profile": "standard", "fields": {"provider": "nanogpt"}}))[1]
    assert st["config"]["profiles"]["standard"]["routes"] == {}                # another provider's hosts: gone
    # a custom provider: https, or local
    status, said = await call(app, "POST", "/api/profile", {"profile": "standard", "fields": {
        "provider": "custom", "base_url": "http://llm.example.com/v1"}})
    assert status == 400 and "https" in said["error"]
    st = (await call(app, "POST", "/api/profile", {"profile": "standard", "fields": {
        "provider": "custom", "base_url": "https://llm.example.com/v1"}}))[1]
    assert st["provider_key"]["provider"] == "custom" and st["config"]["profiles"]["standard"]["base_url"] == \
        "https://llm.example.com/v1"


async def test_each_profiles_models_come_from_its_provider(window, monkeypatch):
    app, *_ = window
    from daprivatecoder.llm import capabilities

    async def nanogpt(base_url, http=None):
        return capabilities.parse([
            {"id": "TEE/kimi-k3", "capabilities": {"vision": True, "tool_calling": True}},
            {"id": "TEE/llama3-3-70b", "capabilities": {"vision": False, "tool_calling": False}},
            {"id": "z-ai/glm-5.3", "capabilities": {"tool_calling": True}, "subscription": {"included": True},
             "pricing": {"prompt": 1.4, "completion": 4.4, "currency": "USD", "unit": "per_million_tokens"}},
            {"id": "flux/image", "capabilities": {"tool_calling": False}}])
    monkeypatch.setattr(manager.capabilities, "fetch_nanogpt", nanogpt)
    tee = (await call(app, "GET", "/api/models?profile=tee"))[1]["models"]
    assert [m["id"] for m in tee] == ["TEE/kimi-k3", "TEE/llama3-3-70b"] and tee[1]["tools"] is False
    assert not any(m["routable"] for m in tee)
    standard = (await call(app, "GET", "/api/models?profile=standard"))[1]["models"]
    assert [m["id"] for m in standard] == ["TEE/kimi-k3", "z-ai/glm-5.3"]       # the ones that take tool calls
    glm = standard[1]
    assert glm["included"] and glm["routable"] and glm["price"] == {"input": 1.4, "cache_read": 1.4, "output": 4.4}

    async def hosts(provider, base_url, model, http=None):
        assert (provider, base_url, model) == ("nanogpt", "https://nano-gpt.com/api/v1", "z-ai/glm-5.3")
        return {"model": model, "supported": True, "auto": {}, "hosts": [{"id": "parasail", "name": "Parasail"}]}
    monkeypatch.setattr(manager.routes, "fetch_hosts", hosts)
    listed = (await call(app, "POST", "/api/hosts", {"profile": "standard", "model": "z-ai/glm-5.3"}))[1]
    assert listed["hosts"][0]["id"] == "parasail"


async def test_a_password_in_a_providers_address_is_kept_in_the_keyring_and_never_said(window, ring, monkeypatch):
    """The Address field keeps what was typed; the settings file and whatever the window says of it
    don't: the password is in the keyring (config.py)."""
    app, *_ = window
    typed = "https://bob:hunter2@llm.example/v1"
    st = (await call(app, "POST", "/api/profile", {"profile": "standard",
                                                   "fields": {"provider": "custom", "base_url": typed}}))[1]
    p = st["config"]["profiles"]["standard"]
    assert p["base_url"] == typed and p["base_url_shown"] == "https://…@llm.example/v1"
    assert "hunter2" not in config.config_path().read_text()
    assert ring.store[("daprivatecoder", "profiles.standard.base_url_with_password")] == typed
    # the server refusing its list of models: httpx's error names the URL it was for, password and all
    import httpx
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: real(*a, **{
        "transport": httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "who?"})), **kw}))
    status, said = await call(app, "GET", "/api/models?profile=standard&fresh=1")
    assert status >= 400 and "hunter2" not in said["error"] and "llm.example" in said["error"]


async def test_some_folders_on_da_private_coder_in_the_editor_the_rest_as_usual(window, tmp_path, monkeypatch):
    """Windows can differ: a folder chosen runs on its kind of session in the editor, any other as
    usual (or as `other` says); Open in VSCodium chooses the folder, switches the editor on, opens it."""
    from daprivatecoder import folders, gate
    app, started, settings, _ = window
    private, tee = tmp_path / "secret-project", tmp_path / "work"
    private.mkdir(), tee.mkdir()
    (private / "sub").mkdir()
    opened = []
    monkeypatch.setattr(editor, "open_command", lambda name: ["/usr/bin/codium"])
    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: opened.append((argv, kw)))
    st = (await call(app, "POST", "/api/open-editor", {"name": "VSCodium", "folder": str(private), "profile": "private"}))[1]
    assert opened[-1][0] == ["/usr/bin/codium", str(private)] and opened[-1][1]["start_new_session"]
    assert st["editors"][0]["state"] == "on" and st["folders"]["list"] == [{"folder": str(private), "profile": "private"}]
    st = (await call(app, "POST", "/api/folder", {"folder": str(tee), "profile": "tee"}))[1]
    assert [f["profile"] for f in st["folders"]["list"]] == ["private", "tee"]
    f = folders.load()
    assert folders.choice(f, str(private / "sub")) == ("private", str(private))     # a folder inside counts
    assert folders.choice(f, str(tee)) == ("tee", str(tee))
    assert folders.choice(f, str(tmp_path)) == ("", "")                            # any other: Claude as usual
    st = (await call(app, "POST", "/api/folder", {"other": "standard"}))[1]
    assert st["folders"]["other"] == "standard" and folders.choice(folders.load(), "/")[0] == "standard"
    status, said = await call(app, "POST", "/api/folder", {"folder": str(tmp_path / "gone"), "profile": "private"})
    assert status == 400 and "isn't a folder" in said["error"]
    st = (await call(app, "POST", "/api/folder", {"folder": str(tee), "profile": ""}))[1]
    assert [f["folder"] for f in st["folders"]["list"]] == [str(private)]

    # a session the editor started in a folder stops once that folder says otherwise, or the switch is off
    g = gate.Gate("private", [("VSCodium", settings)], str(private / "sub"))
    monkeypatch.setattr(gate, "window_open", lambda: True)
    assert g.why() == ""
    await call(app, "POST", "/api/folder", {"folder": str(private), "profile": "tee"})
    assert "now runs DA Private Coder's TEE sessions in VSCodium" in g.why()
    await call(app, "POST", "/api/folder", {"folder": str(private), "profile": ""})
    await call(app, "POST", "/api/folder", {"other": ""})
    assert "now runs Claude as usual in VSCodium" in g.why()
    await call(app, "POST", "/api/folder", {"folder": str(private), "profile": "private"})
    assert g.why() == ""
    await call(app, "POST", "/api/editor", {"name": "VSCodium", "on": False})
    assert "switched off for VSCodium" in g.why()
    monkeypatch.setattr(gate, "window_open", lambda: False)
    assert g.why().startswith("DA Private Coder is closed, so nothing was sent")


async def test_the_window_lists_the_sessions_running_now_and_where_their_requests_went(window, tmp_path, monkeypatch):
    from daprivatecoder import launcher
    from daprivatecoder.usagelog import UsageLog
    app, *_ = window
    me = os.getpid()
    monkeypatch.setattr(launcher, "proxy_alive", lambda pid: pid == me)
    launcher.sessions_dir().mkdir(parents=True)
    launcher.session_file(me).write_text(json.dumps({"session": "s1", "profile": "private", "kind": "private",
                                                     "folder": str(tmp_path), "editors": [], "started": 1}))
    launcher.session_file(1).write_text(json.dumps({"session": "gone", "profile": "tee"}))   # its proxy has ended
    log = UsageLog(config.state_dir() / "requests.log", "s1")
    log.write("request", model="private/kimi-k3", tier="private", sealed=True, status=200)
    st = (await call(app, "GET", "/api/state"))[1]
    [s] = st["sessions"]
    assert s["pid"] == me and s["kind"] == "private" and s["folder"] == str(tmp_path) and s["editor"] == ""
    assert s["last"]["model"] == "private/kimi-k3" and s["last"]["sent"] == "sealed" and s["last"]["status"] == 200
    assert s["stops"].startswith("DA Private Coder is closed")       # this test holds no window open


async def test_where_claude_code_compacts_is_saved_from_the_window(window):
    app, *_ = window
    st = (await call(app, "POST", "/api/profile", {"profile": "private", "fields": {"context": 400000}}))[1]
    assert st["config"]["profiles"]["private"]["context"] == 400000 and config.load().profile("private").context == 400000
    status, said = await call(app, "POST", "/api/profile", {"profile": "private", "fields": {"context": 123}})
    assert status == 400 and "200000, 400000, 1000000" in said["error"]
