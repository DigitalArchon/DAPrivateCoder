"""TEE verification against fixed vectors, mocked servers and one real captured attestation.

Ported from SealedLore (its tests/test_tee.py), with the same fixture (tests/data/tee_attestation.json)."""

from __future__ import annotations

import asyncio
import base64
import json
import struct
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from daprivatecoder.llm import dcap, nras
from daprivatecoder.llm.ethsig import (
    address_of,
    keccak256,
    personal_hash,
    recover,
    recover_personal,
    sign,
)
from daprivatecoder.llm.secure_http import ProviderError
from daprivatecoder.llm.tee import (
    MODEL_ENCLAVE_UNCHECKED,
    NRAS_URL,
    RECEIPT_KEYS_LOOSE,
    TeeClient,
    TeeRefused,
    is_tee,
)

KEY = 0xB25C7DB31FEED9122727BF0939DC769A96564B2DE4C4726D035B36ECF1E5B364
ADDRESS = "0x5ce9454909639D2D17A3F753ce7d93fa0b9aB12E"


def test_keccak_and_addresses_match_known_vectors():
    assert keccak256(b"").hex() == (
        "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    )
    assert keccak256(b"abc").hex() == (
        "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"
    )
    assert address_of(1) == "0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf"
    assert address_of(KEY) == ADDRESS


def test_a_signature_recovers_its_signer_and_only_its_signer():
    digest = personal_hash(b"req:resp")
    signature = sign(digest, KEY, 987654321)
    assert recover(digest, signature) == ADDRESS
    assert recover_personal("req:resp", signature.hex()) == ADDRESS
    assert recover(personal_hash(b"tampered"), signature) != ADDRESS


def jwt(claims: dict) -> str:
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'ES384'})}.{part(claims)}.c2ln"


def fake_quote(report_data: bytes) -> str:
    """A version 4 TDX quote with every field in place and nothing signed: enough
    for parse_quote, and for tests that stand in for Intel's verification."""
    header = (
        struct.pack("<HHI", 4, dcap.ECDSA_P256, dcap.TDX_TEE_TYPE)
        + bytes(4)
        + dcap.INTEL_QE_VENDOR_ID
        + bytes(20)
    )
    body = bytes(520) + report_data
    inner = struct.pack("<HI", dcap.PCK_CHAIN, 0)
    cert = bytes(dcap.QE_REPORT_LEN) + bytes(64) + struct.pack("<H", 0) + inner
    signature_data = bytes(128) + struct.pack("<HI", dcap.QE_REPORT_DATA, len(cert)) + cert
    return (header + body + struct.pack("<I", len(signature_data)) + signature_data).hex()


UP_TO_DATE = dcap.Result(
    status="UpToDate", platform="UpToDate", qe="UpToDate", notes=["Intel's chain verifies"]
)


def tee_server(
    *,
    nonce_bound: bool = True,
    echo_nonce: bool = False,
    gpu: bool = True,
    signer: int = KEY,
    evidence: bool = True,
    attestation_status: int = 200,
    nras_status: int = 200,
):
    address = address_of(KEY).lower()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(NRAS_URL):
            if nras_status != 200:
                return httpx.Response(nras_status, text="NRAS says no")
            return httpx.Response(
                200, json=[["JWT", jwt({"x-nvidia-overall-att-result": gpu})], {}]
            )
        if "/tee/attestation" in url:
            if attestation_status != 200:
                return httpx.Response(attestation_status, text="no attestation")
            nonce = request.url.params["nonce"]
            report = (
                bytes.fromhex(address[2:])
                + bytes(12)
                + (bytes.fromhex(nonce) if nonce_bound else bytes(32))
            )
            return httpx.Response(
                200,
                json={
                    "signing_address": address,
                    # nano-gpt's JSON copy: never believed over the quote's bytes.
                    "attestation": {"report_data": report.hex()},
                    "nonce": nonce if echo_nonce else None,
                    "intel_quote": fake_quote(report),
                    "nvidia_payload": json.dumps(
                        {"evidence_list": [{"evidence": "e"}] if evidence else []}
                    ),
                },
            )
        if "/tee/signature/" in url:
            text = "aa:bb"
            signature = sign(personal_hash(text.encode()), signer, 12345).hex()
            return httpx.Response(
                200, json={"text": text, "signature": signature, "signing_address": address}
            )
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def client_for(*, intel=UP_TO_DATE, nvidia=8, **server) -> TeeClient:
    """A TeeClient on the fake server, Intel's and NVIDIA's checks stood in for
    (a result to return, or an exception to raise); the real ones are checked
    against real evidence below."""

    def intel_verify(raw, session):
        if isinstance(intel, BaseException):
            raise intel
        return intel

    def nvidia_verify(answer, nonce, session):
        if isinstance(nvidia, BaseException):
            raise nvidia
        return nvidia

    return TeeClient(
        "https://nano-gpt.com/api/v1",
        "k",
        "TEE/x",
        client=tee_server(**server),
        intel=intel_verify,
        nvidia=nvidia_verify,
    )


def besides_receipts(result) -> list[str]:
    """The shortfalls other than the receipt keys, which nano-gpt's form always has."""
    return [what for what in result.shortfalls if what != RECEIPT_KEYS_LOOSE]


def test_a_bound_quote_intel_and_nvidia_vouch_for_is_attested_its_receipt_keys_named():
    # The quote binds the signing key, as nano-gpt serves it (Nov 2026): the receipts' keys aren't in
    # it, which is said (SealedLore's: warn wherever it falls short).
    client = client_for()
    result = client.attest()
    assert result.verified and result.shortfalls == [RECEIPT_KEYS_LOOSE]
    assert (result.gpus, result.tcb_status, result.gpu_ok) == (8, "UpToDate", True)
    assert result.signing_address == address_of(KEY).lower()
    assert "binds the signing key to our nonce" in result.detail
    assert "vouches for 8 GPUs" in result.detail
    assert client.verify_reply("req_1") is True


def test_a_nonce_merely_echoed_back_is_refused():
    """The old fallback: a nonce in nano-gpt's JSON proves nothing."""
    with pytest.raises(TeeRefused, match="didn't bind"):
        client_for(nonce_bound=False, echo_nonce=True).attest()


def test_a_quote_intel_does_not_vouch_for_is_refused():
    with pytest.raises(TeeRefused, match="failed verification: .*revoked"):
        client_for(intel=dcap.DcapError("the PCK certificate has been revoked")).attest()
    with pytest.raises(TeeRefused, match="debug TD"):
        client_for(intel=dcap.DcapError("it is a debug TD")).attest()


def test_what_couldnt_be_checked_or_isnt_current_is_partial_and_named():
    stale = dcap.Result(status="OutOfDate", platform="OutOfDate", qe="UpToDate")
    assert besides_receipts(client_for(intel=stale).attest()) == ["Intel TCB OutOfDate"]
    unchecked = client_for(intel=dcap.Result(collateral=False)).attest()
    assert unchecked.level == "partial" and besides_receipts(unchecked) == ["Intel TCB unchecked"]
    no_gpu = client_for(evidence=False).attest()
    assert no_gpu.level == "partial" and no_gpu.gpu_ok is None
    assert besides_receipts(no_gpu) == ["no GPU evidence"]


def test_nvidia_unreachable_is_partial_and_nvidia_rejecting_is_refused():
    down = client_for(nras_status=503).attest()
    assert down.level == "partial" and besides_receipts(down) == ["GPUs unverified"]
    assert "couldn't be reached" in down.detail
    keys = client_for(nvidia=nras.KeysUnavailable("keys could not be fetched")).attest()
    assert besides_receipts(keys) == ["NVIDIA signature unchecked"]
    with pytest.raises(TeeRefused, match="NVIDIA rejected"):
        client_for(nras_status=400).attest()
    with pytest.raises(TeeRefused, match="NVIDIA rejected"):
        client_for(gpu=False, nvidia=nras.KeysUnavailable("no keys")).attest()
    with pytest.raises(TeeRefused, match="GPU attestation failed: .*nonce"):
        client_for(nvidia=nras.NrasError("not made for this request's nonce")).attest()


def test_no_attestation_is_refused():
    with pytest.raises(TeeRefused, match="offers no attestation"):
        client_for(attestation_status=400).attest()


def test_a_reply_signed_by_another_key_fails():
    client = client_for(signer=1)
    client.attest()
    assert client.verify_reply("req_1") is False


# --- real evidence: Intel's and NVIDIA's own signatures ---------------------------------

FIXTURE = Path(__file__).parent / "data" / "tee_attestation.json"


