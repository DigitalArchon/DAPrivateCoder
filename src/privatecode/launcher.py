"""Starting Claude Code on the proxy: `claude-private`, `privatecode claude`, and the editor's
process wrapper (claudeCode.claudeProcessWrapper), which runs this with the extension's own Claude
Code binary as its first argument and Claude Code's arguments after it.

The proxy is a child of this process (`privatecode serve`), and this process then becomes Claude
Code (exec), so the proxy lives exactly as long as Claude Code does: the kernel tells it when its
parent dies (PR_SET_PDEATHSIG), and it checks besides. Its listening socket is made here and
handed to it, so Claude Code starts at once: a connection waits in the socket's queue until the
proxy is ready. The proxy has a session of its own, so Ctrl+C in Claude Code doesn't stop it.

Claude Code is pointed at the proxy with environment variables, and with the same ones in
--settings: a settings file's env (~/.claude/settings.json, the project's) overrides the environment
Claude Code was started with, but --settings overrides every settings file except the managed
ones. If those set any of these variables, nothing starts.

This module is kept light (no HTTP or crypto libraries): the editor starts it for every session.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from .config import Config, Profile, state_dir
from .llm.client import is_private_mode

# Claude Code's own model names that resolve through the tier variables set below
TIER_ALIASES = frozenset({"opus", "sonnet", "haiku", "fable", "opusplan", "default", "inherit"})
# arguments that only print something: Claude Code needs no proxy for them
QUICK_ARGS = frozenset({"--version", "-v", "--help", "-h"})
# where Claude Code's editor extensions keep their own binary (newest version first)
EXTENSION_DIRS = ("~/.vscode-oss/extensions", "~/.vscode/extensions",
                  "~/.var/app/com.vscodium.codium/data/vscode/extensions")
EXTENSION_BINARIES = ("resources/native-binary/claude", "resources/native-binaries/*/claude")
MANAGED_SETTINGS = Path("/etc/claude-code/managed-settings.json")
# ways Claude Code could reach a model other than through the proxy, each neutralised: a settings
# file can't unset a variable, but an empty value is no value
NEUTRALISED = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
# what Claude Code would otherwise send to Anthropic and others beside the model requests
QUIET = {
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",    # updates, telemetry, error reports, release notes…
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "DISABLE_AUTOUPDATER": "1",
    "DISABLE_FEEDBACK_COMMAND": "1",                    # /feedback, /bug and /share send the conversation
    "CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY": "1",
    "ENABLE_CLAUDEAI_MCP_SERVERS": "false",             # claude.ai's connectors run through Anthropic
    "CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL": "1",
    # the billing block Claude Code puts first in its system prompt for Anthropic: nothing to another model
    "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
}


class LaunchError(Exception):
    """Claude Code can't be started on the proxy; the message says why and what to do."""


# ---------------------------------------------------------------------- finding Claude Code

def _version_key(path: Path) -> tuple:
    """anthropic.claude-code-2.1.296-linux-x64/... -> (2, 1, 296)"""
    m = re.search(r"claude-code-(\d+(?:\.\d+)*)", str(path))
    return tuple(int(p) for p in m.group(1).split(".")) if m else ()


def extension_binaries(dirs=None) -> list[Path]:
    """Claude Code binaries bundled with editor extensions, the newest version first."""
    found = []
    for d in EXTENSION_DIRS if dirs is None else dirs:
        root = Path(os.path.expanduser(d))
        for pattern in EXTENSION_BINARIES:
            found += [p for p in root.glob(f"anthropic.claude-code-*/{pattern}") if os.access(p, os.X_OK)]
    return sorted(set(found), key=_version_key, reverse=True)


def _is_ours(path: str) -> bool:
    name = os.path.basename(path)
    return name in ("claude-private", "privatecode")


def find_claude(configured: str = "", dirs=None) -> str:
    if configured:
        if not (os.path.isfile(configured) and os.access(configured, os.X_OK)):
            raise LaunchError(f"The Claude Code program set in PrivateCode's settings ({configured}) isn't there, or "
                              "can't be run.")
        return configured
    on_path = shutil.which("claude")
    if on_path and not _is_ours(os.path.realpath(on_path)):
        return on_path
    bundled = extension_binaries(dirs)
    if bundled:
        return str(bundled[0])
    raise LaunchError("Claude Code isn't installed: there's no `claude` on your PATH, and no Claude Code extension "
                      "in VSCodium or VS Code. Install one, or set `claude` in PrivateCode's settings to its path.")


