"""The DA apps' shared TEE and Private Mode code is one copy (the cross-app security standard, Nov
2026): llm/tee.py, private_mode.py, dcap.py, nras.py and ethsig.py are DA Toolkit's, sdk_guard.py
SealedLore's (which DA Toolkit's is), line for line but their imports and the app's name. Drift is a
failing test here, not a finding in a review: a change is made in DA Toolkit's copy first, then copied.
Skipped where DA Toolkit's repository isn't beside this one."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1] / "src" / "daprivatecoder" / "llm"
CANONICAL = Path(__file__).resolve().parents[1].parent / "DAToolkit" / "src" / "datoolkit" / "llm"
SHARED = ("tee.py", "private_mode.py", "dcap.py", "nras.py", "ethsig.py", "sdk_guard.py", "errors.py", "privacy.py",
          "tinfoil_direct.py", "chutes_e2ee.py", "chutes_client.py")
_PACKAGES = re.compile(r"\b(datoolkit|davibemanager|daprivatecoder)\b")
_NAMES = re.compile(r"DA Toolkit|DA Vibe Manager|DA Private Coder")


def normalised(text: str) -> str:
    """`text` without its top-level import statements (a parenthesised one with its continuation lines),
    and with the packages' and the apps' names made one: what the copies may differ in."""
    out: list[str] = []
    open_parens = 0
    for line in text.splitlines():
        if open_parens:
            open_parens += line.count("(") - line.count(")")
            continue
        if line.startswith(("import ", "from ")):
            open_parens = line.count("(") - line.count(")")
            continue
        out.append(_NAMES.sub("APPNAME", _PACKAGES.sub("APP", line)))
    return "\n".join(out)


@pytest.mark.skipif(not CANONICAL.is_dir(), reason="DA Toolkit's repository isn't beside this one")
@pytest.mark.parametrize("name", SHARED)
def test_the_shared_code_is_da_toolkits_copy_line_for_line(name):
    ours = normalised((HERE / name).read_text(encoding="utf-8")).splitlines()
    theirs = normalised((CANONICAL / name).read_text(encoding="utf-8")).splitlines()
    if ours != theirs:
        import difflib
        diff = "\n".join(list(difflib.unified_diff(theirs, ours, "DA Toolkit", "DA Private Coder", lineterm=""))[:60])
        pytest.fail(f"llm/{name} has drifted from DA Toolkit's copy: change it there first, then copy it\n{diff}")


def test_normalising_leaves_out_only_imports_and_names():
    text = ("from __future__ import annotations\nfrom .x import (\n    a,\n    b,\n)\nimport httpx\n\n"
            "X = 'DA Toolkit'  # datoolkit\n    from .y import z\n")
    assert normalised(text) == "\nX = 'APPNAME'  # APP\n    from .y import z"
