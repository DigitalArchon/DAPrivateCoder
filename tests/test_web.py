"""The window's own script (web/app.js), where it decides something on its own: its pure functions run
here in node, as the browser runs them (DA Vibe Manager's tests/test_web.py does the same with its own).
Skipped where there is no node."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parents[1] / "src" / "daprivatecoder" / "web" / "app.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="no node here to run the window's script")


def script(*names: str) -> str:
    """The constants and functions named, as app.js has them."""
    text = APP_JS.read_text(encoding="utf-8")
    parts = []
    for name in names:
        if m := re.search(rf"^const {name} = .*;$", text, re.M):
            parts.append(m.group(0))
            continue
        # one written over several lines: an object or array closed on a line of its own
        if m := re.search(rf"^const {name} = [{{\[]$.*?^[}}\]];$", text, re.M | re.S):
            parts.append(m.group(0))
            continue
        m = re.search(rf"^function {name}\(.*?^}}$", text, re.M | re.S)
        assert m, f"{name} isn't in app.js"
        parts.append(m.group(0))
    return "\n".join(parts)


def run(code: str, cases: list):
    """`test` (which `code` defines) of each case, as node gives it."""
    js = code + f"\nconst CASES = {json.dumps(cases)};\nconsole.log(JSON.stringify(CASES.map(test)));"
    done = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_tee_attestation_is_named_by_what_was_attested():
    """Enclave checks' chip, and the toast after Check: "TEE gateway attested" for RedPill's gateway, as the window's
    server names it (proxy.attested_as); one logged before Nov 11 2026 says only how far it went."""
    code = script("attestedAs") + "\nconst test = (t) => attestedAs(t);"
    assert run(code, [{"attested_as": "TEE gateway attested, only in part", "level": "partial"},
                      {"level": "full"}, {"level": "partial"}]) == [
        "TEE gateway attested, only in part", "TEE attested", "TEE attested, only in part"]
