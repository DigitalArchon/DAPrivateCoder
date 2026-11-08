"""Whether a session may send anything now. DA Private Coder runs Claude Code only while it is open:
its window (or its server, with --browser) holds a shared lock on window.lock for as long as it runs,
which the kernel lets go of however it ends, and each session's proxy asks before every request. A
session the editor started (its process wrapper) also stops once that editor's switch no longer
names its profile: switched back to normal, or to another kind of session.

Kept light (no HTTP or crypto): the launcher imports it for every session."""

from __future__ import annotations

import fcntl
import os
import time
from pathlib import Path

from .config import state_dir

LABELS = {"private": "Private", "tee": "TEE", "standard": "Standard"}


def lock_path() -> Path:
    return state_dir() / "window.lock"


_held: int | None = None


def hold() -> None:
    """Mark DA Private Coder as open until this process ends (the window's, or the server's)."""
    global _held
    if _held is not None:
        return
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # close-on-exec: a terminal the window opens mustn't keep it open after the window is gone
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    fcntl.flock(fd, fcntl.LOCK_SH)
    _held = fd


def window_open() -> bool:
    """Whether a window of DA Private Coder's holds the lock now. Unknown counts as closed: nothing is
    sent on a guess."""
    try:
        fd = os.open(lock_path(), os.O_RDONLY | os.O_CLOEXEC)
    except OSError:
        return False
    try:
        # a window holds it shared for as long as it runs; another session asking at the same moment
        # holds it for an instant only, so a refusal is asked about twice more before it counts
        for attempt in range(3):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if attempt == 2:
                    return True
                time.sleep(0.005)
                continue
            except OSError:
                return False
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        return True
    finally:
        os.close(fd)


class Gate:
    """For one session of `profile`: why it may not send now ("" when it may). `editors` are the
    (name, settings file) of the editor whose Claude Code started it, if one did, in `folder`."""

    def __init__(self, profile: str, editors: list[tuple[str, Path]] | tuple = (), folder: str = ""):
        self.profile = profile
        self.editors = [(name, Path(path)) for name, path in editors]
        self.folder = folder or os.getcwd()
        self._seen: dict[Path, tuple[tuple, object]] = {}

    def _stamped(self, path: Path, read):
        """read(path), again only once the file has changed; None when it can't be read just now
        (being written, say): not a reason to stop."""
        try:
            st = path.stat()
            stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
        except FileNotFoundError:
            stamp = None
        except OSError:
            return None
        seen = self._seen.get(path)
        if seen and seen[0] == stamp:
            return seen[1]
        try:
            value = read(path) if stamp is not None else read(None)
        except Exception:  # noqa: BLE001 - not readable now: asked again next time
            return None
        self._seen[path] = (stamp, value)
        return value

    def _named(self, path: Path) -> str | None:
        """What `path`'s switch runs now (editor.profile_of: a profile, PER_FOLDER, or "" for nothing of ours)."""
        from . import editor

        def read(p):
            return editor.profile_of(editor.loads(p.read_text(encoding="utf-8")).get(editor.KEY)) if p else ""
        return self._stamped(path, read)

    def _folder_runs(self) -> str | None:
        """The profile this session's folder runs on now ("": Claude as usual), or None: unknown."""
        from . import folders

        def read(p):
            return folders.choice(folders.load(), self.folder)[0] if p else folders.choice(folders.Folders(), self.folder)[0]
        return self._stamped(folders.path(), read)

    def why(self) -> str:
        from .editor import PER_FOLDER
        label = LABELS.get(self.profile, self.profile)
        if not window_open():
            hint = (f" For Claude instead, set this folder to run as usual there (or switch {self.editors[0][0]} back "
                    "to normal) and start a new conversation.") if self.editors else ""
            return f"DA Private Coder is closed, so nothing was sent. Open it to go on with this conversation.{hint}"
        if not self.editors:
            return ""
        said = ""
        for name, path in self.editors:
            named = self._named(path)
            if named is None or named == self.profile:
                return ""
            if named == PER_FOLDER:
                runs = self._folder_runs()
                if runs is None or runs == self.profile:
                    return ""
                said = said or (
                    f"This folder now runs DA Private Coder's {LABELS.get(runs, runs)} sessions in {name}, so nothing was "
                    f"sent: this conversation was started as a {label} one. Start a new conversation." if runs else
                    f"This folder now runs Claude as usual in {name}, so nothing was sent: this conversation was started "
                    f"on DA Private Coder's {label} models. Start a new conversation to use Claude.")
            elif named:
                said = said or (f"{name}'s Claude Code now runs DA Private Coder's {LABELS.get(named, named)} sessions, so "
                                f"nothing was sent: this conversation was started as a {label} one. Start a new conversation.")
            else:
                said = said or (f"DA Private Coder was switched off for {name} (Back to normal), so nothing was sent: this "
                                "conversation was started on it. Start a new conversation to use Claude.")
        return said
