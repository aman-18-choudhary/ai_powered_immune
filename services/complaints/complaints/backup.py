"""Backups of the complaints data (Phase 14): a consistent database snapshot plus the evidence
files, with a SHA-256 manifest.

    python -m complaints.backup --out DIR            # write a new backup under DIR/<timestamp>
    python -m complaints.backup --verify BACKUP      # check every file against the manifest
    python -m complaints.backup --restore BACKUP --to-database-url URL --to-evidence-dir PATH

Backup uses COMPLAINTS_DATABASE_URL and COMPLAINTS_EVIDENCE_DIR.

* Database: SQLite through the online backup API (consistent while the service runs);
  Postgres through ``pg_dump --format=custom`` (needs the PostgreSQL client tools). The
  connection string is never written to the manifest or printed.
* Evidence: files copied as stored, so they stay encrypted when encryption at rest is on. The
  evidence key is NOT part of the backup: keep it separately, or the files cannot be read.
* The Fraud DNA index is not backed up: it is rebuilt from the database at start-up.
* Restore only into an EMPTY target (a new SQLite file / a database without tables, an empty or
  missing evidence directory), after the manifest verifies. It never overwrites data.

Prints paths and counts only.
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import create_engine, inspect

MANIFEST = "manifest.json"
FORMAT = "complaints-backup-v1"


class BackupError(Exception):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _kind(url: str) -> str:
    scheme = url.split(":", 1)[0].split("+", 1)[0]
    if scheme == "sqlite":
        return "sqlite"
    if scheme in ("postgresql", "postgres"):
        return "postgresql"
    raise BackupError(f"unsupported database type: {scheme}")


def _sqlite_path(url: str) -> Path:
    p = url.split("///", 1)[1] if "///" in url else ""
    if not p or p == ":memory:":
        raise BackupError("an in-memory SQLite database cannot be backed up")
    return Path(p)


def _pg_args(url: str) -> tuple[list[str], dict[str, str]]:
    """libpq connection arguments and environment (the password goes in PGPASSWORD only)."""
    u = urlsplit(url.replace("+psycopg2", ""))
    args = ["-h", u.hostname or "localhost", "-p", str(u.port or 5432)]
    if u.username:
        args += ["-U", u.username]
    env = dict(os.environ)
    if u.password:
        env["PGPASSWORD"] = u.password
    return [*args, "-d", u.path.lstrip("/")], env


def _schema_version(url: str) -> int | None:
    from .store import ComplaintStore

    store = ComplaintStore(url, create_schema=False)
    try:
        return store.schema_version()
    finally:
        store.close()


def backup(db_url: str, evidence_dir: Path, out_root: Path, now: datetime | None = None) -> Path:
    now = now or datetime.now(UTC)
    out = out_root / now.strftime("%Y%m%dT%H%M%S%fZ")
    out.mkdir(parents=True, mode=0o700)
    kind = _kind(db_url)
    if kind == "sqlite":
        db_file = out / "complaints.sqlite"
        src = sqlite3.connect(_sqlite_path(db_url))
        dst = sqlite3.connect(db_file)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    else:
        if not shutil.which("pg_dump"):
            raise BackupError("pg_dump is not installed (PostgreSQL client tools)")
        db_file = out / "complaints.pgdump"
        args, env = _pg_args(db_url)
        r = subprocess.run(["pg_dump", "--format=custom", "--no-owner", "-f", str(db_file), *args],
                           env=env, capture_output=True, text=True, check=False)  # fmt: skip
        if r.returncode != 0:
            raise BackupError("pg_dump failed")
    files: dict[str, dict[str, Any]] = {}
    ev_out = out / "evidence"
    if evidence_dir.is_dir():
        for p in sorted(evidence_dir.rglob("*")):
            rel = p.relative_to(evidence_dir)
            if not p.is_file() or rel.parts[0] == ".tmp":
                continue
            target = ev_out / rel
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(p, target)
            files[f"evidence/{rel.as_posix()}"] = {"sha256": _sha256(target),
                                                    "bytes": target.stat().st_size}  # fmt: skip
    files[db_file.name] = {"sha256": _sha256(db_file), "bytes": db_file.stat().st_size}
    manifest = {
        "format": FORMAT, "created_at": now.isoformat(), "database": kind,
        "schema_version": _schema_version(db_url), "database_file": db_file.name,
        "evidence_files": sum(1 for k in files if k.startswith("evidence/")),
        "evidence_key_included": False, "files": files,
    }  # fmt: skip
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return out


def verify(path: Path) -> dict[str, Any]:
    m = json.loads((path / MANIFEST).read_text(encoding="utf-8"))
    if m.get("format") != FORMAT:
        raise BackupError("not a complaints backup (unknown format)")
    bad = [name for name, f in m["files"].items()
           if not (path / name).is_file() or _sha256(path / name) != f["sha256"]]  # fmt: skip
    listed = set(m["files"])
    present = [p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file()]
    extra = [x for x in present if x != MANIFEST and x not in listed]
    if bad or extra:
        raise BackupError(f"backup does not verify: {len(bad)} changed or missing, "
                          f"{len(extra)} unexpected file(s)")  # fmt: skip
    return m


def restore(path: Path, db_url: str, evidence_dir: Path) -> dict[str, Any]:
    m = verify(path)
    kind = _kind(db_url)
    if kind != m["database"]:
        raise BackupError(f"the backup is {m['database']}, the target is {kind}")
    if evidence_dir.exists() and any(evidence_dir.iterdir()):
        raise BackupError("the target evidence directory is not empty")
    if kind == "sqlite":
        _sqlite_path(db_url).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(db_url)
    try:
        if inspect(engine).get_table_names():
            raise BackupError("the target database is not empty")
    finally:
        engine.dispose()
    if kind == "sqlite":
        target = _sqlite_path(db_url)
        target.parent.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(path / m["database_file"])
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    else:
        if not shutil.which("pg_restore"):
            raise BackupError("pg_restore is not installed (PostgreSQL client tools)")
        args, env = _pg_args(db_url)
        r = subprocess.run(["pg_restore", "--no-owner", "--exit-on-error", *args,
                            str(path / m["database_file"])],
                           env=env, capture_output=True, text=True, check=False)  # fmt: skip
        if r.returncode != 0:
            raise BackupError("pg_restore failed")
    evidence_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    src_ev = path / "evidence"
    if src_ev.is_dir():
        for p in src_ev.rglob("*"):
            if p.is_file():
                t = evidence_dir / p.relative_to(src_ev)
                t.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(p, t)
    return {"schema_version": m["schema_version"], "evidence_files": m["evidence_files"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--out", type=Path, help="directory to write a new backup under")
    g.add_argument("--verify", type=Path, help="a backup directory to check")
    g.add_argument("--restore", type=Path, help="a backup directory to restore")
    ap.add_argument("--to-database-url")
    ap.add_argument("--to-evidence-dir", type=Path)
    a = ap.parse_args(argv)
    try:
        if a.verify:
            m = verify(a.verify)
            print(f"backup verifies: {len(m['files'])} file(s), schema {m['schema_version']}")
            return 0
        if a.restore:
            if not a.to_database_url or not a.to_evidence_dir:
                print("--restore needs --to-database-url and --to-evidence-dir",
                      file=sys.stderr)  # fmt: skip
                return 2
            r = restore(a.restore, a.to_database_url, a.to_evidence_dir)
            print(f"restored schema {r['schema_version']} and "
                  f"{r['evidence_files']} evidence file(s)")  # fmt: skip
            return 0
        url = os.environ.get("COMPLAINTS_DATABASE_URL", "").strip()
        ev = os.environ.get("COMPLAINTS_EVIDENCE_DIR", "").strip()
        if not url or not ev:
            print("COMPLAINTS_DATABASE_URL and COMPLAINTS_EVIDENCE_DIR must be set",
                  file=sys.stderr)  # fmt: skip
            return 2
        out = backup(url, Path(ev), a.out)
        m = json.loads((out / MANIFEST).read_text(encoding="utf-8"))
        print(f"backup written to {out}: schema {m['schema_version']}, "
              f"{m['evidence_files']} evidence file(s); key not included")  # fmt: skip
        return 0
    except BackupError as exc:
        print(f"backup error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
