"""What a web search must never carry: who and where this computer is, and secrets.

A search leaves in the clear (through NanoGPT to the search provider), so a query is refused, in
code, before it goes when it holds this computer's names (the user's login and real name, the
host name, the home folder), an e-mail, MAC or private IP address, or what looks like a secret: an
API key or token, a private key, or a long random string. After DA Vibe Manager's
safety/outside.py (its part about the computer's identity; the rest of it guards what came out of
its sandbox, which Claude Code here doesn't have: it works on this computer itself).

A query is read as the search provider reads it: without the characters no one can see (a zero-width
space inside a key, an address or a name hid it from every check, and the provider reads past it),
and with compatibility forms (fullwidth digits) as their plain ones (safety/hidden.py, DA Toolkit's
and DA Vibe Manager's). Plain words go through ("cinnamon", "firefox"): on their own they say
nothing about the user, and no search would be possible without them. Claude Code also asks before
each search, showing it.

The address of every page Claude Code reads with WebFetch is checked the same way (check_url, run
as its PreToolUse hook: launcher.session_settings), as DA Vibe Manager checks its assistant's: an
address carries data out as well as a search does."""

from __future__ import annotations

import ipaddress
import json
import os
import pwd
import re
import socket
import sys
import unicodedata
from urllib.parse import unquote_plus, urlsplit

from .safety.hidden import visible

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_MAC = re.compile(r"\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b", re.I)
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
SECRETS = tuple(re.compile(p) for p in (
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY",
    r"\bsk-[A-Za-z0-9_-]{16,}",                          # OpenAI, Anthropic, NanoGPT and the like
    r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}",       # GitHub
    r"\bgithub_pat_[A-Za-z0-9_]{20,}",
    r"\bgl(?:pat|ptt|dt|rt|cbt)-[A-Za-z0-9_-]{16,}",     # GitLab
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",                    # AWS
    r"\b(?:xox[abeoprs]|xapp)-[A-Za-z0-9-]{10,}",        # Slack
    r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}",           # Stripe
    r"\bAIza[0-9A-Za-z_-]{35}",                          # Google
    r"\bhf_[A-Za-z0-9]{30,}",                            # Hugging Face
    r"\bnpm_[A-Za-z0-9]{30,}",                           # npm
    r"\bpypi-AgE[A-Za-z0-9_-]{50,}",                     # PyPI
    r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}",     # SendGrid
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.",      # a JSON web token
    r"\bhooks\.slack\.com/(?:services|workflows|triggers)/\S+", r"\bdiscord(?:app)?\.com/api/webhooks/\d+/\S+",
    r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@/]+@",        # a password in an address
    r"(?i)\b[\w.-]*(?:password|passwd|pwd|passphrase|secret|token|api[_-]?key|access[_-]?key)[\w.-]*=[^\s&]+",
))
# a long run of letters and digits mixed: a key, a token, a hash
_RANDOM = re.compile(r"[A-Za-z0-9+/_=-]{32,}")
# logins too common to be told apart from a word in a query
COMMON_NAMES = {"user", "admin", "root", "guest", "home", "test", "linux", "ubuntu", "mint", "debian", "fedora",
                "desktop", "laptop", "computer", "pc", "localhost"}


def names() -> list[str]:
    """This computer's names: login, real name, host name, home folder."""
    out = []
    try:
        pw = pwd.getpwuid(os.getuid())
        out += [pw.pw_name, pw.pw_dir]
        out += [p for p in re.split(r"[\s,]+", pw.pw_gecos or "") if len(p) >= 3]
    except KeyError:
        pass
    try:
        out += [socket.gethostname()]
    except OSError:
        pass
    return [x for x in dict.fromkeys(out) if x and len(x) >= 3 and x.lower() not in COMMON_NAMES]


def _private_ip(text: str) -> bool:
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    return not ip.is_global