def split_wrapper_args(argv: list[str]) -> tuple[str | None, list[str]]:
    """(the Claude Code binary, its arguments). Run as the editor's process wrapper, the binary
    comes first: an absolute path to a program called claude."""
    if argv and os.path.isabs(argv[0]) and os.path.basename(argv[0]).startswith("claude") \
            and os.path.isfile(argv[0]) and os.access(argv[0], os.X_OK) and not _is_ours(argv[0]):
        return argv[0], argv[1:]
    return None, argv


# ---------------------------------------------------------------------- what Claude Code is given

def _describe(profile: Profile, model: str) -> str:
    if is_private_mode(model):
        return "Private: sealed on this computer to an attested enclave"
    return "NanoGPT, not private"


def claude_env(profile: Profile, port: int, token: str) -> dict[str, str]:
    """The variables that point Claude Code at the proxy, with the profile's models."""
    env = {
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
        "ANTHROPIC_AUTH_TOKEN": token,
        **{k: "" for k in NEUTRALISED},
        "ANTHROPIC_MODEL": profile.model,
        "API_TIMEOUT_MS": "600000",
        # /model lists what the proxy offers (for a private profile, every private model)
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
        **QUIET,
        "PRIVATECODE_PROFILE": profile.name,
    }
    for tier, model in (("OPUS", profile.opus_model), ("SONNET", profile.model), ("HAIKU", profile.fast_model),
                        ("FABLE", profile.opus_model)):
        env[f"ANTHROPIC_DEFAULT_{tier}_MODEL"] = model
        env[f"ANTHROPIC_DEFAULT_{tier}_MODEL_NAME"] = model
        env[f"ANTHROPIC_DEFAULT_{tier}_MODEL_DESCRIPTION"] = _describe(profile, model)
    if profile.private:
        # no pre-release features (Anthropic's server-side context editing, tool search): nothing here runs them
        env["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] = "1"
    return env


def _settings_value(value: str) -> dict:
    """A --settings value: JSON, or the path of a JSON file."""
    text = value
    if not value.lstrip().startswith("{"):
        try:
            text = Path(value).read_text()
        except OSError as e:
            raise LaunchError(f"The --settings file {value} couldn't be read: {e}") from None
    try:
        data = json.loads(text)
    except ValueError as e:
        raise LaunchError(f"The --settings given to Claude Code isn't valid JSON: {e}") from None
    if not isinstance(data, dict):
        raise LaunchError("The --settings given to Claude Code must be a JSON object.")
    return data


def session_settings(profile: Profile, env: dict[str, str], given: dict | None = None) -> dict:
    """Claude Code's --settings for the session: `given` (any the caller passed), with ours over it."""
    out = dict(given or {})
    out["env"] = {**(out.get("env") or {}), **env}
    out["model"] = profile.model
    # WebFetch tells Anthropic each site it reads (its blocklist check) unless told not to
    out["skipWebFetchPreflight"] = True
    out["disableClaudeAiConnectors"] = True
    permissions = dict(out.get("permissions") or {})
    # Anthropic's own web search runs on its servers, not through NanoGPT
    deny = list(permissions.get("deny") or [])
    permissions["deny"] = deny + [d for d in ("WebSearch",) if d not in deny]
    out["permissions"] = permissions
    return out


def _model_ok(profile: Profile, model: str) -> bool:
    return not profile.private or is_private_mode(model) or model.strip().lower() in TIER_ALIASES


# what PrivateCode's search server (search.py) needs of the environment Claude Code starts it with:
# the keyring's bus, and the settings and log folders
SEARCH_ENV = ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
              "PYTHON_KEYRING_BACKEND")


def mcp_config(profile: Profile, config_path: Path | None, base: dict[str, str]) -> dict | None:
    """The web search server for Claude Code's --mcp-config (None: the profile doesn't search)."""
    if profile.web_search == "off":
        return None
    args = ["-m", "privatecode", "search-server", "--profile", profile.name]
    if config_path is not None:
        args += ["--config", str(config_path)]
    return {"mcpServers": {"privatecode": {"type": "stdio", "command": sys.executable, "args": args,
                                           "env": {k: base[k] for k in SEARCH_ENV if base.get(k)}}}}


def search_notice(profile: Profile) -> str:
    """What a session's searches give away, said when it starts in a terminal."""
    if profile.web_search == "zero-retention":
        return ("Web search: Linkup, through NanoGPT. Searches aren't end-to-end encrypted: NanoGPT and Linkup read "
                "each one, and neither keeps it. Claude Code asks before each search.")
    if profile.web_search == "best":
        return ("Web search: Perplexity and Kagi, through NanoGPT. Searches aren't private: NanoGPT and the provider "
                "read each one, and what the provider keeps is up to its own policy (not zero data retention). "
                "Claude Code asks before each search.")
    return ""


