"""Report persistence (SQLAlchemy Core; SQLite in tests, Postgres in the stack).

``cs_reports`` holds one row per distinct report: a server-keyed hash of the client's message
hash (HMAC-SHA256 under ``CITIZEN_REPORT_KEY``, so a leaked table cannot be reversed by hashing
candidate messages), the category, the optional coarse location code and an ``emitted`` flag for
the complaint event. No message text, no client hash, no IP and no principal is stored.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Index,
    MetaData,
    String,
    Table,
    create_engine,
    delete,
    func,
    select,
    text,
    update,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool, StaticPool

metadata = MetaData()

reports = Table(
    "cs_reports", metadata,
    Column("report_id", String(64), primary_key=True),
    Column("category", String(16), nullable=False),
    Column("city_code", String(4)),
    Column("state_code", String(2)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("emitted", Boolean, nullable=False),
    Index("ix_cs_reports_created", "created_at"),
)  # fmt: skip


def aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


class ReportStore:
    def __init__(self, url: str, *, create_schema: bool = True) -> None:
        self._lock: threading.RLock | None = None
        if url.startswith("sqlite"):
            self._lock = threading.RLock()
            kw: dict[str, Any] = {"connect_args": {"check_same_thread": False, "timeout": 30}}
            in_mem = ":memory:" in url or url in ("sqlite://", "sqlite:///")
            kw["poolclass"] = StaticPool if in_mem else NullPool
            self.engine: Engine = create_engine(url, **kw)
        else:
            self.engine = create_engine(url, pool_pre_ping=True)
        self.fail_next = 0  # failure injection for tests
        if create_schema:
            self.create_schema()

    def create_schema(self) -> None:
        with self.engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(text("SELECT pg_advisory_xact_lock(727032)"))
            metadata.create_all(conn)

    def close(self) -> None:
        self.engine.dispose()

    def ping(self) -> bool:
        with self.engine.connect() as c:
            c.execute(select(1))
        return True

    @contextmanager
    def tx(self) -> Iterator[Connection]:
        if self._lock:
            self._lock.acquire()
        try:
            if self.fail_next > 0:
                self.fail_next -= 1
                raise ConnectionError("injected store failure")
            with self.engine.begin() as conn:
                yield conn
        finally:
            if self._lock:
                self._lock.release()

    def add(
        self, report_id: str, category: str, city_code: str | None, state_code: str | None,
        created_at: datetime, emitted: bool,
    ) -> dict[str, Any]:  # fmt: skip
        """Insert-or-ignore; returns the stored row plus ``duplicate`` (False when new)."""
        with self.tx() as conn:
            if conn.dialect.name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as dins
            else:
                from sqlalchemy.dialects.sqlite import insert as dins
            res = conn.execute(
                dins(reports)
                .values(
                    report_id=report_id,
                    category=category,
                    city_code=city_code,
                    state_code=state_code,
                    created_at=created_at,
                    emitted=emitted,
                )
                .on_conflict_do_nothing(index_elements=["report_id"])
            )
            new = res.rowcount == 1
            row = conn.execute(select(reports).where(reports.c.report_id == report_id)).one()
        out = dict(row._mapping)
        out["created_at"] = aware(out["created_at"])
        out["duplicate"] = not new
        return out

    def mark_emitted(self, report_id: str) -> None:
        with self.tx() as conn:
            conn.execute(
                update(reports).where(reports.c.report_id == report_id).values(emitted=True)
            )

    def prune(self, cutoff: datetime) -> int:
        with self.tx() as conn:
            return int(conn.execute(delete(reports).where(reports.c.created_at < cutoff)).rowcount)

    def count(self) -> int:
        with self.tx() as conn:
            return int(conn.execute(select(func.count()).select_from(reports)).scalar() or 0)
