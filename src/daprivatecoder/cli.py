"""The commands.

  daprivatecoder                                    the window (--browser: in the web browser)
  claude-private [Claude Code's arguments]          Claude Code on NanoGPT's private models
  daprivatecoder claude-private [...]               the same (what the claude-private command runs)
  daprivatecoder claude [--profile NAME] [...]      the same on any profile (tee, standard: not end-to-end encrypted)
  daprivatecoder editor on|off|status [--profile]   the switch for Claude Code in VSCodium and VS Code
  daprivatecoder key set|clear|status [--provider]  an API key (NanoGPT's, OpenRouter's…), in your keyring
  daprivatecoder status                             what is set up here
  daprivatecoder check [--profile NAME]             attest the enclave(s), list the profile's models and prices
  daprivatecoder log [-n N]                         what each session asked of which model, and the cost
  daprivatecoder serve ...                          the proxy itself (claude-private starts it)
  daprivatecoder search-server ...                  the web search tool (Claude Code starts it)
"""

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys
from pathlib import Path

from . import __version__, config
from .config import ConfigError, state_dir
from .netpolicy import shown

PRIVATE = "private"
# the window's own options (app.py): with none of the commands, they open it
WINDOW_OPTIONS = ("--browser", "--window", "--no-open", "--port")


class CliError(Exception):
    pass


def _fail(prog: str, e: Exception) -> int:
    from .netpolicy import addresses_shown
    print(f"{prog}: {addresses_shown(str(e))}", file=sys.stderr)      # no address with its password in it
    return 1


# ---------------------------------------------------------------------- starting Claude Code

def _launch(prog: str, profile: str, args: list[str]) -> int:
    from . import launcher
    try:
        launcher.run(config.load(), profile, args)       # becomes Claude Code: returns only by raising
    except (ConfigError, launcher.LaunchError, OSError) as e:
        return _fail(prog, e)
    return 1


def claude_private() -> None:
    """claude-private: every argument is Claude Code's."""
    sys.exit(_launch("claude-private", PRIVATE, sys.argv[1:]))


def _claude(argv: list[str]) -> int:
    if argv[:1] == ["--folders"]:                    # the editor's switch (claude-editor)
        from . import launcher
        try:
            launcher.run_for_folder(argv[1:])
        except (ConfigError, launcher.LaunchError, OSError) as e:
            return _fail("daprivatecoder", e)
        return 1
    profile = PRIVATE
    if argv[:1] == ["--profile"] and len(argv) > 1:
        profile, argv = argv[1], argv[2:]
    elif argv and argv[0].startswith("--profile="):
        profile, argv = argv[0].split("=", 1)[1], argv[1:]
    return _launch("daprivatecoder", profile, argv)


# ---------------------------------------------------------------------- the key

def _key(args) -> int:
    from . import creds
    provider, base_url = args.provider, ""
    name = config.PROVIDERS[provider]
    if provider == "custom":
        cfg = config.load()
        base_url = cfg.provider_url(cfg.profile("standard"))
        if not base_url:
            raise CliError("The standard profile's provider isn't a custom one: set provider and base_url first.")
        name = f"the provider at {shown(base_url)}"
    flag = "" if provider == "nanogpt" else f" --provider {provider}"
    if args.action == "status":
        key = creds.get_key(provider, base_url)
        print(f"A key for {name} is stored in your keyring (ending {key[-4:]})." if key
              else f"No key for {name} is stored. Store one with: daprivatecoder key set{flag}")
        return 0
    if args.action == "clear":
        creds.delete_key(provider, base_url)
        print(f"The key for {name} was removed from your keyring.")
        return 0
    if args.from_vibe_manager:
        if provider != "nanogpt":
            raise CliError("DA Vibe Manager's key is NanoGPT's.")
        key = creds.vibe_manager_key() or ""
        if not key:
            raise CliError("DA Vibe Manager has no NanoGPT key in your keyring.")
    elif sys.stdin.isatty():
        key = getpass.getpass(f"API key for {name} (it isn't shown as you type): ")
    else:
        key = sys.stdin.readline()
    from .netpolicy import key_problem
    key = key.strip()
    if not key:
        raise CliError("That isn't an API key: nothing was stored.")
    if key_problem(key):
        raise CliError("That isn't an API key (it has a space, a line break or another character no API key has, "
                       "picked up where it was copied from, perhaps): nothing was stored.")
    creds.set_key(key, provider, base_url)
    print(f"The key for {name} is stored in your keyring.")
    return 0


