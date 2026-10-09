import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from scam_contracts.canonical import payload_hash
from scam_contracts.models import LedgerEntryIn

from evidence_ledger import package as pkg
from evidence_ledger.keys import KeyRing, Signer
from evidence_ledger.store import LedgerStore
from evidence_ledger.verify import PACKAGE_MEMBERS, verify_package

from .conftest import entry_in, tid


def fill(store, n=12, case_every=3):
    """n entries; every `case_every`-th belongs to case 'case-1'."""
    for i in range(1, n + 1):
        refs = ["case-1", tid(i)] if i % case_every == 0 else [tid(i)]
        store.append(entry_in(i, refs=refs))


def run_verify(path: Path, *extra: str, cwd: Path | None = None, strict: bool = False):
    """Run the shipped verifier the way an expert would: isolated, no site-packages, clean env."""
    script = (path / "verify.py") if path.is_dir() else None
    assert script is not None
    env = {"PATH": "/usr/bin:/bin"}
    if not any(x.startswith("--trusted") or x == "--allow-unpinned" for x in extra) and not strict:
        extra = (*extra, "--allow-unpinned")  # most tests are about integrity, not authenticity
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


def test_package_verify_script_runs_offline(built, keyring):
    p, d = built
    r = run_verify(d, "--trusted-key-id", keyring.signer.key_id)
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
    assert r.returncode == 2, r.stdout  # unpinned
    r = subprocess.run(
        [sys.executable, "-S", "-I", str(d / "verify.py"), str(z), "--allow-unpinned"],
        capture_output=True, text=True,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout
    r = subprocess.run(
        [sys.executable, "-S", "-I", "verify.py", "--allow-unpinned"],
        cwd=d,
        capture_output=True,
        text=True,
        env={},
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


def pub_b64(ring) -> str:
    return base64.b64encode(ring.signer.public_raw).decode()


def test_genuine_self_forgery_is_exit_2_unpinned_and_exit_1_when_pinned(store, keyring, tmp_path):
    """The attacker rewrites history (changes an actor), re-chains, signs the checkpoints and the
    manifest with their own key and ships their own key list: a fully self-consistent package."""
    fill(store)
    real = extract(
        pkg.build(store, keyring, "case-1", case_refs=["case-1"]).data, tmp_path / "real"
    )
    evil_ring = KeyRing(Signer.from_seed(b"\x09" * 32))
    evil_store = LedgerStore(
        f"sqlite:///{tmp_path}/evil.db", signer=evil_ring.signer, checkpoint_every=5
    )
    for i in range(1, 13):
        refs = ["case-1", tid(i)] if i % 3 == 0 else [tid(i)]
        evil_store.append(entry_in(i, refs=refs, actor="mallory" if i == 6 else "system:txn-guard"))
    evil = extract(
        pkg.build(evil_store, evil_ring, "case-1", case_refs=["case-1"]).data, tmp_path / "evil"
    )
    # unpinned: integrity is fine, authenticity is NOT established -> exit 2, loud last line
    r = run_verify(evil, strict=True)
    assert r.returncode == 2, r.stdout
    assert (
        r.stdout.strip()
        .splitlines()[-1]
        .startswith("INTEGRITY OK - UNPINNED: AUTHENTICITY NOT ESTABLISHED")
    )
    assert "PASS" not in r.stdout
    # pinned to the REAL key (full public key or key id) the forgery is exit 1
    for pin in (
        ("--trusted-pubkey", pub_b64(keyring)),
        ("--trusted-key-id", keyring.signer.key_id),
    ):
        r = run_verify(evil, *pin)
        assert r.returncode == 1 and "not the trusted key" in r.stdout, r.stdout
    # the real package: unpinned -> 2, pinned -> 0, --allow-unpinned -> 0 but still labelled
    assert run_verify(real, strict=True).returncode == 2
    assert run_verify(real, "--trusted-pubkey", pub_b64(keyring)).returncode == 0
    assert run_verify(real, "--trusted-key-id", keyring.signer.key_id).returncode == 0
    r = run_verify(real, "--allow-unpinned")
    assert r.returncode == 0 and "UNPINNED" in r.stdout.strip().splitlines()[-1]
    # a wrong full-key pin fails even if the key_id label were guessed
    other = pub_b64(evil_ring)
    assert run_verify(real, "--trusted-pubkey", other).returncode == 1


def test_trusted_pubkey_accepts_pem_hex_and_file(built, keyring, tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    _, d = built
    pem = Ed25519PublicKey.from_public_bytes(keyring.signer.public_raw).public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
    )
    f = tmp_path / "pub.pem"
    f.write_bytes(pem)
    for arg in (str(f), keyring.signer.public_raw.hex(), pub_b64(keyring)):
        assert run_verify(d, "--trusted-pubkey", arg).returncode == 0, arg
    assert run_verify(d, "--trusted-pubkey", "not a key").returncode == 1


def test_small_order_and_noncanonical_public_keys_rejected():
    from evidence_ledger.verify import ed25519_verify

    identity = b"\x01" + b"\x00" * 31
    sig = identity + b"\x00" * 32
    assert not ed25519_verify(identity, b"m", sig)  # small-order A
    noncanon = (2**255 - 19 + 1).to_bytes(32, "little")  # y = p + 1 encodes the identity
    assert not ed25519_verify(noncanon, b"m", sig)


def test_extra_file_in_package_is_rejected(built):
    _, d = built
    (d / "notes.txt").write_text("hi")
    r = run_verify(d)
    assert r.returncode == 1 and "unexpected file" in r.stdout


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


# ---------------------------------------------------------------- redaction (review item 2)
def resign(d: Path, ring) -> None:
    """Insider helper: fix every member hash and re-sign the manifest with the REAL key, so only
    semantic checks (not hashes or signatures) can catch what was changed."""
    from scam_contracts.canonical import canonical_json

    m = json.loads((d / "manifest.json").read_text())
    for name in m["members"]:
        m["members"][name] = hashlib.sha256((d / name).read_bytes()).hexdigest()
    m.pop("signature_b64", None)
    m["signature_b64"] = base64.b64encode(ring.signer.sign(canonical_json(m))).decode()
    (d / "manifest.json").write_text(json.dumps(m, indent=2, sort_keys=True))


def fill_sentinels(store, n=12):
    for i in range(1, n + 1):
        mine = i % 3 == 0
        store.append(
            entry_in(
                i, refs=["case-1", tid(i)] if mine else [tid(i)],
                payload={"txn_id": tid(i), "note": f"{'MINE' if mine else 'OTHERCASE'}-SENTINEL-{i}"},
            )
        )  # fmt: skip


def test_package_never_contains_other_cases_payload_text(store, keyring, tmp_path):
    fill_sentinels(store)
    p = pkg.build(store, keyring, "case-1", case_refs=["case-1"])
    assert b"OTHERCASE" not in p.data  # stored zip: member bytes are scannable as-is
    assert p.data.count(b"MINE-SENTINEL-3") >= 1
    d = extract(p.data, tmp_path / "r")
    doc = json.loads((d / "entries.json").read_text())
    ctx = [e for e in doc["entries"] if e["seq"] not in doc["selected_seqs"]]
    assert ctx and all(e["payload"] is None and e["payload_present"] is True for e in ctx)
    sel = [e for e in doc["entries"] if e["seq"] in doc["selected_seqs"]]
    assert all(e["payload"] is not None for e in sel)
    m = json.loads((d / "manifest.json").read_text())
    assert m["redacted_seqs"] == [e["seq"] for e in ctx]
    r = run_verify(d, "--allow-unpinned")
    assert r.returncode == 0, r.stdout
    assert "redacted (payload withheld)" in r.stdout and str(ctx[0]["seq"]) in r.stdout


def test_selected_entry_with_payload_nulled_fails(store, keyring, tmp_path):
    fill_sentinels(store)
    d = extract(pkg.build(store, keyring, "case-1", case_refs=["case-1"]).data, tmp_path / "n")
    doc = json.loads((d / "entries.json").read_text())
    sel = next(e for e in doc["entries"] if e["seq"] == doc["selected_seqs"][0])
    sel["payload"] = None  # payload_present stays true: looks like a redaction
    (d / "entries.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    resign(d, keyring)
    r = run_verify(d, "--allow-unpinned")
    assert r.returncode == 1 and "withheld" in r.stdout and "selected" in r.stdout


def test_manifest_redacted_list_must_match(store, keyring, tmp_path):
    fill_sentinels(store)
    d = extract(pkg.build(store, keyring, "case-1", case_refs=["case-1"]).data, tmp_path / "m")
    m = json.loads((d / "manifest.json").read_text())
    m["redacted_seqs"] = m["redacted_seqs"][1:]
    (d / "manifest.json").write_text(json.dumps(m, indent=2, sort_keys=True))
    resign(d, keyring)
    r = run_verify(d, "--allow-unpinned")
    assert r.returncode == 1 and "redacted_seqs" in r.stdout


def test_withheld_payload_hash_tamper_fails(store, keyring, tmp_path):
    fill_sentinels(store)
    d = extract(pkg.build(store, keyring, "case-1", case_refs=["case-1"]).data, tmp_path / "h")
    doc = json.loads((d / "entries.json").read_text())
    ctx = next(e for e in doc["entries"] if e["seq"] not in doc["selected_seqs"])
    ctx["payload_hash"] = "e" * 64
    (d / "entries.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    resign(d, keyring)
    r = run_verify(d, "--allow-unpinned")
    assert r.returncode == 1 and f"entry {ctx['seq']}" in r.stdout


# ---------------------------------------------------------------- package structure attacks (item 5)
def members_of(d: Path) -> list[tuple[str, bytes]]:
    return [(n, (d / n).read_bytes()) for n in [*PACKAGE_MEMBERS, "manifest.json"]]


def write_zip(path: Path, items, *, compress=zipfile.ZIP_STORED, attrs=None):
    with zipfile.ZipFile(path, "w", compress) as z:
        for name, data in items:
            zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = compress
            if attrs and name in attrs:
                zi.external_attr = attrs[name]
            z.writestr(zi, data)


def cli(path: Path, *extra: str):
    """Run the shipped verifier on a path (zip or dir) from outside the package."""
    return subprocess.run(
        [sys.executable, "-S", "-I", str(path.parent / "verify.py" if path.is_file() else path / "verify.py"), str(path), "--allow-unpinned", *extra],
        capture_output=True, text=True, env={}, timeout=120,
    )  # fmt: skip


@pytest.fixture
def pkgdir(built, tmp_path):
    _, d = built
    (tmp_path / "verify.py").write_bytes((d / "verify.py").read_bytes())  # trusted copy
    return d


def assert_clean_fail(r, *needles):
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "Traceback" not in r.stdout + r.stderr
    assert r.stdout.strip().splitlines()[-1].startswith("FAIL:")
    for n in needles:
        assert n in r.stdout, (n, r.stdout)


def test_duplicate_zip_member_name_fails(pkgdir, tmp_path):
    items = members_of(pkgdir)
    evil = [("entries.json", b'{"hidden": "first copy"}')] + items  # Python would read the last
    z = tmp_path / "dup.zip"
    with pytest.warns(UserWarning):
        write_zip(z, evil)
    assert_clean_fail(cli(z), "duplicate")


@pytest.mark.parametrize(
    "name",
    ["sub/entries.json", "../escape.json", "/abs.json", "a\\b.json", "dir/", "x/../manifest.json"],
)
def test_zip_with_nested_or_traversal_names_fails(pkgdir, tmp_path, name):
    z = tmp_path / "n.zip"
    write_zip(z, [*members_of(pkgdir), (name, b"{}")])
    assert_clean_fail(cli(z), "unexpected")


def test_zip_symlink_member_fails(pkgdir, tmp_path):
    z = tmp_path / "s.zip"
    items = members_of(pkgdir)
    write_zip(z, items, attrs={"explanations.md": (0o120777 << 16)})
    assert_clean_fail(cli(z), "regular file")


def test_dir_with_subdirectory_file_symlink_or_extra_fails(pkgdir):
    sub = pkgdir / "sub"
    sub.mkdir()
    (sub / "evil.json").write_text("{}")
    assert_clean_fail(cli(pkgdir), "unexpected")
    (sub / "evil.json").unlink()
    sub.rmdir()
    (pkgdir / "link.json").symlink_to(pkgdir / "entries.json")
    assert_clean_fail(cli(pkgdir), "unexpected")
    (pkgdir / "link.json").unlink()
    (pkgdir / "explanations.md").unlink()
    (pkgdir / "explanations.md").symlink_to(pkgdir / "entries.json")
    assert_clean_fail(cli(pkgdir), "regular file")


def test_oversized_declared_member_is_refused_before_reading(pkgdir, tmp_path):
    z = tmp_path / "big.zip"
    items = [(n, d) for n, d in members_of(pkgdir)]
    items = [(n, b"\x00" * (70 * 1024 * 1024) if n == "entries.json" else d) for n, d in items]
    write_zip(z, items, compress=zipfile.ZIP_DEFLATED)
    assert_clean_fail(cli(z), "too large")


def test_compression_ratio_bomb_is_refused(pkgdir, tmp_path):
    z = tmp_path / "ratio.zip"
    items = [
        (n, b" " * (8 * 1024 * 1024) if n == "explanations.md" else d)
        for n, d in members_of(pkgdir)
    ]
    write_zip(z, items, compress=zipfile.ZIP_DEFLATED)
    assert_clean_fail(cli(z), "compression ratio")


def test_deeply_nested_json_fails_cleanly(pkgdir):
    for depth in (100, 200_000):  # over the cap; far over the interpreter recursion limit
        (pkgdir / "entries.json").write_text("[" * depth + "]" * depth)
        r = cli(pkgdir)  # member hash no longer matches, so test the parser directly too
        assert_clean_fail(r)
    import importlib.util

    spec = importlib.util.spec_from_file_location("v", pkgdir / "verify.py")
    v = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = v
    spec.loader.exec_module(v)
    with pytest.raises(ValueError, match="nested"):
        v.load_json(b"[" * 100 + b"]" * 100)
    with pytest.raises(ValueError, match="nested"):
        v.load_json(b"[" * 200_000 + b"]" * 200_000)


def test_resigned_deep_json_in_a_member_fails_cleanly(store, keyring, pkgdir):
    (pkgdir / "chain_proof.json").write_text("[" * 5000 + "]" * 5000)
    resign(pkgdir, keyring)
    assert_clean_fail(cli(pkgdir), "nested")


def test_non_utf8_and_garbage_members_fail_cleanly(store, keyring, pkgdir):
    (pkgdir / "chain_proof.json").write_bytes(b"\xff\xfe\x00garbage")
    resign(pkgdir, keyring)
    assert_clean_fail(cli(pkgdir))


def test_unexpected_exception_is_not_a_traceback(pkgdir, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("v2", pkgdir / "verify.py")
    v = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = v
    spec.loader.exec_module(v)
    for exc in (MemoryError(), RecursionError(), RuntimeError("boom")):

        def boom(*a, _e=exc, **k):
            raise _e

        monkeypatch.setattr(v, "verify_package", boom)
        assert v.main([str(pkgdir), "--allow-unpinned"]) == 1


# ---------------------------------------------------------------- old Pythons (fix round 2, item 2)
OLD_PYTHONS = [
    p for p in dict.fromkeys(
        ["/usr/bin/python3", "/opt/homebrew/bin/python3.10", "/opt/homebrew/bin/python3.11",
         "/usr/local/bin/python3.11", "/opt/homebrew/bin/python3.13", "/usr/local/bin/python3.13"]
    ) if Path(p).exists()
]  # fmt: skip


def test_verifier_parses_as_python_3_8():
    import ast

    from evidence_ledger import verify

    src = Path(verify.__file__).read_text()
    ast.parse(src, feature_version=(3, 8))
    import re

    for banned in ("datetime.UTC", "removeprefix", "removesuffix", "bit_count", "import tomllib"):
        assert banned not in src, banned
    assert not re.search(r"strict\s*=\s*True", src)  # the zip strict flag is 3.10+
    assert not re.search(r"^\s*match\s+\S+.*:\s*$", src, re.M)
    assert not re.search(r"^_\w+ = (tuple|list|dict|set)\[", src, re.M)  # runtime PEP 585
    assert src.lstrip().startswith("#!") and "from __future__ import annotations" in src
    assert "needs Python 3.8" in src


@pytest.mark.parametrize("py", OLD_PYTHONS)
def test_verifier_runs_identically_on_every_available_python(py, built, keyring, tmp_path):
    _, d = built

    def run(*extra, target=d):
        return subprocess.run(
            [py, "-S", "-I", str(d / "verify.py"), str(target), *extra],
            capture_output=True, text=True, env={}, timeout=120,
        )  # fmt: skip

    pinned = run("--trusted-pubkey", pub_b64(keyring))
    assert pinned.returncode == 0, (py, pinned.stdout, pinned.stderr)
    assert "Traceback" not in pinned.stderr
    unpinned = run()
    assert unpinned.returncode == 2 and "UNPINNED" in unpinned.stdout, (py, unpinned.stderr)
    bad = tmp_path / "bad"
    shutil.copytree(d, bad)
    (bad / "explanations.md").write_bytes(b"tampered")
    r = run("--allow-unpinned", target=bad)
    assert r.returncode == 1 and "Traceback" not in r.stderr and "FAIL:" in r.stdout
