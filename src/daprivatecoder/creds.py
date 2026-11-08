"""The API keys (NanoGPT's, and a standard profile's provider's: OpenRouter, another), in the OS
keyring (Secret Service on Linux). After DA Vibe Manager's creds.py.

Right after login the keyring may not be there yet, or still be locked. A key that can't be read
then is not a key that isn't there: reading raises KeyringUnavailable, and a key that isn't stored
reads as None. The keyring library picks its backend once per process; if it settled on none (no
Secret Service yet), it is asked to look again before each use. What has been read is kept in
memory for the rest of the run: every model request needs the key, and a keyring that locked
itself again (KWallet's timeout) would otherwise stop the session on its password prompt."""

from __future__ import annotations

import keyring
import keyring.core
from keyring.errors import PasswordDeleteError

SERVICE = "daprivatecoder"
USER = "nanogpt"            # NanoGPT's key: private and TEE sessions, web search, and a standard one on NanoGPT
# where DA Vibe Manager keeps the same key, for `daprivatecoder key set --from-vibe-manager`
VIBE_MANAGER = ("davibemanager", "provider:NanoGPT")


class KeyringUnavailable(Exception):
    """The keyring couldn't be read or written: not running yet, locked, or none at all."""


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
    """The keyring entry for a provider's key: "nanogpt", "openrouter", or "custom:<host>[:<port>]"
    (each server its own, two on one host included, so a key never goes to another server)."""
    if provider == "custom":
        from urllib.parse import urlparse
        u = urlparse(base_url)
        try:
            port = u.port
        except ValueError:
            port = None
        return f"custom:{(u.hostname or '').lower()}{f':{port}' if port else ''}"
    return provider if provider == "openrouter" else USER


def get_key(provider: str = "nanogpt", base_url: str = "") -> str | None:
    """The provider's key (NanoGPT's when not said), or None if none is stored."""
    return _get(SERVICE, account(provider, base_url))


def set_key(value: str, provider: str = "nanogpt", base_url: str = "") -> None:
    user = account(provider, base_url)
    try:
        _backend().set_password(SERVICE, user, value)
    except KeyringUnavailable:
        raise
    except Exception as e:  # noqa: BLE001 - said to the user as it is
        raise KeyringUnavailable(f"Your keyring couldn't store the key: {e}") from e
    _known[(SERVICE, user)] = value


def delete_key(provider: str = "nanogpt", base_url: str = "") -> None:
    user = account(provider, base_url)
    try:
        _backend().delete_password(SERVICE, user)
    except PasswordDeleteError:
        pass
    except KeyringUnavailable:
        raise
    except Exception as e:  # noqa: BLE001 - locked, say: said to the user as it is
        raise KeyringUnavailable(f"Your keyring couldn't remove the key: {e}") from e
    _known.pop((SERVICE, user), None)


def vibe_manager_key() -> str | None:
    """The NanoGPT key DA Vibe Manager stored, if it did: asked each time (stored there since)."""
    _known.pop(VIBE_MANAGER, None)
    return _get(*VIBE_MANAGER)


def forget() -> None:
    """Read the keyring again next time (tests)."""
    _known.clear()
