"""The editor switch. Claude Code's extension in VSCodium (or VS Code) starts Claude Code through
`claude-private` while its claudeCode.claudeProcessWrapper setting names it, and as usual when it
doesn't. The extension takes that setting only from the user's settings, never a workspace's, so
the switch is for every window of that editor; sessions started after the switch use it.

The settings file is JSON with comments, often edited by hand: only that one entry is changed, in
place, and the rest of the text is kept as it was. The result is checked (every other setting the
same) before it is written, and the file as it was is kept beside it (settings.json.daprivatecoder-backup)."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

KEY = "claudeCode.claudeProcessWrapper"
EDITORS = (
    ("VSCodium", "~/.config/VSCodium/User/settings.json"),
    ("VSCodium (Flatpak)", "~/.var/app/com.vscodium.codium/config/VSCodium/User/settings.json"),
    ("VS Code", "~/.config/Code/User/settings.json"),
)
_SPACE = " \t\r\n"


class EditorError(Exception):
    """The editor's settings can't be changed safely; the message says what to do instead."""


def _mask(text: str, keep_strings: bool = False) -> str:
    """The text with its comments blanked out (newlines kept) and, unless `keep_strings`, the inside
    of every string too: what's left is the structure, each character where it was."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            end = min(j, n - 1)
            token = text[i:end + 1]
            out.append(token if keep_strings or len(token) < 2 else '"' + "x" * (len(token) - 2) + '"')
            i = end + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join("\n" if ch == "\n" else " " for ch in text[i:j]))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def loads(text: str) -> dict:
    """Settings as VS Code reads them: JSON with comments and trailing commas."""
    code = list(_mask(text, keep_strings=True))
    for m in re.finditer(r",(?=\s*[}\]])", _mask(text)):
        code[m.start()] = " "
    try:
        data = json.loads("".join(code).strip() or "{}")
    except ValueError as e:
        raise EditorError(f"isn't valid settings JSON ({e})") from None
    if not isinstance(data, dict):
        raise EditorError("isn't a JSON object")
    return data


def _value_end(s: str, v: int) -> int:
    """Where the value starting at `v` ends (exclusive), in a structure mask."""
    depth, i, n = 0, v, len(s)
    while i < n:
        c = s[i]
        if c == '"':
            j = s.find('"', i + 1)
            i = j + 1 if j >= 0 else n
            if depth == 0:
                return i
            continue
        if c in "{[":
            depth += 1
        elif c in "}]":
            if depth == 0:
                break                      # the object's end: the value ended before it
            depth -= 1
            if depth == 0:
                return i + 1
        elif c == "," and depth == 0:
            break
        i += 1
    while i > v and s[i - 1] in _SPACE:
        i -= 1
    return i


def _members(text: str) -> list[tuple[str, int, int, int]]:
    """The top-level object's entries: (key, where the key starts, where its value starts and ends)."""
    s = _mask(text)
    start = s.find("{")
    out: list[tuple[str, int, int, int]] = []
    if start < 0:
        return out
    depth, i, n = 1, start + 1, len(s)
    while i < n and depth > 0:
        c = s[i]
        if c == '"':
            j = s.find('"', i + 1)
            if j < 0:
                break
            if depth == 1:
                k = j + 1
                while k < n and s[k] in _SPACE:
                    k += 1
                if k < n and s[k] == ":":
                    v = k + 1
                    while v < n and s[v] in _SPACE:
                        v += 1
                    end = _value_end(s, v)
                    out.append((json.loads(text[i:j + 1]), i, v, end))
                    i = max(end, v + 1)
                    continue
            i = j + 1
            continue
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        i += 1
    return out


def set_value(text: str, key: str, value) -> str:
    """The settings text with `key` set to `value`: replaced where it is, or added at the end."""
    rendered = json.dumps(value)
    for k, _, vs, ve in _members(text):
        if k == key:
            return text[:vs] + rendered + text[ve:]
    pair = f"{json.dumps(key)}: {rendered}"
    if not text.strip():
        return "{\n    " + pair + "\n}\n"
    s = _mask(text)
    close = s.rfind("}")
    if close < 0 or s.find("{") < 0:
        raise EditorError("isn't a JSON object")
    p = close - 1
    while p >= 0 and s[p] in _SPACE:
        p -= 1
    joint = "\n    " if s[p] in "{," else ",\n    "
    return text[:p + 1] + joint + pair + text[p + 1:]


def remove_key(text: str, key: str) -> str:
    """The settings text without `key` (as it was if it isn't there)."""
    for k, ks, _, ve in _members(text):
        if k != key:
            continue
        s = _mask(text)
        start, end = ks, ve
        j = end
        while j < len(s) and s[j] in _SPACE:
            j += 1
        if j < len(s) and s[j] == ",":
            end = j + 1                    # with the comma after it
        else:
            i = start - 1
            while i >= 0 and s[i] in _SPACE:
                i -= 1
            if i >= 0 and s[i] == ",":
                start = i                  # or, the last entry, the comma before it
        line_start = text.rfind("\n", 0, start) + 1
        eol = text.find("\n", end)
        eol = len(text) if eol < 0 else eol
        if not text[line_start:start].strip() and not s[end:eol].strip():
            start, end = line_start, min(eol + 1, len(text))     # the whole line it was on
        return text[:start] + text[end:]
    return text


def editors() -> list[tuple[str, Path]]:
    """The editors installed here, by their settings folder."""
    return [(name, Path(os.path.expanduser(p))) for name, p in EDITORS if Path(os.path.expanduser(p)).parent.is_dir()]


def wrapper_path() -> str:
    """claude-private, as installed with this DA Private Coder."""
    beside = Path(sys.executable).parent / "claude-private"
    if beside.is_file() and os.access(beside, os.X_OK):
        return str(beside)
    found = shutil.which("claude-private")
    if found:
        return os.path.abspath(found)
    raise EditorError("claude-private isn't installed where the editor could run it; install DA Private Coder first "
                      "(see the README).")


def current(path: Path) -> str | None:
    try:
        return loads(path.read_text()).get(KEY) if path.exists() else None
    except (OSError, EditorError):
        return None


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def switch(path: Path, wrapper: str | None) -> bool:
    """Name `wrapper` as the extension's process wrapper in the settings at `path` (None: name none).
    Whether the file changed."""
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError as e:
        raise EditorError(f"{path} couldn't be read: {e}") from None
    try:
        before = loads(text)
    except EditorError as e:
        raise EditorError(f"{path} {e}, so it wasn't changed. Set {KEY} in the editor's Settings instead.") from None
    new = set_value(text, KEY, wrapper) if wrapper is not None else remove_key(text, KEY)
    if new == text:
        return False
    try:
        after = loads(new)
    except EditorError:
        after = None
    want = dict(before)
    if wrapper is None:
        want.pop(KEY, None)
    else:
        want[KEY] = wrapper
    if after != want:
        raise EditorError(f"Editing {path} in place didn't give the settings it should, so it wasn't changed. "
                          f"Set {KEY} in the editor's Settings instead.")
    if text:
        backup = path.with_name(path.name + ".daprivatecoder-backup")
        backup.write_text(text, encoding="utf-8")
    _write(path, new)
    return True
