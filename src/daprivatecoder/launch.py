"""Opening Claude Code in a terminal window, in a folder of the user's, on their click in the window.

The terminal is found as DA Vibe Manager's launch.py finds one (this is its terminal_argv): with
xdg-terminal-exec (the desktop's own choice, and GLib's first), else the terminal $TERMINAL names,
else the terminals the desktop in use and the common ones have, each with its own way of taking a
command. If Claude Code ends with an error (no key, no Claude Code), the window stays open to say so.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Callable

# a terminal -> the words before the command ("rest": the command's words follow as they are;
# "string": the command as one shell string after them)
TERMINALS: dict[str, tuple[tuple[str, ...], str]] = {
    "xdg-terminal-exec": ((), "rest"),
    "ghostty": (("-e",), "rest"),
    "ptyxis": (("--",), "rest"),
    "kgx": (("--",), "rest"),
    "gnome-terminal": (("--",), "rest"),
    "mate-terminal": (("-x",), "rest"),
    "xfce4-terminal": (("-x",), "rest"),
    "tilix": (("-e",), "string"),
    "konsole": (("-e",), "rest"),
    "alacritty": (("-e",), "rest"),
    "kitty": ((), "rest"),
    "foot": ((), "rest"),
    "wezterm": (("start", "--"), "rest"),
    "x-terminal-emulator": (("-e",), "rest"),
    "xterm": (("-e",), "rest"),
    "urxvt": (("-e",), "rest"),
}
# the desktop's own terminal first (XDG_CURRENT_DESKTOP names it), then the rest in TERMINALS' order
PREFERRED = {
    "KDE": ("konsole",), "XFCE": ("xfce4-terminal",), "MATE": ("mate-terminal",),
    "GNOME": ("ptyxis", "kgx", "gnome-terminal"), "X-CINNAMON": ("gnome-terminal",), "CINNAMON": ("gnome-terminal",),
    "HYPRLAND": ("alacritty", "ghostty", "kitty", "foot"), "SWAY": ("foot", "alacritty", "kitty"),
}
# run in the folder; if what ran failed, keep the window open to say so
_IN_FOLDER = ('cd -- "$1" || { echo "That folder could not be opened. Press Enter to close."; read -r _; exit 1; }; '
              'shift; "$@"; status=$?; '
              'if [ "$status" -ne 0 ]; then printf "\\nClaude Code ended (exit %s). Press Enter to close." "$status"; '
              'read -r _; fi')


class LaunchError(Exception):
    pass


def terminal_argv(command: list[str], env: dict | None = None,
                  which: Callable[[str], str | None] | None = None) -> list[str] | None:
    """`command` as started in a terminal window, or None when no terminal emulator is here."""
    env = os.environ if env is None else env
    which = which or shutil.which
    order = ["xdg-terminal-exec"]
    own = os.path.basename(env.get("TERMINAL", "").strip())
    if own:
        order.append(own)
    for part in env.get("XDG_CURRENT_DESKTOP", "").upper().split(":"):
        order += PREFERRED.get(part, ())
    order += [t for t in TERMINALS if t not in order]
    for name in order:
        if name.startswith(".") or "/" in name or not which(name):
            continue
        before, how = TERMINALS.get(name, (("-e",), "rest"))
        return [name, *before, *(command if how == "rest" else [shlex.join(command)])]
    return None


def in_folder(folder: str, command: list[str]) -> list[str]:
    """`command`, run in `folder` by sh, the window kept open if it fails."""
    return ["/bin/sh", "-c", _IN_FOLDER, "sh", folder, *command]


def open_in_terminal(folder: str, command: list[str], env: dict[str, str],
                     which: Callable[[str], str | None] | None = None,
                     popen: Callable[..., object] | None = None) -> list[str]:
    """Open a terminal window running `command` in `folder`; the argv it was started with."""
    where = Path(os.path.expanduser(folder))
    if not where.is_dir():
        raise LaunchError(f"{folder} isn't a folder here.")
    argv = terminal_argv(in_folder(str(where), command), env, which)
    if argv is None:
        raise LaunchError("No terminal program was found here. Open one yourself and run claude-private in the "
                          "folder.")
    (popen or subprocess.Popen)(argv, cwd=str(where), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
          stderr=subprocess.DEVNULL, start_new_session=True)
    return argv
