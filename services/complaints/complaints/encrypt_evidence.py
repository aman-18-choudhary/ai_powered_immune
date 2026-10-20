"""Encrypt evidence files stored before encryption at rest was enabled (Phase 14).

    python -m complaints.encrypt_evidence --new-key     # a fresh COMPLAINTS_EVIDENCE_KEY
    python -m complaints.encrypt_evidence               # dry run: counts plaintext files
    python -m complaints.encrypt_evidence --apply       # encrypt them in place

Uses COMPLAINTS_DATABASE_URL, COMPLAINTS_EVIDENCE_DIR and COMPLAINTS_EVIDENCE_KEY. Each file is
checked against the SHA-256 recorded at upload before and after encryption, then atomically
replaced. Already encrypted and missing files are skipped and counted. Keep the key safe and
backed up separately: without it the encrypted files cannot be read. Prints counts only.
"""

import argparse
import base64
import os
import secrets
import sys
from pathlib import Path

from sqlalchemy import select

from .config import ConfigError, _evidence_key
from .evidence import EvidenceStorage, EvidenceUnreadable
from .schema import complaint_evidence
from .store import ComplaintStore


def run(store: ComplaintStore, storage: EvidenceStorage, apply: bool) -> dict[str, int]:
    out = {"plaintext": 0, "encrypted_now": 0, "already_encrypted": 0, "missing": 0, "errors": 0}
    with store.engine.connect() as conn:
        rows = conn.execute(
            select(complaint_evidence.c.storage_key, complaint_evidence.c.sha256)
            .where(complaint_evidence.c.status == "stored")
        ).all()  # fmt: skip
    for r in rows:
        try:
            path = storage.path_for(r.storage_key)
        except ValueError:
            out["errors"] += 1
            continue
        if not path.is_file():
            out["missing"] += 1
            continue
        if storage.is_encrypted(r.storage_key):
            out["already_encrypted"] += 1
            continue
        out["plaintext"] += 1
        if apply:
            try:
                storage.encrypt_existing(r.storage_key, r.sha256)
                out["encrypted_now"] += 1
            except (EvidenceUnreadable, OSError):
                out["errors"] += 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="encrypt (default: dry run)")
    ap.add_argument("--new-key", action="store_true", help="print a new random key and exit")
    a = ap.parse_args(argv)
    if a.new_key:
        print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
        return 0
    url = os.environ.get("COMPLAINTS_DATABASE_URL", "").strip()
    ev_dir = os.environ.get("COMPLAINTS_EVIDENCE_DIR", "").strip()
    try:
        key = _evidence_key(os.environ.get("COMPLAINTS_EVIDENCE_KEY"))
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not url or not ev_dir or key is None:
        print("COMPLAINTS_DATABASE_URL, COMPLAINTS_EVIDENCE_DIR and COMPLAINTS_EVIDENCE_KEY must "
              "be set", file=sys.stderr)  # fmt: skip
        return 2
    store = ComplaintStore(url)
    try:
        res = run(store, EvidenceStorage(Path(ev_dir), key=key), a.apply)
    finally:
        store.close()
    print("evidence encryption: " + ", ".join(f"{k} {v}" for k, v in res.items())
          + ("" if a.apply else " (dry run; add --apply)"))  # fmt: skip
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
