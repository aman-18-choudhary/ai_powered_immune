import asyncio
import base64
import hashlib
import io
import json
import sys
import zipfile

import pytest
from conftest import login
from evidence_ledger.package import verifier_source

from demo_server import evidence


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def case(hero_client):
    c = hero_client
    off = login(c, "officer")
    r = c.post("/api/cases", json={}, headers=off)
    assert r.status_code == 201, r.text
    return {"id": r.json()["case_id"], "off": off, "c": c, "record": r.json()}


@pytest.fixture(scope="module")
def package(case):
    r = case["c"].get(f"/api/packages/{case['id']}", headers=case["off"])
    assert r.status_code == 200
    return r.content, r


@pytest.fixture(scope="module")
def pubkey(case):
    return case["c"].get("/api/ledger/head", headers=case["off"]).json()["public_key_hex"]


def rezip(data: bytes, edit) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as zin, zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            b = zin.read(info)
            b = edit(info.filename, b)
            zout.writestr(info.filename, b)
    return out.getvalue()


def test_real_package_passes_pinned_via_api(case):
    r = case["c"].post("/api/verify", json={"case_id": case["id"]}, headers=case["off"])
    v = r.json()
    assert r.status_code == 200 and v["exit_code"] == 0 and v["status"] == "PASS_PINNED"
    assert v["entries"] >= 8 and len(v["redacted_seqs"]) >= 1
    assert all(len(x) <= evidence.MAX_LINE_CHARS for x in v["lines"])
    assert any("signed by key_id" in x for x in v["lines"])


def test_direct_run_passes(package, pubkey):
    res = run(evidence.run_verifier(package[0], pubkey))
    assert res["status"] == "PASS_PINNED" and res["exit_code"] == 0


def test_tampered_entry_fails(package, pubkey):
    def flip(name, b):
        if name == "entries.json":
            i = b.index(b'"entry_hash": "') + len(b'"entry_hash": "') + 3
            return b[:i] + (b"0" if b[i : i + 1] != b"0" else b"1") + b[i + 1 :]
        return b

    bad = rezip(package[0], flip)
    res = run(evidence.run_verifier(bad, pubkey))
    assert res["status"] == "FAIL" and res["exit_code"] == 1
    assert any(x.startswith("FAIL") for x in res["lines"])


def test_tampered_manifest_signature_fails(package, pubkey):
    def flip(name, b):
        if name == "manifest.json":
            d = json.loads(b)
            sig = next(k for k in d if "signature" in k)
            raw = bytearray(base64.b64decode(d[sig]))
            raw[0] ^= 0x01
            d[sig] = base64.b64encode(bytes(raw)).decode()
            return json.dumps(d).encode()
        return b

    res = run(evidence.run_verifier(rezip(package[0], flip), pubkey))
    assert res["status"] == "FAIL"


def test_wrong_pinned_key_fails(package):
    other = "ab" * 32
    res = run(evidence.run_verifier(package[0], other))
    assert res["status"] == "FAIL"


def test_replaced_verifier_is_not_executed(package, pubkey):
    marker = []

    def swap(name, b):
        if name == "verify.py":
            marker.append(1)
            return b"import sys\nprint('PASS: forged')\nsys.exit(0)\n"
        return b

    res = run(evidence.run_verifier(rezip(package[0], swap), pubkey))
    assert marker and res["status"] == "FAIL" and "not executed" in res["lines"][0]


def test_timeout_and_output_cap_and_isolation(package, pubkey, monkeypatch):
    def with_script(src: bytes) -> bytes:
        return rezip(package[0], lambda n, b: src if n == "verify.py" else b)

    hang = b"import time\ntime.sleep(60)\n"
    res = run(
        evidence.run_verifier(with_script(hang), pubkey, timeout_s=1.0, expected_verifier=hang)
    )
    assert res["status"] == "FAIL" and "timed out" in res["lines"][0]
    spam = b"import sys\nsys.stdout.write('x' * 5_000_000)\nsys.exit(0)\n"
    res = run(
        evidence.run_verifier(with_script(spam), pubkey, output_cap=4096, expected_verifier=spam)
    )
    assert res["status"] == "FAIL"  # capped output is a failure, never a pass
    assert sum(len(x) for x in res["lines"]) <= 4096 + 100

    seen = {}
    real = asyncio.create_subprocess_exec

    async def spy(*args, **kw):
        seen["args"], seen["env"] = args, kw.get("env")
        return await real(*args, **kw)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    run(evidence.run_verifier(package[0], pubkey))
    a = list(seen["args"])
    assert a[0] == sys.executable and a[1:3] == ["-I", "-S"]
    assert "--trusted-pubkey" in a and a[a.index("--trusted-pubkey") + 1] == pubkey
    assert set(seen["env"]) == {"PATH"}  # nothing inherited from the server's environment