def readings(text: str) -> list[str]:
    """`text` as a search provider may read it: without what no one can see, and with compatibility
    forms (fullwidth digits and letters, ligatures) as their plain ones."""
    shown = visible(text)
    plain = unicodedata.normalize("NFKC", shown)
    return [shown] + ([plain] if plain != shown else [])


def _secrets(text: str, runs: bool = True) -> list[re.Match]:
    found = [m for pattern in SECRETS for m in pattern.finditer(text)]
    if runs:
        found += [m for m in _RANDOM.finditer(text) if re.search(r"\d", m.group()) and re.search(r"[A-Za-z]", m.group())]
    return found


def check(query: str, known: list[str] | None = None, runs: bool = True) -> list[str]:
    """The parts of `query` that mustn't leave in a search (empty: it may be searched); a secret cut to
    its first four characters, so the refusal doesn't carry it again. `runs`: a long run of letters and
    digits counts as a secret (not in an address, whose pages have long names of their own)."""
    found: list[str] = []
    who = names() if known is None else known
    for text in readings(query):
        secret = _secrets(text, runs)
        low = text.lower()
        named = [(m, name) for name in who for m in re.finditer(rf"(?<![\w]){re.escape(name.lower())}(?![\w])", low)]
        named += [(m, m.group()) for m in [*_EMAIL.finditer(text), *_MAC.finditer(text)]]
        named += [(m, m.group()) for m in _IPV4.finditer(text) if _private_ip(m.group())]
        # (bob:PASSWORD@host reads as an e-mail address too: the secret shows it, cut)
        found += [shown for m, shown in named if not any(m.start() < s.end() and s.start() < m.end() for s in secret)]
        found += [s.group()[:4] + "…" for s in secret]
    return list(dict.fromkeys(found))


def check_url(url: str, known: list[str] | None = None) -> list[str]:
    """The parts of a web address that mustn't leave in it (empty: it may be read), as for a search: in
    its path, query and fragment, and in its host's labels before the site's own name
    (data.attacker.example), where a page's address carries data out as well as a search does. The
    site itself is what is read, and isn't checked (an address on this computer's network goes no
    further than the network)."""
    try:
        parts = urlsplit(visible(str(url or "")).strip())
        host = parts.hostname or ""
    except ValueError:
        return ["an address that can't be read"]
    rest = unquote_plus(" ".join((parts.path, parts.query, parts.fragment)))
    labels = [x for x in host.rstrip(".").split(".") if x]
    sub = " ".join(labels[:-2]) if len(labels) > 2 and not re.fullmatch(r"[\d.]+", host) else ""
    found = check(" ".join([*re.split(r"[/?&=#+;]+", rest), sub]), known, runs=False)
    # a token=… whole, and a password in the address itself (https://bob:…@host)
    found += [m.group()[:4] + "…" for text in readings(rest + " " + str(url or "")) for m in _secrets(text, runs=False)]
    return list(dict.fromkeys(found))


def fetch_hook(stdin=None, stdout=None) -> int:
    """Claude Code's PreToolUse hook for WebFetch (launcher.session_settings: `python -I -m
    daprivatecoder.identity`): an address that names this computer, holds a secret or what can't be
    read is refused before Claude Code reads the page, whatever the permission mode."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    try:
        event = json.load(stdin)
        url = event["tool_input"]["url"]
        found = check_url(url) if isinstance(url, str) else ["an address that isn't text"]
    except (ValueError, KeyError, TypeError):
        found = ["a request the check couldn't read"]
    if found:
        reason = ("Not fetched: the address names this computer or its user, or holds a secret ("
                  + ", ".join(repr(x) for x in found[:5]) + "). It leaves in the clear, as a search does: read a "
                  "general page instead (the project's documentation, its issues or releases).")
        json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                          "permissionDecisionReason": reason}}, stdout)
    return 0


if __name__ == "__main__":
    sys.exit(fetch_hook())
