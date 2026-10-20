"""A non-blocking lock so two runs of the same maintenance job never overlap (Phase 14).

* Postgres: a session advisory lock held on a dedicated connection (works across hosts and
  containers that share the database).
* SQLite: an exclusive ``flock`` on ``<database file>.<job>.lock`` next to the database (the
  database is local to one host anyway).

``job_lock`` raises ``JobLocked`` at once when another run holds the lock; it never waits.
"""

import contextlib
import fcntl
import hashlib
import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, text


class JobLocked(Exception):
    pass


def _pg_key(name: str) -> int:
    return int.from_bytes(hashlib.sha256(f"complaints-job:{name}".encode()).digest()[:8], "big",
                          signed=True)  # fmt: skip


def sqlite_lock_path(url: str, name: str) -> Path:
    p = url.split("///", 1)[1] if "///" in url else ""
    if not p or p == ":memory:":
        raise ValueError("job locks need a file-based SQLite database")
    return Path(f"{p}.{name}.lock")


@contextlib.contextmanager
def job_lock(url: str, name: str) -> Iterator[None]:
    scheme = url.split(":", 1)[0].split("+", 1)[0]
    if scheme in ("postgresql", "postgres"):
        engine = create_engine(url, pool_size=1, max_overflow=0)
        conn = engine.connect()
        try:
            key = _pg_key(name)
            if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar():
                raise JobLocked(name)
            conn.commit()
            try:
                yield
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                conn.commit()
        finally:
            conn.close()
            engine.dispose()
        return
    path = sqlite_lock_path(url, name)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise JobLocked(name) from None
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
