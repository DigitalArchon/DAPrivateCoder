"""Every test gets its own settings and state folders and an in-memory keyring: nothing of the
user's (their PrivateCode settings, request log or keyring) is read or written."""

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from privatecode import creds


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
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    memory = MemoryKeyring()
    keyring.set_keyring(memory)
    creds.forget()
    yield memory
    creds.forget()