def test_hostile_zip_members_and_garbage(pubkey):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("../evil.py", b"x")
        z.writestr("verify.py", verifier_source())
    with pytest.raises(evidence.VerifyError):
        run(evidence.run_verifier(out.getvalue(), pubkey))
    with pytest.raises(evidence.VerifyError):
        run(evidence.run_verifier(b"not a zip", pubkey))
    nov = io.BytesIO()
    with zipfile.ZipFile(nov, "w") as z:
        z.writestr("entries.json", b"[]")
    with pytest.raises(evidence.VerifyError):
        run(evidence.run_verifier(nov.getvalue(), pubkey))


def test_parse_status_mapping_and_sanitising():
    assert evidence.parse("INTEGRITY OK - UNPINNED: AUTHENTICITY NOT ESTABLISHED", 2)["status"] == (
        "INTEGRITY_OK_UNPINNED"
    )
    assert evidence.parse("FAIL: bad", 1)["status"] == "FAIL"
    p = evidence.parse("ok\x1b[31mred\x00\n" + "y" * 1000 + "\n" * 5 + "z", 0)
    assert all("\x1b" not in x and "\x00" not in x for x in p["lines"])
    assert max(len(x) for x in p["lines"]) <= evidence.MAX_LINE_CHARS
    many = evidence.parse("\n".join(str(i) for i in range(500)), 0)
    assert len(many["lines"]) == evidence.MAX_LINES


def test_case_idempotency_and_conflict(case):
    c, off = case["c"], case["off"]
    again = c.post("/api/cases", json={}, headers=off)
    assert again.status_code == 200 and again.json()["case_id"] == case["id"]
    clash = c.post(
        "/api/cases", json={"case_id": case["id"], "title": "Different title"}, headers=off
    )
    assert clash.status_code == 409 and clash.json()["error"] == "case_conflict"
    refs = case["record"]["case_refs"]
    assert any(r.startswith("txn_") for r in refs)
    assert any(r.startswith("payee_ref:") for r in refs)
    assert any(r.startswith("call_ref:") for r in refs)


def test_package_etag_and_unknown_case(case, package):
    data, resp = package
    assert resp.headers["content-type"] == "application/zip"
    assert resp.headers["etag"].strip('"') == hashlib.sha256(data).hexdigest()
    c, off = case["c"], case["off"]
    assert c.get("/api/packages/no-such-case", headers=off).status_code == 404
    assert c.post("/api/verify", json={"case_id": "no-such-case"}, headers=off).status_code == 404
    assert c.get("/api/packages/bad id", headers=off).status_code in (404, 422)


def test_ledger_reads_are_bounded(case):
    c, off = case["c"], case["off"]
    assert c.get("/api/ledger/entries?limit=201", headers=off).status_code == 422
    assert c.get("/api/ledger/entries?from_seq=0", headers=off).status_code == 422
    r = c.get("/api/ledger/entries?limit=3", headers=off).json()
    assert len(r["items"]) == 3 and r["next_from_seq"] == 4
    head = c.get("/api/ledger/head", headers=off).json()
    assert head["seq"] >= 8 and head["algorithm"] == "Ed25519" and len(head["key_id"]) == 16
    cp = c.get("/api/ledger/checkpoints/latest", headers=off).json()
    assert cp["checkpoint"]["seq"] >= 8 and cp["checkpoint"]["signature_b64"]
    assert c.get("/api/ledger/checkpoints", headers=off).json()["items"]


def test_signing_key_never_leaves_the_server(case, package):
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    run_ = case["c"].app.state.engine.run
    pem = run_.harness.signer.private_pem()
    key = load_pem_private_key(pem, None)
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

    raw = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    needles = [raw.hex().encode(), base64.b64encode(raw), base64.urlsafe_b64encode(raw), raw]
    bodies = [package[0]]
    c, off = case["c"], case["off"]
    for path in ("/api/ledger/head", "/api/ledger/entries?limit=200", "/api/ledger/checkpoints",
                 "/api/demo/state", "/api/holds?state=all", "/api/antibodies?state=all"):  # fmt: skip
        bodies.append(c.get(path, headers=off).content)
    bodies.append(c.post("/api/verify", json={"case_id": case["id"]}, headers=off).content)
    with zipfile.ZipFile(io.BytesIO(package[0])) as z:
        bodies += [z.read(n) for n in z.namelist()]
    for body in bodies:
        for n in needles:
            assert n not in body