def fixture_server(fixture: dict, *, intel_status: int = 200, jwks: dict | None = None, extra: dict | None = None):
    """One real attestation, captured live from TEE/glm-5.3-flash with Intel's
    collateral and NVIDIA's answer and keys exactly as they came (in the author's
    AI-Assistant app, Sept 26 2026; the same nano-gpt endpoint)."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url.copy_with(query=None))
        params = dict(request.url.params)
        if "/tee/attestation" in url:
            return httpx.Response(
                200,
                json={
                    "signing_address": fixture["signing_address"],
                    "intel_quote": fixture["intel_quote"],
                    "nvidia_payload": json.dumps(
                        {"nonce": fixture["nonce"], "evidence_list": [{"evidence": "e"}]}
                    ),
                    **(extra or {}),
                },
            )
        if url == NRAS_URL:
            return httpx.Response(200, json=fixture["nras_answer"])
        if url == nras.JWKS_URL:
            return httpx.Response(200, json=fixture["jwks"] if jwks is None else jwks)
        for item in fixture["collateral"]:
            if item["url"] == url and (item["params"] or {}) == params:
                if intel_status != 200:
                    return httpx.Response(intel_status)
                return httpx.Response(
                    item["status"],
                    content=base64.b64decode(item["body"]),
                    headers=item["headers"],
                )
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def real():
    fixture = json.loads(FIXTURE.read_text())
    dcap._cache.clear()
    nras._keys.update(at=0.0, keys={})
    yield fixture
    dcap._cache.clear()
    nras._keys.update(at=0.0, keys={})


def at(fixture: dict) -> datetime:
    return datetime.fromtimestamp(fixture["now"], UTC)


def intel(fixture: dict, raw: bytes | None = None, **server) -> dcap.Result:
    quote = raw or bytes.fromhex(fixture["intel_quote"])
    return dcap.verify(quote, fixture_server(fixture, **server), now=at(fixture))


def nvidia(fixture: dict, answer=None, nonce=None, now=None, **server) -> int:
    return nras.verify(
        answer or fixture["nras_answer"],
        nonce or fixture["nonce"],
        fixture_server(fixture, **server),
        now=now or fixture["now"],
    )


def test_the_quote_verifies_up_to_intels_root_and_is_current(real):
    result = intel(real)
    assert result.collateral and result.status == "UpToDate"
    assert (result.platform, result.module, result.qe) == ("UpToDate",) * 3
    assert result.fmspc == "20a06f000000"


def test_a_changed_report_breaks_the_quotes_signature(real):
    tampered = bytearray(bytes.fromhex(real["intel_quote"]))
    tampered[dcap.HEADER_LEN + 520] ^= 1  # the report data: the signing key
    with pytest.raises(dcap.DcapError, match="quote's signature"):
        intel(real, bytes(tampered))


def test_a_debug_td_is_refused(real):
    debug = bytearray(bytes.fromhex(real["intel_quote"]))
    debug[dcap.HEADER_LEN + 120] |= 0x01  # TDATTRIBUTES.DEBUG
    with pytest.raises(dcap.DcapError, match="debug TD"):
        intel(real, bytes(debug))


def test_a_chain_to_any_other_root_is_refused(real, monkeypatch):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name(
        [
            x509.NameAttribute(x509.NameOID.COMMON_NAME, "Intel SGX Root CA"),
            x509.NameAttribute(x509.NameOID.ORGANIZATION_NAME, "Intel Corporation"),
            x509.NameAttribute(x509.NameOID.LOCALITY_NAME, "Santa Clara"),
            x509.NameAttribute(x509.NameOID.STATE_OR_PROVINCE_NAME, "CA"),
            x509.NameAttribute(x509.NameOID.COUNTRY_NAME, "US"),
        ]
    )
    # Intel's root in every respect but its key, extensions included.
    impostor = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(datetime(2018, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2049, 1, 1, tzinfo=UTC))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .add_extension(key_usage(cert_sign=True), critical=True)
        .sign(key, hashes.SHA256())
    )
    monkeypatch.setattr(dcap, "intel_root", lambda: impostor)
    with pytest.raises(dcap.DcapError, match="signature does not verify"):
        intel(real)


def test_a_revocation_list_intel_did_not_sign_is_refused(real):
    forged = json.loads(json.dumps(real))
    crl = bytearray(base64.b64decode(forged["collateral"][0]["body"]))
    crl[-5] ^= 1  # inside the CRL's signature
    forged["collateral"][0]["body"] = base64.b64encode(bytes(crl)).decode()
    with pytest.raises(dcap.DcapError, match="CRL is not signed"):
        intel(forged)


def test_tcb_information_intel_did_not_sign_is_refused(real):
    forged = json.loads(json.dumps(real))
    body = base64.b64decode(forged["collateral"][2]["body"]).decode()
    body = body.replace('"tcbEvaluationDataNumber":', '"tcbEvaluationDataNumber":1', 1)
    forged["collateral"][2]["body"] = base64.b64encode(body.encode()).decode()
    with pytest.raises(dcap.DcapError, match="signature on its tcbInfo"):
        intel(forged)


def test_intels_collateral_out_of_reach_is_reported_not_passed(real):
    result = intel(real, intel_status=503)
    assert not result.collateral and result.status == ""
    assert "could not be fetched" in result.notes[-1]


def test_a_revoked_tcb_is_refused_whatever_else_is_named(real, monkeypatch):
    """Only the worst status was compared with Revoked, and one this app doesn't list ranks above it:
    a platform Intel had revoked passed as partial beside it. (DA Assistant's test, Nov 2026.)"""
    for platform, module, qe in (("Revoked", "TDRelaunchAdvised", "UpToDate"),
                                 ("UpToDate", "Revoked", "SomethingNew"),
                                 ("SomethingNew", "UpToDate", "Revoked")):
        dcap._cache.clear()
        monkeypatch.setattr(dcap, "platform_level", lambda *_a, status=platform: {"tcbStatus": status})
        monkeypatch.setattr(dcap, "module_status", lambda *_a, status=module: status)
        monkeypatch.setattr(dcap, "qe_status", lambda *_a, status=qe: status)
        with pytest.raises(dcap.DcapError, match="revoked this platform"):
            intel(real)
    monkeypatch.undo()
    dcap._cache.clear()
    monkeypatch.setattr(dcap, "module_status", lambda *_a: "TDRelaunchAdvised")
    assert intel(real).status == "TDRelaunchAdvised"            # partial, and named


def test_the_td_qe_identity_pinned_is_the_one_intel_signed(real):
    item = next(c for c in real["collateral"] if c["url"] == dcap.QE_IDENTITY_URL)
    signed = json.loads(base64.b64decode(item["body"]))["enclaveIdentity"]
    assert dcap.INTEL_TD_QE == {key: signed[key] for key in dcap.INTEL_TD_QE}


# --- Intel's chain held to the CA rules (DA Assistant's tests, Nov 2026) ------------------------
# verify_chain held a certificate to its issuer's name, signature and dates, and no more: any
# certificate Intel's root vouched for (the TCB signing one, a PCK certificate whose key a compromised
# platform gave up) could issue certificates of its own that passed as Intel's. Synthetic chains, under
# a root of their own; the real one is checked above.

CHAIN_NOW = datetime(2026, 9, 26, tzinfo=UTC)


def key_usage(cert_sign: bool):
    """keyUsage as Intel's certificates have it: a CA's key signs certificates and CRLs, any other's
    signs data."""
    from cryptography import x509

    return x509.KeyUsage(digital_signature=not cert_sign, content_commitment=not cert_sign,
                         key_encipherment=False, data_encipherment=False, key_agreement=False,
                         key_cert_sign=cert_sign, crl_sign=cert_sign, encipher_only=False, decipher_only=False)


def synthetic_cert(name, key, issuer=None, ca=None, path_length=None, cert_sign=None):
    """A certificate named `name` for `key`, signed by `issuer` ((its certificate, its key)) or by
    itself; with basicConstraints if `ca` is given, and keyUsage if `cert_sign` is."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes

    subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, name)])
    issuer_cert, issuer_key = issuer or (None, key)
    builder = (x509.CertificateBuilder().subject_name(subject)
               .issuer_name(issuer_cert.subject if issuer_cert else subject)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(datetime(2018, 1, 1, tzinfo=UTC))
               .not_valid_after(datetime(2049, 1, 1, tzinfo=UTC)))
    if ca is not None:
        builder = builder.add_extension(x509.BasicConstraints(ca=ca, path_length=path_length), critical=True)
    if cert_sign is not None:
        builder = builder.add_extension(key_usage(cert_sign), critical=True)
    return builder.sign(issuer_key, hashes.SHA256())


@pytest.fixture(scope="module")
def chain_keys() -> dict:
    from cryptography.hazmat.primitives.asymmetric import ec

    return {name: ec.generate_private_key(ec.SECP256R1()) for name in ("root", "ca", "leaf")}


def _root(keys, path_length=1):
    return synthetic_cert("Test Root CA", keys["root"], ca=True, path_length=path_length, cert_sign=True)


def _under(keys, issuer, issuer_key, name, key, **extensions):
    return synthetic_cert(name, keys[key], (issuer, keys[issuer_key]), **extensions)


def test_a_chain_of_authorities_allowed_to_sign_verifies(chain_keys):
    root = _root(chain_keys)
    ca = _under(chain_keys, root, "root", "Test CA", "ca", ca=True, path_length=0, cert_sign=True)
    leaf = _under(chain_keys, ca, "ca", "Test leaf", "leaf", ca=False, cert_sign=False)
    dcap.verify_chain([leaf, ca], root, CHAIN_NOW)


def test_a_chain_through_a_certificate_that_is_no_ca_is_refused(chain_keys):
    root = _root(chain_keys)
    signing = _under(chain_keys, root, "root", "Intel SGX TCB Signing", "ca", ca=False, cert_sign=False)
    leaf = _under(chain_keys, signing, "ca", "Intel SGX PCK Certificate", "leaf", ca=False, cert_sign=False)
    with pytest.raises(dcap.DcapError, match="CN=Intel SGX TCB Signing signed a certificate, and is not a "
                                             "certificate authority"):
        dcap.verify_chain([leaf, signing], root, CHAIN_NOW)
    bare = _under(chain_keys, root, "root", "Bare", "ca")                 # nor one that doesn't say what it is
    leaf = _under(chain_keys, bare, "ca", "Test leaf", "leaf", ca=False)
    with pytest.raises(dcap.DcapError, match="Bare signed a certificate, and is not a certificate authority"):
        dcap.verify_chain([leaf, bare], root, CHAIN_NOW)


def test_an_authority_beyond_its_path_length_is_refused(chain_keys):
    root = _root(chain_keys, path_length=0)
    ca = _under(chain_keys, root, "root", "Test CA", "ca", ca=True, path_length=0, cert_sign=True)
    leaf = _under(chain_keys, ca, "ca", "Test leaf", "leaf", ca=False, cert_sign=False)
    with pytest.raises(dcap.DcapError, match="Test Root CA may have 0 certificate authorities below it, and the "
                                             "chain puts 1 there"):
        dcap.verify_chain([leaf, ca], root, CHAIN_NOW)


def test_an_authority_whose_key_is_not_for_certificates_is_refused(chain_keys):
    root = _root(chain_keys)
    ca = _under(chain_keys, root, "root", "Test CA", "ca", ca=True, path_length=0, cert_sign=False)
    leaf = _under(chain_keys, ca, "ca", "Test leaf", "leaf", ca=False, cert_sign=False)
    with pytest.raises(dcap.DcapError, match="Test CA signed a certificate with a key not meant for that"):
        dcap.verify_chain([leaf, ca], root, CHAIN_NOW)


def test_tcb_info_signed_by_any_but_the_tcb_signing_certificate_is_refused(chain_keys):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.serialization import Encoding

    def signed_tcb_info(signer_name):
        """Intel's TCB info as its service sends it, signed by a certificate named `signer_name` that the
        root issued."""
        root = _root(chain_keys)
        signer = _under(chain_keys, root, "root", signer_name, "leaf", ca=False, cert_sign=False)
        raw = json.dumps({"id": "TDX", "fmspc": "20a06f000000"}, separators=(",", ":"))
        r, s = decode_dss_signature(chain_keys["leaf"].sign(raw.encode(), ec.ECDSA(hashes.SHA256())))
        content = f'{{"tcbInfo":{raw},"signature":"{(r.to_bytes(32, "big") + s.to_bytes(32, "big")).hex()}"}}'
        chain = signer.public_bytes(Encoding.PEM) + root.public_bytes(Encoding.PEM)
        return dcap._signed_json(content.encode(), "tcbInfo", chain.decode(), root, CHAIN_NOW)

    info, _signer = signed_tcb_info("Intel SGX TCB Signing")
    assert info["fmspc"] == "20a06f000000"
    for name in ("Intel SGX PCK Certificate", "Intel SGX TCB Signing 2"):
        with pytest.raises(dcap.DcapError, match="tcbInfo isn't signed by Intel's TCB signing certificate"):
            signed_tcb_info(name)


# --- a clock that counts sleep (DA Assistant's tests, Nov 2026) ---------------------------------


def test_the_boot_clock_is_read_and_the_monotonic_one_only_without_it(monkeypatch):
    import time

    if not hasattr(time, "CLOCK_BOOTTIME"):
        pytest.skip("no CLOCK_BOOTTIME here")
    real_gettime = time.clock_gettime
    monkeypatch.setattr(time, "clock_gettime",
                        lambda clock: 1000.0 if clock == time.CLOCK_BOOTTIME else real_gettime(clock))
    monkeypatch.setattr(time, "monotonic", lambda: 50.0)
    assert dcap.since_boot() == 1000.0
    monkeypatch.setattr(dcap, "_BOOTTIME", None)
    assert dcap.since_boot() == 50.0
    monkeypatch.setattr(dcap, "_BOOTTIME", time.CLOCK_BOOTTIME)

    def no_boottime(_clock):
        raise OSError("no such clock")
    monkeypatch.setattr(time, "clock_gettime", no_boottime)
    assert dcap.since_boot() == 50.0


def test_nvidias_keys_are_fetched_again_after_a_sleep(suspend, monkeypatch):
    """NVIDIA's keys, kept for an hour, were aged by time.monotonic too."""
    monkeypatch.setattr(nras, "_keys", {"at": 0.0, "keys": {}})
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, json={"keys": [{"kid": "k1"}]})

    session = httpx.Client(transport=httpx.MockTransport(handler))
    for _ in range(2):
        assert "k1" in nras._jwks(session)
    assert asked == [nras.JWKS_URL]
    suspend(2)
    nras._jwks(session)
    assert asked == [nras.JWKS_URL] * 2


