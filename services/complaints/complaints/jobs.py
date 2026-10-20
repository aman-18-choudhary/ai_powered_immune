"""Scheduled maintenance for the complaints service (Phase 14): backups and the retention job.

    python -m complaints.jobs --once     # run every enabled job once, then exit (cron, timers)
    python -m complaints.jobs            # stay running; run each job at its interval

Configuration (environment; COMPLAINTS_DATABASE_URL and COMPLAINTS_EVIDENCE_DIR as the service):

* ``COMPLAINTS_BACKUP_DIR``: enables backups (unset: no backup job). Each backup is verified
  right after it is written.
* ``COMPLAINTS_BACKUP_KEEP``: after a verified backup, delete older ones beyond the newest N
  verified (unset: never delete backups).
* ``COMPLAINTS_BACKUP_INTERVAL_HOURS`` (default 24).
* ``COMPLAINTS_RETENTION_DAYS``: enables the retention job (unset: no retention job). There is
  no default period; it is a policy decision.
* ``COMPLAINTS_RETENTION_APPLY=1``: actually erase. Unset or 0: DRY RUN, only a summary is
  logged. Do not enable before the retention policy is approved.
* ``COMPLAINTS_RETENTION_MAX_ERASE`` (default 100), ``COMPLAINTS_RETENTION_INTERVAL_HOURS``
  (default 24).

Every job takes the same lock as the command-line tools, so a scheduled run never overlaps a
manual one (the later one is skipped and logged). Only counts and paths are logged.
"""

import argparse
import logging
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import backup, retention
from .evidence import EvidenceStorage
from .joblock import JobLocked, job_lock
from .store import ComplaintStore

log = logging.getLogger("complaints.jobs")


class JobConfigError(Exception):
    pass


@dataclass(frozen=True)
class JobConfig:
    database_url: str
    evidence_dir: Path
    backup_dir: Path | None = None
    backup_keep: int | None = None
    backup_interval_h: float = 24.0
    retention_days: int | None = None
    retention_apply: bool = False
    retention_max_erase: int = retention.DEFAULT_MAX_ERASE
    retention_interval_h: float = 24.0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "JobConfig":
        e = dict(os.environ if env is None else env)

        def get(name: str) -> str:
            return e.get(name, "").strip()

        def number(name: str, kind: Callable[[str], Any], minimum: float) -> Any:
            raw = get(name)
            if not raw:
                return None
            try:
                v = kind(raw)
            except ValueError:
                raise JobConfigError(f"{name} must be a number") from None
            if v < minimum:
                raise JobConfigError(f"{name} must be at least {minimum}")
            return v

        url, ev = get("COMPLAINTS_DATABASE_URL"), get("COMPLAINTS_EVIDENCE_DIR")
        if not url or not ev:
            raise JobConfigError(
                "COMPLAINTS_DATABASE_URL and COMPLAINTS_EVIDENCE_DIR must be set"
            )
        apply_raw = get("COMPLAINTS_RETENTION_APPLY").lower()
        if apply_raw not in ("", "0", "1", "false", "true"):
            raise JobConfigError("COMPLAINTS_RETENTION_APPLY must be 0 or 1")
        cfg = cls(
            database_url=url, evidence_dir=Path(ev),
            backup_dir=Path(get("COMPLAINTS_BACKUP_DIR")) if get("COMPLAINTS_BACKUP_DIR") else None,
            backup_keep=number("COMPLAINTS_BACKUP_KEEP", int, 1),
            backup_interval_h=number("COMPLAINTS_BACKUP_INTERVAL_HOURS", float, 0.1) or 24.0,
            retention_days=number("COMPLAINTS_RETENTION_DAYS", int, 1),
            retention_apply=apply_raw in ("1", "true"),
            retention_max_erase=number("COMPLAINTS_RETENTION_MAX_ERASE", int, 1)
            or retention.DEFAULT_MAX_ERASE,
            retention_interval_h=number("COMPLAINTS_RETENTION_INTERVAL_HOURS", float, 0.1) or 24.0,
        )  # fmt: skip
        if cfg.retention_apply and cfg.retention_days is None:
            raise JobConfigError("COMPLAINTS_RETENTION_APPLY needs COMPLAINTS_RETENTION_DAYS")
        return cfg