# ---------------------------------------------------------------------- the editor switch

def _wrapper_state(value) -> str:
    from . import editor
    named = editor.profile_of(value)
    if named == editor.PER_FOLDER:
        return "on (DA Private Coder: each folder as chosen, the rest as usual or as `other` says)"
    if named:
        return f"on (every session on DA Private Coder's {named} profile: claude-private set by hand)"
    return "off" if not value else f"another wrapper: {value}"


def _kind_words(profile: str) -> str:
    return {"private": "NanoGPT's private models", "tee": "NanoGPT's TEE models (attested, NOT end-to-end encrypted)",
            "": "Claude as usual, on Anthropic's models"}.get(profile, f"the {profile} profile's models (NOT private: in the clear)")


def _folders_said() -> str:
    from . import folders
    f = folders.load()
    lines = [f"  {folder}: {_kind_words(profile)}" for folder, profile in sorted(f.folders.items())]
    lines.append(f"  every other folder: {_kind_words(f.other)}")
    return "\n".join(lines)


def _editor(args) -> int:
    from . import editor, folders
    if args.action == "folder":
        if not args.which or not args.profile:
            raise CliError("Say the folder and how it runs: daprivatecoder editor folder PATH private|tee|standard|usual")
        profile = "" if args.profile == "usual" else args.profile
        if profile:
            config.load().profile(profile)               # said now if there's no such profile
        if not os.path.isdir(os.path.expanduser(args.which)):
            raise CliError(f"{args.which} isn't a folder.")
        folders.save(folders.with_folder(folders.load(), args.which, profile))
        print(f"Claude Code in {folders.norm(args.which)} runs on {_kind_words(profile)}, in a new conversation, "
              "wherever the editor's switch is on.\n" + _folders_said())
        return 0
    found = [(name, path) for name, path in editor.editors()
             if not args.which or args.which.lower() in name.lower().replace(" ", "")]
    if not found:
        raise CliError("No VSCodium or VS Code settings were found here." if not args.which
                       else f"No settings of {args.which} were found here.")
    if args.action == "status":
        for name, path in found:
            print(f"{name}: {_wrapper_state(editor.current(path))}")
        print("Folders:\n" + _folders_said())
        return 0
    if args.action == "on" and args.other:
        other = "" if args.other == "usual" else args.other
        if other:
            config.load().profile(other)                 # said now if there's no such profile
        f = folders.load()
        folders.save(folders.Folders(f.folders, other))
    wrapper = editor.wrapper_path() if args.action == "on" else None
    for name, path in found:
        value = editor.current(path)
        if editor.owner(value) == "other":
            print(f"{name}: left as it is: its Claude Code already runs through another program ({value}).")
            continue
        changed = editor.switch(path, wrapper)
        print(f"{name}: {'changed' if changed else 'already so'} ({path})")
    if wrapper:
        print("Claude Code in the editor now runs as each folder says (in a new conversation):\n" + _folders_said()
              + "\nTo choose a folder: daprivatecoder editor folder PATH private|tee|standard|usual"
              "\nTo switch back: daprivatecoder editor off")
    else:
        print("Claude Code in the editor runs as usual, on Anthropic's models, in every folder. Conversations open on "
              "DA Private Coder stop at their next request: start a new one.")
    return 0


# ---------------------------------------------------------------------- looking

def _status(args) -> int:
    from . import creds, editor, launcher
    cfg = config.load()
    print(f"DA Private Coder {__version__}\nSettings: {cfg.path}\nNanoGPT: {shown(cfg.base_url)}")
    searches = {"zero-retention": "Linkup (keeps nothing)", "best": "Perplexity and Kagi (not zero retention)",
                "off": "off"}
    for p in cfg.profiles.values():
        kind = {"private": "private", "tee": "TEE, not end-to-end encrypted"}.get(
            p.kind, f"standard, not private, on {config.PROVIDERS[p.provider]}")
        print(f"  [{p.name}] {kind}: {p.model} (opus {p.opus_model}, quick {p.fast_model}); "
              f"vision {p.vision_model or 'none'}; reasoning {p.reasoning}; web search {searches[p.web_search]}")
    try:
        print(f"Claude Code: {launcher.find_claude(cfg.claude)}")
    except launcher.LaunchError as e:
        print(f"Claude Code: {e}")
    try:
        key = creds.get_key()
        print("NanoGPT key: stored in your keyring" if key else "NanoGPT key: none (daprivatecoder key set)")
    except creds.KeyringUnavailable as e:
        print(f"NanoGPT key: {e}")
    for name, path in editor.editors():
        value = editor.current(path)
        print(f"Editor, {name}: {_wrapper_state(value)}")
    print(f"Request log: {state_dir() / 'requests.log'}")
    return 0


