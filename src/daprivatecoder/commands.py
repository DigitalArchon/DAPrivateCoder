"""The commands in ~/.local/bin: claude-private (Claude Code on the private profile, and what the
editor's switch names) and daprivatecoder (the rest: key, check, log…).

Each is a small script of DA Private Coder's that runs it the way it runs now: the AppImage, where it
is now, or from source, its Python. A path inside the AppImage won't do: it is mounted somewhere new
each time it runs. The scripts are written again when the window starts, so they follow an AppImage
that has moved. A file there that isn't DA Private Coder's is never overwritten; PrivateCode's (this
app's earlier name: links into its own environment) are replaced."""

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
    """"current" (ours, as it should be), "stale" (ours, or PrivateCode's: to write again), "missing", or
    "taken" (someone else's)."""
    path = path_of(name)
    if path.is_symlink() and "/privatecode/" in os.readlink(path):
        return "stale"                            # PrivateCode's install.sh: a link into its environment
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
            path = path_of(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                path.unlink()
            tmp = path.with_name(f".{name}.tmp")
            tmp.write_text(script(name))
            tmp.chmod(0o755)
            os.replace(tmp, path)
            now = "current"
        out[name] = now
    return out


def refresh() -> None:
    """Write again the commands DA Private Coder installed that no longer run it as it runs now (an
    AppImage moved); never install ones the user hasn't."""
    stale = [name for name in COMMANDS if state(name) == "stale" and MARK in _text(path_of(name))]
    if stale:
        install(stale)


def _text(path: Path) -> str:
    try:
        return "" if path.is_symlink() else path.read_text(errors="replace")
    except OSError:
        return ""