def backup_job(cfg: JobConfig) -> dict[str, Any]:
    assert cfg.backup_dir is not None
    with job_lock(cfg.database_url, "backup"):
        out = backup.backup(cfg.database_url, cfg.evidence_dir, cfg.backup_dir)
        m = backup.verify(out)
        res: dict[str, Any] = {"backup": out.name, "evidence_files": m["evidence_files"],
                               "schema_version": m["schema_version"]}  # fmt: skip
        if cfg.backup_keep is not None:
            p = backup.prune(cfg.backup_dir, cfg.backup_keep, apply=True)
            res.update(kept=len(p["kept"]), deleted=len(p["deleted"]),
                       unverified_kept=len(p["unverified_kept"]))  # fmt: skip
    log.info("backup job: %s", res)
    return res


def retention_job(cfg: JobConfig) -> dict[str, Any]:
    assert cfg.retention_days is not None
    with job_lock(cfg.database_url, "retention"):
        store = ComplaintStore(cfg.database_url)
        try:
            res = retention.run(
                store, EvidenceStorage(cfg.evidence_dir), cfg.retention_days,
                apply=cfg.retention_apply, max_erase=cfg.retention_max_erase,
            )  # fmt: skip
        finally:
            store.close()
    res.pop("ids", None)
    log.info("%s", retention.summary_line(res))
    return res


def run_once(cfg: JobConfig) -> dict[str, Any]:
    """Each enabled job once. A job that fails or is locked is reported; the others still run."""
    results: dict[str, Any] = {}
    jobs: list[tuple[str, Callable[[JobConfig], dict[str, Any]]]] = []
    if cfg.backup_dir is not None:
        jobs.append(("backup", backup_job))
    if cfg.retention_days is not None:
        jobs.append(("retention", retention_job))
    for name, fn in jobs:
        try:
            res = fn(cfg)
            results[name] = {"ok": not (res.get("failed") or res.get("files_failed")), **res}
        except JobLocked:
            log.warning("%s job skipped: another run holds the lock", name)
            results[name] = {"ok": False, "reason": "locked"}
        except Exception as exc:  # noqa: BLE001 - reported, never kills the scheduler
            log.error("%s job failed: %s", name, type(exc).__name__)
            results[name] = {"ok": False, "reason": type(exc).__name__}
    return results


def loop(
    cfg: JobConfig, sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic, max_cycles: int | None = None,
) -> None:  # fmt: skip
    intervals = {"backup": cfg.backup_interval_h * 3600,
                 "retention": cfg.retention_interval_h * 3600}  # fmt: skip
    next_at = {"backup": 0.0, "retention": 0.0}
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        now = clock()
        for name, fn in (("backup", backup_job), ("retention", retention_job)):
            enabled = (cfg.backup_dir if name == "backup" else cfg.retention_days) is not None
            if not enabled or now < next_at[name]:
                continue
            next_at[name] = now + intervals[name]
            try:
                fn(cfg)
            except JobLocked:
                log.warning("%s job skipped: another run holds the lock", name)
            except Exception as exc:  # noqa: BLE001
                log.error("%s job failed: %s", name, type(exc).__name__)
        cycles += 1
        sleep(60)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--once", action="store_true", help="run each enabled job once and exit")
    a = ap.parse_args(argv)
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")  # fmt: skip
    try:
        cfg = JobConfig.from_env()
    except JobConfigError as exc:
        print(f"jobs: {exc}", file=sys.stderr)
        return 2
    if cfg.backup_dir is None and cfg.retention_days is None:
        print("jobs: nothing enabled (set COMPLAINTS_BACKUP_DIR and/or "
              "COMPLAINTS_RETENTION_DAYS)", file=sys.stderr)  # fmt: skip
        return 2
    mode = "APPLY" if cfg.retention_apply else "dry run"
    log.info("jobs: backup %s, retention %s",
             "on" if cfg.backup_dir else "off",
             f"{cfg.retention_days} days ({mode})" if cfg.retention_days else "off")  # fmt: skip
    if a.once:
        res = run_once(cfg)
        return 0 if all(r["ok"] for r in res.values()) else 1
    loop(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