def _check(args) -> int:
    import asyncio

    from .llm import capabilities
    from .llm.client import is_tee
    from .llm.private_mode import Enclave, list_private_models, offered, offers_private_mode, relay_url
    cfg = config.load()
    profile = cfg.profile(args.profile)
    if profile.kind != "standard" and not offers_private_mode(cfg.base_url):
        raise CliError(f"{shown(cfg.base_url)} isn't NanoGPT, so it has no Private Mode or TEE models.")

    def line(m: str, c: dict, markup: float = 1.0) -> str:
        price = c.get("pricing")
        cost = (" ${:.2f} / ${:.2f} / ${:.2f}".format(*(price[k] * markup for k in ("input", "cache_read", "output")))
                if price else "")
        ctx = f" {c['context'] // 1000}k context" if c.get("context") else ""
        return f"  {m}{cost}{ctx}{', sees images' if c.get('vision') else ''}{', no tool calls' if c and not c.get('tools') else ''}"

    async def private() -> int:
        ids = await list_private_models(cfg.base_url)
        try:
            caps = await capabilities.fetch_nanogpt(cfg.base_url)
        except Exception:  # noqa: BLE001 - shown without what it can do
            caps = {}
        print("NanoGPT Private Mode models now (prices per million tokens, input / cached input / output):")
        for m in ids:
            print(line(m, capabilities.lookup(caps, m) or {}, capabilities.PRIVATE_MARKUP))
        bad = [m for m in (*profile.models, profile.vision_model) if m and offered(m, ids) is False]
        for m in bad:
            print(f"Profile [{profile.name}] uses {m}, which NanoGPT no longer offers: change it in {cfg.path}")
        print("Attesting the enclave...")
        att = (await Enclave(relay_url(cfg.base_url)).current()).attestation
        print(f"Verified: {att.summary}\n  key fingerprint (sha256) {att.hpke_key_sha256}")
        return 1 if bad else 0

    async def tee() -> int:
        from . import creds
        from .proxy import attest_tee
        caps = await capabilities.fetch_nanogpt(cfg.base_url)
        print("NanoGPT TEE models now (prices per million tokens, input / cached input / output). Not end-to-end "
              "encrypted: NanoGPT's gateway reads what is sent to them.")
        for m in sorted(m for m in caps if is_tee(m)):
            print(line(m, caps[m]))
        key = creds.get_key()
        if not key:
            raise CliError("NanoGPT shows a TEE model's attestation only with your API key: daprivatecoder key set")
        failed = 0
        for m in dict.fromkeys(x for x in (*profile.models, profile.vision_model) if is_tee(x)):
            print(f"Attesting {m}...")
            try:
                att = await asyncio.to_thread(attest_tee, cfg.base_url, key, m)
            except Exception as e:  # noqa: BLE001 - said as it is
                print(f"  NOT verified: {e}")
                failed += 1
                continue
            print(f"  {'Verified' if att.get('level') == 'full' else 'Verified in part'}: {att.get('summary')}")
        return 1 if failed else 0

    async def standard() -> int:
        url = cfg.provider_url(profile)
        caps = await capabilities.fetch(profile.provider, url)
        if caps is None:
            print(f"{shown(url)} lists nothing of what its models can do or cost.")
            return 0
        print(f"{config.PROVIDERS[profile.provider]}: the profile's models (prices per million tokens, input / cached "
              "input / output). Not private: they're sent in the clear.")
        missing = [m for m in (*profile.models, profile.vision_model) if m and capabilities.lookup(caps, m) is None]
        for m in dict.fromkeys(x for x in (*profile.models, profile.vision_model) if x):
            print(line(m, capabilities.lookup(caps, m) or {}) + ("  (not offered now)" if m in missing else ""))
        return 1 if missing else 0
    return asyncio.run({"private": private, "tee": tee}.get(profile.kind, standard)())


