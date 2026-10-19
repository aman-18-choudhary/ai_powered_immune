"""Erase complaints their owners deleted, after a retention period (Phase 14).

    python -m complaints.retention --older-than-days N            # dry run: counts only
    python -m complaints.retention --older-than-days N --apply    # erase

Uses COMPLAINTS_DATABASE_URL and COMPLAINTS_EVIDENCE_DIR. The retention period is a policy
decision: there is no default, ``N`` must be given (minimum 1). Only complaints that were
soft-deleted (``deleted_at``) more than ``N`` days ago are touched.

What is erased (in one transaction per complaint, then the files):
* evidence files on disk, and the evidence rows are marked ``deleted``; file names are cleared;
* audio transcripts and screenshot (OCR) text;
* the complaint's text and every optional detail (identifiers, bank details, place, coordinates);
* network identifiers (``complaint_entities``), link-review notes, investigation note text,
  status-history notes, analysis explanations, indicators and details (they quote the text).

What is kept: the complaint id, category, status, timestamps and the history rows without notes,
so references and the audit trail stay consistent. Each erasure is audited
(``retention_purge``, complaint id and counts only). Nothing is printed except counts.
"""

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update

from .evidence import EvidenceStorage
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

PURGED = "[erased under the retention policy]"
OPTIONAL_FIELDS = (
    "incident_at", "amount", "currency", "payment_method", "upi_id", "utr_reference",
    "suspect_phone", "suspect_email", "suspicious_url", "location_text", "city", "state",
    "latitude", "longitude", "social_platform", "social_account", "bank_name", "bank_account",
    "ifsc",
)  # fmt: skip


def due(store: ComplaintStore, older_than_days: int, now: datetime) -> list[str]:
    cutoff = now - timedelta(days=older_than_days)
    with store.engine.connect() as conn:
        rows = conn.execute(
            select(complaints.c.id, complaints.c.description).where(
                complaints.c.deleted_at.is_not(None), complaints.c.deleted_at < cutoff
            )
        ).all()
    # already erased ones are skipped (idempotent)
    return [r.id for r in rows if r.description != PURGED]


def purge_one(store: ComplaintStore, storage: EvidenceStorage | None, cid: str,
              now: datetime) -> dict[str, int]:  # fmt: skip
    counts: dict[str, int] = {}
    keys: list[str] = []
    with store._tx() as conn:
        ev = conn.execute(
            select(complaint_evidence.c.id, complaint_evidence.c.storage_key)
            .where(complaint_evidence.c.complaint_id == cid)
        ).all()  # fmt: skip
        keys = [r.storage_key for r in ev]
        counts["evidence_files"] = len(ev)
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
                title=PURGED[:200], description=PURGED, updated_at=now,
                **{f: None for f in OPTIONAL_FIELDS},
            )
        )  # fmt: skip
        store._audit(conn, None, "retention_purge", None, {},
                     {"complaint_id": cid, **counts}, now)  # fmt: skip
    if storage is not None:
        for k in keys:
            storage.remove(k)
    return counts


def run(store: ComplaintStore, storage: EvidenceStorage | None, older_than_days: int,
        apply: bool, now: datetime | None = None) -> dict[str, Any]:  # fmt: skip
    if older_than_days < 1:
        raise ValueError("older_than_days must be at least 1")
    now = now or datetime.now(UTC)
    ids = due(store, older_than_days, now)
    out: dict[str, Any] = {"due": len(ids), "applied": apply, "erased": 0}
    if apply:
        for cid in ids:
            purge_one(store, storage, cid, now)
            out["erased"] += 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--older-than-days", type=int, required=True)
    ap.add_argument("--apply", action="store_true", help="erase (default: dry run)")
    a = ap.parse_args(argv)
    url = os.environ.get("COMPLAINTS_DATABASE_URL", "").strip()
    ev_dir = os.environ.get("COMPLAINTS_EVIDENCE_DIR", "").strip()
    if not url or not ev_dir:
        print("COMPLAINTS_DATABASE_URL and COMPLAINTS_EVIDENCE_DIR must be set", file=sys.stderr)
        return 2
    store = ComplaintStore(url)
    try:
        res = run(store, EvidenceStorage(Path(ev_dir)), a.older_than_days, a.apply)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        store.close()
    verb = "erased" if a.apply else "would erase (dry run; add --apply)"
    print(f"retention: {res['due']} deleted complaint(s) older than {a.older_than_days} days; "
          f"{verb} {res['erased'] if a.apply else res['due']}")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
