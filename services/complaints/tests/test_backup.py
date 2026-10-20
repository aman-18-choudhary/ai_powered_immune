"""Backups (Phase 14): snapshot, manifest verification, restore into empty targets only.
Synthetic TEST DATA only."""

import json
import shutil
from pathlib import Path

import pytest

from complaints import backup
from complaints.store import ComplaintStore

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_kyc_sms.png"


async def populate(client, tok):
    for who in ("a", "b"):
        h = tok("a", sub=f"user_{who}", email=f"citizen.{who}@example.org")
        r = await client.post("/complaints", headers=h, json={
            "category": "UPI_FRAUD", "title": f"Report {who}",
            "description": "Synthetic complaint for the backup test, with a screenshot."})  # fmt: skip
        cid = r.json()["id"]
        up = await client.post(f"/complaints/{cid}/evidence", headers=h,
                               files={"file": ("shot.png", FIXTURE.read_bytes(), "image/png")})  # fmt: skip
        assert up.status_code == 201


def counts(url):
    store = ComplaintStore(url, create_schema=False)
    try:
        with store.engine.connect() as c:
            from sqlalchemy import func, select

            from complaints.schema import complaint_evidence, complaints

            return (c.execute(select(func.count()).select_from(complaints)).scalar_one(),
                    c.execute(select(func.count()).select_from(complaint_evidence)).scalar_one())  # fmt: skip
    finally:
        store.close()


async def test_backup_verify_restore_round_trip(app, client, tok, settings, tmp_path):
    if not shutil.which("pg_dump") and not settings.database_url.startswith("sqlite"):
        pytest.skip("pg_dump not installed")
    await populate(client, tok)
    out = backup.backup(settings.database_url, settings.evidence_dir, tmp_path / "backups")
    m = backup.verify(out)
    assert m["evidence_files"] == 2 and m["evidence_key_included"] is False
    assert m["schema_version"] >= 12
    text = (out / backup.MANIFEST).read_text()
    assert "@" not in text and "sqlite:///" not in text  # no connection string in the manifest
    if not settings.database_url.startswith("sqlite"):
        _pg_restore_round_trip(out, settings, tmp_path)
        return
    target_db = f"sqlite:///{tmp_path / 'restored' / 'c.db'}"
    target_ev = tmp_path / "restored" / "evidence"
    r = backup.restore(out, target_db, target_ev)
    assert r["evidence_files"] == 2
    assert counts(target_db) == counts(settings.database_url) == (2, 2)
    src = sorted(p.relative_to(settings.evidence_dir) for p in settings.evidence_dir.rglob("*")
                 if p.is_file() and ".tmp" not in p.parts)  # fmt: skip
    for rel in src:
        assert (target_ev / rel).read_bytes() == (settings.evidence_dir / rel).read_bytes()
    # never over existing data
    with pytest.raises(backup.BackupError, match="not empty"):
        backup.restore(out, target_db, tmp_path / "other-ev")
    with pytest.raises(backup.BackupError, match="evidence directory is not empty"):
        backup.restore(out, f"sqlite:///{tmp_path / 'fresh.db'}", target_ev)


def _pg_restore_round_trip(out, settings, tmp_path):
    """Postgres: restore into a second, empty database created for the test, then drop it."""
    import secrets

    import sqlalchemy as sa

    base = settings.database_url
    name = f"restore_{secrets.token_hex(4)}"
    admin = sa.create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{name}"'))
    target = base.rsplit("/", 1)[0] + "/" + name
    try:
        r = backup.restore(out, target, tmp_path / "pg-restored-ev")
        assert r["evidence_files"] == 2
        assert counts(target) == counts(base) == (2, 2)
        with pytest.raises(backup.BackupError, match="not empty"):
            backup.restore(out, target, tmp_path / "pg-other-ev")
    finally:
        sa.create_engine(target).dispose()
        with admin.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


async def test_tampering_is_detected(app, client, tok, settings, tmp_path):
    if not settings.database_url.startswith("sqlite"):
        pytest.skip("SQLite path")
    await populate(client, tok)
    out = backup.backup(settings.database_url, settings.evidence_dir, tmp_path / "b")
    ev = next(p for p in (out / "evidence").rglob("*") if p.is_file())
    ev.write_bytes(ev.read_bytes() + b"x")
    with pytest.raises(backup.BackupError, match="1 changed or missing"):
        backup.verify(out)
    ev.unlink()
    (out / "evidence" / "planted.bin").write_bytes(b"x")
    with pytest.raises(backup.BackupError, match="1 unexpected"):
        backup.verify(out)
    m = json.loads((out / backup.MANIFEST).read_text())
    m["format"] = "something-else"
    (out / backup.MANIFEST).write_text(json.dumps(m))
    with pytest.raises(backup.BackupError, match="unknown format"):
        backup.verify(out)


def test_cli_needs_configuration(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("COMPLAINTS_DATABASE_URL", raising=False)
    monkeypatch.delenv("COMPLAINTS_EVIDENCE_DIR", raising=False)
    assert backup.main(["--out", str(tmp_path)]) == 2
    assert backup.main(["--restore", str(tmp_path)]) == 2
    with pytest.raises(SystemExit):
        backup.main([])  # one of --out / --verify / --restore is required


async def test_files_are_private_and_prune_keeps_the_newest_verified(app, client, tok, settings,
                                                                    tmp_path):  # fmt: skip
    if not shutil.which("pg_dump") and not settings.database_url.startswith("sqlite"):
        pytest.skip("pg_dump not installed")
    from datetime import UTC, datetime, timedelta

    await populate(client, tok)
    root = tmp_path / "backups"
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    made = [backup.backup(settings.database_url, settings.evidence_dir, root,
                          now=t0 + timedelta(days=i)) for i in range(4)]  # fmt: skip
    for p in made[0].rglob("*"):
        assert (p.stat().st_mode & 0o077) == 0, p  # nothing readable by group or others
    (root / "notes").mkdir()  # not a backup: never touched
    (root / "notes" / "readme.txt").write_text("operator notes")
    db_file = made[3] / backup.verify(made[3])["database_file"]
    with open(db_file, "ab") as f:  # the newest backup is damaged
        f.write(b"x")

    dry = backup.prune(root, keep=2, apply=False)
    assert dry["unverified_kept"] == [made[3].name]  # the damaged newest is kept, not counted
    assert dry["kept"] == [made[2].name, made[1].name]
    assert dry["would_delete"] == [made[0].name] and made[0].exists()

    done = backup.prune(root, keep=2, apply=True)
    assert done["deleted"] == [made[0].name]
    assert not made[0].exists() and all(m.exists() for m in made[1:])
    assert (root / "notes" / "readme.txt").exists()
    with pytest.raises(backup.BackupError):
        backup.prune(root, keep=0, apply=True)


def test_overlapping_backups_are_refused(settings, monkeypatch, tmp_path, capsys):
    from complaints.joblock import job_lock

    monkeypatch.setenv("COMPLAINTS_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("COMPLAINTS_EVIDENCE_DIR", str(settings.evidence_dir))
    with job_lock(settings.database_url, "backup"):
        assert backup.main(["--out", str(tmp_path / "b")]) == 3
    assert not (tmp_path / "b").exists()
