"""Erase complaints their owners deleted, after a retention period (Phase 14).

    python -m complaints.retention --older-than-days N            # dry run: summary only
    python -m complaints.retention --older-than-days N --apply    # erase

Uses COMPLAINTS_DATABASE_URL and COMPLAINTS_EVIDENCE_DIR. The retention period is a policy
decision: there is no default, ``N`` must be given (minimum 1). Only complaints that were
soft-deleted (``deleted_at``) more than ``N`` days ago and are not yet erased (``purged_at``)
are touched. Owners can delete a complaint only while it is SUBMITTED, so a complaint staff are
working on is never deleted and never erased; each complaint is re-checked inside its own
transaction before anything changes.

What is erased (one transaction per complaint, then the files):
* evidence files on disk, and the evidence rows are marked ``deleted``; file names are cleared;
* audio transcripts and screenshot (OCR) text;
* the complaint's text and every optional detail (identifiers, bank details, place, coordinates);
* network identifiers (``complaint_entities``), link-review notes, investigation note text,
  status-history notes, analysis explanations, indicators and details (they quote the text).

What is kept: the complaint id, category, status, timestamps and the history rows without notes,
so references and the audit trail stay consistent. Backups taken before the erasure still hold
the data until they expire (see docs/OPERATIONS.md).

Safety
* Dry run by default: prints what WOULD be erased (counts per kind) and changes nothing.
* ``--max-erase`` (default 100): an apply run that finds more due complaints refuses to start,
  so a wrong clock or period cannot erase everything at once. Raise it deliberately.
* Overlapping runs are refused (``complaints.joblock``); exit code 3.
* Partial failure: a complaint whose transaction fails is rolled back and reported; a file that
  cannot be deleted is reported and retried by every later apply run (erased complaints whose
  files remain on disk are swept again). Exit code 1 when anything failed.
* Idempotent: erased complaints are marked ``purged_at`` and never processed twice.
* Audited: one ``retention_purge`` row per complaint and one ``retention_run`` row per apply run
  (ids and counts only). Nothing is printed or logged except counts.
"""

import argparse
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update

from .evidence import EvidenceStorage
from .joblock import JobLocked, job_lock
from .schema import (
    analyses,
    complaint_entities,
    complaint_evidence,
    complaint_status_history,
    complaints,
    evidence_ocr,
    evidence_transcripts,
    investigation_notes,
    link_reviews,
)
from .store import ComplaintStore

log = logging.getLogger("complaints.retention")

PURGED = "[erased under the retention policy]"
DEFAULT_MAX_ERASE = 100
OPTIONAL_FIELDS = (
    "incident_at", "amount", "currency", "payment_method", "upi_id", "utr_reference",
    "suspect_phone", "suspect_email", "suspicious_url", "location_text", "city", "state",
    "latitude", "longitude", "social_platform", "social_account", "bank_name", "bank_account",
    "ifsc",
)  # fmt: skip


class TooMany(Exception):
    pass


def _eligible(cutoff: datetime) -> list[Any]:
    return [complaints.c.deleted_at.is_not(None), complaints.c.deleted_at < cutoff,
            complaints.c.purged_at.is_(None)]  # fmt: skip


def due(store: ComplaintStore, older_than_days: int, now: datetime) -> list[str]:
    cutoff = now - timedelta(days=older_than_days)
    with store.engine.connect() as conn:
        return list(conn.execute(
            select(complaints.c.id).where(*_eligible(cutoff)).order_by(complaints.c.deleted_at)
        ).scalars())  # fmt: skip


def _count(conn: Any, tbl: Any, col: Any, ids: list[str], *extra: Any) -> int:
    if not ids:
        return 0
    return int(conn.execute(
        select(func.count()).select_from(tbl).where(col.in_(ids), *extra)
    ).scalar_one())  # fmt: skip


def leftover_files(store: ComplaintStore, storage: EvidenceStorage | None) -> list[str]:
    """Storage keys of erased complaints whose files are still on disk (an earlier failure)."""
    if storage is None:
        return []
    with store.engine.connect() as conn:
        keys = conn.execute(
            select(complaint_evidence.c.storage_key)
            .join(complaints, complaints.c.id == complaint_evidence.c.complaint_id)
            .where(complaints.c.purged_at.is_not(None))
        ).scalars().all()  # fmt: skip
    return [k for k in keys if storage.path_for(k).exists()]


