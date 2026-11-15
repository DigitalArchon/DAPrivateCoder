"""The commands in ~/.local/bin: claude-private (Claude Code on the private profile, and what the
editor's switch names) and daprivatecoder (the rest: key, check, log…).

Each is a small script of DA Private Coder's that runs it the way it runs now: the AppImage, where it
is now, or from source, its Python. A path inside the AppImage won't do: it is mounted somewhere new
each time it runs. The scripts are written again when the window starts, so they follow an AppImage
that has moved. A file there that isn't DA Private Coder's is never overwritten.

They are only for typing in a terminal: the window runs the app itself. The editor's switch names a
script of the same kind kept out of the PATH (wrapper_dir: claude-editor), which runs each of the
editor's Claude Code sessions as its folder says (folders.py), and is rewritten the same way."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

MARK = "# Written by DA Private Coder"
COMMANDS = {"claude-private": "Claude Code on NanoGPT's private models", "daprivatecoder": "DA Private Coder's commands"}


def bin_dir() -> Path:
    return Path(os.path.expanduser("~/.local/bin"))


def path_of(name: str) -> Path:
    return bin_dir() / name


def target_argv() -> list[str]:
    """How to run DA Private Coder: the AppImage, or this Python."""
    appimage = os.environ.get("APPIMAGE")
    return [appimage] if appimage else [sys.executable, "-I", "-m", "daprivatecoder"]


def script(name: str) -> str:
    argv = target_argv() + (["claude-private"] if name == "claude-private" else [])
    return f'#!/bin/sh\n{MARK}: {COMMANDS[name]}.\nexec {shlex.join(argv)} "$@"\n'


def state(name: str) -> str:
    """"current" (ours, as it should be), "stale" (ours, to write again), "missing", or "taken"
    (someone else's)."""
    path = path_of(name)
    if not path.exists():
        return "missing"
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return "taken"
    if MARK not in text:
        return "taken"
    return "current" if text == script(name) else "stale"


def on_path() -> bool:
    return str(bin_dir()) in [os.path.expanduser(p.rstrip("/")) for p in os.environ.get("PATH", "").split(":")]


def install(names=tuple(COMMANDS)) -> dict[str, str]:
    """Write the commands that are missing or out of date; each one's state afterwards."""
    out = {}
    for name in names:
        now = state(name)
        if now in ("missing", "stale"):
            _write(path_of(name), script(name))
            now = "current"
        out[name] = now
    return out


def refresh() -> None:
    """Write again the commands and editor scripts DA Private Coder installed that no longer run it as
    it runs now (an AppImage moved); never install ones the user hasn't. The folders of the editor's script are
    made this user's alone first (own_folders), where an earlier version left them open."""
    own_folders()
    stale = [name for name in COMMANDS if state(name) == "stale" and MARK in _text(path_of(name))]
    if stale:
        install(stale)
    path = wrapper_dir() / EDITOR_SCRIPT
    if MARK in _text(path) and _text(path) != editor_script():
        _write(path, editor_script())


# ---------------------------------------------------------------------- the editor's

EDITOR_SCRIPT = "claude-editor"


def wrapper_dir() -> Path:
    """Where the editor's script is: out of the PATH, in the app's data folder (~/.local/share/daprivatecoder)."""
    from . import config
    return config.data_dir() / "editor"


def own_folders() -> None:
    """The app's data folder, and the editor's script's folder in it, this user's alone (config.made: 0700), where they
    are and an earlier version made them as the umask said (0775 or 0755, so that others could list them, and with a
    umask of 002 the user's group change the script the editor runs). Nothing is made where nothing was written."""
    from . import config
    for folder in (wrapper_dir(), config.data_dir()):
        if folder.is_dir():
            config.made(folder)


def editor_script() -> str:
    argv = target_argv() + ["claude", "--folders"]
    return (f'#!/bin/sh\n{MARK}: Claude Code in the editor, on DA Private Coder or as usual, as its folder says.\n'
            f'exec {shlex.join(argv)} "$@"\n')


def write_editor_script() -> Path:
    """The editor's script, written if it isn't as it should be."""
    path = wrapper_dir() / EDITOR_SCRIPT
    if _text(path) != editor_script():
        _write(path, editor_script())
    return path


def _write(path: Path, text: str) -> None:
    """A command, written whole or not at all and runnable (config.write_private): a link where it stood is
    replaced, never followed. The fixed temporary name it was written under before was any other copy's too."""
    from . import config
    config.made(path.parent)                    # the editor's script's folder this user's alone; ~/.local/bin as it is
    if path.is_symlink():
        path.unlink()
    config.write_private(path, text, mode=0o755)


def _text(path: Path) -> str:
    try:
        return "" if path.is_symlink() else path.read_text(errors="replace")
    except OSError:
        return ""