def test_intels_collateral_is_fetched_again_after_a_sleep(suspend, monkeypatch):
    """Intel's collateral was aged by time.monotonic, which stops while the machine is suspended: a laptop
    that slept for hours took it for as fresh as when it went to sleep."""
    monkeypatch.setattr(dcap, "_cache", {})
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, content=b"collateral")

    session = httpx.Client(transport=httpx.MockTransport(handler))
    for _ in range(2):
        assert dcap._fetch(session, dcap.ROOT_CRL_URL)[0] == b"collateral"
    assert len(asked) == 1
    suspend(2)
    dcap._fetch(session, dcap.ROOT_CRL_URL)
    assert len(asked) == 2


def test_nvidias_verdict_verifies_for_every_gpu(real):
    assert nvidia(real) == 8


def test_a_verdict_for_another_nonce_is_refused(real):
    with pytest.raises(nras.NrasError, match="nonce"):
        nvidia(real, nonce="00" * 32)


def test_an_old_verdict_is_refused(real):
    with pytest.raises(nras.NrasError, match="validity window"):
        nvidia(real, now=real["now"] + 86400)


def test_edited_claims_break_nvidias_signature(real):
    answer = json.loads(json.dumps(real["nras_answer"]))
    header, claims, signature = answer[0][1].split(".")
    decoded = json.loads(base64.urlsafe_b64decode(claims + "=="))
    decoded["jti"] = "replayed"
    claims = base64.urlsafe_b64encode(json.dumps(decoded).encode()).decode().rstrip("=")
    answer[0][1] = f"{header}.{claims}.{signature}"
    with pytest.raises(nras.NrasError, match="signature does not verify"):
        nvidia(real, answer)


def test_a_gpu_token_swapped_for_another_is_refused(real):
    answer = json.loads(json.dumps(real["nras_answer"]))
    gpus = sorted(answer[1])
    answer[1][gpus[0]] = answer[1][gpus[1]]
    with pytest.raises(nras.NrasError, match="not the one"):
        nvidia(real, answer)


def test_a_key_nvidia_does_not_publish_is_refused(real):
    with pytest.raises(nras.NrasError, match="does not publish"):
        nvidia(real, jwks={"keys": []})


def test_the_whole_real_attestation_holds_with_only_its_receipt_keys_short(real, monkeypatch):
    import daprivatecoder.llm.tee as tee_module

    client = TeeClient(
        "https://nano-gpt.com/api/v1",
        "k",
        real["model"],
        client=fixture_server(real),
        intel=lambda raw, s: dcap.verify(raw, s, now=at(real)),
        nvidia=lambda a, n, s: nras.verify(a, n, s, now=real["now"]),
    )
    monkeypatch.setattr(tee_module.secrets, "token_hex", lambda _n: real["nonce"])
    result = client.attest()
    # Sept 2026's capture has no keyset: no receipts, so nothing binds them either
    assert result.verified and result.shortfalls == [RECEIPT_KEYS_LOOSE]
    assert (result.gpus, result.tcb_status) == (8, "UpToDate")
    assert result.signing_address == real["signing_address"]


def test_the_real_quote_for_another_nonce_is_refused(real):
    """The same genuine quote, replayed against a fresh request."""
    client = TeeClient(
        "https://nano-gpt.com/api/v1",
        "k",
        real["model"],
        client=fixture_server(real),
        intel=lambda raw, s: dcap.verify(raw, s, now=at(real)),
    )
    with pytest.raises(TeeRefused, match="didn't bind"):
        client.attest()  # our nonce is random: the captured quote holds another


def test_tee_models_are_named_so():
    assert is_tee("TEE/glm-5.3-flash")
    assert not is_tee("Steelskull/teething")


# --- per-instance providers (Chutes), relays and outages ---------------------------------


