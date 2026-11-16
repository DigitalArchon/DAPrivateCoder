"""The API keys (NanoGPT's, and a standard profile's provider's: OpenRouter, another), in the OS
keyring (Secret Service on Linux), and an address with a user name and password written into it, as it
was typed (config.py keeps it without them). After DA Vibe Manager's creds.py.

Right after login the keyring may not be there yet, or still be locked. A key that can't be read
then is not a key that isn't there: reading raises KeyringUnavailable, and a key that isn't stored
reads as None. The keyring library picks its backend once per process; if it settled on none (no
Secret Service yet), it is asked to look again before each use. What has been read is kept in
memory for the rest of the run: every model request needs the key, and a keyring that locked
itself again (KWallet's timeout) would otherwise stop the session on its password prompt.

A key no HTTP header can carry (a line break, a zero-width space from a web page) is refused when it
is stored and whenever it is read, saying so and nothing of the key (netpolicy.key_problem).

A key never follows an edited address. Beside each key the keyring keeps the origin (scheme, host and
port) it was stored for, and a key is read for an address only when that address has the same origin
(`get_key` with its base URL): NanoGPT's base_url edited in the settings file to another server, or a
provider of your own's from https to http, gets no key, and says why and what to do (KeyElsewhere). A
key stored before origins were kept takes the origin of the address it is first read for, with no
question. No key is ever deleted for it. (A provider of your own's key was already its server's own,
by host and port: `account`.) A provider whose address is fixed (OpenRouter's; providers.py `key_origins`) has
its key held to that address's origin alone, whatever is stored beside it.

Nor does a key cross a network unencrypted without the user's say (the cross-app standard, Nov 2026, as
SealedLore's keys_in_the_clear): to plain http on another machine of the local network (netpolicy.in_the_clear),
where anyone on it can read it, a key goes only once the user has agreed for that origin (`answer_in_the_clear`:
the window asks, No the default, and a No is kept for that origin, not asked again; `daprivatecoder key set`
asks in a terminal). Without that it is refused, in words naming the address and the ways out (KeyInTheClear).
A key already going there when this version first reads it (stored before origins were kept) takes that address's
origin, as any such key does, but is not taken as agreed (nothing is grandfathered): it is held back until the user
says Yes."""

from __future__ import annotations

import keyring
import keyring.core
from keyring.errors import PasswordDeleteError

from .providers import PROVIDERS

SERVICE = "daprivatecoder"
USER = "nanogpt"            # NanoGPT's key: private and TEE sessions, web search, and a standard one on NanoGPT
# where DA Vibe Manager keeps the same key, for `daprivatecoder key set --from-vibe-manager`
VIBE_MANAGER = ("davibemanager", "provider:NanoGPT")


class KeyringUnavailable(Exception):
    """The keyring couldn't be read or written: not running yet, locked, or none at all."""


class KeyElsewhere(KeyringUnavailable):
    """The key stored was stored for another address than the one it would be sent to now: nothing is sent. A
    KeyringUnavailable, so whatever says why there is no key to send says this too."""


class KeyInTheClear(KeyringUnavailable):
    """The key would go over plain http to another machine on the local network, which the user hasn't agreed to
    for that address: nothing is sent. A KeyringUnavailable, so whatever says why there is no key says this too."""


class KeyUnusable(KeyringUnavailable):
    """The key stored, or to be stored, has a character no HTTP header can carry. A KeyringUnavailable,
    so whatever says why there is no key to send (the proxy, the web search, the window, the commands)
    says this too, rather than sending it to be quoted back in an error."""


# whose key, in what is said of it (providers.py)
NAMES = {p.id: p.name for p in PROVIDERS.values()}


def _usable(key: str | None, provider: str) -> None:
    from .netpolicy import key_problem
    problem = key_problem(key, NAMES.get(provider, provider))
    if problem:
        raise KeyUnusable(problem)


def _backend():
    backend = keyring.get_keyring()
    if getattr(backend, "priority", 0) < 1:
        keyring.core.init_backend()          # the Secret Service may have started since
        backend = keyring.get_keyring()
    if getattr(backend, "priority", 0) < 1:
        raise KeyringUnavailable(
            f"No secure keyring is available ({type(backend).__name__}). Install and unlock a Secret Service "
            "provider such as GNOME Keyring or KWallet.")
    return backend


_known: dict[tuple[str, str], str | None] = {}


def _get(service: str, user: str) -> str | None:
    if (service, user) in _known:
        return _known[(service, user)]
    try:
        value = _backend().get_password(service, user)
    except KeyringUnavailable:
        raise
    except Exception as e:  # noqa: BLE001 - locked, not running yet (never kept: asked again)
        raise KeyringUnavailable(f"Your keyring couldn't be read ({e or type(e).__name__}). Unlock it, then try "
                                 "again.") from e
    if value is not None:
        _known[(service, user)] = value       # none isn't kept: one may be stored from a terminal meanwhile
    return value


