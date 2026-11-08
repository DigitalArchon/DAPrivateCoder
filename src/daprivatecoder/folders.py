"""Which folders' Claude Code runs on DA Private Coder in the editor, and on which kind of session.

The editor's extension starts Claude Code through one program for every one of its windows (its
process wrapper, the switch: commands.editor_wrapper), in the window's folder. That program runs
each session as its folder says: a folder chosen here (or one inside it, the nearest chosen
counting), on that folder's profile; any other folder as `other` says: Claude as usual (""), or a
profile. So some windows run on Anthropic's models as usual while others run private.

Kept light: the launcher reads it for every session the editor starts."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import config_dir


class FoldersError(Exception):
    """The folders' file can't be read: nothing starts on a guess."""


@dataclass(frozen=True)
class Folders:
    folders: dict[str, str] = field(default_factory=dict)   # folder (absolute) -> the profile it runs on
    other: str = ""                                         # every other folder: "" (Claude as usual) or a profile


def path() -> Path:
    return config_dir() / "editor-folders.json"


def norm(folder: str) -> str:
    return os.path.realpath(os.path.expanduser(folder))


def load() -> Folders:
    try:
        text = path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return Folders()
    except OSError as e:
        raise FoldersError(f"{path()} couldn't be read: {e}") from None
    try:
        data = json.loads(text)
        chosen = data.get("folders") or {}
        other = data.get("other") or ""
        if not (isinstance(chosen, dict) and isinstance(other, str)
                and all(isinstance(k, str) and isinstance(v, str) and v for k, v in chosen.items())):
            raise ValueError("not as written")
    except (ValueError, AttributeError) as e:
        raise FoldersError(f"{path()} isn't as DA Private Coder writes it ({e}): fix or delete it.") from None
    return Folders(dict(chosen), other)


def save(f: Folders) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=p.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump({"folders": dict(sorted(f.folders.items())), "other": f.other}, out, indent=2)
            out.write("\n")
        os.replace(tmp, p)
    except BaseException:
        os.unlink(tmp)
        raise


def choice(f: Folders, cwd: str) -> tuple[str, str]:
    """(the profile a session started in `cwd` runs on, "" for Claude as usual; the chosen folder that
    says so, "" for `other`)."""
    here = norm(cwd)
    best = ""
    for folder in f.folders:
        if (here == folder or here.startswith(folder.rstrip(os.sep) + os.sep)) and len(folder) > len(best):
            best = folder
    return (f.folders[best], best) if best else (f.other, "")


def with_folder(f: Folders, folder: str, profile: str) -> Folders:
    """`f` with `folder` on `profile` ("": not chosen any more)."""
    chosen = dict(f.folders)
    chosen.pop(norm(folder), None)
    if profile:
        chosen[norm(folder)] = profile
    return Folders(chosen, f.other)


def any_on(f: Folders) -> bool:
    """Whether any session would run on DA Private Coder: the switch is worth having on."""
    return bool(f.other or f.folders)