class InstanceEvidence:
    """Chutes' per-instance attestation, built with real keys: each instance's
    certificate key is bound into its quote's report data, and signs a body
    carrying the nonce, the quote and the GPU evidence (TEE/kimi-k3's shape)."""

    def __init__(self, count: int = 2) -> None:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa

        self.keys, self.certs = [], []
        for i in range(count):
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "attestation")])
            cert = (
                x509.CertificateBuilder()
                .subject_name(name)
                .issuer_name(name)
                .public_key(key.public_key())
                .serial_number(i + 1)
                .not_valid_before(datetime(2026, 1, 1, tzinfo=UTC))
                .not_valid_after(datetime(2027, 1, 1, tzinfo=UTC))
                .sign(key, hashes.SHA256())
            )
            self.keys.append(key)
            self.certs.append(cert)
        self.gpu_nonce = "ab" * 32

    def body(
        self,
        nonce: str,
        *,
        sign_with=None,
        bind: bool = True,
        signed_nonce: str | None = None,
        other_quote: bool = False,
        failed: tuple = (),
        quote_key: str = "tdx_quote",
    ) -> dict:
        import hashlib

        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        evidence = []
        for i, (key, cert) in enumerate(zip(self.keys, self.certs, strict=True)):
            spki = cert.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
            bound = hashlib.sha256(spki).digest() if bind else bytes(32)
            quote = bytes.fromhex(fake_quote(bytes.fromhex(self.gpu_nonce) + bound))
            signed_quote = bytes.fromhex(fake_quote(bytes(64))) if other_quote else quote
            gpu = [{"arch": "HOPPER", "evidence": "e", "certificate": "c"}]
            attested = json.dumps(
                {
                    "nonce": signed_nonce or nonce,
                    "evidence": {
                        quote_key: base64.b64encode(signed_quote).decode(),
                        "nvtrust_evidence": json.dumps(gpu),
                    },
                }
            ).encode()
            signature = (sign_with or key).sign(attested, padding.PKCS1v15(), hashes.SHA256())
            evidence.append(
                {
                    "instance_id": f"inst-{i}",
                    "quote": base64.b64encode(quote).decode(),
                    "certificate": base64.b64encode(cert.public_bytes(Encoding.DER)).decode(),
                    "signature": base64.b64encode(signature).decode(),
                    "attested_body": base64.b64encode(attested).decode(),
                }
            )
        return {"evidence": evidence, "failed_instance_ids": list(failed)}


def shaped(respond, *, model: str = "TEE/kimi-k3", seen: dict | None = None) -> TeeClient:
    """A TeeClient whose /tee/attestation answers `respond(nonce) -> (status, body)`."""
    seen = seen if seen is not None else {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(NRAS_URL):
            seen.setdefault("nras_nonces", []).append(json.loads(request.content)["nonce"])
            return httpx.Response(200, json=[["JWT", jwt({"x-nvidia-overall-att-result": True})]])
        if "/tee/attestation" in url:
            status, body = respond(request.url.params["nonce"])
            return httpx.Response(status, json=body)
        if "/tee/signature/" in url:
            return httpx.Response(400, json={"error": "TEE signature is not available"})
        return httpx.Response(404)

    def nvidia_verify(answer, nonce, session):
        seen.setdefault("verified_nonces", []).append(nonce)
        return 8

    return TeeClient(
        "https://nano-gpt.com/api/v1",
        "k",
        model,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        intel=lambda raw, session: UP_TO_DATE,
        nvidia=nvidia_verify,
    )


@pytest.fixture(scope="module")
def instances() -> InstanceEvidence:
    return InstanceEvidence()


def test_every_instance_bound_and_signed_is_fully_attested(instances):
    seen: dict = {}
    client = shaped(lambda nonce: (200, instances.body(nonce)), seen=seen)
    result = client.attest()
    assert result.level == "full" and result.instances == 2 and result.gpus == 16
    assert "signs no replies" in result.detail
    # NVIDIA's verdict is sought for the nonce each quote binds, as measured live.
    assert seen["nras_nonces"] == seen["verified_nonces"] == [instances.gpu_nonce] * 2
    with pytest.raises(ProviderError, match="signs no replies"):
        client.verify_reply("chatcmpl-1")


@pytest.mark.parametrize(
    "shape, refusal",
    [
        ({"signed_nonce": "00" * 32}, "didn't sign our nonce"),
        ({"bind": False}, "quote doesn't bind"),
        ({"other_quote": True}, "signed a different quote"),
        ({"failed": ("inst-9",)}, "1 of 3 .* couldn't be attested"),
    ],
)
def test_an_instance_that_doesnt_hold_refuses_them_all(instances, shape, refusal):
    with pytest.raises(TeeRefused, match=refusal):
        shaped(lambda nonce: (200, instances.body(nonce, **shape))).attest()


def test_the_signed_quote_is_read_under_its_name_now_and_before(instances):
    """Chutes renamed the signed copy of the quote tdx_quote -> quote (Nov 2026): either is read, and
    either must be exactly the quote the instance presented."""
    assert shaped(lambda nonce: (200, instances.body(nonce, quote_key="quote"))).attest().level == "full"
    with pytest.raises(TeeRefused, match="signed a different quote"):
        shaped(lambda nonce: (200, instances.body(nonce, quote_key="quote", other_quote=True))).attest()
    with pytest.raises(TeeRefused, match="signed a different quote"):
        shaped(lambda nonce: (200, instances.body(nonce, quote_key="neither"))).attest()


def test_an_instance_signed_by_another_key_is_refused(instances):
    other = InstanceEvidence(1).keys[0]
    with pytest.raises(TeeRefused, match="didn't sign its evidence"):
        shaped(lambda nonce: (200, instances.body(nonce, sign_with=other))).attest()


def test_an_attestation_relayed_for_the_relays_own_nonce_is_refused():
    body = {
        "attestation_type": "chutes",
        "nonce": "f9" * 32,
        "all_attestations": [{"intel_quote": "", "gpu_evidence": []}],
    }
    with pytest.raises(TeeRefused, match=r"relayed by its provider \(chutes\).*nonce the relay"):
        shaped(lambda nonce: (200, body), model="TEE/glm-5.1").attest()


def test_nano_gpts_reason_is_given_and_tinfoil_models_point_to_their_twin():
    reason = "Fresh nonce-bound attestation is not available for Tinfoil via this endpoint."
    with pytest.raises(TeeRefused) as caught:
        shaped(lambda nonce: (400, {"error": reason}), model="TEE/gemma4-31b").attest()
    assert reason in str(caught.value) and "private/ version" in str(caught.value)


def test_a_provider_outage_says_so():
    with pytest.raises(TeeRefused, match="Unable to reach RedPill.*provider's side.*again"):
        shaped(
            lambda nonce: (502, {"error": "Unable to reach RedPill attestation service"})
        ).attest()


def test_an_unknown_shape_names_what_it_held():
    with pytest.raises(TeeRefused, match="doesn't recognise .*holds novel"):
        shaped(lambda nonce: (200, {"novel": 1})).attest()


def test_intels_collateral_that_arrived_but_cant_be_read_refuses(real):
    """An outage is "partial"; an answer that doesn't parse is not an outage."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not a CRL</html>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(dcap.DcapError, match="could not be read"):
        dcap.verify(bytes.fromhex(real["intel_quote"]), client, now=at(real))


def test_intels_tcb_ratings_count_only_signed_by_its_own_tcb_signing_certificate():
    """Not by any certificate under Intel's root: a platform's PCK certificate (whose key an out-of-date
    platform can give up) would otherwise rate its own platform's TCB."""
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    from daprivatecoder.llm import dcap
    now = datetime.now(UTC)

    def cert(name, key, issuer, issuer_key, ca):
        named = lambda n: x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, n)])  # noqa: E731
        return (x509.CertificateBuilder().subject_name(named(name)).issuer_name(named(issuer))
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
                .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
                .sign(issuer_key, hashes.SHA256()))
    root_key, ca_key, pck_key = (ec.generate_private_key(ec.SECP256R1()) for _ in range(3))
    root = cert("Intel SGX Root CA", root_key, "Intel SGX Root CA", root_key, True)
    ca = cert("Intel SGX PCK Platform CA", ca_key, "Intel SGX Root CA", root_key, True)
    pck = cert("Intel SGX PCK Certificate", pck_key, "Intel SGX PCK Platform CA", ca_key, False)
    raw = '{"id":"TDX","version":3,"tcbLevels":[{"tcbStatus":"UpToDate"}]}'
    r, s = decode_dss_signature(pck_key.sign(raw.encode(), ec.ECDSA(hashes.SHA256())))
    content = ('{"tcbInfo":' + raw + ',"signature":"' + (r.to_bytes(32, "big") + s.to_bytes(32, "big")).hex() + '"}').encode()
    chain = b"".join(c.public_bytes(serialization.Encoding.PEM) for c in (pck, ca, root)).decode()
    with pytest.raises(dcap.DcapError, match="TCB signing certificate"):
        dcap._signed_json(content, "tcbInfo", chain, root, now)


def test_without_nvidias_keys_all_but_its_signature_is_still_checked(real, monkeypatch):
    """NVIDIA's keys can't be fetched: its verdict counts as unchecked (a partial attestation), but evidence
    made for another nonce (replayed) or out of date is refused all the same; and keys fetched before,
    though more than an hour ago, are still NVIDIA's: used when no fresh ones can be had."""
    monkeypatch.setitem(nras._keys, "keys", {})
    with pytest.raises(nras.KeysUnavailable):
        nvidia(real, jwks={"no": "keys"})
    with pytest.raises(nras.NrasError, match="nonce"):
        nvidia(real, jwks={"no": "keys"}, nonce="00" * 32)
    with pytest.raises(nras.NrasError, match="validity window"):
        nvidia(real, jwks={"no": "keys"}, now=real["now"] + 86400)
    assert nvidia(real) == 8                              # fetched now: kept
    monkeypatch.setitem(nras._keys, "at", 0.0)            # long ago
    assert nvidia(real, jwks={"no": "keys"}) == 8


