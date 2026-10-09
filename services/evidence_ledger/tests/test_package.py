import base64
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from scam_contracts.canonical import payload_hash
from scam_contracts.models import LedgerEntryIn

from evidence_ledger import package as pkg
from evidence_ledger.keys import KeyRing, Signer
from evidence_ledger.verify import PACKAGE_MEMBERS, verify_package

from .conftest import entry_in, tid


def fill(store, n=12, case_every=3):
    """n entries; every `case_every`-th belongs to case 'case-1'."""
    for i in range(1, n + 1):
        refs = ["case-1", tid(i)] if i % case_every == 0 else [tid(i)]
        store.append(entry_in(i, refs=refs))


def run_verify(path: Path, *extra: str, cwd: Path | None = None):
    """Run the shipped verifier the way an expert would: isolated, no site-packages, clean env."""
    script = (path / "verify.py") if path.is_dir() else None
    assert script is not None
    env = {"PATH": "/usr/bin:/bin"}
    return subprocess.run(
        [sys.executable, "-S", "-I", str(script), str(path), *extra],
        capture_output=True, text=True, env=env, cwd=cwd or path, timeout=60,
    )  # fmt: skip


def extract(data: bytes, dest: Path) -> Path:
    dest.mkdir()
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as z:
        z.extractall(dest)
    return dest


@pytest.fixture
def built(store, keyring, tmp_path):
    fill(store)
    p = pkg.build(store, keyring, "case-1", title="Hero case", case_refs=["case-1"])
    return p, extract(p.data, tmp_path / "pkg")


def test_package_verify_script_runs_offline(built):
    p, d = built
    r = run_verify(d)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS" in r.stdout
    # the verifier did not need cryptography or any other third-party package
    probe = subprocess.run(
        [sys.executable, "-S", "-I", "-c", "import cryptography"], capture_output=True, env={}
    )
    assert probe.returncode != 0