def account(provider: str = "nanogpt", base_url: str = "") -> str:
    """The keyring entry for a provider's key (providers.Provider.account): "nanogpt", "openrouter", or, for a
    provider of your own, "custom:<host>[:<port>]" (each server its own, two on one host included, so a key never
    goes to another server)."""
    spec = PROVIDERS.get(provider)
    if spec is not None and spec.own_address:
        from urllib.parse import urlparse
        u = urlparse(base_url)
        try:
            port = u.port
        except ValueError:
            port = None
        return f"{spec.account}:{(u.hostname or '').lower()}{f':{port}' if port else ''}"
    return spec.account if spec is not None else USER


# the keyring entry of the origin a key was stored for, beside the key's own (its account's name)
ORIGIN = "{}_origin"
# the origin the user agreed the key may go to over plain http (another machine on the local network), and the one
# they said no for (kept so that the window doesn't ask again: answer_in_the_clear)
CLEAR = "{}_in_the_clear"
CLEAR_NO = "{}_in_the_clear_no"


def origin(url: str) -> str:
    """`url`'s origin as a key is held to it: scheme://host:port, in lower case, the port its scheme's when none
    is written. "" for what isn't an http(s) address. The DA apps' one form (llm/privacy.py key_origin), which the
    Chutes key's three origins are written in."""
    from .llm.privacy import key_origin
    return key_origin(url)


def _said(kept: str) -> str:
    """An origin as said to the user: without its scheme's own port."""
    for scheme, port in (("https", 443), ("http", 80)):
        if kept.startswith(f"{scheme}://") and kept.endswith(f":{port}"):
            return kept[: -len(f":{port}")]
    return kept


def _held_to(user: str, provider: str, base_url: str) -> None:
    """Raise KeyElsewhere unless the key `user` names was stored for `base_url`'s origin. A key with no origin kept
    (stored before they were) takes this one's; one going there over plain http to another machine is not taken as
    agreed to (the author, Nov 14 2026: nothing is grandfathered), and is held back until the user says Yes."""
    here = origin(base_url)
    spec = PROVIDERS.get(provider)
    fixed = spec.key_origins if spec is not None else frozenset()
    from .netpolicy import shown
    where = shown(base_url) or "an address that can't be read"
    if fixed:
        # a provider whose address is fixed: its key goes there alone, whatever origin is stored beside it
        if here in fixed:
            return
        raise KeyElsewhere(
            f"The {spec.name} API key goes only to {' or '.join(_said(o) for o in sorted(fixed))}, and would go to "
            f"{where} now, so it wasn't sent.")
    kept = _get(SERVICE, ORIGIN.format(user))
    if kept is None and here:
        try:
            _keep(ORIGIN.format(user), here)
        except KeyringUnavailable:
            pass                                    # used as before, and its origin kept the next time it can be
        return
    if kept == here:
        return
    name = NAMES.get(provider, provider)
    raise KeyElsewhere(
        f"The {name} API key in your keyring was stored for {_said(kept or '') or 'another address'}, and would "
        f"go to {where} now, so it wasn't sent: a key goes only to the address it was stored for. If that is where "
        "it should go, store the key again for it (in DA Private Coder's window, or with daprivatecoder key set); "
        "otherwise put the address back as it was.")


def _keep(user: str, value: str) -> None:
    try:
        _backend().set_password(SERVICE, user, value)
    except KeyringUnavailable:
        raise
    except Exception as e:  # noqa: BLE001 - said to the user as it is
        raise KeyringUnavailable(f"Your keyring couldn't store the key: {e}") from e
    _known[(SERVICE, user)] = value


def _drop(user: str) -> None:
    try:
        _backend().delete_password(SERVICE, user)
    except PasswordDeleteError:
        pass
    except KeyringUnavailable:
        raise
    except Exception as e:  # noqa: BLE001 - locked, say: said to the user as it is
        raise KeyringUnavailable(f"Your keyring couldn't remove the key: {e}") from e
    _known.pop((SERVICE, user), None)


def in_the_clear(base_url: str) -> bool:
    from .netpolicy import in_the_clear as crosses
    return crosses(base_url)


def get_key(provider: str = "nanogpt", base_url: str = "", *, sending: bool = True) -> str | None:
    """The provider's key (NanoGPT's when not said), or None if none is stored. With `base_url`, the address it is
    to go to: KeyElsewhere if the key was stored for another origin, KeyInTheClear if it would go there over plain
    http to another machine without the user's say (`sending`: the window's look at what is stored asks neither).
    None is sent then. Raises KeyUnusable for one no header can carry (stored before this was checked, or by another
    program)."""
    user = account(provider, base_url)
    key = _get(SERVICE, user)
    _usable(key, provider)
    if key and base_url:
        _held_to(user, provider, base_url)
    # the key, or a password written into the address (sent beside it as a login), over plain http to another
    # machine of the network: only once the user has said so for that address
    if sending and base_url and (key or has_password(base_url)) and in_the_clear(base_url) \
            and _get(SERVICE, CLEAR.format(user)) != origin(base_url):
        from .netpolicy import shown
        what = "key" if key else "password in its address"
        raise KeyInTheClear(
            f"The {what} for {NAMES.get(provider, provider)} would go unencrypted, over plain http, to "
            f"{shown(base_url)}, another machine on your network, where anyone on it can read it, so nothing was "
            "sent. Use the server's https:// address, or say Yes where DA Private Coder's window asks (Models, "
            "Standard, API key), or store the key again with `daprivatecoder key set --provider custom` in a "
            "terminal, which asks.")
    return key