def test_the_quoting_enclave_must_be_intels_whether_or_not_intels_collateral_can_be_had(real, monkeypatch):
    """It's what makes the attestation key the QE's, not any enclave's the platform certified: checked
    against Intel's TD_QE identity even when Intel's collateral is out of reach (or made so)."""
    quote = dcap.parse_quote(bytes.fromhex(real["intel_quote"]))
    dcap.qe_is_intels(dcap.INTEL_TD_QE, quote.qe_report)                  # the real one is Intel's
    other = bytearray(quote.qe_report)
    other[128:160] = b"\x11" * 32                                          # signed by another key
    with pytest.raises(dcap.DcapError, match="not signed by Intel"):
        dcap.qe_is_intels(dcap.INTEL_TD_QE, bytes(other))
    checked = []
    real_check = dcap.qe_is_intels
    monkeypatch.setattr(dcap, "qe_is_intels", lambda identity, report: checked.append(identity) or real_check(identity, report))
    monkeypatch.setattr(dcap, "_cache", {})
    result = intel(real, intel_status=503)
    assert not result.collateral and checked == [dcap.INTEL_TD_QE]


def _real_client(real, monkeypatch, **server):
    import daprivatecoder.llm.tee as tee_module
    monkeypatch.setattr(tee_module.secrets, "token_hex", lambda _n: real["nonce"])
    return TeeClient("https://nano-gpt.com/api/v1", "k", real["model"], client=fixture_server(real, **server),
                     intel=lambda raw, s: dcap.verify(raw, s, now=at(real)),
                     nvidia=lambda a, n, s: nras.verify(a, n, s, now=real["now"]))


REDPILL = {"api_version": "aci/1", "service_capabilities": {"serving": "aggregator"},
           "attestation": {"workload_keyset": {"not_after": 4102444800}}}      # (2100)


def test_redpills_gateway_is_attested_only_in_part_and_its_expired_keys_are_refused(real, monkeypatch):
    """RedPill's gateway passes each request on to the model's host: its quote and the GPUs' evidence hold,
    but not that the model itself runs in an enclave, so it isn't called "full"."""
    result = _real_client(real, monkeypatch, extra=REDPILL).attest()
    assert result.level == "partial" and result.shortfalls == [
        RECEIPT_KEYS_LOOSE, "the model's own enclave unchecked (RedPill's gateway)"]
    assert result.gpus == 8 and result.tcb_status == "UpToDate"
    expired = {**REDPILL, "attestation": {"workload_keyset": {"not_after": 1_700_000_000}}}
    with pytest.raises(TeeRefused, match="attested keys expired"):
        _real_client(real, monkeypatch, extra=expired).attest()


# --- the verifier's client: where a redirect may point (SealedLore's, Nov 2026) ----------------


def redirecting(location: str, base: str = "https://nano-gpt.com/api/v1", follow: bool = False):
    """make_client's client on a server that redirects its first request to `location`, and what it
    was asked."""
    from daprivatecoder.llm.secure_http import make_client

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if len(seen) == 1:
            return httpx.Response(302, headers={"location": location})
        return httpx.Response(200, json={"ok": True})

    return make_client(base, transport=httpx.MockTransport(handler), follow_redirects=follow), seen


def test_a_relative_redirect_is_not_taken_for_an_insecure_one():
    """httpx runs response hooks before it resolves `Location`: read raw, a relative one
    ("/v1/x") had no scheme, and every one was refused as insecure."""
    client, seen = redirecting("/v1/tee/attestation", follow=True)
    assert client.get("https://nano-gpt.com/api/v1/tee/attestation").json() == {"ok": True}
    assert seen == ["https://nano-gpt.com/api/v1/tee/attestation", "https://nano-gpt.com/v1/tee/attestation"]
    client, _ = redirecting("/v1/tee/attestation")                   # not followed, but not refused either
    assert client.get("https://nano-gpt.com/api/v1/tee/attestation").status_code == 302


@pytest.mark.parametrize("location", ["http://nano-gpt.com/api/v1/x", "http://evil.example/x"])
def test_a_redirect_off_https_is_refused(location):
    client, seen = redirecting(location, follow=True)
    with pytest.raises(ProviderError, match="insecure connection"):
        client.get("https://nano-gpt.com/api/v1/tee/attestation")
    assert len(seen) == 1, "the redirect was not followed"


@pytest.mark.parametrize("location", ["https://192.168.1.1/admin", "http://192.168.1.1/admin",
                                      "http://localhost:8080/x", "https://[fd00::1]/x"])
def test_a_server_on_the_internet_cannot_redirect_into_the_local_network(location):
    client, seen = redirecting(location, follow=True)
    with pytest.raises(ProviderError, match="local network"):
        client.get("https://nano-gpt.com/api/v1/tee/attestation")
    assert len(seen) == 1, "the redirect was not followed"


def test_a_local_server_may_redirect_on_the_local_network():
    client, seen = redirecting("http://192.168.1.20:7860/v1/x", base="http://192.168.1.10:7860/v1", follow=True)
    assert client.get("http://192.168.1.10:7860/v1/tee/attestation").json() == {"ok": True}
    assert seen[-1] == "http://192.168.1.20:7860/v1/x"


# --- receipts: a reply's exact bytes and its upstream (Nov 2026; SealedLore's tests) ---------------

STREAM = (
    b'data: {"id": "req_9", "choices": [{"delta": {"content": "ok"}}]}\n\n'
    b'data: {"id": "req_9", "choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n'
    b"data: [DONE]\n\n"
)


def _public(private_key: int) -> str:
    """KEY's uncompressed public key, as RedPill's keyset lists the e2ee key whose address is the
    attested signing address."""
    from daprivatecoder.llm import ethsig

    point = ethsig._mul(private_key, ethsig._G)
    return "04" + point[0].to_bytes(32, "big").hex() + point[1].to_bytes(32, "big").hex()


def receipt_server(
    *,
    stream: bytes = STREAM,
    body_hash: str | None = None,
    upstream: str = "verified",
    receipt_key=None,
    tied: str = "e2ee",
    digest: str | None = None,
    not_ready: int = 0,
    seen: list | None = None,
    not_after: int = 4_000_000_000,
    signer: int = KEY,
    aci: str = "none",
    plain=None,
    also: int | None = None,
):
    """nano-gpt as live (Nov 4 2026): the attestation's quote binds the signing key; the keyset beside
    it lists that key as its e2ee key ("e2ee"), or the quote binds the keyset itself ("statement"), or
    neither ("none"); the signature lookup returns the record the signing key signed and RedPill's
    receipt, signed by the keyset's Ed25519 key, for `stream`'s bytes.

    `aci` is what `report=aci` answers (Nov 10 2026): RedPill's report binding the keyset to our nonce
    ("bound"), to another ("unbound"), a 400 ("400"), or the plain report as before nano-gpt offered it
    ("none"). `plain(nonce) -> (status, body)` stands in for the plain report. With `also`, the keyset
    lists a second e2ee key, that one's."""
    import hashlib

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from daprivatecoder.llm import tee

    gateway = Ed25519PrivateKey.generate()
    receipt_signer = receipt_key or gateway
    public = gateway.public_key().public_bytes_raw().hex()
    keyset = {
        "subject": None,
        "not_after": not_after,
        "receipt_signing_keys": [
            {"key_id": "dstack-kms-receipt-ed25519-v1", "algo": "ed25519", "public_key": public}
        ],
        "e2ee_public_keys": [
            {
                "key_id": "dstack-kms-e2ee-v1",
                "algo": "secp256k1-aes-256-gcm-hkdf-sha256",
                "public_key": _public(KEY if tied != "none" else 7),
            }
        ] + ([{"key_id": "dstack-kms-e2ee-v2", "algo": "secp256k1-aes-256-gcm-hkdf-sha256",
               "public_key": _public(also)}] if also is not None else []),
        "tls_public_keys": [],
    }
    keyset_digest = tee._digest(tee._canonical(keyset))
    address = address_of(KEY).lower()
    request_hash = hashlib.sha256(b"the request forwarded").hexdigest()
    response_hash = (body_hash or "sha256:" + hashlib.sha256(stream).hexdigest())[7:]
    pending = [not_ready]

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if seen is not None:
            seen.append(request)
        if url.startswith(NRAS_URL):
            return httpx.Response(200, json=[["JWT", jwt({"x-nvidia-overall-att-result": True})], {}])
        if "/tee/attestation" in url:
            nonce = request.url.params["nonce"]
            if request.url.params.get("report") == "aci" and aci != "none":
                if aci == "400":
                    return httpx.Response(
                        400, json={"error": "report=aci is only available for models served through RedPill"})
                bound_to = nonce if aci == "bound" else "ab" * 32
                statement = (
                    f'{{"keyset_digest":"{keyset_digest}","nonce":"{bound_to}",'
                    '"purpose":"aci.report_data.v1"}'
                ).encode()
                report = hashlib.sha256(statement).digest() + bytes(32)
                return httpx.Response(
                    200,
                    json={
                        "api_version": "aci/1",
                        "workload_keyset_digest": keyset_digest,
                        "attestation": {
                            "tee_type": "tdx",
                            "workload_keyset": keyset,
                            "report_data": report[:32].hex(),
                            "evidence": {"quote": fake_quote(report)},
                        },
                    },
                )
            if plain is not None:
                status, body = plain(nonce)
                return httpx.Response(status, json=body)
            if tied == "statement":
                statement = (
                    f'{{"keyset_digest":"{keyset_digest}","nonce":"{nonce}",'
                    '"purpose":"aci.report_data.v1"}'
                ).encode()
                report = hashlib.sha256(statement).digest() + bytes(32)
            else:
                report = bytes.fromhex(address[2:]) + bytes(12) + bytes.fromhex(nonce)
            return httpx.Response(
                200,
                json={
                    "signing_address": address,
                    "intel_quote": fake_quote(report),
                    "nvidia_payload": json.dumps({"evidence_list": [{"evidence": "e"}]}),
                    "attestation": {"report_data": report[:32].hex(), "workload_keyset": keyset},
                },
            )
        if "/tee/signature/" in url:
            if pending[0]:
                pending[0] -= 1
                return httpx.Response(404, json={"error": "not yet"})
            text = f"{request_hash}:{response_hash}"
            receipt = {
                "api_version": "aci/1",
                "receipt_id": "rcpt-1",
                "key_id": "dstack-kms-receipt-ed25519-v1",
                "served_at": 1_800_000_000,
                "workload_keyset_digest": digest or keyset_digest,
                "event_log": [
                    {"type": "request.received", "body_hash": f"sha256:{request_hash}"},
                    {"type": "upstream.verified", "result": upstream, "required": True,
                     "session_id": "s", "model_id": "m"},
                    {"type": "response.returned", "body_hash": f"sha256:{response_hash}"},
                ],
            }
            receipt["signature"] = receipt_signer.sign(tee._canonical(receipt)).hex()
            return httpx.Response(
                200,
                json={
                    "text": text,
                    "signature": sign(personal_hash(text.encode()), signer, 4242).hex(),
                    "signing_address": address,
                    "signing_algo": "ecdsa",
                    "receipt": receipt,
                },
            )
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def receipt_client(model: str = "TEE/glm-5.3-flash", **server) -> TeeClient:
    client = TeeClient(
        "https://nano-gpt.com/api/v1",
        "k",
        model,
        client=receipt_server(**server),
        intel=lambda raw, session: UP_TO_DATE,
        nvidia=lambda answer, nonce, session: 8,
    )
    client.attest()
    return client


