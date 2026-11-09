"""Every test gets its own home, settings and state folders and an in-memory keyring: nothing of
the user's (their DA Private Coder settings, request log, ~/.local/bin or keyring) is read or written."""

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from daprivatecoder import creds


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.store:
            raise PasswordDeleteError("not stored")
        del self.store[(service, username)]


@pytest.fixture(autouse=True)
def ring(tmp_path, monkeypatch):
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    memory = MemoryKeyring()
    keyring.set_keyring(memory)
    creds.forget()
    yield memory
    creds.forget()


@pytest.fixture
def suspend(monkeypatch):
    """`suspend(hours)`: the computer sleeps that long. The boot clock (dcap.since_boot) moves on;
    time.monotonic, which stops while the computer is suspended, doesn't (and the event loop keeps
    running on it). As DA Toolkit's tests have it."""
    import time

    if not hasattr(time, "CLOCK_BOOTTIME"):
        pytest.skip("no CLOCK_BOOTTIME here")
    slept = [0.0]
    real = time.clock_gettime
    monkeypatch.setattr(time, "clock_gettime",
                        lambda which: real(which) + (slept[0] if which == time.CLOCK_BOOTTIME else 0))

    def sleep(hours: float) -> None:
        slept[0] += hours * 3600

    return sleep
