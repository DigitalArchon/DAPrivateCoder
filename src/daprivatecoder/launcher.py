"""Starting Claude Code on the proxy: `claude-private`, `daprivatecoder claude`, and the editor's
process wrapper (claudeCode.claudeProcessWrapper), which runs this with the extension's own Claude
Code binary as its first argument and Claude Code's arguments after it.

The proxy is a child of this process (`daprivatecoder serve`), and this process then becomes Claude
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
import shlex
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from .config import PROVIDERS, Config, Profile, state_dir
from .llm.client import RANK, tier_of

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


# the profile's effort as Claude Code's effortLevel setting, which takes no "max": its xhigh, which the
# proxy runs as the models' max (none takes xhigh)
START_EFFORT = {"max": "xhigh"}


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
    return name in ("claude-private", "daprivatecoder")


def find_claude(configured: str = "", dirs=None) -> str:
    if configured:
        if not (os.path.isfile(configured) and os.access(configured, os.X_OK)):
            raise LaunchError(f"The Claude Code program set in DA Private Coder's settings ({configured}) isn't there, or "
                              "can't be run.")
        return configured
    on_path = shutil.which("claude")
    if on_path and not _is_ours(os.path.realpath(on_path)):
        return on_path
    bundled = extension_binaries(dirs)
    if bundled:
        return str(bundled[0])
    raise LaunchError("Claude Code isn't installed: there's no `claude` on your PATH, and no Claude Code extension "
                      "in VSCodium or VS Code. Install one, or set `claude` in DA Private Coder's settings to its path.")


def split_wrapper_args(argv: list[str]) -> tuple[str | None, list[str]]:
    """(the Claude Code binary, its arguments). Run as the editor's process wrapper, the binary
    comes first: an absolute path to a program called claude."""
    if argv and os.path.isabs(argv[0]) and os.path.basename(argv[0]).startswith("claude") \
            and os.path.isfile(argv[0]) and os.access(argv[0], os.X_OK) and not _is_ours(argv[0]):
        return argv[0], argv[1:]
    return None, argv


# ---------------------------------------------------------------------- what Claude Code is given

def _describe(profile: Profile, model: str) -> str:
    """What /model says of a model: how its requests travel."""
    tier = tier_of(model, profile.provider == "nanogpt")
    if tier == "private":
        return "Private: sealed on this computer to an attested enclave"
    if tier == "tee":
        return "TEE: an attested enclave, but NanoGPT's gateway reads it (not end-to-end encrypted)"
    return f"{PROVIDERS[profile.provider].capitalize()}, in the clear: not private"


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
        "DAPRIVATECODER_PROFILE": profile.name,
    }
    for tier, model in (("OPUS", profile.opus_model), ("SONNET", profile.model), ("HAIKU", profile.fast_model),
                        ("FABLE", profile.opus_model)):
        env[f"ANTHROPIC_DEFAULT_{tier}_MODEL"] = model
        env[f"ANTHROPIC_DEFAULT_{tier}_MODEL_NAME"] = model
        env[f"ANTHROPIC_DEFAULT_{tier}_MODEL_DESCRIPTION"] = _describe(profile, model)
    # where Claude Code compacts the conversation: for model names it doesn't know (these), it would
    # assume 200K and say so at every start; Claude's own models (a standard profile's anthropic/…) keep theirs
    env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(profile.context)
    if profile.kind != "standard":
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


# the search server's tool, as Claude Code's permission rules name it (mcp_config's server, search.py's tool)
SEARCH_TOOL = "mcp__daprivatecoder__web_search"
# WebFetch's address is checked before Claude Code reads a page, as a search's query is (identity.py)
FETCH_CHECK = [sys.executable, "-I", "-m", "daprivatecoder.identity"]


def session_settings(profile: Profile, env: dict[str, str], given: dict | None = None, asks: bool = True) -> dict:
    """Claude Code's --settings for the session: `given` (any the caller passed), with ours over it.
    `asks`: Claude Code can ask the user (not `claude -p`)."""
    out = dict(given or {})
    out["env"] = {**(out.get("env") or {}), **env}
    out["model"] = profile.model
    # the effort the session starts at (the window's): the editor's Effort and /effort change it in the
    # session, and --effort from the start; the proxy runs the model at whatever Claude Code asks for
    out.setdefault("effortLevel", START_EFFORT.get(profile.reasoning, profile.reasoning))
    # WebFetch tells Anthropic each site it reads (its blocklist check) unless told not to
    out["skipWebFetchPreflight"] = True
    out["disableClaudeAiConnectors"] = True
    permissions = dict(out.get("permissions") or {})
    # Anthropic's own web search runs on its servers, not through NanoGPT
    deny = list(permissions.get("deny") or [])
    permissions["deny"] = deny + [d for d in ("WebSearch",) if d not in deny]
    if profile.web_search != "off" and asks:
        # each search is asked about, showing its query: a search leaves in the clear. An ask rule outranks
        # an allow rule, so "don't ask again" in an earlier session (kept in its settings) doesn't skip it.
        # With no one to ask (claude -p), a search runs only when --allowedTools allows it, as before
        ask = list(permissions.get("ask") or [])
        permissions["ask"] = ask + [a for a in (SEARCH_TOOL,) if a not in ask]
    out["permissions"] = permissions
    # a denial from a hook holds whatever the permission mode, and alongside the hooks given
    hooks = dict(out.get("hooks") or {})
    hooks["PreToolUse"] = list(hooks.get("PreToolUse") or []) + [
        {"matcher": "WebFetch", "hooks": [{"type": "command", "command": shlex.join(FETCH_CHECK), "timeout": 30}]}]
    out["hooks"] = hooks
    return out


def _model_ok(profile: Profile, model: str) -> bool:
    """Whether Claude Code may be given `model` (as --model) in this profile: one no less protected than
    the profile's kind, or one of Claude Code's own aliases, which resolve to the profile's models."""
    return model.strip().lower() in TIER_ALIASES or \
        RANK[tier_of(model, profile.provider == "nanogpt")] >= RANK[profile.kind]


