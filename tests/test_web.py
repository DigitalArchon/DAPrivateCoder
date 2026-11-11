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


def sent_chip() -> str:
    """How a request left this computer, as Usage and Running now mark it (sentChip): [colour, words, tooltip]."""
    return script("TEE_CLEAR", "REPLY", "TEE_UNPROVEN", "SENT", "sentChip") + "\nconst test = (r) => sentChip(r);"


def test_a_tee_replys_mark_says_what_is_the_gateways_word_and_that_it_passed_in_the_clear():
    """SealedLore's marks (Nov 11 2026): for a model RedPill serves, the attested enclave is its gateway, and the
    machine that ran the model is the gateway's word; every mark short of end-to-end encryption says the text
    passed NanoGPT in the clear. "Verified" read as though the model itself had been checked."""
    cases = [{"sent": "tee", "reply": "verified"}, {"sent": "tee", "reply": "signed"},
             {"sent": "tee", "reply": "unchecked",
              "reply_detail": "the reply came with no receipt to say the model's enclave answered"},
             {"sent": "tee", "reply": "failed"}, {"sent": "tee", "reply": "checking"}]
    verified, signed, unchecked, failed, checking = run(sent_chip(), cases)
    assert verified[:2] == ["tee", "TEE, reply verified"]
    assert "that enclave is RedPill's gateway" in verified[2] and "which is the gateway's word, not checked" in verified[2]
    assert signed[1] == "TEE, reply signed" and "the machine running the model is the gateway's word" in signed[2]
    assert unchecked[:2] == ["warn", "TEE ⚠ reply not verified"] and "as Chutes doesn't" in unchecked[2]
    assert "a model may be served elsewhere when its provider is busy" in unchecked[2]
    assert unchecked[2].endswith("(the reply came with no receipt to say the model's enclave answered)")
    for tip in (verified[2], signed[2], unchecked[2], checking[2]):
        assert "passed NanoGPT's servers in the clear: a private model is end-to-end encrypted instead" in tip
    assert failed[0] == "bad" and "its receipt doesn't hold" in failed[2] and "attested again" in failed[2]


def test_a_tee_requests_mark_follows_its_reply():
    """A request was marked "TEE" (to an enclave) and Running now said it went "to a verified enclave", whatever its
    reply's check showed. NanoGPT routes each request: only a reply whose record the attested key signed shows the
    attested enclave received it (SealedLore's, Nov 11 2026), and one with no reply to check is said not to be shown."""
    requests = [{"sent": "tee"}, {"sent": "tee", "reply": ""}, {"sent": "clear"}, {"sent": "sealed"}]
    none, empty, clear, sealed = run(sent_chip(), requests)
    for chip in (none, empty):
        assert chip[:2] == ["warn", "TEE ⚠ not shown to reach the enclave"]
        assert chip[2].startswith("Not shown to reach the attested enclave: sent to the TEE model while its attestation held")
        assert "in the clear" in chip[2]
    assert clear[:2] == ["warn", "clear"] and sealed[:2] == ["private", "sealed"]

    went = script("whereItWent") + "\nconst test = (last) => whereItWent(last);"
    lasts = [{"sent": "tee", "reply": "verified"}, {"sent": "tee", "reply": "signed", "attested": "gateway"},
             {"sent": "tee", "reply": "verified", "attested": "gateway"}, {"sent": "tee", "reply": "checking"},
             {"sent": "tee", "reply": "unchecked"}, {"sent": "tee", "reply": "failed"}, {"sent": "tee"},
             # a model Chutes serves, whose instances sign no replies: verified, its reply was the gateway's (live,
             # TEE/deepseek-v3.2, Nov 11 2026)
             {"sent": "tee", "reply": "verified", "attested": "instances"},
             {"sent": "tee", "reply": "unchecked", "attested": "instances"}]
    said = run(went, lasts)
    assert said[0] == "received by the attested TEE, through NanoGPT (which can read it)"
    assert said[1] == said[2] == said[7] == "received by the attested TEE gateway, through NanoGPT (which can read it)"
    assert "received" not in said[3] and said[3].endswith("its reply is being checked")
    assert set(said[4:7] + said[8:]) == {"⚠ not shown to reach the attested enclave; sent through NanoGPT (which can read it)"}


def test_a_tee_reply_says_who_ran_its_model_beside_its_mark_when_settings_ask():
    """Enclave checks' switch (SealedLore's, Nov 11 2026): the provider RedPill's record names goes beside a mark whose
    receipt the name rests on, "TEE, reply verified · run by Phala", and what that means in its tooltip; never beside
    a failed check, and a lookup that named nobody says why in the tooltip alone."""
    phala = {"name": "phala-244", "label": "Phala", "words": "Run by Phala: RedPill's record of its gateway's session …"}
    nobody = {"name": "", "label": "", "words": "The provider that ran it wasn't named: api.redpill.ai answered HTTP 503."}
    cases = [{"sent": "tee", "reply": "verified", "reply_upstream": phala},
             {"sent": "tee", "reply": "signed", "reply_upstream": {**phala, "label": "Tinfoil"}},
             {"sent": "tee", "reply": "failed", "reply_upstream": phala},
             {"sent": "tee", "reply": "verified", "reply_upstream": nobody}, {"sent": "tee", "reply": "verified"}]
    verified, signed, failed, unnamed, plain = run(sent_chip(), cases)
    assert verified[1] == "TEE, reply verified · run by Phala" and verified[2].endswith("\n\n" + phala["words"])
    assert signed[1] == "TEE, reply signed · run by Tinfoil"
    assert failed[1] == "TEE ⚠ reply check failed" and phala["words"] not in failed[2]
    assert unnamed[1] == "TEE, reply verified" and unnamed[2].endswith("\n\n" + nobody["words"])
    assert plain[1] == "TEE, reply verified" and "\n\n" not in plain[2]


def test_naming_a_tee_replys_provider_is_a_switch_with_its_note_beside_it():
    """The author, Nov 11 2026: "Port it, off by default, include the warning to the user about what it does". The
    note is the settings file's (config.TEE_UPSTREAMS_NOTE), sent with the window's state."""
    text = APP_JS.read_text(encoding="utf-8")
    switch = text[text.index("Name the provider behind each TEE reply (config.py"):]
    switch = switch[:switch.index(");\n}")]
    assert 'checked: Boolean(st.config.tee_name_upstreams)' in switch and 'act("POST", "/api/tee-names"' in switch
    assert '"Name the provider behind each TEE reply"' in switch and "st.tee_upstreams_note" in switch