def plan(store: ComplaintStore, storage: EvidenceStorage | None, older_than_days: int,
         now: datetime) -> dict[str, Any]:  # fmt: skip
    """What an apply run would erase now: complaint ids and counts per kind (no content)."""
    ids = due(store, older_than_days, now)
    with store.engine.connect() as conn:
        counts = {
            "complaints": len(ids),
            "evidence_files": _count(conn, complaint_evidence, complaint_evidence.c.complaint_id,
                                     ids),  # fmt: skip
            "transcripts": _count(conn, evidence_transcripts,
                                  evidence_transcripts.c.complaint_id, ids),  # fmt: skip
            "ocr_texts": _count(conn, evidence_ocr, evidence_ocr.c.complaint_id, ids),
            "identifiers": _count(conn, complaint_entities, complaint_entities.c.complaint_id,
                                  ids),  # fmt: skip
            "investigation_notes": _count(conn, investigation_notes,
                                          investigation_notes.c.complaint_id, ids),  # fmt: skip
            "status_notes": _count(conn, complaint_status_history,
                                   complaint_status_history.c.complaint_id, ids,
                                   complaint_status_history.c.note.is_not(None)),  # fmt: skip
            "analyses": _count(conn, analyses, analyses.c.complaint_id, ids),
        }
    return {"cutoff": (now - timedelta(days=older_than_days)).isoformat(), "ids": ids,
            "counts": counts, "leftover_files": len(leftover_files(store, storage))}  # fmt: skip


def purge_one(store: ComplaintStore, cid: str, cutoff: datetime,
              now: datetime) -> tuple[dict[str, int], list[str]] | None:  # fmt: skip
    """Erase one complaint in one transaction. Returns (counts, storage keys to delete), or None
    when the complaint is no longer eligible (re-checked under the row lock)."""
    counts: dict[str, int] = {}
    with store._tx() as conn:
        row = conn.execute(
            select(complaints.c.id).where(complaints.c.id == cid, *_eligible(cutoff))
            .with_for_update()
        ).first()  # fmt: skip
        if row is None:
            return None
        keys = list(conn.execute(
            select(complaint_evidence.c.storage_key)
            .where(complaint_evidence.c.complaint_id == cid)
        ).scalars())  # fmt: skip
        counts["evidence_files"] = len(keys)
        conn.execute(
            update(complaint_evidence).where(complaint_evidence.c.complaint_id == cid)
            .values(status="deleted", original_filename="erased",
                    deleted_at=func.coalesce(complaint_evidence.c.deleted_at, now))
        )  # fmt: skip
        for name, tbl in (("transcripts", evidence_transcripts), ("ocr_texts", evidence_ocr),
                          ("identifiers", complaint_entities)):  # fmt: skip
            counts[name] = conn.execute(delete(tbl).where(tbl.c.complaint_id == cid)).rowcount
        counts["investigation_notes"] = conn.execute(
            update(investigation_notes).where(investigation_notes.c.complaint_id == cid)
            .values(body=PURGED)
        ).rowcount  # fmt: skip
        conn.execute(
            update(complaint_status_history).where(complaint_status_history.c.complaint_id == cid)
            .values(note=None)
        )  # fmt: skip
        for col in ("complaint_low", "complaint_high"):
            conn.execute(update(link_reviews).where(link_reviews.c[col] == cid).values(note=None))
        counts["analyses"] = conn.execute(
            update(analyses).where(analyses.c.complaint_id == cid)
            .values(explanation=None, detail=None, indicators=[])
        ).rowcount  # fmt: skip
        conn.execute(
            update(complaints).where(complaints.c.id == cid).values(
                title=PURGED[:200], description=PURGED, updated_at=now, purged_at=now,
                **{f: None for f in OPTIONAL_FIELDS},
            )
        )  # fmt: skip
        store._audit(conn, None, "retention_purge", None, {},
                     {"complaint_id": cid, **counts}, now)  # fmt: skip
    return counts, keys


def _remove_files(storage: EvidenceStorage | None, keys: list[str]) -> tuple[int, int]:
    if storage is None:
        return 0, len(keys)
    removed = failed = 0
    for k in keys:
        existed = storage.path_for(k).exists()
        if storage.remove_checked(k):
            removed += existed
        else:
            failed += 1
    return removed, failed