def streamed(stream: bytes = STREAM) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(stream).hexdigest()


def attested(seen: list) -> list:
    """The model's own attestations asked for, not the gateway's report asked for beside each."""
    return [r for r in seen if "/tee/attestation" in str(r.url) and r.url.params.get("report") != "aci"]


def test_a_streamed_reply_is_checked_to_its_exact_bytes_and_its_receipt():
    client = receipt_client()
    assert client.keyset is not None, "the keyset holds the attested key"
    assert client.check_reply("req_9", receipt_id="rcpt-1", body_hash=streamed()) == "verified"


def test_bytes_other_than_those_signed_fail():
    client = receipt_client()
    assert client.check_reply("req_9", body_hash=streamed(b"data: [DONE]\n\n")) == "failed"


def test_a_record_signed_by_another_key_fails():
    assert receipt_client(signer=1).check_reply("req_9", body_hash=streamed()) == "failed"


def test_a_model_whose_streams_nano_gpt_rebuilds_is_checked_by_signature_alone():
    client = receipt_client(model="TEE/gemma-4-31b-it")
    assert client.check_reply("req_9", body_hash=streamed(b"rebuilt")) == "signed"


def test_a_receipt_whose_upstream_wasnt_verified_fails():
    assert receipt_client(upstream="failed").check_reply("req_9", body_hash=streamed()) == "failed"


def test_a_receipt_signed_by_another_key_fails():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    client = receipt_client(receipt_key=Ed25519PrivateKey.generate())
    assert client.check_reply("req_9", body_hash=streamed()) == "failed"


def test_a_receipt_under_keys_renewed_since_is_unchecked_not_failed():
    client = receipt_client(digest="sha256:" + "0" * 64)
    with pytest.raises(ProviderError, match="keys changed"):
        client.check_reply("req_9", body_hash=streamed())


def test_a_keyset_the_quote_binds_itself_is_taken_and_one_tied_neither_way_is_not():
    bound = receipt_client(tied="statement")
    assert bound.keyset is not None
    assert bound.attest().shortfalls == [], "RedPill's own form binds the receipt keys"
    loose = receipt_client(tied="none")
    assert loose.keyset is None
    # Still signed by the attested key, for these bytes: the receipt goes unread.
    assert loose.check_reply("req_9", body_hash=streamed()) == "verified"


def test_a_stream_just_ended_waits_for_its_receipt(monkeypatch):
    from daprivatecoder.llm import tee

    monkeypatch.setattr(tee, "RECEIPT_RETRY_SECONDS", 0)
    seen: list = []
    client = receipt_client(not_ready=2, seen=seen)
    assert client.check_reply("req_9", receipt_id="rcpt-1", body_hash=streamed(), route="r1") == "verified"
    lookups = [r for r in seen if "/tee/signature/" in str(r.url)]
    assert len(lookups) == 3 and "rcpt-1" in str(lookups[-1].url)
    assert all(r.headers.get("X-TEE-Verification-Route") == "r1" for r in lookups)


def test_a_reply_that_named_no_receipt_is_looked_up_once(monkeypatch):
    from daprivatecoder.llm import tee

    monkeypatch.setattr(tee, "RECEIPT_RETRY_SECONDS", 0)
    seen: list = []
    client = receipt_client(not_ready=1, seen=seen)
    with pytest.raises(ProviderError, match="no signature for this reply"):
        client.check_reply("req_9", body_hash=streamed())
    assert len([r for r in seen if "/tee/signature/" in str(r.url)]) == 1


def test_a_reply_whose_bytes_cant_be_compared_is_signed_not_verified():
    client = receipt_client()
    assert client.check_reply("req_9", body_hash=streamed()) == "verified"
    assert client.check_reply("req_9") == "signed", "no bytes to compare"
    assert client.verify_reply("req_9") is True
    assert receipt_client(upstream="failed").verify_reply("req_9", body_hash=streamed()) is False


def test_the_receipt_keys_are_named_short_whether_a_keyset_comes_or_not():
    """Leaving the keyset out must never read better than sending it loose."""
    with_keys = receipt_client()
    assert with_keys.keyset is not None
    assert RECEIPT_KEYS_LOOSE in with_keys.attest().shortfalls
    without = receipt_client(tied="none")
    assert without.keyset is None
    assert RECEIPT_KEYS_LOOSE in without.attest().shortfalls


def test_expired_receipt_keys_are_refused():
    """SealedLore names them short ("receipt keys expired"); here, as for RedPill's gateway before,
    keys the attestation says have expired are refused."""
    with pytest.raises(TeeRefused, match="attested keys expired"):
        receipt_client(tied="statement", not_after=1)


# --- the gateway's own report (report=aci, nano-gpt, Nov 10 2026; SealedLore's tests) ---------------

def test_the_gateways_report_binds_the_receipt_keys_and_only_the_model_behind_it_is_short():
    seen: list = []
    client = receipt_client(aci="bound", seen=seen)
    result = client.attest()
    # the signing key is in the keyset the gateway's quote binds: the gateway's, so the model behind it unchecked
    assert result.level == "partial" and result.shortfalls == [MODEL_ENCLAVE_UNCHECKED]
    assert "binds its receipt keys" in result.detail
    assert client.keyset is not None
    assert client.check_reply("req_9", receipt_id="rcpt-1", body_hash=streamed()) == "verified"
    asked = [r for r in seen if "/tee/attestation" in str(r.url)]
    aci = [r for r in asked if r.url.params.get("report") == "aci"]
    assert aci and all(
        len(r.url.params["nonce"]) == 64 and r.url.params["nonce"] == r.url.params["nonce"].lower() for r in asked)
    assert len({r.url.params["nonce"] for r in asked}) == len(asked), "a fresh nonce for each"
    assert aci[0].extensions["timeout"]["read"] == 90.0, "Chutes' evidence can take a minute"


def test_a_reply_is_checked_against_every_key_the_gateways_quote_binds():
    """Any e2ee key of the keyset the gateway's quote binds may sign a record, and no other key."""
    assert receipt_client(aci="bound", also=5, signer=5).check_reply("req_9", body_hash=streamed()) == "verified"
    # without the gateway's report only the attested signing key counts
    assert receipt_client(also=5, signer=5).check_reply("req_9", body_hash=streamed()) == "failed"
    assert receipt_client(aci="bound", also=5, signer=6).check_reply("req_9", body_hash=streamed()) == "failed"


def test_a_gateway_report_for_another_nonce_is_refused():
    with pytest.raises(TeeRefused, match="gateway didn't bind its keys to our nonce"):
        receipt_client(aci="unbound")


def test_without_the_gateways_report_the_receipt_keys_are_named_short():
    result = receipt_client(aci="400").attest()
    assert RECEIPT_KEYS_LOOSE in result.shortfalls