def held_origin(provider: str, base_url: str) -> str:
    """The origin the provider's key is held to, for `base_url` (origin's form; "" when none is kept): one of a fixed
    address's own, else the one it was stored for. Chutes' end-to-end encryption sends the key to api.chutes.ai only
    when it was stored for one of Chutes' own hosts (llm/chutes_client.py not_chutes)."""
    spec = PROVIDERS.get(provider)
    here = origin(base_url)
    if spec is not None and spec.key_origins:
        return here if here in spec.key_origins else ""
    return _get(SERVICE, ORIGIN.format(account(provider, base_url))) or ""


def has_password(url: str) -> bool:
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        return parsed.username is not None or parsed.password is not None
    except ValueError:
        return False


def in_the_clear_state(provider: str, base_url: str) -> str:
    """What the user said of the key going over plain http to `base_url`: "" (it doesn't: https, or this computer),
    "allowed", "declined" (asked, and No: not asked again for that origin), or "ask"."""
    if not in_the_clear(base_url):
        return ""
    user, here = account(provider, base_url), origin(base_url)
    if _get(SERVICE, CLEAR.format(user)) == here:
        return "allowed"
    return "declined" if _get(SERVICE, CLEAR_NO.format(user)) == here else "ask"


def answer_in_the_clear(provider: str, base_url: str, allow: bool | None) -> None:
    """The user's answer for the key going over plain http to `base_url`'s origin: True sends it there from now on,
    False keeps it from there and isn't asked again for that origin, None asks again (the window's Ask again)."""
    user, here = account(provider, base_url), origin(base_url)
    if allow is True:
        _keep(CLEAR.format(user), here)
        _drop(CLEAR_NO.format(user))
    elif allow is False:
        _keep(CLEAR_NO.format(user), here)
        if _get(SERVICE, CLEAR.format(user)) == here:
            _drop(CLEAR.format(user))
    else:
        _drop(CLEAR_NO.format(user))
        if _get(SERVICE, CLEAR.format(user)) == here:
            _drop(CLEAR.format(user))


def set_key(value: str, provider: str = "nanogpt", base_url: str = "") -> None:
    """Store `value` as the provider's key, for `base_url` (the address it is to go to: its origin is kept beside
    it). Stored without one, it takes the origin of the address it is first read for."""
    _usable(value, provider)
    user = account(provider, base_url)
    _keep(user, value)
    here = origin(base_url)
    if here:
        _keep(ORIGIN.format(user), here)
    else:
        _drop(ORIGIN.format(user))


def delete_key(provider: str = "nanogpt", base_url: str = "") -> None:
    user = account(provider, base_url)
    _drop(user)
    _drop(ORIGIN.format(user))
    _drop(CLEAR.format(user))
    _drop(CLEAR_NO.format(user))


# the keyring entry of an address typed with a user name and password (`setting`: config.py's name for
# where it is, "base_url" or "profiles.standard.base_url"), as DA Assistant names its own
ADDRESS = "{}_with_password"


def address(setting: str) -> str | None:
    """The address `setting` was typed as, user name and password and all, if the keyring has one."""
    return _get(SERVICE, ADDRESS.format(setting))


def keep_address(setting: str, value: str) -> None:
    """Keep `value`, an address with a user name and password written into it, as `setting`'s."""
    user = ADDRESS.format(setting)
    if _known.get((SERVICE, user)) == value:
        return                                # it has it already: nothing is written
    try:
        _backend().set_password(SERVICE, user, value)
    except KeyringUnavailable:
        raise
    except Exception as e:  # noqa: BLE001 - said to the user as it is
        raise KeyringUnavailable(f"Your keyring couldn't store the address's password: {e}") from e
    _known[(SERVICE, user)] = value


def forget_address(setting: str) -> None:
    """Take the address kept for `setting` out of the keyring, if there is one."""
    user = ADDRESS.format(setting)
    try:
        _backend().delete_password(SERVICE, user)
    except PasswordDeleteError:
        pass
    except KeyringUnavailable:
        raise
    except Exception as e:  # noqa: BLE001 - said to the user as it is
        raise KeyringUnavailable(f"Your keyring couldn't remove the address's password: {e}") from e
    _known.pop((SERVICE, user), None)


def vibe_manager_key() -> str | None:
    """The NanoGPT key DA Vibe Manager stored, if it did: asked each time (stored there since)."""
    _known.pop(VIBE_MANAGER, None)
    return _get(*VIBE_MANAGER)


def forget() -> None:
    """Read the keyring again next time (tests)."""
    _known.clear()