def run(store: ComplaintStore, storage: EvidenceStorage | None, older_than_days: int,
        apply: bool, now: datetime | None = None,
        max_erase: int = DEFAULT_MAX_ERASE) -> dict[str, Any]:  # fmt: skip
    if older_than_days < 1:
        raise ValueError("older_than_days must be at least 1")
    if max_erase < 1:
        raise ValueError("max_erase must be at least 1")
    now = now or datetime.now(UTC)
    p = plan(store, storage, older_than_days, now)
    out: dict[str, Any] = {
        "applied": apply, "older_than_days": older_than_days, "cutoff": p["cutoff"],
        "due": len(p["ids"]), "ids": p["ids"], "would_erase": p["counts"],
        "leftover_files": p["leftover_files"],
        "erased": 0, "skipped": 0, "failed": 0, "files_removed": 0, "files_failed": 0,
    }  # fmt: skip
    if not apply:
        return out
    if len(p["ids"]) > max_erase:
        raise TooMany(f"{len(p['ids'])} complaints are due, more than --max-erase {max_erase}")
    cutoff = now - timedelta(days=older_than_days)
    for cid in p["ids"]:
        try:
            res = purge_one(store, cid, cutoff, now)
        except Exception:  # rolled back; reported, the run continues with the next one
            log.exception("retention: erasing complaint %s failed", cid)
            out["failed"] += 1
            continue
        if res is None:
            out["skipped"] += 1
            continue
        out["erased"] += 1
        removed, failed = _remove_files(storage, res[1])
        out["files_removed"] += removed
        out["files_failed"] += failed
    # files left behind by earlier runs (or by this one) are retried
    removed, failed = _remove_files(storage, leftover_files(store, storage))
    out["files_removed"] += removed
    out["files_failed"] = failed  # what is still on disk now
    store.record_audit(None, "retention_run", {
        k: out[k] for k in ("older_than_days", "cutoff", "due", "erased", "skipped", "failed",
                            "files_removed", "files_failed")}, now)  # fmt: skip
    return out


def summary_line(res: dict[str, Any]) -> str:
    c = res["would_erase"]
    kinds = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in c.items() if k != "complaints")
    head = (f"retention (older than {res['older_than_days']} days, deleted before "
            f"{res['cutoff']}): {res['due']} complaint(s) due")  # fmt: skip
    if not res["applied"]:
        return (f"{head}; DRY RUN, nothing changed. Would erase: {kinds}; "
                f"{res['leftover_files']} leftover file(s) to retry. "
                "Add --apply to erase.")  # fmt: skip
    return (f"{head}; erased {res['erased']}, skipped {res['skipped']}, "
            f"failed {res['failed']}; files removed {res['files_removed']}, "
            f"still on disk {res['files_failed']}")  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--older-than-days", type=int, required=True)
    ap.add_argument("--apply", action="store_true", help="erase (default: dry run)")
    ap.add_argument("--max-erase", type=int, default=DEFAULT_MAX_ERASE,
                    help="refuse an apply run with more complaints due (default 100)")  # fmt: skip
    ap.add_argument("--list-ids", action="store_true", help="also print the due complaint ids")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    a = ap.parse_args(argv)
    url = os.environ.get("COMPLAINTS_DATABASE_URL", "").strip()
    ev_dir = os.environ.get("COMPLAINTS_EVIDENCE_DIR", "").strip()
    if not url or not ev_dir:
        print("COMPLAINTS_DATABASE_URL and COMPLAINTS_EVIDENCE_DIR must be set", file=sys.stderr)
        return 2
    store = ComplaintStore(url)  # applies pending migrations, like the service does
    try:
        with job_lock(url, "retention"):
            storage = EvidenceStorage(Path(ev_dir))
            res = run(store, storage, a.older_than_days, a.apply, max_erase=a.max_erase)
    except JobLocked:
        print("retention: another run is in progress; nothing done", file=sys.stderr)
        return 3
    except TooMany as exc:
        print(f"retention: refused: {exc}; nothing done", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        store.close()
    ids = res.pop("ids")
    if a.json:
        import json

        print(json.dumps({**res, **({"ids": ids} if a.list_ids else {})}, indent=2))
    else:
        print(summary_line(res))
        if a.list_ids:
            print("\n".join(ids))
    return 1 if res["failed"] or res["files_failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
