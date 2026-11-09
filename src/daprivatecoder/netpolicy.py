"""Which addresses DA Private Coder may talk to, and how (after DA Vibe Manager's netpolicy.py).

One rule for every client it builds (NanoGPT, the relay, attestation): https anywhere; plain
http only to this machine, the local network or a Tor onion service. "Local" is exactly
localhost, loopback (127.0.0.0/8, ::1), the private ranges (10/8, 172.16/12, 192.168/16,
fc00::/7), link-local, and `.local` names. An onion service is authenticated and encrypted end to
end by Tor itself, so http to a `.onion` name counts as secure. A URL outside all that which isn't
https is refused with an explanation, never used and never rewritten.

A user name and password written into an address (a provider of your own behind a password) are
never said: every message and log names the address as shown() has it, and the settings file keeps it
without them (config.py; the keyring has it as typed). And an API key goes only where a header can
carry it (key_problem).
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

_PRIVATE_NETS = tuple(ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "::1/128",                                    # loopback
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",  # private
    "169.254.0.0/16", "fe80::/10",                                # link-local
))


# What no API key has in it: a backslash, white space, a control character (DA Assistant's netguard)
_UNSAFE = re.compile(r"[\\\s\x00-\x1f\x7f]")
# Everything of a text up to its last "@" before a query or a fragment, the scheme and "//" aside: where
# an address that can't be read may have a user name and password (DA Assistant's netguard)
_UP_TO_AT = re.compile(r"^((?:[^:/?#]+:)?//)?[^?#]*@")
# an address in a text, as an error message quotes one
_ADDRESS_IN_TEXT = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>]+")


class InsecureURL(ValueError):
    """A URL DA Private Coder won't use: not https, and not local or an onion service."""


def _ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def is_local_host(host: str | None) -> bool:
    """localhost, loopback, private, link-local or `.local`. An empty host is not local."""
    host = (host or "").lower()
    if host.endswith(".") and _ip(host.rstrip(".")) is not None:
        return False                    # 192.168.1.50. is a name to the resolver, not that address: DNS says where
    host = host.rstrip(".")
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return True
    ip = _ip(host)
    return ip is not None and any(ip in net for net in _PRIVATE_NETS)


def is_onion(host: str | None) -> bool:
    """A Tor onion service's name (Tor encrypts and authenticates the whole way to it)."""
    host = (host or "").lower().rstrip(".")
    return host.endswith(".onion") and len(host) > len(".onion")


def is_secure(url: str) -> bool:
    """https anywhere, or http to a local address or an onion service."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname
    except ValueError:
        return False
    if parsed.scheme == "https":
        return bool(host)
    return parsed.scheme == "http" and (is_local_host(host) or is_onion(host))


def check_url(url: str, what: str = "This address") -> str:
    """Return `url` unchanged if DA Private Coder may use it, else raise InsecureURL saying why."""
    url = (url or "").strip()
    try:
        parsed = urlparse(url)
        host = parsed.hostname
    except ValueError as e:              # one urlparse can't take (http://[::1]@evil.example/)
        raise InsecureURL(f"{what} isn't an address DA Private Coder can use ({e}): {shown(url)[:80]!r}") from None
    if parsed.scheme not in ("http", "https") or not host:
        raise InsecureURL(f"{what} must be a full https:// URL (got {shown(url)[:80]!r})")
    if not is_secure(url):
        raise InsecureURL(
            f"{what} uses plain http to {host}, which isn't on this machine, the local network or a "
            "Tor onion service. Use its https:// address instead."
        )
    return url


def shown(url: str | None) -> str:
    """`url` as it may be shown, logged or copied into a bug report: without the user name and password
    an address may carry (https://alice:secret@host/v1 is https://…@host/v1).

    For any text at all, and never raising: what is said of an address that can't be used names it too.
    One that can't be read as an address (half typed, without a scheme, with a port no address can
    have) says nothing certain of where its host begins, so all it has up to its last "@" goes. (DA
    Assistant's netguard.shown, Nov 2026.)"""
    text = str(url or "")
    try:
        parsed = urlparse(text)
        parsed.port                 # raises on a port that is not a number
    except ValueError:
        parsed = None
    if parsed is None or not parsed.netloc:
        return _UP_TO_AT.sub(r"\1…@", text, count=1)
    if "@" not in parsed.netloc:
        return text
    return parsed._replace(netloc="…@" + parsed.netloc.rsplit("@", 1)[1]).geturl()


def without_password(url: str | None) -> str:
    """`url` with the user name and password written into it taken out, as shown() does, but leaving an
    address, not a placeholder, where they were: what the settings file keeps of an address whose
    password is in the keyring. Text with neither is returned as it is. (DA Assistant's netguard.)"""
    text = str(url or "")
    if "@" not in text:
        return text
    try:
        parsed = urlparse(text)
        parsed.port                 # raises on a port that is not a number
    except ValueError:
        parsed = None
    if parsed is None or not parsed.netloc:
        return _UP_TO_AT.sub(r"\1", text, count=1)
    if "@" not in parsed.netloc:
        return text
    return parsed._replace(netloc=parsed.netloc.rsplit("@", 1)[1]).geturl()


def scrubbed(text: str) -> str:
    """`text` with every address in it as shown() has it: for an error that quotes the address a
    request went to, as httpx's do ("… for url 'https://alice:secret@host/v1/models'")."""
    return _ADDRESS_IN_TEXT.sub(lambda m: shown(m.group(0)), str(text))


def key_problem(key: str | None, name: str = "the provider") -> str | None:
    """Why `key` can't be sent as an API key, or None. A key is plain ASCII with no spaces in it; one
    pasted with a line break, or a zero-width space from a web page, would be refused by the HTTP
    library in words that quote the header it was to go in, key and all ("Illegal header value
    b'Bearer …'"), which Claude Code shows and the log keeps. Said without anything of the key. (DA
    Assistant's netguard.key_problem, Nov 2026.)"""
    if key and (not key.isascii() or _UNSAFE.search(key) or " " in key):
        return (f"The API key for {name} has a space, a line break or another character no API key has in it, "
                "picked up where it was copied from, perhaps. Enter it again in DA Private Coder's window (or with "
                "daprivatecoder key set in a terminal).")
    return None
