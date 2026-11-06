"""The NanoGPT API key, in the OS keyring (Secret Service on Linux). After DA Vibe Manager's creds.py.

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
USER = "nanogpt"
# where DA Vibe Manager keeps the same key, for `daprivatecoder key set --from-vibe-manager`
VIBE_MANAGER = ("davibemanager", "provider:NanoGPT")
# where PrivateCode, this app's earlier name, kept it: taken over once, while none is stored here
PRIVATECODE = ("privatecode", "nanogpt")


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
    _known[(service, user)] = value
    return value


def get_key() -> str | None:
    """The NanoGPT key, or None if none is stored. One PrivateCode stored, while there is none of
    DA Private Coder's own, is taken over (copied: PrivateCode's stays where it is)."""
    key = _get(SERVICE, USER)
    if key is None:
        earlier = _get(*PRIVATECODE)
        if earlier:
            set_key(earlier)
            key = earlier
    return key


def set_key(value: str) -> None:
    try:
        _backend().set_password(SERVICE, USER, value)
    except KeyringUnavailable:
        raise
    except Exception as e:  # noqa: BLE001 - said to the user as it is
        raise KeyringUnavailable(f"Your keyring couldn't store the key: {e}") from e
    _known[(SERVICE, USER)] = value


def delete_key() -> None:
    try:
        _backend().delete_password(SERVICE, USER)
    except PasswordDeleteError:
        pass
    _known[(SERVICE, USER)] = None


def vibe_manager_key() -> str | None:
    """The NanoGPT key DA Vibe Manager stored, if it did: asked each time (stored there since)."""
    _known.pop(VIBE_MANAGER, None)
    return _get(*VIBE_MANAGER)


def forget() -> None:
    """Read the keyring again next time (tests)."""
    _known.clear()