def _log(args) -> int:
    from . import usagelog
    path = state_dir() / "requests.log"
    records = [r for r in usagelog.read(path) if r.get("kind") in ("request", "vision", "search")]
    if not records:
        print(f"Nothing logged yet ({path}).")
        return 0
    shown = records[-args.n:]
    total = 0.0
    for r in shown:
        if r.get("kind") == "search":
            cost = r.get("cost")
            total += cost or 0
            what = f"web search, {r.get('provider', '')}" if r.get("provider") else "web search"
            extra = f" {r['error']}" if r.get("error") else f" {r['reason']}" if r.get("reason") else ""
            how = "unsent" if r.get("status") == "refused" else "clear "
            print(f"{r.get('t', '')[:19]} {r.get('session', '')} {how} {str(r.get('status', '')):>3} {what:<30} "
                  f"{str(r.get('results', '')) + ' results' if 'results' in r else '':<36} "
                  f"{f'${cost:.4f}' if cost is not None else '':>9} {r.get('seconds', '')}s{extra}")
            continue
        u = r.get("usage") or {}
        cost = u.get("cost")
        total += cost or 0
        tokens = f"{u.get('input', 0) + u.get('cache_read', 0):>7} in ({u.get('cache_read', 0)} cached) {u.get('output', 0):>6} out" \
            if u else " " * 20
        price = (f"${cost:.4f}{'~' if u.get('cost_estimated') else ''}" if cost is not None else "")
        # by its tier, as the window says it (manager._sent): a request refused here never left this computer
        from .manager import _sent
        how = {"sealed": "sealed", "tee": "TEE   ", "clear": "clear "}.get(_sent(r), "unsent")
        what = r.get("model", "") + (" (vision)" if r.get("kind") == "vision" else "")
        extra = f" {r['error']}" if r.get("error") else ""
        print(f"{r.get('t', '')[:19]} {r.get('session', '')} {how} {r.get('status', ''):>3} {what:<30} {tokens} "
              f"{price:>9} {r.get('seconds', '')}s{extra}")
    print(f"{len(shown)} requests, ${total:.4f} (~: estimated from NanoGPT's listed prices)")
    return 0


def _search_server(args) -> int:
    import asyncio

    from . import search
    from .usagelog import UsageLog
    cfg = config.load(Path(args.config) if args.config else None)
    profile = cfg.profile(args.profile)
    log = UsageLog(state_dir() / "requests.log", secrets.token_hex(4))
    server = search.SearchServer(profile.web_search, cfg.base_url, log=log)
    asyncio.run(search.serve_stdio(server))
    return 0


# ---------------------------------------------------------------------- the proxy