# what DA Private Coder's search server (search.py) needs of the environment Claude Code starts it with:
# the keyring's bus, the settings and log folders, and the CA store the AppImage's AppRun chose
SEARCH_ENV = ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
              "PYTHON_KEYRING_BACKEND", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")
# what the AppImage runtime sets about its own mount: nothing to Claude Code or the programs it runs
# (another AppImage started from its shell would misread them)
APPIMAGE_VARS = ("APPIMAGE", "APPDIR", "ARGV0", "OWD", "APPIMAGE_EXTRACT_AND_RUN")
# AppRun names here the variables it set itself (the CA store), so they aren't passed on either
APPRUN_SET = "DAPRIVATECODER_APPRUN_SET"


def python_argv(*args: str) -> list[str]:
    """This app's Python, running DA Private Coder with `args`: isolated (-I), as the AppImage runs it."""
    return [sys.executable, "-I", "-m", "daprivatecoder", *args]


def mcp_config(profile: Profile, config_path: Path | None, base: dict[str, str],
               editors: list[tuple[str, Path]] | tuple = (), folder: str = "") -> dict | None:
    """The web search server for Claude Code's --mcp-config (None: the profile doesn't search). It is given
    what the session's proxy is (start_proxy): the editor whose switch started the session, if one did, and
    the session's folder, so that before each search it asks what the proxy asks before each request (gate.py)."""
    if profile.web_search == "off":
        return None
    args = python_argv("search-server", "--profile", profile.name)[1:]
    if config_path is not None:
        args += ["--config", str(config_path)]
    for name, path in editors:
        args += ["--editor", name, str(path)]
    if folder:
        args += ["--folder", folder]
    return {"mcpServers": {"daprivatecoder": {"type": "stdio", "command": sys.executable, "args": args,
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
    private or TEE profile, a model given by a name below its kind (the editor passes the one picked in
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
    asks = not any(arg in ("-p", "--print") for arg in out)
    return servers + ["--settings", json.dumps(session_settings(profile, env, given, asks))] + out


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
    """Claude Code's whole environment: the caller's, without what would take it elsewhere or what the
    AppImage set for itself, and ours."""
    drop = set(NEUTRALISED) | set(APPIMAGE_VARS) | set((base.get(APPRUN_SET) or "").split())
    out = {k: v for k, v in base.items() if k not in drop and not k.startswith("DAPRIVATECODER_")}
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


def sessions_dir() -> Path:
    """Where each session's --settings file is while it runs (only this user can read them: they hold
    the session's token, which Claude Code's command line would show every user of this computer)."""
    return state_dir() / "sessions"


def settings_file(pid: int) -> Path:
    """The --settings file of the session whose proxy is `pid` (the proxy removes it as it ends)."""
    return sessions_dir() / f"settings-{pid}.json"


def session_file(pid: int) -> Path:
    """What the window says of the session whose proxy is `pid`: its profile, folder, editor (cli.py)."""
    return sessions_dir() / f"session-{pid}.json"


def _pid_of(path: Path) -> int | None:
    try:
        return int(path.stem.split("-", 1)[1])
    except (IndexError, ValueError):
        return None


def proxy_alive(pid: int | None) -> bool:
    """Whether `pid` is a proxy of DA Private Coder's now (not a process that has its old number)."""
    if not pid:
        return False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    return b"serve" in cmdline and any(b"daprivatecoder" in part for part in cmdline)


def live_sessions() -> list[tuple[int, Path]]:
    """(pid, session file) of each session running now."""
    folder = sessions_dir()
    found = []
    for path in sorted(folder.glob("session-*.json")) if folder.is_dir() else ():
        pid = _pid_of(path)
        if proxy_alive(pid):
            found.append((pid, path))
    return found


def write_settings(args: list[str], pid: int) -> list[str]:
    """`args` with the --settings JSON in a file of its own (settings_file), named in its place. Those
    of sessions that have ended without removing theirs go first."""
    folder = sessions_dir()
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    for old in list(folder.glob("settings-*.json")) + list(folder.glob("session-*.json")):
        if not proxy_alive(_pid_of(old)):
            old.unlink(missing_ok=True)
    out = list(args)
    at = out.index("--settings") + 1
    path = settings_file(pid)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(out[at])
    out[at] = str(path)
    return out


def start_proxy(profile: Profile, sock: socket.socket, token: str, config_path: Path | None,
                editors: list[tuple[str, Path]] = ()) -> subprocess.Popen:
    errors = state_dir() / "proxy-errors.log"
    errors.parent.mkdir(parents=True, exist_ok=True)
    try:
        if errors.stat().st_size > 1 << 20:
            errors.unlink()
    except OSError:
        pass
    command = python_argv("serve", "--profile", profile.name, "--fd", str(sock.fileno()), "--parent", str(os.getpid()))
    if config_path is not None:
        command += ["--config", str(config_path)]
    for name, path in editors:
        command += ["--editor", name, str(path)]
    with open(errors, "ab") as log:
        return subprocess.Popen(command, pass_fds=(sock.fileno(),), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                start_new_session=True, env={**os.environ, "DAPRIVATECODER_TOKEN": token})


def usual_env(base: dict[str, str]) -> dict[str, str]:
    """The environment for Claude Code as usual: the caller's, without what the AppImage set for itself."""
    drop = set(APPIMAGE_VARS) | set((base.get(APPRUN_SET) or "").split())
    return {k: v for k, v in base.items() if k not in drop and not k.startswith("DAPRIVATECODER_")}


def run_for_folder(argv: list[str]):
    """claude-editor, the editor's switch: its Claude Code (first in `argv`) as the folder it starts in
    says (folders.py), on that profile, or as usual: then exactly as the editor started it, nothing of
    DA Private Coder's read or needed (its settings, whether it's open). Returns only by raising."""
    from . import folders
    from .config import load
    wrapped, args = split_wrapper_args(argv)
    if not wrapped:
        raise LaunchError("`daprivatecoder claude --folders` is what the editor's switch runs: its Claude Code first, "
                          "then that Claude Code's arguments.")
    try:
        profile_name = folders.choice(folders.load(), os.getcwd())[0]
    except folders.FoldersError as e:
        raise LaunchError(str(e)) from None
    if not profile_name:
        os.execve(wrapped, [wrapped, *args], usual_env(dict(os.environ)))
    run(load(), profile_name, argv)


def run(cfg: Config, profile_name: str, argv: list[str]):
    """Become Claude Code, on a proxy for `profile_name`. Returns only by raising."""
    from . import editor, gate
    profile = cfg.profile(profile_name)
    wrapped, args = split_wrapper_args(argv)
    # the editor's process wrapper: the editor whose switch this is, so the session stops when it's off
    editors = editor.started_by(wrapped, profile.name) if wrapped else []
    quick = bool(args) and set(args) <= QUICK_ARGS
    if not quick and not wrapped and not gate.window_open():
        # the editor's Claude Code starts all the same: its proxy says why in the conversation
        raise LaunchError("DA Private Coder isn't open, so Claude Code wasn't started on it. Open DA Private Coder "
                          "first (its window, or `daprivatecoder`), then run this again.")
    binary = wrapped or find_claude(cfg.claude)
    if quick:
        os.execv(binary, [binary, *args])
    token = secrets.token_urlsafe(32)
    sock = listen()
    env = claude_env(profile, sock.getsockname()[1], token)
    clash = managed_conflicts(env)
    if clash:
        raise LaunchError(f"Your managed Claude Code settings ({MANAGED_SETTINGS}) set {', '.join(clash)}, which would "
                          "override DA Private Coder's, so Claude Code wasn't started.")
    full_args = claude_args(profile, env, args, mcp_config(profile, cfg.path, dict(os.environ), editors, os.getcwd()))
    proxy = start_proxy(profile, sock, token, cfg.path, editors)
    sock.close()                                  # the proxy has it now
    full_args = write_settings(full_args, proxy.pid)
    notice = search_notice(profile)
    if notice and sys.stderr.isatty():
        print(f"daprivatecoder: {notice}", file=sys.stderr)
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
