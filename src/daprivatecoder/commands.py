"""The commands in ~/.local/bin: claude-private (Claude Code on the private profile, and what the
editor's switch names) and daprivatecoder (the rest: key, check, log…).

Each is a small script of DA Private Coder's that runs it the way it runs now: the AppImage, where it
is now, or from source, its Python. A path inside the AppImage won't do: it is mounted somewhere new
each time it runs. The scripts are written again when the window starts, so they follow an AppImage
that has moved. A file there that isn't DA Private Coder's is never overwritten.

They are only for typing in a terminal: the window runs the app itself, and the editor's switch
writes claude-private when it is turned on (editor.wrapper_path). For a TEE or standard profile, the
editor runs a script of the same kind kept out of the PATH (wrapper_dir: claude-tee, claude-standard),
which DA Private Coder rewrites the same way."""

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
    it runs now (an AppImage moved); never install ones the user hasn't."""
    stale = [name for name in COMMANDS if state(name) == "stale" and MARK in _text(path_of(name))]
    if stale:
        install(stale)
    folder = wrapper_dir()
    if folder.is_dir():
        for path in sorted(folder.glob("claude-*")):
            profile = path.name.removeprefix("claude-")
            if MARK in _text(path) and _text(path) != wrapper_script(profile):
                _write(path, wrapper_script(profile))


# ---------------------------------------------------------------------- the editor's, for other profiles

def wrapper_dir() -> Path:
    """Where the editor's scripts for profiles other than the private one are: out of the PATH."""
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "daprivatecoder" / "editor"


def wrapper_script(profile: str) -> str:
    argv = target_argv() + ["claude", "--profile", profile]
    return f'#!/bin/sh\n{MARK}: Claude Code on its {profile} profile, for the editor.\nexec {shlex.join(argv)} "$@"\n'


def write_wrapper(profile: str) -> Path:
    """The editor's script for `profile` (a TEE or standard one), written if it isn't as it should be."""
    path = wrapper_dir() / f"claude-{profile}"
    if _text(path) != wrapper_script(profile):
        _write(path, wrapper_script(profile))
    return path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        path.unlink()
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    tmp.chmod(0o755)
    os.replace(tmp, path)


def _text(path: Path) -> str:
    try:
        return "" if path.is_symlink() else path.read_text(errors="replace")
    except OSError:
        return ""
