"""Evidence encryption at rest (Phase 14). Synthetic TEST DATA only; test keys only."""

import dataclasses
import hashlib
from pathlib import Path

import httpx
import pytest

from complaints import encrypt_evidence
from complaints.analysis import ComplaintAnalysisPipeline
from complaints.evidence import MAGIC, EvidenceStorage, EvidenceUnreadable

KEY = bytes(range(32))  # test key
OTHER = bytes(32)
FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_kyc_sms.png"
PNG = FIXTURE.read_bytes()


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def complaint_with_file(c, tok, data=PNG):
    r = await c.post("/complaints", headers=tok("a"), json={
        "category": "PHISHING", "title": "Bank message",
        "description": "I received a message about my bank account; screenshot attached."})  # fmt: skip
    cid = r.json()["id"]
    up = await c.post(f"/complaints/{cid}/evidence", headers=tok("a"),
                      files={"file": ("shot.png", data, "image/png")})  # fmt: skip
    assert up.status_code == 201, up.text
    return cid, up.json()


def on_disk(app, settings, ev_id):
    store = app.state.store
    row = next(e for e in store.list_evidence(store_cid(app, ev_id)) if e["id"] == ev_id)
    return EvidenceStorage(settings.evidence_dir).path_for(row["storage_key"]), row


def store_cid(app, ev_id):
    import sqlalchemy as sa

    from complaints.schema import complaint_evidence as ce

    with app.state.store.engine.connect() as c:
        return c.execute(sa.select(ce.c.complaint_id).where(ce.c.id == ev_id)).scalar_one()


async def test_files_are_ciphertext_on_disk_and_download_is_exact(make_app, settings, tok):
    app = make_app(settings=dataclasses.replace(settings, evidence_key=KEY))
    c = client_for(app)
    cid, ev = await complaint_with_file(c, tok)
    path, row = on_disk(app, settings, ev["id"])
    blob = path.read_bytes()
    assert blob.startswith(MAGIC) and PNG[:16] not in blob and b"IHDR" not in blob
    assert row["sha256"] == hashlib.sha256(PNG).hexdigest()  # the hash is of the original
    r = await c.get(f"/complaints/{cid}/evidence/{ev['id']}/download", headers=tok("a"))
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png" and r.headers["x-content-type-options"] == "nosniff"


async def test_swapped_or_unkeyed_files_are_refused_not_served(make_app, settings, tok):
    app = make_app(settings=dataclasses.replace(settings, evidence_key=KEY))
    c = client_for(app)
    cid, a = await complaint_with_file(c, tok)
    _, b = await complaint_with_file(c, tok, PNG + b"\x00")  # a second, different file
    pa, _ = on_disk(app, settings, a["id"])
    pb, _ = on_disk(app, settings, b["id"])
    pb.write_bytes(pa.read_bytes())  # someone copies A's ciphertext over B on disk
    r = await c.get(f"/complaints/{store_cid(app, b['id'])}/evidence/{b['id']}/download",
                    headers=tok("a"))  # fmt: skip
    assert r.status_code == 500 and r.json()["error"] == "evidence_unreadable"
    # a server started without the key cannot serve encrypted files either
    nokey = make_app()
    r = await client_for(nokey).get(f"/complaints/{cid}/evidence/{a['id']}/download",
                                    headers=tok("a"))  # fmt: skip
    assert r.status_code == 500 and r.json()["error"] == "evidence_unreadable"


async def test_files_stored_before_encryption_stay_readable(make_app, settings, tok):
    plain_app = make_app()
    cid, ev = await complaint_with_file(client_for(plain_app), tok)
    keyed = make_app(settings=dataclasses.replace(settings, evidence_key=KEY))
    r = await client_for(keyed).get(f"/complaints/{cid}/evidence/{ev['id']}/download",
                                    headers=tok("a"))  # fmt: skip
    assert r.status_code == 200 and r.content == PNG


class ReadingOcr:
    """Fake OCR engine that checks it is given the plaintext image."""

    def __init__(self):
        self.paths = []

    def status(self):
        return {"status": "ready", "reason": None, "engine": "tesseract", "version": "fake",
                "languages": ["eng"]}  # fmt: skip

    def read(self, path):
        self.paths.append(Path(path))
        assert Path(path).read_bytes() == PNG  # decrypted for the engine
        return "Your account will be blocked, update KYC and share the OTP.", 90.0


async def test_ocr_reads_a_temporary_plaintext_copy_that_is_then_deleted(make_app, settings, tok):
    pytest.importorskip("call_guard")
    eng = ReadingOcr()
    app = make_app(settings=dataclasses.replace(settings, evidence_key=KEY), ocr_engine=eng,
                   analysis=ComplaintAnalysisPipeline(category_loader=lambda: None))  # fmt: skip
    c = client_for(app)
    cid, _ = await complaint_with_file(c, tok)
    r = await c.post(f"/complaints/{cid}/analysis", headers=tok("a"))
    assert r.status_code == 201
    assert eng.paths and eng.paths[0].name.startswith("plain-")
    assert not eng.paths[0].exists()  # the decrypted copy did not outlive the analysis
    assert not list((settings.evidence_dir / ".tmp").glob("plain-*"))


async def test_migration_command_encrypts_existing_files(make_app, settings, tok):
    plain_app = make_app()
    c = client_for(plain_app)
    _, ev = await complaint_with_file(c, tok)
    store = plain_app.state.store
    storage = EvidenceStorage(settings.evidence_dir, key=KEY)
    assert encrypt_evidence.run(store, storage, apply=False)["plaintext"] == 1
    res = encrypt_evidence.run(store, storage, apply=True)
    assert res["encrypted_now"] == 1 and res["errors"] == 0
    again = encrypt_evidence.run(store, storage, apply=True)
    assert again["already_encrypted"] == 1 and again["encrypted_now"] == 0
    path, row = on_disk(plain_app, settings, ev["id"])
    assert path.read_bytes().startswith(MAGIC)
    assert storage.read_plain(row["storage_key"]) == PNG
    # a wrong key cannot read them
    with pytest.raises(EvidenceUnreadable):
        EvidenceStorage(settings.evidence_dir, key=OTHER).read_plain(row["storage_key"])


def test_new_key_is_32_random_bytes(capsys):
    import base64

    assert encrypt_evidence.main(["--new-key"]) == 0
    a = capsys.readouterr().out.strip()
    assert encrypt_evidence.main(["--new-key"]) == 0
    b = capsys.readouterr().out.strip()
    assert len(base64.urlsafe_b64decode(a)) == 32 and a != b
