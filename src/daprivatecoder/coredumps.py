"""No core dumps: a crash must not write a process's memory to disk. The proxy holds every
session's requests and replies in the clear (a private model's too, once opened from what the enclave
sealed), the window and the commands the NanoGPT key, a search the user's query. Called first by every
entry point the app has: the window, every command (cli.main), claude-private, the session's proxy
(serve) and search server (search-server), and WebFetch's check (identity.py). The same three steps as
every DA app (the cross-app standard, Nov 2026; DA Privacy Kit's coredump_filter):

- not dumpable (PR_SET_DUMPABLE 0): the kernel writes no core of the process, and so neither does the
  system's collector (systemd-coredump reads the flag, not the limit), and, as a side effect, no
  debugger attaches from another process;
- the core size's soft limit 1: the kernel aborts the core of a process whose limit is exactly 1, even
  of a dumpable one ("RLIMIT_CORE is set to 1, aborting core"), where a limit of 0 alone is ignored by
  systemd-coredump's pipe (measured on MintDev, Nov 14 2026). The hard limit is left as it was: Claude
  Code (which claude-private becomes), the editor and the terminals the window opens inherit the soft
  limit, and may raise it again (`ulimit -c`), as a hard 0 would never let them;
- /proc/self/coredump_filter 0: whatever core is written anyway holds none of the process's memory.

Not dumpable isn't inherited past a program's start (exec); the limit and the filter are. Linux only;
anything that fails is let go."""

from __future__ import annotations

import sys

PR_SET_DUMPABLE = 4


def no_core_dumps() -> None:
    if not sys.platform.startswith("linux"):
        return
    try:
        # first: once not dumpable, the process's own /proc files are root's, this one included
        with open("/proc/self/coredump_filter", "w", encoding="ascii") as f:
            f.write("0")
    except OSError:
        pass
    try:
        import resource

        _soft, hard = resource.getrlimit(resource.RLIMIT_CORE)
        soft = 1 if hard == resource.RLIM_INFINITY else min(1, hard)
        resource.setrlimit(resource.RLIMIT_CORE, (soft, hard))
    except (ImportError, ValueError, OSError):
        pass
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)
    except (OSError, AttributeError):
        pass
