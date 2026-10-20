"""Scheduled maintenance jobs (Phase 14): configuration, dry-run default, no overlap.
Synthetic TEST DATA only, in disposable databases."""

import shutil
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from complaints import jobs
from complaints.joblock import job_lock
from complaints.schema import complaints


def env(settings, **extra):
    return {"COMPLAINTS_DATABASE_URL": settings.database_url,
            "COMPLAINTS_EVIDENCE_DIR": str(settings.evidence_dir), **extra}  # fmt: skip


async def deleted_complaint(app, client, tok, days=40):
    h = tok("a", sub="user_jobs", email="citizen.jobs@example.org")
    r = await client.post("/complaints", headers=h, json={
        "category": "UPI_FRAUD", "title": "Synthetic", "description":
        "Synthetic complaint for the jobs test: a caller asked for my UPI PIN."})  # fmt: skip
    cid = r.json()["id"]
    assert (await client.delete(f"/complaints/{cid}", headers=h)).status_code == 204
    with app.state.store.engine.begin() as c:
        c.execute(sa.update(complaints).where(complaints.c.id == cid)
                  .values(deleted_at=datetime.now(UTC) - timedelta(days=days)))  # fmt: skip
    return cid


def purged(app, cid):
    with app.state.store.engine.connect() as c:
        return c.execute(sa.select(complaints.c.purged_at).where(complaints.c.id == cid)).scalar()


def test_configuration_is_explicit(settings):
    cfg = jobs.JobConfig.from_env(env(settings))
    assert cfg.backup_dir is None and cfg.retention_days is None and not cfg.retention_apply
    with pytest.raises(jobs.JobConfigError, match="COMPLAINTS_RETENTION_DAYS"):
        jobs.JobConfig.from_env(env(settings, COMPLAINTS_RETENTION_APPLY="1"))
    with pytest.raises(jobs.JobConfigError, match="COMPLAINTS_RETENTION_POLICY_REF"):
        jobs.JobConfig.from_env(env(settings, COMPLAINTS_RETENTION_DAYS="30",
                                    COMPLAINTS_RETENTION_APPLY="1"))  # fmt: skip
    for bad in ({"COMPLAINTS_RETENTION_DAYS": "0"}, {"COMPLAINTS_RETENTION_DAYS": "ten"},
                {"COMPLAINTS_BACKUP_KEEP": "0"}, {"COMPLAINTS_RETENTION_APPLY": "yes"}):  # fmt: skip
        with pytest.raises(jobs.JobConfigError):
            jobs.JobConfig.from_env(env(settings, **bad))
    with pytest.raises(jobs.JobConfigError):
        jobs.JobConfig.from_env({})


async def test_scheduled_retention_is_a_dry_run_unless_applied(app, client, tok, settings):
    cid = await deleted_complaint(app, client, tok)
    dry = jobs.run_once(jobs.JobConfig.from_env(env(settings, COMPLAINTS_RETENTION_DAYS="30")))
    assert dry["retention"]["ok"] and dry["retention"]["applied"] is False
    assert dry["retention"]["due"] == 1 and purged(app, cid) is None
    done = jobs.run_once(jobs.JobConfig.from_env(env(
        settings, COMPLAINTS_RETENTION_DAYS="30", COMPLAINTS_RETENTION_APPLY="1",
        COMPLAINTS_RETENTION_POLICY_REF="TEST-POLICY-1")))  # fmt: skip
    assert done["retention"]["erased"] == 1 and purged(app, cid) is not None
    assert done["retention"]["policy_ref"] == "TEST-POLICY-1"


async def test_a_locked_job_is_skipped_and_the_others_run(app, client, tok, settings, tmp_path):
    if not shutil.which("pg_dump") and not settings.database_url.startswith("sqlite"):
        pytest.skip("pg_dump not installed")
    cid = await deleted_complaint(app, client, tok)
    cfg = jobs.JobConfig.from_env(env(settings, COMPLAINTS_RETENTION_DAYS="30",
                                      COMPLAINTS_RETENTION_APPLY="1",
                                      COMPLAINTS_RETENTION_POLICY_REF="TEST-POLICY-1",
                                      COMPLAINTS_BACKUP_DIR=str(tmp_path / "b"),
                                      COMPLAINTS_BACKUP_KEEP="2"))  # fmt: skip
    with job_lock(settings.database_url, "retention"):  # e.g. a manual run in progress
        res = jobs.run_once(cfg)
    assert res["retention"] == {"ok": False, "reason": "locked"} and purged(app, cid) is None
    assert res["backup"]["ok"] and res["backup"]["kept"] == 1
    for _ in range(3):
        jobs.run_once(cfg)
    assert len(list((tmp_path / "b").iterdir())) == 2  # pruned to the newest 2 verified


async def test_the_loop_runs_each_job_at_its_interval(app, settings, monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "retention_job", lambda cfg: calls.append("retention") or {})
    monkeypatch.setattr(jobs, "backup_job", lambda cfg: calls.append("backup") or {})
    cfg = jobs.JobConfig.from_env(env(settings, COMPLAINTS_RETENTION_DAYS="30",
                                      COMPLAINTS_RETENTION_INTERVAL_HOURS="1"))  # fmt: skip
    t = [0.0]
    jobs.loop(cfg, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0],
              max_cycles=130)  # 130 minutes of fake time  # fmt: skip
    assert calls == ["retention"] * 3  # at 0, 60 and 120 minutes; backups are off


def test_cli_refuses_when_nothing_is_enabled(settings, monkeypatch):
    monkeypatch.setenv("COMPLAINTS_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("COMPLAINTS_EVIDENCE_DIR", str(settings.evidence_dir))
    for k in ("COMPLAINTS_BACKUP_DIR", "COMPLAINTS_RETENTION_DAYS"):
        monkeypatch.delenv(k, raising=False)
    assert jobs.main(["--once"]) == 2
