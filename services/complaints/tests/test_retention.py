"""Retention erasure of complaints deleted by their owners (Phase 14). Synthetic TEST DATA only,
in a disposable database per test."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from complaints import retention
from complaints.evidence import EvidenceStorage
from complaints.joblock import JobLocked, job_lock
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


async def make(client, tok, inv, who, description=None):
    h = citizen(tok, who)
    r = await client.post("/complaints", headers=h, json={
        "category": "UPI_FRAUD", "title": f"Secret title {who}",
        "description": description or f"Private story of {who}: a caller asked for my UPI PIN.",
        "upi_id": "mule.one@ybl", "bank_account": "123456789012", "city": "Pune",
        "latitude": 18.5, "longitude": 73.8})  # fmt: skip
    cid = r.json()["id"]
    up = await client.post(f"/complaints/{cid}/evidence", headers=h,
                           files={"file": ("shot.png", PNG, "image/png")})  # fmt: skip
    assert up.status_code == 201, up.text
    await client.post(f"/complaints/{cid}/notes", headers=inv, json={"body": f"Note about {who}"})
    return cid, h


async def make_deleted(client, tok, inv, who, days, app, description=None):
    cid, h = await make(client, tok, inv, who, description)
    assert (await client.delete(f"/complaints/{cid}", headers=h)).status_code == 204
    backdate(app, cid, days)
    return cid


def backdate(app, cid, days):
    with app.state.store.engine.begin() as c:
        c.execute(sa.update(complaints).where(complaints.c.id == cid)
                  .values(deleted_at=datetime.now(UTC) - timedelta(days=days)))  # fmt: skip


def row(store, cid):
    with store.engine.connect() as c:
        return c.execute(sa.select(complaints).where(complaints.c.id == cid)).one()


def evidence_keys(store, cid):
    with store.engine.connect() as c:
        return list(c.execute(sa.select(complaint_evidence.c.storage_key)
                              .where(complaint_evidence.c.complaint_id == cid)).scalars())  # fmt: skip


async def test_dry_run_then_erase_only_old_deleted_complaints(app, client, tok, inv, settings):
    store = app.state.store
    old = await make_deleted(client, tok, inv, "old", 40, app)
    recent = await make_deleted(client, tok, inv, "recent", 5, app)
    active, _ = await make(client, tok, inv, "active")
    storage = EvidenceStorage(settings.evidence_dir)
    assert storage.path_for(evidence_keys(store, old)[0]).exists()

    dry = retention.run(store, storage, 30, apply=False)
    assert dry["applied"] is False and dry["due"] == 1 and dry["ids"] == [old]
    assert dry["would_erase"]["complaints"] == 1 and dry["would_erase"]["evidence_files"] == 1
    assert dry["would_erase"]["investigation_notes"] == 1 and dry["erased"] == 0
    assert row(store, old).description.startswith("Private story")  # dry run changed nothing
    with store.engine.connect() as c:  # and wrote no audit row
        assert c.execute(sa.select(admin_audit).where(
            admin_audit.c.action.like("retention%"))).all() == []  # fmt: skip
    assert "DRY RUN" in retention.summary_line(dry)

    done = retention.run(store, storage, 30, apply=True)
    assert done["erased"] == 1 and done["failed"] == 0 and done["files_removed"] == 1
    assert done["files_failed"] == 0
    r = row(store, old)
    assert r.description == retention.PURGED and "Secret" not in r.title and r.purged_at
    for f in ("upi_id", "bank_account", "city", "latitude", "longitude"):
        assert getattr(r, f) is None
    with store.engine.connect() as c:
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
        run_audit = c.execute(sa.select(admin_audit).where(admin_audit.c.action == "retention_run")).one()
        assert run_audit.after["erased"] == 1 and "ids" not in run_audit.after
    # the recent deleted one and the active one are untouched
    for cid in (recent, active):
        assert row(store, cid).description.startswith("Private story")
        assert row(store, cid).purged_at is None
    # idempotent: nothing left to erase, and a second apply changes nothing
    again = retention.run(store, storage, 30, apply=True)
    assert again["due"] == 0 and again["erased"] == 0 and again["files_failed"] == 0


async def test_a_complaint_containing_the_marker_text_is_still_erased(app, client, tok, inv,
                                                                     settings):  # fmt: skip
    """Erased complaints are recognised by purged_at, not by their text."""
    store = app.state.store
    cid = await make_deleted(client, tok, inv, "marker", 40, app, description=retention.PURGED
                             + " but also my account 123456789012 and the caller's story.")  # fmt: skip
    res = retention.run(store, EvidenceStorage(settings.evidence_dir), 30, apply=True)
    assert res["erased"] == 1 and row(store, cid).bank_account is None


async def test_active_complaints_are_never_erased_even_when_asked_directly(app, client, tok, inv):
    store = app.state.store
    active, _ = await make(client, tok, inv, "active")
    future_cutoff = datetime.now(UTC) + timedelta(days=365)
    assert retention.purge_one(store, active, future_cutoff, datetime.now(UTC)) is None
    assert row(store, active).description.startswith("Private story")


async def test_a_file_that_cannot_be_deleted_is_reported_and_retried(app, client, tok, inv,
                                                                    settings, monkeypatch):  # fmt: skip
    store = app.state.store
    cid = await make_deleted(client, tok, inv, "stuck", 40, app)
    storage = EvidenceStorage(settings.evidence_dir)
    monkeypatch.setattr(storage, "remove_checked", lambda key: False)  # e.g. a read-only volume
    first = retention.run(store, storage, 30, apply=True)
    assert first["erased"] == 1 and first["files_failed"] == 1
    assert row(store, cid).purged_at is not None  # the database part is done and committed
    key = evidence_keys(store, cid)[0]
    assert storage.path_for(key).exists()
    assert retention.run(store, storage, 30, apply=False)["leftover_files"] == 1
    monkeypatch.undo()
    second = retention.run(store, storage, 30, apply=True)
    assert second["due"] == 0 and second["files_removed"] == 1 and second["files_failed"] == 0
    assert not storage.path_for(key).exists()


async def test_a_failing_complaint_is_rolled_back_and_the_others_continue(app, client, tok, inv,
                                                                         settings, monkeypatch):  # fmt: skip
    store = app.state.store
    bad = await make_deleted(client, tok, inv, "bad", 50, app)
    good = await make_deleted(client, tok, inv, "good", 40, app)
    real_audit = store._audit

    def failing_audit(conn, actor, action, target, before, after, now):
        if action == "retention_purge" and after.get("complaint_id") == bad:
            raise RuntimeError("simulated database failure")
        return real_audit(conn, actor, action, target, before, after, now)

    monkeypatch.setattr(store, "_audit", failing_audit)
    storage = EvidenceStorage(settings.evidence_dir)
    res = retention.run(store, storage, 30, apply=True)
    assert res["failed"] == 1 and res["erased"] == 1
    b = row(store, bad)  # rolled back as a whole: text kept, not marked, file kept
    assert b.purged_at is None and b.description.startswith("Private story")
    assert storage.path_for(evidence_keys(store, bad)[0]).exists()
    assert row(store, good).purged_at is not None
    monkeypatch.undo()
    assert retention.run(store, storage, 30, apply=True)["erased"] == 1  # retried next run


async def test_max_erase_refuses_an_unexpectedly_large_run(app, client, tok, inv, settings):
    store = app.state.store
    ids = [await make_deleted(client, tok, inv, f"bulk{i}", 40, app) for i in range(3)]
    storage = EvidenceStorage(settings.evidence_dir)
    with pytest.raises(retention.TooMany):
        retention.run(store, storage, 30, apply=True, max_erase=2)
    assert all(row(store, c).purged_at is None for c in ids)  # nothing was touched
    assert retention.run(store, storage, 30, apply=False, max_erase=2)["due"] == 3  # dry run ok


def test_overlapping_runs_are_refused(settings):
    with job_lock(settings.database_url, "retention"):
        with pytest.raises(JobLocked):
            with job_lock(settings.database_url, "retention"):
                pass
        with job_lock(settings.database_url, "backup"):  # other jobs are independent
            pass
    with job_lock(settings.database_url, "retention"):  # released afterwards
        pass


async def test_cli_dry_run_by_default_and_refuses_while_locked(app, client, tok, inv, settings,
                                                              monkeypatch, capsys):  # fmt: skip
    cid = await make_deleted(client, tok, inv, "cli", 40, app)
    monkeypatch.setenv("COMPLAINTS_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("COMPLAINTS_EVIDENCE_DIR", str(settings.evidence_dir))
    assert retention.main(["--older-than-days", "30"]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "1 complaint(s) due" in out and "Private" not in out
    assert row(app.state.store, cid).purged_at is None
    with job_lock(settings.database_url, "retention"):
        assert retention.main(["--older-than-days", "30", "--apply"]) == 3
    assert row(app.state.store, cid).purged_at is None
    assert retention.main(["--older-than-days", "30", "--apply", "--max-erase", "5"]) == 0
    assert row(app.state.store, cid).purged_at is not None


def test_the_period_is_required_and_positive(app):
    with pytest.raises(ValueError):
        retention.run(app.state.store, None, 0, apply=False)
    with pytest.raises(SystemExit):
        retention.main([])  # --older-than-days is required, there is no default


async def test_migration_marks_complaints_erased_before_purged_at_existed(app, client, tok, inv):
    from complaints import migrations

    store = app.state.store
    cid = await make_deleted(client, tok, inv, "legacy", 40, app)
    with store.engine.begin() as c:  # the state an older version of this command left behind
        c.execute(sa.update(complaints).where(complaints.c.id == cid)
                  .values(description=retention.PURGED, purged_at=None))  # fmt: skip
    with store.engine.begin() as c:
        migrations.run(c)  # every start re-checks the steps (idempotent)
    assert row(store, cid).purged_at is not None
