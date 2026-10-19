"""Retention erasure of complaints deleted by their owners (Phase 14). Synthetic TEST DATA only."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from complaints import retention
from complaints.evidence import EvidenceStorage
from complaints.schema import (
    admin_audit,
    complaint_entities,
    complaint_evidence,
    complaints,
    investigation_notes,
)

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
       b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00"
       b"\x00\x00IEND\xaeB`\x82")  # fmt: skip


def citizen(tok, who):
    return tok("a", sub=f"user_{who}", email=f"citizen.{who}@example.org")


async def make(client, tok, inv, who):
    h = citizen(tok, who)
    r = await client.post("/complaints", headers=h, json={
        "category": "UPI_FRAUD", "title": f"Secret title {who}",
        "description": f"Private story of {who}: a caller asked for my UPI PIN.",
        "upi_id": "mule.one@ybl", "bank_account": "123456789012", "city": "Pune",
        "latitude": 18.5, "longitude": 73.8})  # fmt: skip
    cid = r.json()["id"]
    up = await client.post(f"/complaints/{cid}/evidence", headers=h,
                           files={"file": ("shot.png", PNG, "image/png")})  # fmt: skip
    assert up.status_code == 201, up.text
    await client.post(f"/complaints/{cid}/notes", headers=inv, json={"body": f"Note about {who}"})
    return cid, h


def backdate(app, cid, days):
    with app.state.store.engine.begin() as c:
        c.execute(sa.update(complaints).where(complaints.c.id == cid)
                  .values(deleted_at=datetime.now(UTC) - timedelta(days=days)))  # fmt: skip


async def test_dry_run_then_erase_only_old_deleted_complaints(app, client, tok, inv, settings):
    store = app.state.store
    old, h_old = await make(client, tok, inv, "old")
    recent, h_recent = await make(client, tok, inv, "recent")
    active, _ = await make(client, tok, inv, "active")
    for cid, h in ((old, h_old), (recent, h_recent)):
        assert (await client.delete(f"/complaints/{cid}", headers=h)).status_code == 204
    backdate(app, old, 40)
    backdate(app, recent, 5)
    storage = EvidenceStorage(settings.evidence_dir)
    files = {cid: list(storage.root.rglob("*")) for cid in (old,)}
    assert files[old]

    dry = retention.run(store, storage, 30, apply=False)
    assert dry == {"due": 1, "applied": False, "erased": 0}
    with store.engine.connect() as c:
        row = c.execute(sa.select(complaints).where(complaints.c.id == old)).one()
    assert row.description.startswith("Private story")  # dry run changed nothing

    done = retention.run(store, storage, 30, apply=True)
    assert done == {"due": 1, "applied": True, "erased": 1}
    with store.engine.connect() as c:
        row = c.execute(sa.select(complaints).where(complaints.c.id == old)).one()
        assert row.description == retention.PURGED and "Secret" not in row.title
        for f in ("upi_id", "bank_account", "city", "latitude", "longitude"):
            assert getattr(row, f) is None
        assert c.execute(sa.select(complaint_entities).where(
            complaint_entities.c.complaint_id == old)).all() == []  # fmt: skip
        ev = c.execute(sa.select(complaint_evidence).where(
            complaint_evidence.c.complaint_id == old)).one()  # fmt: skip
        assert ev.status == "deleted" and ev.original_filename == "erased"
        assert not storage.path_for(ev.storage_key).exists()  # the file is gone from disk
        note = c.execute(sa.select(investigation_notes.c.body).where(
            investigation_notes.c.complaint_id == old)).scalar_one()  # fmt: skip
        assert note == retention.PURGED
        audit = c.execute(sa.select(admin_audit).where(admin_audit.c.action == "retention_purge")).one()
        assert audit.after["complaint_id"] == old and audit.after["evidence_files"] == 1
        assert "Private" not in str(audit.after)
        # the recent deleted one and the active one are untouched
        for cid in (recent, active):
            r = c.execute(sa.select(complaints.c.description).where(complaints.c.id == cid)).scalar_one()
            assert r.startswith("Private story")
    # idempotent: nothing left to erase
    assert retention.run(store, storage, 30, apply=True)["due"] == 0


def test_the_period_is_required_and_positive(app):
    with pytest.raises(ValueError):
        retention.run(app.state.store, None, 0, apply=False)
    with pytest.raises(SystemExit):
        retention.main([])  # --older-than-days is required, there is no default