def _serve(args) -> int:
    import asyncio
    import json
    import socket
    import time

    from . import gate, launcher, proxy
    from .usagelog import UsageLog
    if args.parent:
        launcher.die_with_parent(args.parent)
    cfg = config.load(Path(args.config) if args.config else None)
    profile = cfg.profile(args.profile)
    token = os.environ.pop("DAPRIVATECODER_TOKEN", "")
    if args.fd is not None:
        if not token:
            raise CliError("serve --fd needs its token in DAPRIVATECODER_TOKEN.")
        sock = socket.socket(fileno=args.fd)
    else:
        sock = proxy.listen(args.port)
        token = token or secrets.token_urlsafe(32)
    sock.setblocking(False)
    log = UsageLog(state_dir() / "requests.log", secrets.token_hex(4))
    editors = [(name, Path(path)) for name, path in args.editor]
    log.write("session", profile=profile.name, profile_kind=profile.kind, private=profile.private, models=list(profile.models),
              vision=profile.vision_model, provider=profile.provider, base_url=shown(cfg.provider_url(profile)), pid=os.getpid(),
              folder=os.getcwd(), editor=", ".join(name for name, _ in editors))
    # only while DA Private Coder is open, and the editor that started it (if one did) still runs its profile
    p = proxy.Proxy(profile, cfg.base_url, token, log=log, provider_url=cfg.provider_url(profile),
                    gate=gate.Gate(profile.name, editors).why)

    def ready(port: int) -> None:
        if args.fd is None:
            print(f"DA Private Coder's proxy for [{profile.name}] is on http://127.0.0.1:{port}\n"
                  f"  ANTHROPIC_BASE_URL=http://127.0.0.1:{port}\n  ANTHROPIC_AUTH_TOKEN={token}", flush=True)
    # the session's --settings file goes with it (its token is of no use to anyone then): uvicorn hands a
    # SIGTERM back to the handler it found once it has shut down, which is this
    import signal
    settings, about = launcher.settings_file(os.getpid()), launcher.session_file(os.getpid())
    if args.fd is not None:
        # what the window's Sessions says of it: what it runs, where, started by what
        about.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(about, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"session": log.session, "profile": profile.name, "kind": profile.kind, "folder": os.getcwd(),
                       "editors": [[name, str(path)] for name, path in editors], "started": time.time()}, f)

    def gone() -> None:
        settings.unlink(missing_ok=True)
        about.unlink(missing_ok=True)

    def ended(signum, _frame) -> None:
        gone()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
    signal.signal(signal.SIGTERM, ended)
    try:
        asyncio.run(proxy.serve(p, sock, ready=ready, parent=args.parent))
    finally:
        gone()
    return 0


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="daprivatecoder", description="Claude Code on NanoGPT's end-to-end encrypted "
                                 "private models. Start it with: claude-private")
    ap.add_argument("--version", action="version", version=f"DA Private Coder {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("claude", help="start Claude Code on a profile: daprivatecoder claude [--profile NAME] [its arguments]")
    k = sub.add_parser("key", help="an API key, in your keyring: NanoGPT's, or a standard profile's provider's")
    k.add_argument("action", choices=("set", "clear", "status"))
    k.add_argument("--provider", choices=tuple(config.PROVIDERS), default="nanogpt",
                   help="whose key (default nanogpt; custom: the standard profile's own endpoint)")
    k.add_argument("--from-vibe-manager", action="store_true", help="set: copy the key DA Vibe Manager stored")
    k.set_defaults(func=_key)
    e = sub.add_parser("editor", help="the switch for Claude Code in VSCodium and VS Code")
    e.add_argument("action", choices=("on", "off", "status", "folder"))
    e.add_argument("which", nargs="?", help="on, off, status: only this editor (vscodium or code); folder: the folder")
    e.add_argument("profile", nargs="?", help="folder: how it runs (private; tee, standard: not end-to-end encrypted; "
                   "usual: Claude as usual)")
    e.add_argument("--profile", dest="other", help="on: how every folder not chosen runs (private, tee, standard, "
                   "or usual: the default)")
    e.set_defaults(func=_editor)
    sub.add_parser("status", help="what is set up here").set_defaults(func=_status)
    c = sub.add_parser("check", help="attest the profile's enclaves, and list its models and their prices")
    c.add_argument("--profile", default=PRIVATE)
    c.set_defaults(func=_check)
    lg = sub.add_parser("log", help="what each session asked of which model, and what it cost")
    lg.add_argument("-n", type=int, default=30, help="how many requests (default 30)")
    lg.set_defaults(func=_log)
    s = sub.add_parser("serve", help="the proxy itself (claude-private starts one for each session)")
    s.add_argument("--profile", default=PRIVATE)
    s.add_argument("--port", type=int, default=0, help="on its own: the port on 127.0.0.1 (default: any free one)")
    s.add_argument("--fd", type=int, help=argparse.SUPPRESS)
    s.add_argument("--parent", type=int, help=argparse.SUPPRESS)
    s.add_argument("--config", help=argparse.SUPPRESS)
    # the editor whose switch started the session (its name and settings file): it stops once that's off
    s.add_argument("--editor", nargs=2, action="append", default=[], help=argparse.SUPPRESS)
    s.set_defaults(func=_serve)
    w = sub.add_parser("search-server", help="the web search tool for Claude Code (claude-private adds it)")
    w.add_argument("--profile", default=PRIVATE)
    w.add_argument("--config", help=argparse.SUPPRESS)
    w.set_defaults(func=_search_server)
    return ap


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in WINDOW_OPTIONS:
        from . import app
        app.main(argv)
        return 0
    if argv[:1] == ["claude-private"]:
        return _launch("claude-private", PRIVATE, argv[1:])
    if argv[:1] == ["claude"]:
        return _claude(argv[1:])
    args = _parser().parse_args(argv)
    from . import creds, editor
    try:
        return args.func(args) or 0
    except (ConfigError, CliError, creds.KeyringUnavailable, editor.EditorError) as e:
        return _fail("daprivatecoder", e)
    except KeyboardInterrupt:
        return 130
