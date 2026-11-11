"""Starting Claude Code on the proxy: what it is given, and a whole session with a stand-in for it."""

import io
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from daprivatecoder import launcher
from daprivatecoder.config import Profile
from daprivatecoder.launcher import (LaunchError, child_env, claude_args, claude_env, extension_binaries, find_claude,
                                  managed_conflicts, split_wrapper_args)
from daprivatecoder.usagelog import read

PRIVATE = Profile("private", "private", "private/glm-5-3", "private/kimi-k3", "private/deepseek-v4-1-flash",
                  "private/deepseek-v4-1-flash", "low")
NANO = Profile("nano", "standard", "z-ai/glm-5.3", "moonshotai/kimi-k3", "z-ai/glm-5.3-flash", "", "low")
BIN = Path(sys.executable).parent


def program(path: Path, text: str = "#!/bin/sh\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


def test_the_editors_binary_comes_first_when_run_as_its_wrapper(tmp_path):
    claude = program(tmp_path / "resources" / "native-binary" / "claude")
    assert split_wrapper_args([str(claude), "--output-format", "stream-json"]) == \
        (str(claude), ["--output-format", "stream-json"])
    assert split_wrapper_args(["-p", "hi"]) == (None, ["-p", "hi"])
    assert split_wrapper_args(["claude", "-p"]) == (None, ["claude", "-p"])          # not a path: an argument
    plain = tmp_path / "claude-notes.txt"
    plain.write_text("")
    assert split_wrapper_args([str(plain)]) == (None, [str(plain)])                  # not a program
    ours = program(tmp_path / "bin" / "claude-private")
    assert split_wrapper_args([str(ours), "x"]) == (None, [str(ours), "x"])           # never itself


def test_claude_code_is_found_on_path_or_in_an_editor(tmp_path, monkeypatch):
    old = program(tmp_path / "ext" / "anthropic.claude-code-2.1.9-linux-x64" / "resources" / "native-binary" / "claude")
    new = program(tmp_path / "ext" / "anthropic.claude-code-2.1.296-linux-x64" / "resources" / "native-binaries"
                  / "linux-x64" / "claude")
    assert extension_binaries([str(tmp_path / "ext")]) == [new, old]               # newest first, not by name
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert find_claude("", [str(tmp_path / "ext")]) == str(new)
    on_path = program(tmp_path / "bin" / "claude")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert find_claude("", [str(tmp_path / "ext")]) == str(on_path)
    assert find_claude(str(old), []) == str(old)
    with pytest.raises(LaunchError, match="isn't there"):
        find_claude(str(tmp_path / "gone"), [])
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(LaunchError, match="isn't installed"):
        find_claude("", [str(tmp_path / "nothing")])


def test_claude_code_is_pointed_at_the_proxy_with_the_profiles_models():
    env = claude_env(PRIVATE, 4321, "tok")
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:4321" and env["ANTHROPIC_AUTH_TOKEN"] == "tok"
    assert env["ANTHROPIC_MODEL"] == env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "private/glm-5-3"
    assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == env["ANTHROPIC_DEFAULT_FABLE_MODEL"] == "private/kimi-k3"
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "private/deepseek-v4-1-flash"
    assert env["ANTHROPIC_DEFAULT_OPUS_MODEL_DESCRIPTION"].startswith("Private")
    for way_around in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"):
        assert env[way_around] == ""
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1" and env["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false"
    assert env["CLAUDE_CODE_ATTRIBUTION_HEADER"] == "0" and env["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] == "1"
    assert "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS" not in claude_env(NANO, 1, "t")


def test_the_settings_go_in_once_with_ours_over_any_given(tmp_path):
    env = claude_env(PRIVATE, 4321, "tok")
    given = {"env": {"FOO": "1", "ANTHROPIC_BASE_URL": "https://elsewhere.example"}, "permissions": {"deny": ["Bash(rm:*)"]},
             "theme": "dark"}
    args = claude_args(PRIVATE, env, ["--settings", json.dumps(given), "-p", "hi"])
    assert args[0] == "--settings" and args[2:] == ["-p", "hi"] and args.count("--settings") == 1
    s = json.loads(args[1])
    assert s["env"]["FOO"] == "1" and s["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:4321"
    assert s["permissions"]["deny"] == ["Bash(rm:*)", "WebSearch"] and s["theme"] == "dark"
    assert s["skipWebFetchPreflight"] is True and s["disableClaudeAiConnectors"] is True and s["model"] == "private/glm-5-3"
    file = tmp_path / "s.json"
    file.write_text(json.dumps({"env": {"BAR": "2"}}))
    s = json.loads(claude_args(PRIVATE, env, [f"--settings={file}"])[1])
    assert s["env"]["BAR"] == "2" and s["env"]["ANTHROPIC_AUTH_TOKEN"] == "tok"
    with pytest.raises(LaunchError, match="isn't valid JSON"):
        claude_args(PRIVATE, env, ["--settings", "{nope"])


def test_a_session_starts_at_the_profiles_effort_and_claude_codes_own_changes_it():
    from dataclasses import replace
    env = claude_env(PRIVATE, 4321, "tok")
    assert json.loads(claude_args(PRIVATE, env, [])[1])["effortLevel"] == PRIVATE.reasoning
    # Claude Code's effortLevel setting has no "max": its xhigh, which the proxy runs as max
    assert json.loads(claude_args(replace(PRIVATE, reasoning="max"), env, [])[1])["effortLevel"] == "xhigh"
    # one the caller gives in --settings stands; --effort is Claude Code's, passed on as it is
    given = claude_args(PRIVATE, env, ["--settings", '{"effortLevel": "high"}', "--effort", "medium"])
    assert json.loads(given[1])["effortLevel"] == "high" and given[2:] == ["--effort", "medium"]


def test_a_model_the_editor_passes_by_a_name_that_isnt_private_becomes_the_profiles():
    env = claude_env(PRIVATE, 1, "t")
    args = claude_args(PRIVATE, env, ["--model", "claude-opus-5-5", "--fallback-model=claude-sonnet-5-5", "--model",
                                      "opus", "--model", "private/kimi-k3", "-p", "--model"])
    assert args[2:] == ["--model", "private/glm-5-3", "--fallback-model", "private/glm-5-3", "--model", "opus",
                        "--model", "private/kimi-k3", "-p", "--model"]
    assert claude_args(NANO, env, ["--model", "claude-opus-5-5"])[2:] == ["--model", "claude-opus-5-5"]


def test_managed_settings_that_would_override_ours_stop_the_start(tmp_path):
    env = claude_env(PRIVATE, 1, "t")
    managed = tmp_path / "managed-settings.json"
    assert managed_conflicts(env, managed) == []
    managed.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://gateway.corp", "OTHER": "x"}}))
    (tmp_path / "managed-settings.d").mkdir()
    (tmp_path / "managed-settings.d" / "10.json").write_text(json.dumps({"env": {"ANTHROPIC_MODEL": "claude-x"}}))
    assert managed_conflicts(env, managed) == ["ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL"]


def test_claude_codes_environment():
    env = claude_env(PRIVATE, 4321, "tok")
    out = child_env({"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-ant-real", "NO_PROXY": "corp.example",
                     "DAPRIVATECODER_TOKEN": "proxy's", "HTTPS_PROXY": "http://10.0.0.1:3128"}, env)
    assert out["PATH"] == "/usr/bin" and out["ANTHROPIC_API_KEY"] == "" and "DAPRIVATECODER_TOKEN" not in out
    assert out["NO_PROXY"] == "corp.example,127.0.0.1,localhost" and out["no_proxy"] == "127.0.0.1,localhost"
    assert out["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:4321"


STAND_IN = '''\
#!{python}
"""Stands in for Claude Code: asks the proxy what Claude Code would, and writes down what it got."""
import json, os, sys, urllib.request, urllib.error
base, token = os.environ["ANTHROPIC_BASE_URL"], os.environ["ANTHROPIC_AUTH_TOKEN"]
out = {{"argv": sys.argv[1:], "env": {{k: v for k, v in os.environ.items() if k.startswith(("ANTHROPIC", "CLAUDE", "DISABLE"))}}}}
def ask(method, path, body=None, auth=token):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={{"authorization": "Bearer " + auth, "content-type": "application/json"}})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
msg = {{"model": "private/glm-5-3", "max_tokens": 10, "messages": [{{"role": "user", "content": "hi"}}]}}
out["probe"] = ask("HEAD", "/api/hello")[0]
out["count"] = ask("POST", "/v1/messages/count_tokens", msg)
out["models"] = ask("GET", "/v1/models?limit=1000")
out["message"] = ask("POST", "/v1/messages?beta=true", msg)
out["intruder"] = ask("POST", "/v1/messages", msg, auth="guess")[0]
at = sys.argv.index("--settings") + 1
out["settings"] = json.load(open(sys.argv[at]))
out["settings_mode"] = os.stat(sys.argv[at]).st_mode & 0o777
open(os.environ["STAND_IN_RESULT"], "w").write(json.dumps(out))
'''


import contextlib


@contextlib.contextmanager
def window_open(env):
    """DA Private Coder's window, as far as sessions can tell: a process holding its lock (gate.py)."""
    holder = subprocess.Popen([sys.executable, "-c", "import sys, time\nfrom daprivatecoder import gate\n"
                               "gate.hold()\nprint('held', flush=True)\nsys.stdin.read()"],
                              env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        yield
    finally:
        holder.kill()
        holder.wait()


def session_env(tmp_path, base_url="http://127.0.0.1:9/api/v1"):
    cfg = tmp_path / "config" / "daprivatecoder" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(f'base_url = "{base_url}"\n[profiles.private]\nprivate = true\n'
                   'model = "private/glm-5-3"\nfast_model = "private/deepseek-v4-1-flash"\n')
    (tmp_path / "home").mkdir(exist_ok=True)
    return {**os.environ, "HOME": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_STATE_HOME": str(tmp_path / "state"), "STAND_IN_RESULT": str(tmp_path / "result.json"),
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring"}


def proxy_gone(session: dict, started: float) -> None:
    while True:                                                   # the proxy goes with Claude Code
        try:
            os.kill(session["pid"], 0)
        except ProcessLookupError:
            return
        assert time.monotonic() - started < 30, "the proxy outlived Claude Code"
        time.sleep(0.1)


def test_a_whole_session_with_a_stand_in_for_claude_code(tmp_path):
    """claude-private as the editor runs it: the proxy starts beside Claude Code (here a stand-in), serves
    it on 127.0.0.1 with the session's token, and stops when it exits. Nothing goes beyond this computer:
    the settings point at a NanoGPT that isn't one, on this machine."""
    env = session_env(tmp_path)
    stand_in = program(tmp_path / "ext" / "resources" / "native-binary" / "claude", STAND_IN.format(python=sys.executable))
    result = tmp_path / "result.json"
    started = time.monotonic()
    with window_open(env):
        done = subprocess.run([str(BIN / "claude-private"), str(stand_in), "--output-format", "stream-json"], env=env,
                              capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    out = json.loads(result.read_text())
    assert out["argv"][0] == "--mcp-config" and out["argv"][2] == "--settings"   # a private profile searches (Linkup)
    assert out["argv"][4:] == ["--output-format", "stream-json"]
    assert json.loads(out["argv"][1])["mcpServers"]["daprivatecoder"]["args"][3:6] == ["search-server", "--profile", "private"]
    # the session's token isn't on Claude Code's command line (any user of this computer can read that)
    assert out["env"]["ANTHROPIC_AUTH_TOKEN"] not in " ".join(out["argv"])
    assert out["settings"]["env"]["ANTHROPIC_AUTH_TOKEN"] == out["env"]["ANTHROPIC_AUTH_TOKEN"]
    assert out["settings_mode"] == 0o600
    assert out["env"]["ANTHROPIC_MODEL"] == "private/glm-5-3" and out["env"]["ANTHROPIC_API_KEY"] == ""
    assert out["probe"] == 200 and out["intruder"] == 401
    assert out["count"][0] == 200 and json.loads(out["count"][1])["input_tokens"] > 0
    assert out["models"][0] == 200 and json.loads(out["models"][1])["data"][0]["id"] == "private/glm-5-3"
    status, body = out["message"]
    assert status == 403 and "isn't NanoGPT" in body               # sealed or nothing: here, nothing
    session = next(r for r in read(tmp_path / "state" / "daprivatecoder" / "requests.log") if r["kind"] == "session")
    assert session["profile"] == "private" and session["private"] is True
    proxy_gone(session, started)
    assert not Path(out["argv"][3]).exists()                       # its settings file goes with it


def test_nothing_is_sent_while_da_private_coder_is_closed(tmp_path):
    """Closed, a session the editor starts says so to Claude Code at each request (and goes on once it's
    open again); one asked for in a terminal doesn't start at all."""
    env = session_env(tmp_path)
    stand_in = program(tmp_path / "ext" / "resources" / "native-binary" / "claude", STAND_IN.format(python=sys.executable))
    started = time.monotonic()
    done = subprocess.run([str(BIN / "claude-private"), str(stand_in)], env=env, capture_output=True, text=True,
                          timeout=60)
    assert done.returncode == 0, done.stderr
    out = json.loads((tmp_path / "result.json").read_text())
    status, body = out["message"]
    assert status == 403 and "DA Private Coder is closed, so nothing was sent" in body
    assert out["count"][0] == 403 and out["probe"] == 200
    session = next(r for r in read(tmp_path / "state" / "daprivatecoder" / "requests.log") if r["kind"] == "session")
    proxy_gone(session, started)
    terminal = subprocess.run([str(BIN / "claude-private"), "-p", "hi"], env=env, capture_output=True, text=True,
                              timeout=60)
    assert terminal.returncode != 0 and "DA Private Coder isn't open" in terminal.stderr


def test_the_search_server_comes_first_with_what_it_needs(tmp_path):
    from daprivatecoder.launcher import mcp_config, search_notice
    base = {"XDG_STATE_HOME": "/s", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus", "SECRET": "x"}
    mcp = mcp_config(PRIVATE_SEARCH, tmp_path / "c.toml", base)
    server = mcp["mcpServers"]["daprivatecoder"]
    assert server["command"] == sys.executable and server["type"] == "stdio"
    assert server["args"] == ["-I", "-m", "daprivatecoder", "search-server", "--profile", "private", "--config", str(tmp_path / "c.toml")]
    assert server["env"] == {"XDG_STATE_HOME": "/s", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}
    args = claude_args(PRIVATE_SEARCH, claude_env(PRIVATE_SEARCH, 1, "t"), ["fix the build"], mcp)
    assert args[0] == "--mcp-config" and args[2] == "--settings" and args[4:] == ["fix the build"]
    assert mcp_config(PRIVATE, None, base) is None and claude_args(PRIVATE, {}, [])[0] == "--settings"
    # what the session's proxy is given to know whether it may send (gate.py), the search server is given too
    editors = [("VSCodium", tmp_path / "settings.json")]
    asks = mcp_config(PRIVATE_SEARCH, None, base, editors, "/home/u/project")["mcpServers"]["daprivatecoder"]["args"]
    assert asks[asks.index("--editor"):] == ["--editor", "VSCodium", str(tmp_path / "settings.json"),
                                             "--folder", "/home/u/project"]
    assert "neither keeps it" in search_notice(PRIVATE_SEARCH)
    assert "not zero data retention" in search_notice(Profile("p", "private", "private/glm-5-3", "", "", web_search="best"))
    assert search_notice(PRIVATE) == ""


PRIVATE_SEARCH = Profile("private", "private", "private/glm-5-3", "private/kimi-k3", "private/deepseek-v4-1-flash",
                         "private/deepseek-v4-1-flash", "low", "zero-retention")


def test_claude_code_gets_nothing_the_appimage_set_for_itself():
    env = claude_env(PRIVATE, 4321, "tok")
    base = {"PATH": "/usr/bin", "APPIMAGE": "/home/u/Apps/DAPrivateCoder.AppImage", "APPDIR": "/tmp/.mount_DAPriv",
            "ARGV0": "x", "OWD": "/home/u", "SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt",
            "REQUESTS_CA_BUNDLE": "/etc/ssl/certs/ca-certificates.crt", "DAPRIVATECODER_APPRUN_SET": "SSL_CERT_FILE REQUESTS_CA_BUNDLE"}
    out = child_env(base, env)
    for gone in ("APPIMAGE", "APPDIR", "ARGV0", "OWD", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "DAPRIVATECODER_APPRUN_SET"):
        assert gone not in out, gone
    mine = child_env({"SSL_CERT_FILE": "/my/ca.pem"}, env)              # the user's own: kept
    assert mine["SSL_CERT_FILE"] == "/my/ca.pem"
    from daprivatecoder.launcher import mcp_config
    server = mcp_config(PRIVATE_SEARCH, None, base)["mcpServers"]["daprivatecoder"]
    assert server["env"]["SSL_CERT_FILE"] == "/etc/ssl/certs/ca-certificates.crt"   # the search server's TLS: the host's store


USUAL = '''\
#!{python}
import json, os, sys, urllib.request
if "ANTHROPIC_BASE_URL" in os.environ:               # on the proxy: once it answers, its session is written down
    urllib.request.urlopen(urllib.request.Request(os.environ["ANTHROPIC_BASE_URL"] + "/api/hello", method="HEAD",
        headers={{"authorization": "Bearer " + os.environ["ANTHROPIC_AUTH_TOKEN"]}}), timeout=30).close()
json.dump({{"argv": sys.argv[1:], "env": dict(os.environ), "cwd": os.getcwd()}}, open(os.environ["STAND_IN_RESULT"], "w"))
'''


def test_the_editors_script_runs_each_folder_as_it_was_chosen(tmp_path):
    """claude-editor: a folder not chosen gets Claude Code as the editor started it, untouched (and even
    with DA Private Coder closed, or its settings broken); a folder chosen, its kind of session."""
    from daprivatecoder import folders
    env = session_env(tmp_path)
    env["ANTHROPIC_API_KEY"] = "sk-ant-mine"                        # the user's own way to Claude: kept as usual
    usual = program(tmp_path / "ext" / "resources" / "native-binary" / "claude", USUAL.format(python=sys.executable))
    chosen, other = tmp_path / "secret", tmp_path / "other"
    chosen.mkdir(), other.mkdir()
    (tmp_path / "config" / "daprivatecoder" / "config.toml").write_text("this isn't TOML [")
    run = [sys.executable, "-I", "-m", "daprivatecoder", "claude", "--folders", str(usual), "--output-format", "stream-json"]
    done = subprocess.run(run, env=env, cwd=other, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    out = json.loads((tmp_path / "result.json").read_text())
    assert out["argv"] == ["--output-format", "stream-json"] and out["cwd"] == str(other)
    assert "ANTHROPIC_BASE_URL" not in out["env"] and out["env"]["ANTHROPIC_API_KEY"] == "sk-ant-mine"
    session_env(tmp_path)                                           # the settings as they were
    xdg = {k: env[k] for k in ("XDG_CONFIG_HOME",)}
    old = os.environ.get("XDG_CONFIG_HOME")
    os.environ.update(xdg)
    try:
        folders.save(folders.with_folder(folders.Folders(), str(chosen), "private"))
    finally:
        os.environ.pop("XDG_CONFIG_HOME") if old is None else os.environ.update(XDG_CONFIG_HOME=old)
    with window_open(env):
        done = subprocess.run(run, env=env, cwd=chosen, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    out = json.loads((tmp_path / "result.json").read_text())
    assert out["env"]["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1:") and out["env"]["ANTHROPIC_API_KEY"] == ""
    assert out["env"]["ANTHROPIC_MODEL"] == "private/glm-5-3" and out["cwd"] == str(chosen)
    session = [r for r in read(tmp_path / "state" / "daprivatecoder" / "requests.log") if r["kind"] == "session"][-1]
    assert session["folder"] == str(chosen)


def test_claude_code_is_told_where_to_compact():
    from dataclasses import replace
    assert claude_env(PRIVATE, 1, "t")["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "200000"
    assert claude_env(replace(PRIVATE, context=1_000_000), 1, "t")["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "1000000"


def test_each_search_is_asked_about_whatever_was_allowed_before():
    """Claude Code asks before an MCP tool runs until the user says "don't ask again", which an allow rule
    keeps; an ask rule outranks it, so each search shows its query and waits."""
    from daprivatecoder import search
    from daprivatecoder.launcher import SEARCH_TOOL, mcp_config
    env = claude_env(PRIVATE_SEARCH, 1, "t")
    given = {"permissions": {"allow": [SEARCH_TOOL], "ask": ["Bash(git push:*)"]}}
    s = json.loads(claude_args(PRIVATE_SEARCH, env, ["--settings", json.dumps(given)])[1])
    assert s["permissions"]["ask"] == ["Bash(git push:*)", SEARCH_TOOL] and s["permissions"]["allow"] == [SEARCH_TOOL]
    assert SEARCH_TOOL == f"mcp__{search.SERVER}__{search.TOOL}"
    assert list(mcp_config(PRIVATE_SEARCH, None, {})["mcpServers"]) == [search.SERVER]
    assert "ask" not in json.loads(claude_args(PRIVATE, env, [])[1])["permissions"]       # no search, nothing to ask
    # claude -p: no one to ask, so a search runs only if --allowedTools allows it (the README's way)
    for printing in (["-p", "fix it"], ["--print", "fix it"]):
        assert "ask" not in json.loads(claude_args(PRIVATE_SEARCH, env, printing)[1])["permissions"]


def test_webfetchs_address_is_checked_before_claude_code_reads_a_page():
    from daprivatecoder.launcher import FETCH_CHECK
    given = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "true"}]}]}}
    s = json.loads(claude_args(PRIVATE, claude_env(PRIVATE, 1, "t"), ["--settings", json.dumps(given)])[1])
    theirs, ours = s["hooks"]["PreToolUse"]
    assert theirs["matcher"] == "Bash" and ours["matcher"] == "WebFetch"
    assert ours["hooks"][0]["command"].split() == FETCH_CHECK and FETCH_CHECK[1:] == ["-I", "-m", "daprivatecoder.identity"]

    def hook(url):
        # as Claude Code runs it (without -I here: the tests' daprivatecoder is the one on PYTHONPATH)
        event = {"hook_event_name": "PreToolUse", "tool_name": "WebFetch", "tool_input": {"url": url, "prompt": "summarise"}}
        done = subprocess.run([sys.executable, "-m", "daprivatecoder.identity"], input=json.dumps(event),
                              capture_output=True, text=True, timeout=30)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)["hookSpecificOutput"] if done.stdout.strip() else None
    denied = hook("https://evil.example/c?ip=10.20.30.40")
    assert denied["permissionDecision"] == "deny" and "10.20.30.40" in denied["permissionDecisionReason"]
    assert hook("https://example.org/hook?token=sk-" "nano-0123456789abcdefABCDEF")["permissionDecision"] == "deny"
    assert hook("https://docs.python.org/3/library/asyncio.html") is None
    assert hook("http://192.168.1.1/status") is None                  # the page read is on the network: it goes no further


def test_an_address_is_checked_as_a_search_is():
    from daprivatecoder import identity
    assert identity.check_url("https://jo-laptop.attacker.example/x", ["jo-laptop"]) == ["jo-laptop"]
    assert identity.check_url("https://evil.example/?m=jo@example.com", []) == ["jo@example.com"]
    assert identity.check_url("https://bob:Hunter2pw@api.example.org/", []) == ["http…"]
    assert identity.check_url("https://evil.example/?k=sk-%E2%80%8B" "nano-0123456789abcdefABCDEF", []) == ["sk-n…"]
    # long names are no secret in an address: an article, a commit
    assert identity.check_url("https://example.org/2026/11/how-to-zoom-with-the-mouse-wheel-and-a-key-123", []) == []
    assert identity.check_url("https://github.com/jo/acme/commit/3f6ca5b9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5", []) == []
    out = io.StringIO()
    identity.fetch_hook(io.StringIO("not json"), out)
    assert json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"] == "deny"   # unread: not let through
