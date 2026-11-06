"""Which addresses DA Private Coder may talk to, and how (after DA Vibe Manager's netpolicy.py).

One rule for every client it builds (NanoGPT, the relay, attestation): https anywhere; plain
http only to this machine, the local network or a Tor onion service. "Local" is exactly
localhost, loopback (127.0.0.0/8, ::1), the private ranges (10/8, 172.16/12, 192.168/16,
fc00::/7), link-local, and `.local` names. An onion service is authenticated and encrypted end to
end by Tor itself, so http to a `.onion` name counts as secure. A URL outside all that which isn't
https is refused with an explanation, never used and never rewritten.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

_PRIVATE_NETS = tuple(ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "::1/128",                                    # loopback
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",  # private
    "169.254.0.0/16", "fe80::/10",                                # link-local
))


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
        raise InsecureURL(f"{what} isn't an address DA Private Coder can use ({e}): {url[:80]!r}") from None
    if parsed.scheme not in ("http", "https") or not host:
        raise InsecureURL(f"{what} must be a full https:// URL (got {url[:80]!r})")
    if not is_secure(url):
        raise InsecureURL(
            f"{what} uses plain http to {host}, which isn't on this machine, the local network or a "
            "Tor onion service. Use its https:// address instead."
        )
    return url