def claude_args(profile: Profile, env: dict[str, str], args: list[str], mcp: dict | None = None) -> list[str]:
    """Claude Code's arguments for the session: one --settings, ours merged into any given; and in a
    private profile, a model given by a name that isn't private (the editor passes the one picked in
    it) is the profile's, so Claude Code knows what it is talking to. The search server, `mcp`, comes
    first: --mcp-config takes every value up to the next option (--settings), and the editor's own
    --mcp-config adds to it."""
    out: list[str] = []
    given: dict | None = None
    i = 0
    while i < len(args):
        arg = args[i]
        name, eq, inline = arg.partition("=")
        if name in ("--settings", "--model", "--fallback-model") and (eq or i + 1 < len(args)):
            value = inline if eq else args[i + 1]
            i += 1 if eq else 2
            if name == "--settings":
                given = _settings_value(value)
            else:
                out += [name, value if _model_ok(profile, value) else profile.model]
            continue
        out.append(arg)
        i += 1
    servers = ["--mcp-config", json.dumps(mcp)] if mcp else []
    return servers + ["--settings", json.dumps(session_settings(profile, env, given))] + out


def managed_conflicts(env: dict[str, str], path: Path = MANAGED_SETTINGS) -> list[str]:
    """The variables of ours the managed settings set: they outrank --settings."""
    drop_ins = path.with_suffix(".d")
    paths = [path] + (sorted(drop_ins.glob("*.json")) if drop_ins.is_dir() else [])
    clash: list[str] = []
    for p in paths:
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        managed_env = data.get("env") if isinstance(data, dict) else None
        if isinstance(managed_env, dict):
            clash += [k for k in managed_env if k in env and k not in clash]
    return clash


def child_env(base: dict[str, str], env: dict[str, str]) -> dict[str, str]:
    """Claude Code's whole environment: the caller's, without what would take it elsewhere, and ours."""
    out = {k: v for k, v in base.items() if k not in NEUTRALISED and not k.startswith("PRIVATECODE_")}
    out.update(env)
    # an HTTP(S)_PROXY of the user's must not carry Claude Code's requests to this computer
    for key in ("NO_PROXY", "no_proxy"):
        have = [h for h in (out.get(key) or "").split(",") if h.strip()]
        out[key] = ",".join(have + [h for h in ("127.0.0.1", "localhost") if h not in have])
    return out


# ---------------------------------------------------------------------- starting it

def listen() -> socket.socket:
    """The proxy's listening socket, on 127.0.0.1 at a free port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    return sock


def start_proxy(profile: Profile, sock: socket.socket, token: str, config_path: Path | None) -> subprocess.Popen:
    errors = state_dir() / "proxy-errors.log"
    errors.parent.mkdir(parents=True, exist_ok=True)
    try:
        if errors.stat().st_size > 1 << 20:
            errors.unlink()
    except OSError:
        pass
    command = [sys.executable, "-m", "privatecode", "serve", "--profile", profile.name, "--fd", str(sock.fileno()),
               "--parent", str(os.getpid())]
    if config_path is not None:
        command += ["--config", str(config_path)]
    with open(errors, "ab") as log:
        return subprocess.Popen(command, pass_fds=(sock.fileno(),), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                start_new_session=True, env={**os.environ, "PRIVATECODE_TOKEN": token})


def run(cfg: Config, profile_name: str, argv: list[str]):
    """Become Claude Code, on a proxy for `profile_name`. Returns only by raising."""
    profile = cfg.profile(profile_name)
    binary, args = split_wrapper_args(argv)
    binary = binary or find_claude(cfg.claude)
    if args and set(args) <= QUICK_ARGS:
        os.execv(binary, [binary, *args])
    token = secrets.token_urlsafe(32)
    sock = listen()
    env = claude_env(profile, sock.getsockname()[1], token)
    clash = managed_conflicts(env)
    if clash:
        raise LaunchError(f"Your managed Claude Code settings ({MANAGED_SETTINGS}) set {', '.join(clash)}, which would "
                          "override PrivateCode's, so Claude Code wasn't started.")
    full_args = claude_args(profile, env, args, mcp_config(profile, cfg.path, dict(os.environ)))
    start_proxy(profile, sock, token, cfg.path)
    sock.close()                                  # the proxy has it now
    notice = search_notice(profile)
    if notice and sys.stderr.isatty():
        print(f"privatecode: {notice}", file=sys.stderr)
    os.execve(binary, [binary, *full_args], child_env(dict(os.environ), env))


def die_with_parent(parent: int) -> None:
    """The proxy's end: SIGTERM when the process that started it (Claude Code, by then) exits."""
    import ctypes
    import ctypes.util
    import signal
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or None, use_errno=True)
        libc.prctl(1, signal.SIGTERM, 0, 0, 0)     # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass                                       # the proxy checks for its parent too
    if os.getppid() != parent:
        sys.exit(0)                                # gone already