def test_verify_script_accepts_zip_and_cwd_default(built, tmp_path):
    p, d = built
    z = tmp_path / "p.zip"
    z.write_bytes(p.data)
    r = subprocess.run(
        [sys.executable, "-S", "-I", str(d / "verify.py"), str(z)], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout
    r = subprocess.run(
        [sys.executable, "-S", "-I", "verify.py"], cwd=d, capture_output=True, text=True, env={}
    )
    assert r.returncode == 0  # `python verify.py .` style usage (default path = its own directory)


def test_package_layout_and_gap_handling(built):
    p, d = built
    assert sorted(x.name for x in d.iterdir()) == sorted([*PACKAGE_MEMBERS, "manifest.json"])
    doc = json.loads((d / "entries.json").read_text())
    assert doc["selected_seqs"] == [3, 6, 9, 12]  # non-contiguous selection
    seqs = [e["seq"] for e in doc["entries"]]
    assert seqs == list(range(3, seqs[-1] + 1)) and seqs[-1] >= 12
    proof = json.loads((d / "chain_proof.json").read_text())
    assert proof["segment"]["to_seq"] == seqs[-1] == proof["checkpoints"][0]["seq"]
    assert proof["anchor_prev_hash"] == doc["entries"][0]["prev_hash"]
    assert (p.from_seq, p.to_seq) == (3, 12)


def test_deterministic_bytes(store, keyring):
    fill(store)
    a = pkg.build(store, keyring, "case-1", case_refs=["case-1"])
    b = pkg.build(store, keyring, "case-1", case_refs=["case-1"])
    assert a.data == b.data and a.sha256 == hashlib.sha256(a.data).hexdigest()


def test_zip_members_have_zeroed_timestamps_and_fixed_order(built):
    p, _ = built
    with zipfile.ZipFile(__import__("io").BytesIO(p.data)) as z:
        assert [i.filename for i in z.infolist()] == [*PACKAGE_MEMBERS, "manifest.json"]
        assert {i.date_time for i in z.infolist()} == {(1980, 1, 1, 0, 0, 0)}


def edit(d: Path, name: str, fn):
    f = d / name
    f.write_bytes(fn(f.read_bytes()))


def _flip_sig(b: bytes) -> bytes:
    m = json.loads(b)
    raw = bytearray(base64.b64decode(m["signature_b64"]))
    raw[0] ^= 1
    m["signature_b64"] = base64.b64encode(bytes(raw)).decode()
    return json.dumps(m, indent=2, sort_keys=True).encode()


TAMPERS = {
    "entries_value": ("entries.json", lambda b: b.replace(b"hold_verify", b"allow", 1)),
    "entries_actor": ("entries.json", lambda b: b.replace(b"system:txn-guard", b"mallory", 1)),
    "chain_proof": ("chain_proof.json", lambda b: b.replace(b'"count": ', b'"count": 1', 1)),
    "explanations": ("explanations.md", lambda b: b + b"\nThe suspect confessed.\n"),
    "certificate": ("certificate_section63_template.md", lambda b: b + b"x"),
    "verify_py": ("verify.py", lambda b: b + b"\n# changed\n"),
    "manifest_hash": ("manifest.json", lambda b: b.replace(b'"explanations.md": "', b'"explanations.md": "0', 1)),
    "signature_flip": ("manifest.json", _flip_sig),
    "wrong_key_id": ("manifest.json", lambda b: b.replace(b'"key_id": "', b'"key_id": "0', 1)),
    "manifest_case_id": ("manifest.json", lambda b: b.replace(b'"case_id": "case-1"', b'"case_id": "case-2"')),
}  # fmt: skip


@pytest.mark.parametrize("name", sorted(TAMPERS))
def test_tampering_any_member_fails_with_reason(built, name):
    _, d = built
    member, fn = TAMPERS[name]
    edit(d, member, fn)
    r = run_verify(d)
    assert r.returncode == 1, (name, r.stdout)
    assert "FAIL" in r.stdout and len(r.stdout.split("FAIL:")[1].strip()) > 5


def test_member_hash_mismatch_is_reported_before_anything_else(built):
    _, d = built
    edit(d, "entries.json", lambda b: b.replace(b"hold_verify", b"allow", 1))
    assert "does not match the manifest hash" in run_verify(d).stdout


def test_signature_invalid_when_manifest_edited(built):
    _, d = built
    edit(d, "manifest.json", lambda b: b.replace(b'"case_id": "case-1"', b'"case_id": "case-2"'))
    res, _notes = verify_package(d)
    assert not res.ok and "signature" in res.reason


def test_resigned_by_attacker_needs_key_pinning(built, store, keyring):
    """A forger can rebuild a self-consistent package with their own key; only an independently
    obtained key id (--trusted-key-id) exposes it."""
    p, d = built
    evil_ring = KeyRing(Signer.from_seed(b"\x09" * 32))
    evil = pkg.build(store, evil_ring, "case-1", case_refs=["case-1"])
    # the forger cannot forge the real checkpoint signature, so the chain proof fails...
    ed = extract(evil.data, d.parent / "evil")
    r = run_verify(ed)
    assert r.returncode == 1 and "FAIL" in r.stdout
    # ... and a pin on the genuine key rejects an otherwise valid package from another signer
    ok = run_verify(d, "--trusted-key-id", keyring.signer.key_id)
    assert ok.returncode == 0 and "not pinned" not in ok.stdout
    bad = run_verify(d, "--trusted-key-id", "0" * 16)
    assert bad.returncode == 1 and "not the trusted key" in bad.stdout
    assert "WARNING: signer key not pinned" in run_verify(d).stdout


def test_extra_file_in_package_is_rejected(built):
    _, d = built
    (d / "notes.txt").write_text("hi")
    r = run_verify(d)
    assert r.returncode == 1 and "unexpected files" in r.stdout


def test_chain_entry_forged_inside_package_with_valid_member_hashes_is_caught(built):
    """An attacker edits an entry and fixes every manifest hash; the signature on the manifest
    (and the checkpoint) still expose it."""
    _, d = built
    doc = json.loads((d / "entries.json").read_text())
    doc["entries"][1]["actor"] = "mallory"
    (d / "entries.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    m = json.loads((d / "manifest.json").read_text())
    m["members"]["entries.json"] = hashlib.sha256((d / "entries.json").read_bytes()).hexdigest()
    (d / "manifest.json").write_text(json.dumps(m, indent=2, sort_keys=True))
    r = run_verify(d)
    assert r.returncode == 1 and "signature invalid" in r.stdout


def test_truncated_zip_or_garbage_fails_cleanly(tmp_path):
    z = tmp_path / "bad.zip"
    z.write_bytes(b"PK\x03\x04garbage")
    res, _ = verify_package(z)
    assert not res.ok
    res, _ = verify_package(tmp_path / "missing")
    assert not res.ok


# ------------------------------------------------------------------ selection / limits
def test_package_for_gap_case_still_verifies_in_process(built):
    p, _ = built
    assert verify_package_bytes(p.data)


def verify_package_bytes(data: bytes) -> bool:
    import tempfile

    with tempfile.TemporaryDirectory() as t:
        zp = Path(t) / "p.zip"
        zp.write_bytes(data)
        res, _ = verify_package(zp)
        return res.ok


def test_explicit_seqs_and_refs_union(store, keyring):
    fill(store)
    p = pkg.build(store, keyring, "c", seqs=[2], case_refs=["case-1"])
    assert verify_package_bytes(p.data) and (p.from_seq, p.to_seq) == (2, 12)


def test_span_over_limit_refused(store, keyring):
    fill(store, 12)
    with pytest.raises(pkg.PackageTooLarge):
        pkg.build(store, keyring, "c", case_refs=["case-1"], max_span=5)
    with pytest.raises(pkg.EmptyCase):
        pkg.build(store, keyring, "c", case_refs=["nothing"])


def test_package_covers_head_with_on_demand_checkpoint(store, keyring):
    for i in range(1, 4):  # fewer than checkpoint_every=5: no periodic checkpoint exists yet
        store.append(entry_in(i, refs=["c"]))
    p = pkg.build(store, keyring, "c", case_refs=["c"])
    assert p.segment_to_seq == 3 and verify_package_bytes(p.data)
    assert [c["seq"] for c in store.checkpoints(0, 10)] == [3]


def test_retired_key_checkpoints_still_verify_after_rotation(store, tmp_path):
    old = Signer.from_seed(b"\x01" * 32)
    store.signer = old
    for i in range(1, 7):
        store.append(entry_in(i, refs=["c"]))
    new = Signer.from_seed(b"\x02" * 32)
    store.signer = new
    for i in range(7, 12):
        store.append(entry_in(i, refs=["c"]))
    ring = KeyRing(new, {old.key_id: old.public_raw})
    p = pkg.build(store, ring, "c", case_refs=["c"])
    assert verify_package_bytes(p.data)
    d = extract(p.data, tmp_path / "rot")
    proof = json.loads((d / "chain_proof.json").read_text())
    assert set(proof["public_keys"]) == {old.key_id, new.key_id}


# ------------------------------------------------------------------ explanations
def test_explanations_render_only_stored_content(store, keyring):
    store.append(
        entry_in(
            1,
            refs=["c"],
            payload={
                "decision": "hold_verify",
                "reasons": ["URGENCY", "MYSTERY_CODE"],
                "txn_id": tid(1),
            },
        )
    )
    store.append(entry_in(2, refs=["c"], payload=None, event_type="hold.resolved"))
    p = pkg.build(store, keyring, "c", title="T", case_refs=["c"])
    with zipfile.ZipFile(__import__("io").BytesIO(p.data)) as z:
        md = z.read("explanations.md").decode()
    assert "Decision: hold_verify" in md and "URGENCY: caller applies time pressure" in md
    assert "MYSTERY_CODE: no description in the dictionary" in md
    assert "No payload retained (hash only)" in md
    assert tid(1) in md and "Producing service: txn-guard" in md and "Model version: m1" in md
    assert "confess" not in md.lower()


def test_certificate_template_has_disclaimer_and_member_hashes(built):
    p, d = built
    cert = (d / "certificate_section63_template.md").read_text()
    assert "not legal advice" in cert and "Section 63" in cert and "counsel" in cert
    for name in ("entries.json", "chain_proof.json", "explanations.md", "verify.py"):
        assert hashlib.sha256((d / name).read_bytes()).hexdigest() in cert


def test_verifier_source_is_stdlib_only():
    import ast

    from evidence_ledger import verify

    tree = ast.parse(Path(verify.__file__).read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add((n.module or "").split(".")[0])
    assert mods <= set(sys.stdlib_module_names), mods - set(sys.stdlib_module_names)


# ------------------------------------------------------------------ hero audit trail
def test_hero_audit_trail_end_to_end(store, keyring, tmp_path):
    txn, ab = tid(77), hashlib.sha256(b"ab-1").hexdigest()

    def put(service, event, actor, payload, refs):
        ph = payload_hash(payload)
        return store.append(
            LedgerEntryIn(service=service, actor=actor, event_type=event, payload_hash=ph,
                          model_version="txn-v3", payload=payload, case_refs=refs)
        )  # fmt: skip

    for i in range(1, 5):  # unrelated traffic before the case
        store.append(entry_in(i))
    put("txn-guard", "hold.created", "system:txn-guard",
        {"txn_id": txn, "decision": "hold_verify", "decision_seq": 1, "score": 0.91,
         "reasons": ["ACTIVE_SCAM_CALL", "YOUNG_PAYEE_ACCOUNT", "NEW_PAYEE"]},
        [txn, "campaign-12"])  # fmt: skip
    store.append(entry_in(5))
    put(
        "antibody-hub",
        "antibody.created",
        "analyst-1@bank_a",
        {"antibody_id": ab, "kind": "mule_account", "source_bank": "bank_a"},
        [ab, "campaign-12"],
    )
    put("txn-guard", "hold.upgraded", "system:txn-guard",
        {"txn_id": txn, "decision": "hold_verify", "decision_seq": 2, "reasons": ["ANTIBODY_MATCH"]},
        [txn, "campaign-12"])  # fmt: skip
    put(
        "txn-guard",
        "hold.resolved",
        "officer-1",
        {"txn_id": txn, "resolution": "confirmed_scam", "role": "officer"},
        [txn, "campaign-12"],
    )
    p = pkg.build(
        store, keyring, "campaign-12", title="Digital arrest ring", case_refs=["campaign-12"]
    )
    d = extract(p.data, tmp_path / "hero")
    r = run_verify(d, "--trusted-key-id", keyring.signer.key_id)
    assert r.returncode == 0, r.stdout + r.stderr
    md = (d / "explanations.md").read_text()
    assert (
        md.index("hold.created")
        < md.index("antibody.created")
        < md.index("hold.upgraded")
        < md.index("hold.resolved")
    )
    assert "ANTIBODY_MATCH: the payee matches a confirmed shared threat marker" in md
    assert (
        "Context entries" in md
    )  # entry 5 sits inside the proof segment but is not part of the case
    assert os.path.getsize(d / "entries.json") < 100_000


REASONS = {
    "manifest_case_id": "signature invalid", "signature_flip": "signature invalid",
    "wrong_key_id": "not among the package public keys", "entries_value": "manifest hash",
    "chain_proof": "manifest hash", "verify_py": "manifest hash", "manifest_hash": "manifest hash",
}  # fmt: skip


@pytest.mark.parametrize("name", sorted(REASONS))
def test_tamper_reasons_are_specific(built, name):
    _, d = built
    member, fn = TAMPERS[name]
    edit(d, member, fn)
    assert REASONS[name] in run_verify(d).stdout