def test_a_gateway_whose_keys_dont_hold_the_signing_key_counts_for_nothing():
    client = receipt_client(aci="bound", tied="none")
    result = client.attest()
    assert RECEIPT_KEYS_LOOSE in result.shortfalls
    assert "don't include the signing key" in result.detail
    assert client.keyset is None


def test_a_gateway_report_whose_keys_have_expired_is_refused():
    with pytest.raises(TeeRefused, match="attested keys expired"):
        receipt_client(aci="bound", not_after=1)


RELAYED = {
    "attestation_type": "chutes",
    "nonce": "f9" * 32,
    "all_attestations": [{"intel_quote": "", "gpu_evidence": []}],
}


def test_the_gateway_stands_in_for_a_model_whose_own_evidence_was_relayed():
    """Live, TEE/deepseek-v3.2: its own report is Chutes' for the relay's nonce, its replies RedPill's, with
    receipts."""
    client = receipt_client(aci="bound", plain=lambda nonce: (200, RELAYED))
    result = client.attest()
    assert result.level == "partial" and result.shortfalls == ["no GPU evidence", MODEL_ENCLAVE_UNCHECKED]
    assert "the gateway that answers it is attested instead" in result.detail
    assert client.check_reply("req_9", receipt_id="rcpt-1", body_hash=streamed()) == "verified"
    # Without the gateway, refused as before.
    with pytest.raises(TeeRefused, match="nonce the relay chose"):
        receipt_client(aci="400", plain=lambda nonce: (200, RELAYED))


def test_a_slow_attestation_is_asked_again_with_a_new_nonce_then_the_gateway_stands_in():
    timeout = {
        "error": "RedPill attestation service took too long to respond. Please retry.",
        "code": "tee_attestation_upstream_timeout",
    }
    asked: list[str] = []

    def once(nonce):
        asked.append(nonce)
        return (504, timeout) if len(asked) == 1 else (200, RELAYED)

    receipt_client(aci="bound", plain=once)
    assert len(asked) == 2 and asked[0] != asked[1]
    always = receipt_client(aci="bound", plain=lambda nonce: (504, timeout))
    assert always.attest().shortfalls == ["no GPU evidence", MODEL_ENCLAVE_UNCHECKED]
    with pytest.raises(TeeRefused, match="provider's side"):
        receipt_client(aci="400", plain=lambda nonce: (504, timeout))


def test_only_a_relay_or_a_timeout_has_the_gateway_stand_in():
    """Not a rate limit, an outage or nano-gpt's error: a model Chutes answers first would then be sent to
    instances not attested this time. (Live, a 429 on TEE/gemma-4-31b-it's own report had the gateway stand
    in under SealedLore's rule, which takes any failure.)"""
    for status, body, refusal in (
        (429, {"error": "Rate limit exceeded"}, "couldn't be fetched: .*429"),
        (502, {"error": "Unable to reach RedPill attestation service"}, "provider's side"),
        (200, {"error": "unavailable"}, "couldn't attest"),
    ):
        with pytest.raises(TeeRefused, match=refusal):
            receipt_client(aci="bound", plain=lambda nonce, s=status, b=body: (s, b))


def test_a_chutes_model_with_the_gateway_checks_the_replies_redpill_answers(instances):
    """Chutes answers first and signs nothing; a reply RedPill answers instead names its receipt, checked
    against the gateway's bound keys."""
    client = receipt_client(model="TEE/glm-5.1", aci="bound", plain=lambda nonce: (200, instances.body(nonce)))
    result = client.attest()
    assert result.level == "full" and result.instances
    assert client.check_reply("req_9", receipt_id="rcpt-1", body_hash=streamed()) == "verified"
    with pytest.raises(ProviderError, match="signs no replies"):
        client.check_reply("req_9", body_hash=streamed())
    # Chutes alone (report=aci answers 400): unsigned, as before.
    alone = receipt_client(model="TEE/kimi-k3", aci="400", plain=lambda nonce: (200, instances.body(nonce)))
    with pytest.raises(ProviderError, match="signs no replies"):
        alone.check_reply("req_9", receipt_id="rcpt-1", body_hash=streamed())



# --- the stream of a TEE model's reply, every byte of it hashed (LLMClient, exact) ------------------

def test_the_stream_keeps_its_receipt_id_and_the_hash_of_every_byte():
    """Every byte, to the end: [DONE] and what comes after it too (SealedLore's test, for the client the
    proxy uses)."""
    import gzip

    from daprivatecoder.llm.client import LLMClient

    tail = b": a comment after the end\n\n"
    for encoded in (False, True):
        headers = {"content-type": "text/event-stream", "X-Receipt-Id": "rcpt-7", "X-TEE-Verification-Route": "r2"}
        if encoded:
            headers["content-encoding"] = "gzip"                   # hashed as decoded, as the receipt's is
        content = gzip.compress(STREAM + tail) if encoded else STREAM + tail
        http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=content, headers=headers)))

        async def collect():
            return [e async for e in LLMClient("https://nano-gpt.com/api/v1", "k", http).stream(
                "TEE/glm-5.3-flash", [{"role": "user", "content": "hi"}], exact=True)]
        events = asyncio.run(collect())
        done = events[-1][1]
        assert events[0] == ("open", None) and "".join(v for k, v in events if k == "text") == "ok"
        assert (done.id, done.receipt_id, done.tee_route, done.finish_reason) == ("req_9", "rcpt-7", "r2", "stop")
        assert done.body_hash == streamed(STREAM + tail)


def test_a_whole_reply_asked_for_exactly_is_read_as_a_stream_and_kept_for_its_check():
    """complete(exact=True), as the vision helper asks a TEE model: read as a stream, every byte hashed,
    the reply's id, receipt and hash on last_reply (DA Vibe Manager's shape). Without it, as before."""
    from daprivatecoder.llm.client import LLMClient

    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(json.loads(request.content))
        if asked[-1].get("stream"):
            return httpx.Response(200, content=STREAM, headers={"content-type": "text/event-stream", "X-Receipt-Id": "rcpt-5"})
        return httpx.Response(200, json={"id": "x", "object": "chat.completion", "created": 0, "model": "m", "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "whole"}, "finish_reason": "stop"}]})
    client = LLMClient("https://nano-gpt.com/api/v1", "k", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    text = asyncio.run(client.complete("TEE/kimi-k3", [{"role": "user", "content": "hi"}], exact=True))
    reply = client.last_reply
    assert text == "ok" and asked[0]["stream"] is True
    assert (reply.id, reply.receipt_id, reply.body_hash) == ("req_9", "rcpt-5", streamed())
    assert asyncio.run(client.complete("z-ai/glm-5.3", [{"role": "user", "content": "hi"}])) == "whole"
    assert client.last_reply is None and "stream" not in asked[1]


def test_an_error_partway_through_an_exact_stream_is_the_sdks_error():
    from openai import APIError

    from daprivatecoder.llm.client import LLMClient

    body = STREAM.split(b"\n\n")[0] + b'\n\ndata: {"error": {"message": "context too long"}}\n\n'
    http = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})))

    async def collect():
        return [e async for e in LLMClient("https://nano-gpt.com/api/v1", "k", http).stream(
            "TEE/glm-5.3-flash", [{"role": "user", "content": "hi"}], exact=True)]
    with pytest.raises(APIError, match="context too long"):
        asyncio.run(collect())


# --- every TEE reply through the proxy, checked once it has gone on to Claude Code -------------------


TEE_CAPS = [{"id": "TEE/glm-5.3-flash", "capabilities": {"vision": False, "tool_calling": True}},
            {"id": "TEE/kimi-k3", "capabilities": {"vision": True, "tool_calling": True}}]


def _tee_proxy(tmp_path, seen: list, stream: bytes = STREAM, receipt: str | None = "rcpt-1", vision: str = "",
               sent: list | None = None, route: str | None = None, **server):
    """A TEE session's proxy whose models' enclaves attest on receipt_server, and whose models stream
    `stream` as it is through the real client, naming its receipt (and `route`; `sent`: the bodies they
    were sent). With `vision`, that TEE model describes images for its own model, which can't see
    (TEE_CAPS)."""
    from daprivatecoder.config import Profile
    from daprivatecoder.llm.client import LLMClient
    from daprivatecoder.proxy import AttestedTee, Proxy
    from daprivatecoder.usagelog import UsageLog

    def attest(base, key, model):
        client = TeeClient(base, key, model, client=receipt_server(seen=seen, **server),
                           intel=lambda raw, session: UP_TO_DATE, nvidia=lambda answer, nonce, session: 8)
        return AttestedTee(client, client.attest().to_dict())

    def chat(request: httpx.Request) -> httpx.Response:
        if sent is not None:
            sent.append(json.loads(request.content))
        return httpx.Response(200, content=stream, headers={
            "content-type": "text/event-stream", **({"X-Receipt-Id": receipt} if receipt else {}),
            **({"X-TEE-Verification-Route": route} if route else {})})

    async def caps():
        from daprivatecoder.llm import capabilities
        return capabilities.parse(TEE_CAPS) if vision else {}
    http = httpx.AsyncClient(transport=httpx.MockTransport(chat))
    profile = Profile("tee", "tee", "TEE/glm-5.3-flash", "TEE/glm-5.3-flash", "TEE/glm-5.3-flash", vision, "low")
    return Proxy(profile, "https://nano-gpt.com/api/v1", "t" * 40, key=lambda: "sk-nano",
                 log=UsageLog(tmp_path / "requests.log", "s1"), tee_check=attest,
                 plain_client=lambda url, key: LLMClient(url, key, http), capabilities_loader=caps,
                 http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: pytest.fail(f"nothing else: {r.url}"))))


