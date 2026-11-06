"""Starting Claude Code on the proxy: what it is given, and a whole session with a stand-in for it."""

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from privatecode import launcher
from privatecode.config import Profile
from privatecode.launcher import (LaunchError, child_env, claude_args, claude_env, extension_binaries, find_claude,
                                  managed_conflicts, split_wrapper_args)
from privatecode.usagelog import read

PRIVATE = Profile("private", True, "private/glm-5-3", "private/kimi-k3", "private/deepseek-v4-1-flash",
                  "private/deepseek-v4-1-flash", "low")
NANO = Profile("nano", False, "z-ai/glm-5.3", "moonshotai/kimi-k3", "z-ai/glm-5.3-flash", "", "low")
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
                     "PRIVATECODE_TOKEN": "proxy's", "HTTPS_PROXY": "http://10.0.0.1:3128"}, env)
    assert out["PATH"] == "/usr/bin" and out["ANTHROPIC_API_KEY"] == "" and "PRIVATECODE_TOKEN" not in out
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
open(os.environ["STAND_IN_RESULT"], "w").write(json.dumps(out))
'''


def test_a_whole_session_with_a_stand_in_for_claude_code(tmp_path):
    """claude-private as the editor runs it: the proxy starts beside Claude Code (here a stand-in), serves
    it on 127.0.0.1 with the session's token, and stops when it exits. Nothing goes beyond this computer:
    the settings point at a NanoGPT that isn't one, on this machine."""
    cfg = tmp_path / "config" / "privatecode" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('base_url = "http://127.0.0.1:9/api/v1"\n[profiles.private]\nprivate = true\n'
                   'model = "private/glm-5-3"\nfast_model = "private/deepseek-v4-1-flash"\n')
    stand_in = program(tmp_path / "ext" / "resources" / "native-binary" / "claude", STAND_IN.format(python=sys.executable))
    result = tmp_path / "result.json"
    env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path / "config"), "XDG_STATE_HOME": str(tmp_path / "state"),
           "STAND_IN_RESULT": str(result), "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring"}
    started = time.monotonic()
    done = subprocess.run([str(BIN / "claude-private"), str(stand_in), "--output-format", "stream-json"], env=env,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    out = json.loads(result.read_text())
    assert out["argv"][0] == "--mcp-config" and out["argv"][2] == "--settings"   # a private profile searches (Linkup)
    assert out["argv"][4:] == ["--output-format", "stream-json"]
    assert json.loads(out["argv"][1])["mcpServers"]["privatecode"]["args"][2:5] == ["search-server", "--profile", "private"]
    assert json.loads(out["argv"][3])["env"]["ANTHROPIC_AUTH_TOKEN"] == out["env"]["ANTHROPIC_AUTH_TOKEN"]
    assert out["env"]["ANTHROPIC_MODEL"] == "private/glm-5-3" and out["env"]["ANTHROPIC_API_KEY"] == ""
    assert out["probe"] == 200 and out["intruder"] == 401
    assert out["count"][0] == 200 and json.loads(out["count"][1])["input_tokens"] > 0
    assert out["models"][0] == 200 and json.loads(out["models"][1])["data"][0]["id"] == "private/glm-5-3"
    status, body = out["message"]
    assert status == 403 and "isn't NanoGPT" in body               # sealed or nothing: here, nothing
    session = next(r for r in read(tmp_path / "state" / "privatecode" / "requests.log") if r["kind"] == "session")
    assert session["profile"] == "private" and session["private"] is True
    while True:                                                   # the proxy goes with Claude Code
        try:
            os.kill(session["pid"], 0)
        except ProcessLookupError:
            break
        assert time.monotonic() - started < 30, "the proxy outlived Claude Code"
        time.sleep(0.1)


def test_the_search_server_comes_first_with_what_it_needs(tmp_path):
    from privatecode.launcher import mcp_config, search_notice
    base = {"XDG_STATE_HOME": "/s", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus", "SECRET": "x"}
    mcp = mcp_config(PRIVATE_SEARCH, tmp_path / "c.toml", base)
    server = mcp["mcpServers"]["privatecode"]
    assert server["command"] == sys.executable and server["type"] == "stdio"
    assert server["args"] == ["-m", "privatecode", "search-server", "--profile", "private", "--config", str(tmp_path / "c.toml")]
    assert server["env"] == {"XDG_STATE_HOME": "/s", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}
    args = claude_args(PRIVATE_SEARCH, claude_env(PRIVATE_SEARCH, 1, "t"), ["fix the build"], mcp)
    assert args[0] == "--mcp-config" and args[2] == "--settings" and args[4:] == ["fix the build"]
    assert mcp_config(PRIVATE, None, base) is None and claude_args(PRIVATE, {}, [])[0] == "--settings"
    assert "neither keeps it" in search_notice(PRIVATE_SEARCH)
    assert "not zero data retention" in search_notice(Profile("p", True, "private/glm-5-3", "", "", web_search="best"))
    assert search_notice(PRIVATE) == ""


PRIVATE_SEARCH = Profile("private", True, "private/glm-5-3", "private/kimi-k3", "private/deepseek-v4-1-flash",
                         "private/deepseek-v4-1-flash", "low", "zero-retention")
