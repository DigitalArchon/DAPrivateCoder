"""The commands.

  claude-private [Claude Code's arguments]      Claude Code on NanoGPT's private models
  privatecode claude [--profile NAME] [...]     the same on any profile (nano: NanoGPT's other models)
  privatecode editor on|off|status              the switch for Claude Code in VSCodium and VS Code
  privatecode key set|clear|status              the NanoGPT API key, in your keyring
  privatecode status                            what is set up here
  privatecode check [--profile NAME]            attest the enclave, list the private models and prices
  privatecode log [-n N]                        what each session asked of which model, and the cost
  privatecode serve ...                         the proxy itself (claude-private starts it)
  privatecode search-server ...                 the web search tool (Claude Code starts it)
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

PRIVATE = "private"


class CliError(Exception):
    pass


def _fail(prog: str, e: Exception) -> int:
    print(f"{prog}: {e}", file=sys.stderr)
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
    profile = PRIVATE
    if argv[:1] == ["--profile"] and len(argv) > 1:
        profile, argv = argv[1], argv[2:]
    elif argv and argv[0].startswith("--profile="):
        profile, argv = argv[0].split("=", 1)[1], argv[1:]
    return _launch("privatecode", profile, argv)


# ---------------------------------------------------------------------- the key

def _key(args) -> int:
    from . import creds
    if args.action == "status":
        key = creds.get_key()
        print(f"A NanoGPT key is stored in your keyring (ending {key[-4:]})." if key
              else "No NanoGPT key is stored. Store one with: privatecode key set")
        return 0
    if args.action == "clear":
        creds.delete_key()
        print("The NanoGPT key was removed from your keyring.")
        return 0
    if args.from_vibe_manager:
        key = creds.vibe_manager_key() or ""
        if not key:
            raise CliError("DA Vibe Manager has no NanoGPT key in your keyring.")
    elif sys.stdin.isatty():
        key = getpass.getpass("NanoGPT API key (it isn't shown as you type): ")
    else:
        key = sys.stdin.readline()
    key = key.strip()
    if not key or any(c.isspace() for c in key):
        raise CliError("That isn't an API key: nothing was stored.")
    creds.set_key(key)
    print("The NanoGPT key is stored in your keyring.")
    return 0


# ---------------------------------------------------------------------- the editor switch

def _editor(args) -> int:
    from . import editor
    found = [(name, path) for name, path in editor.editors()
             if not args.which or args.which.lower() in name.lower().replace(" ", "")]
    if not found:
        raise CliError("No VSCodium or VS Code settings were found here." if not args.which
                       else f"No settings of {args.which} were found here.")
    ours = lambda v: isinstance(v, str) and os.path.basename(v) == "claude-private"   # noqa: E731
    if args.action == "status":
        for name, path in found:
            value = editor.current(path)
            state = "on (PrivateCode)" if ours(value) else f"another wrapper: {value}" if value else "off"
            print(f"{name}: {state}")
        return 0
    wrapper = editor.wrapper_path() if args.action == "on" else None
    for name, path in found:
        value = editor.current(path)
        if value and not ours(value):
            print(f"{name}: left as it is: its Claude Code already runs through another program ({value}).")
            continue
        changed = editor.switch(path, wrapper)
        print(f"{name}: {'changed' if changed else 'already so'} ({path})")
    if wrapper:
        print("Claude Code sessions started in the editor from now on run on NanoGPT's private models "
              "(open a new Claude Code tab, or reload the window).\nTo switch back: privatecode editor off")
    else:
        print("Claude Code sessions started in the editor from now on run as usual, on Anthropic's models.")
    return 0


# ---------------------------------------------------------------------- looking

def _status(args) -> int:
    from . import creds, editor, launcher
    cfg = config.load()
    print(f"PrivateCode {__version__}\nSettings: {cfg.path}\nNanoGPT: {cfg.base_url}")
    searches = {"zero-retention": "Linkup (keeps nothing)", "best": "Perplexity and Kagi (not zero retention)",
                "off": "off"}
    for p in cfg.profiles.values():
        kind = "private" if p.private else "not private"
        print(f"  [{p.name}] {kind}: {p.model} (opus {p.opus_model}, quick {p.fast_model}); "
              f"vision {p.vision_model or 'none'}; reasoning {p.reasoning}; web search {searches[p.web_search]}")
    try:
        print(f"Claude Code: {launcher.find_claude(cfg.claude)}")
    except launcher.LaunchError as e:
        print(f"Claude Code: {e}")
    try:
        key = creds.get_key()
        print("NanoGPT key: stored in your keyring" if key else "NanoGPT key: none (privatecode key set)")
    except creds.KeyringUnavailable as e:
        print(f"NanoGPT key: {e}")
    for name, path in editor.editors():
        value = editor.current(path)
        state = "on" if value and os.path.basename(value) == "claude-private" else f"another wrapper ({value})" if value \
            else "off"
        print(f"Editor, {name}: {state}")
    print(f"Request log: {state_dir() / 'requests.log'}")
    return 0


def _check(args) -> int:
    import asyncio

    from .llm import capabilities
    from .llm.private_mode import Enclave, list_private_models, offered, offers_private_mode, relay_url
    cfg = config.load()
    profile = cfg.profile(args.profile)
    if not offers_private_mode(cfg.base_url):
        raise CliError(f"{cfg.base_url} isn't NanoGPT, so it has no Private Mode.")

    async def go() -> int:
        ids = await list_private_models(cfg.base_url)
        try:
            caps = await capabilities.fetch_nanogpt(cfg.base_url)
        except Exception:  # noqa: BLE001 - shown without what it can do
            caps = {}
        print("NanoGPT Private Mode models now (prices per million tokens, input / cached input / output):")
        for m in ids:
            c = capabilities.lookup(caps, m) or {}
            price = c.get("pricing")
            cost = (" ${:.2f} / ${:.2f} / ${:.2f}".format(*(price[k] * capabilities.PRIVATE_MARKUP
                                                           for k in ("input", "cache_read", "output")))
                    if price else "")
            ctx = f" {c['context'] // 1000}k context" if c.get("context") else ""
            print(f"  {m}{cost}{ctx}{', sees images' if c.get('vision') else ''}{', no tool calls' if c and not c.get('tools') else ''}")
        bad = [m for m in (*profile.models, profile.vision_model) if m and offered(m, ids) is False]
        for m in bad:
            print(f"Profile [{profile.name}] uses {m}, which NanoGPT no longer offers: change it in {cfg.path}")
        print("Attesting the enclave...")
        att = (await Enclave(relay_url(cfg.base_url)).current()).attestation
        print(f"Verified: {att.summary}\n  key fingerprint (sha256) {att.hpke_key_sha256}")
        return 1 if bad else 0
    return asyncio.run(go())


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
            print(f"{r.get('t', '')[:19]} {r.get('session', '')} clear  {str(r.get('status', '')):>3} {what:<30} "
                  f"{str(r.get('results', '')) + ' results' if 'results' in r else '':<36} "
                  f"{f'${cost:.4f}' if cost is not None else '':>9} {r.get('seconds', '')}s{extra}")
            continue
        u = r.get("usage") or {}
        cost = u.get("cost")
        total += cost or 0
        tokens = f"{u.get('input', 0) + u.get('cache_read', 0):>7} in ({u.get('cache_read', 0)} cached) {u.get('output', 0):>6} out" \
            if u else " " * 20
        price = (f"${cost:.4f}{'~' if u.get('cost_estimated') else ''}" if cost is not None else "")
        how = "sealed" if r.get("sealed") or r.get("kind") == "vision" else "clear "
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
    import socket

    from . import launcher, proxy
    from .usagelog import UsageLog
    if args.parent:
        launcher.die_with_parent(args.parent)
    cfg = config.load(Path(args.config) if args.config else None)
    profile = cfg.profile(args.profile)
    token = os.environ.pop("PRIVATECODE_TOKEN", "")
    if args.fd is not None:
        if not token:
            raise CliError("serve --fd needs its token in PRIVATECODE_TOKEN.")
        sock = socket.socket(fileno=args.fd)
    else:
        sock = proxy.listen(args.port)
        token = token or secrets.token_urlsafe(32)
    sock.setblocking(False)
    log = UsageLog(state_dir() / "requests.log", secrets.token_hex(4))
    log.write("session", profile=profile.name, private=profile.private, models=list(profile.models),
              vision=profile.vision_model, base_url=cfg.base_url, pid=os.getpid())
    p = proxy.Proxy(profile, cfg.base_url, token, log=log)

    def ready(port: int) -> None:
        if args.fd is None:
            print(f"PrivateCode's proxy for [{profile.name}] is on http://127.0.0.1:{port}\n"
                  f"  ANTHROPIC_BASE_URL=http://127.0.0.1:{port}\n  ANTHROPIC_AUTH_TOKEN={token}", flush=True)
    asyncio.run(proxy.serve(p, sock, ready=ready, parent=args.parent))
    return 0


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="privatecode", description="Claude Code on NanoGPT's end-to-end encrypted "
                                 "private models. Start it with: claude-private")
    ap.add_argument("--version", action="version", version=f"PrivateCode {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("claude", help="start Claude Code on a profile: privatecode claude [--profile NAME] [its arguments]")
    k = sub.add_parser("key", help="the NanoGPT API key, in your keyring")
    k.add_argument("action", choices=("set", "clear", "status"))
    k.add_argument("--from-vibe-manager", action="store_true", help="set: copy the key DA Vibe Manager stored")
    k.set_defaults(func=_key)
    e = sub.add_parser("editor", help="the switch for Claude Code in VSCodium and VS Code")
    e.add_argument("action", choices=("on", "off", "status"))
    e.add_argument("which", nargs="?", help="only this editor: vscodium or code")
    e.set_defaults(func=_editor)
    sub.add_parser("status", help="what is set up here").set_defaults(func=_status)
    c = sub.add_parser("check", help="attest the enclave, and list NanoGPT's private models and their prices")
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
    s.set_defaults(func=_serve)
    w = sub.add_parser("search-server", help="the web search tool for Claude Code (claude-private adds it)")
    w.add_argument("--profile", default=PRIVATE)
    w.add_argument("--config", help=argparse.SUPPRESS)
    w.set_defaults(func=_search_server)
    return ap


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["claude"]:
        return _claude(argv[1:])
    args = _parser().parse_args(argv)
    from . import creds, editor
    try:
        return args.func(args) or 0
    except (ConfigError, CliError, creds.KeyringUnavailable, editor.EditorError) as e:
        return _fail("privatecode", e)
    except KeyboardInterrupt:
        return 130
