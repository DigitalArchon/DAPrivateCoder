"""The environment for programs DA Private Coder starts on the user's behalf (a terminal with Claude
Code in it). When DA Private Coder runs from an AppImage, the AppImage runtime adds variables
describing its own mount, and AppRun the CA store it chose; they mean nothing to those programs,
and another AppImage started from that terminal would misread them, so they are left out. From DA
Vibe Manager's hostenv.py."""

from __future__ import annotations

import os

# where the system keeps its GObject introspection typelibs (Fedora, Debian/Ubuntu, Arch)
HOST_TYPELIB_DIRS = ("/usr/lib64/girepository-1.0", "/usr/lib/x86_64-linux-gnu/girepository-1.0",
                     "/usr/lib/girepository-1.0")
APPIMAGE_VARS = ("APPIMAGE", "APPDIR", "ARGV0", "OWD", "APPIMAGE_EXTRACT_AND_RUN")
# AppRun names here the variables it set itself (the CA store)
APPRUN_SET = "DAPRIVATECODER_APPRUN_SET"
# variables DA Private Coder changed for itself, with their values before (None: they weren't set)
ORIGINAL: dict[str, str | None] = {}


def set_for_self(name: str, value: str) -> None:
    """Set an environment variable for DA Private Coder's own process only: host_env() gives children
    the value it had before."""
    ORIGINAL.setdefault(name, os.environ.get(name))
    os.environ[name] = value


def host_env(**extra: str) -> dict[str, str]:
    drop = set(APPIMAGE_VARS) | set((os.environ.get(APPRUN_SET) or "").split()) | {APPRUN_SET}
    env = {k: v for k, v in os.environ.items() if k not in drop}
    for name, value in ORIGINAL.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    env.update(extra)
    return env