async def _ask(p, tmp_path, times: int = 1, stream: bool = True, checked: bool = True,
               images: bool = False) -> tuple[list[str], list[dict]]:
    """Ask the proxy `times`, each time waiting for the replies' checks (`checked`) to be logged: the
    request's, and with `images` (a new one each time) its description's too."""
    from daprivatecoder.llm import anthropic_bridge
    from daprivatecoder.usagelog import read

    said = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=p.app()), base_url="http://127.0.0.1") as c:
        for n in range(times):
            content = [{"type": "text", "text": "hi"}] + (
                [anthropic_bridge.b64_image(b"\x89PNG\r\n\x1a\n picture " + bytes([n]))] if images else [])
            r = await c.post("/v1/messages", headers={"authorization": "Bearer " + "t" * 40}, json={
                "model": "TEE/glm-5.3-flash", "max_tokens": 100, "stream": stream,
                "messages": [{"role": "user", "content": content}]})
            said.append(r.text)
            for _ in range(500 if checked else 0):
                if sum(1 for e in read(tmp_path / "requests.log") if e["kind"] == "tee_reply") == len(said) * (1 + images):
                    break
                await asyncio.sleep(0.01)
    return said, read(tmp_path / "requests.log")


async def test_a_tee_reply_is_checked_to_its_exact_bytes_once_it_has_gone_on(tmp_path):
    seen: list = []
    said, log = await _ask(_tee_proxy(tmp_path, seen), tmp_path)
    assert '"ok"' in said[0]                                       # Claude Code got the reply as it came
    request = next(e for e in log if e["kind"] == "request")
    check = next(e for e in log if e["kind"] == "tee_reply")
    assert request["reply"] == "checking" and check["of"] == request["ref"] and check["reply"] == "verified"
    lookups = [str(r.url) for r in seen if "/tee/signature/" in str(r.url)]
    assert lookups and "rcpt-1" in lookups[-1]                     # by the receipt its stream named


async def test_a_tee_vision_helpers_description_is_checked_as_any_reply_is(tmp_path):
    """The vision helper's reply is the description the model is given: a TEE one's is read as a stream,
    every byte hashed, and checked against its record as the chat's is, on a line naming the image's
    own row in the request log."""
    seen, sent = [], []
    said, log = await _ask(_tee_proxy(tmp_path, seen, vision="TEE/kimi-k3", sent=sent), tmp_path, images=True)
    vision = next(e for e in log if e["kind"] == "vision")
    request = next(e for e in log if e["kind"] == "request")
    checks = {e["of"]: e for e in log if e["kind"] == "tee_reply"}
    assert vision["model"] == "TEE/kimi-k3" and vision["tier"] == "tee" and vision["reply"] == "checking"
    assert checks[vision["ref"]]["reply"] == "verified" and checks[vision["ref"]]["model"] == "TEE/kimi-k3"
    assert checks[request["ref"]]["reply"] == "verified" and vision["ref"] != request["ref"]
    asked = [body["model"] for body in sent]
    assert asked == ["TEE/kimi-k3", "TEE/glm-5.3-flash"] and all(body["stream"] for body in sent)
    # the description went on to the model as it came
    assert sent[1]["messages"][-1]["content"].endswith("described for you by a vision model (TEE/kimi-k3):]\nok")


async def test_a_tee_description_that_fails_its_check_has_its_model_attested_again(tmp_path, monkeypatch):
    from daprivatecoder.llm import tee as tee_mod

    monkeypatch.setattr(tee_mod, "RECEIPT_RETRY_SECONDS", 0)
    seen: list = []
    p = _tee_proxy(tmp_path, seen, vision="TEE/kimi-k3", body_hash="sha256:" + "0" * 64)   # it signed other bytes
    said, log = await _ask(p, tmp_path, times=2, images=True)
    descriptions = {e["ref"] for e in log if e["kind"] == "vision"}
    assert [e["reply"] for e in log if e["kind"] == "tee_reply" and e["of"] in descriptions] == ["failed", "failed"]
    attested_models = [r.url.params["model"] for r in attested(seen)]
    # each model attested before its first request, and again before its second: both replies failed
    assert attested_models.count("TEE/kimi-k3") == 2 and attested_models.count("TEE/glm-5.3-flash") == 2
    assert [e.get("after") for e in log if e["kind"] == "attested_tee" and e["model"] == "TEE/kimi-k3"] == [
        None, "a reply that failed its check"]


async def test_a_tee_reply_not_streamed_is_checked_too(tmp_path):
    said, log = await _ask(_tee_proxy(tmp_path, []), tmp_path, stream=False)
    assert json.loads(said[0])["content"][0]["text"] == "ok"
    assert [e["reply"] for e in log if e["kind"] == "tee_reply"] == ["verified"]


async def test_a_model_attested_through_the_gateway_alone_is_logged_in_part_and_its_replies_checked(tmp_path):
    """TEE/deepseek-v3.2 as live (Nov 10 2026): its own report relayed for the relay's nonce, the gateway attested
    for ours instead. Enclave checks says how far that goes; each reply is checked as any other's, its route
    named on the lookups of its record, never on an attestation (made before the request, when no reply has
    one)."""
    seen: list = []
    p = _tee_proxy(tmp_path, seen, route="r9", aci="bound", plain=lambda nonce: (200, RELAYED))
    said, log = await _ask(p, tmp_path)
    assert [e["reply"] for e in log if e["kind"] == "tee_reply"] == ["verified"]
    (att,) = [e for e in log if e["kind"] == "attested_tee"]
    assert att["level"] == "partial" and att["shortfalls"] == ["no GPU evidence", MODEL_ENCLAVE_UNCHECKED]
    assert "partially verified: no GPU evidence" in att["summary"]
    lookups = [r for r in seen if "/tee/signature/" in str(r.url)]
    assert lookups and all(r.headers.get("X-TEE-Verification-Route") == "r9" for r in lookups)
    assert not [r for r in seen if "/tee/attestation" in str(r.url) and "X-TEE-Verification-Route" in r.headers]


async def test_a_tee_reply_whose_record_doesnt_match_is_said_and_its_model_attested_again(tmp_path, monkeypatch, capfd):
    from daprivatecoder.llm import tee as tee_mod

    monkeypatch.setattr(tee_mod, "RECEIPT_RETRY_SECONDS", 0)
    seen: list = []
    p = _tee_proxy(tmp_path, seen, body_hash="sha256:" + "0" * 64)     # it signed other bytes
    said, log = await _ask(p, tmp_path, times=2)
    assert all('"ok"' in text for text in said)                    # nothing held back or changed
    assert [e["reply"] for e in log if e["kind"] == "tee_reply"] == ["failed", "failed"]
    # attested before the first request, and again before the second, though the first was minutes fresh
    assert len(attested(seen)) == 2
    assert [e.get("after") for e in log if e["kind"] == "attested_tee"] == [None, "a reply that failed its check"]
    assert "didn't match the record its attested enclave signed" in capfd.readouterr().err


async def test_a_tee_reply_that_cant_be_checked_is_unchecked_and_its_model_not_attested_again(tmp_path, monkeypatch):
    """A record not found is no reason to doubt the enclave (a provider that keeps none, say)."""
    from daprivatecoder.llm import tee as tee_mod

    monkeypatch.setattr(tee_mod, "RECEIPT_WAIT_SECONDS", 0)
    seen: list = []
    said, log = await _ask(_tee_proxy(tmp_path, seen, not_ready=99), tmp_path, times=2)
    checks = [e for e in log if e["kind"] == "tee_reply"]
    assert [e["reply"] for e in checks] == ["unchecked", "unchecked"] and "no signature" in checks[0]["detail"]
    assert len(attested(seen)) == 1


async def test_a_reply_cut_short_is_not_checked(tmp_path):
    """Only a reply its stream carried to the end has a record to match."""
    seen: list = []
    p = _tee_proxy(tmp_path, seen, stream=STREAM.split(b"\n\n")[0] + b"\n\n" + b"data: {not json\n\n")
    said, log = await _ask(p, tmp_path, checked=False)
    await asyncio.sleep(0.2)
    assert '"ok"' in said[0] and "error" in said[0]                 # what came, then that it broke off
    assert not [e for e in log if e["kind"] == "tee_reply"] and not p._checks
    assert not [r for r in seen if "/tee/signature/" in str(r.url)]


def test_the_plain_client_and_the_verifier_take_no_key_a_header_cant_carry():
    """Whoever builds them, as DA Vibe Manager's copies have it: refused before anything is sent."""
    from daprivatecoder.llm.client import LLMClient
    from daprivatecoder.netpolicy import UnusableKey
    for build in (lambda key: LLMClient("https://nano-gpt.com/api/v1", key),
                  lambda key: TeeClient("https://nano-gpt.com/api/v1", key, "TEE/glm-5.3")):
        with pytest.raises(UnusableKey, match="no API key has") as refused:
            build("sk-abc\n123")
        assert "abc" not in str(refused.value)
