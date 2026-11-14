"""No core dumps: a crash must not write a process's memory to disk. The proxy holds every
session's requests and replies in the clear (a private model's too, once opened from what the enclave
sealed), the window and the commands the NanoGPT key, a search the user's query. As SealedLore's
gui/app.py _no_core_dumps (Nov 2026), called first by every entry point the app has: the window, every
command (cli.main), claude-private, the session's proxy (serve) and search server (search-server), and
WebFetch's check (identity.py).

Not dumpable (PR_SET_DUMPABLE 0) stops the kernel writing a core of the process, and so the system's
collector (systemd-coredump reads the flag, not the limit), and, as a side effect, a debugger attaching
from another process. The core size's soft limit 0 stops a core file too. Its hard limit is left as it
was: Claude Code (which claude-private becomes), the editor and the terminals the window opens inherit
the limit, and a hard 0 would keep the programs the user runs in them from ever having a core dump of
their own, `ulimit -c` included. Not dumpable isn't inherited past a program's start (exec). Linux
only; anything that fails is let go."""

from __future__ import annotations

import sys

PR_SET_DUMPABLE = 4


def no_core_dumps() -> None:
    try:
        import resource

        _soft, hard = resource.getrlimit(resource.RLIMIT_CORE)
        resource.setrlimit(resource.RLIMIT_CORE, (0, hard))
    except (ImportError, ValueError, OSError):
        pass
    if not sys.platform.startswith("linux"):
        return
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)
    except (OSError, AttributeError):
        pass
