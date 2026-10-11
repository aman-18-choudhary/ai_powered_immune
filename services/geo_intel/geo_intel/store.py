"""SQLAlchemy Core persistence (SQLite in tests, Postgres opt-in; one sync code path that the
service runs through ``asyncio.to_thread``).

One table, ``gi_complaints``: one row per accepted complaint (the complaint id is unique, so a
replay is a no-op), holding only the resolved city code, a precision flag, the kind, the coarse
amount bucket and the opaque case reference. No text, no coordinate and no person identifier is
stored; the coordinates served by the API come from the static city table.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    case,
    create_engine,
    delete,
    func,
    select,
    text,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool, StaticPool

metadata = MetaData()

complaints = Table(
    "gi_complaints", metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("complaint_id", String(64), nullable=False, unique=True),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("city_code", String(4), nullable=False),
    Column("precision", String(5), nullable=False),  # "city" | "state" (state anchor city)
    Column("kind", String(16), nullable=False),
    Column("amount_bucket", String(12)),
    Column("case_ref", String(64)),
    Index("ix_gi_complaints_ts", "ts"),
    Index("ix_gi_complaints_city_ts", "city_code", "ts"),
)  # fmt: skip


def aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


class GeoStore:
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
        self.fail_next = 0  # failure injection for tests: raise on the next N operations
        if create_schema:
            self.create_schema()

    def create_schema(self) -> None:
        with self.engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(text("SELECT pg_advisory_xact_lock(727031)"))
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

    # ---------------------------------------------------------------------- writes
    def insert(
        self, *, complaint_id: str, ts: datetime, city_code: str, precision: str, kind: str,
        amount_bucket: str | None, case_ref: str | None,
    ) -> bool:  # fmt: skip
        """True if the complaint is new, False when the id was already accepted."""
        with self.tx() as conn:
            if conn.dialect.name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as dins
            else:
                from sqlalchemy.dialects.sqlite import insert as dins
            res = conn.execute(
                dins(complaints)
                .values(
                    complaint_id=complaint_id,
                    ts=ts,
                    city_code=city_code,
                    precision=precision,
                    kind=kind,
                    amount_bucket=amount_bucket,
                    case_ref=case_ref,
                )
                .on_conflict_do_nothing(index_elements=["complaint_id"])
            )
            return res.rowcount == 1

    def prune(self, cutoff: datetime, batch: int) -> int:
        """Delete complaints older than ``cutoff`` in bounded batches; returns the count."""
        total = 0
        while True:
            with self.tx() as conn:
                ids = [
                    r[0]
                    for r in conn.execute(
                        select(complaints.c.seq).where(complaints.c.ts < cutoff).limit(batch)
                    )
                ]
                if not ids:
                    return total
                conn.execute(delete(complaints).where(complaints.c.seq.in_(ids)))
                total += len(ids)

    # ----------------------------------------------------------------------- reads
    def max_ts(self) -> datetime | None:
        with self.tx() as conn:
            v = conn.execute(select(func.max(complaints.c.ts))).scalar()
        return aware(v) if v is not None else None

    def count(self) -> int:
        with self.tx() as conn:
            return int(conn.execute(select(func.count()).select_from(complaints)).scalar() or 0)

    def window_counts(
        self, base_start: datetime, prev_start: datetime, cur_start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """Per (city, kind): complaints in the current window ``(cur_start, end]``, the previous
        window ``(prev_start, cur_start]``, the baseline span ``(base_start, cur_start]`` (which
        contains the previous window) and the retained total up to ``end``."""
        c = complaints.c

        def within(lo: datetime, hi: datetime) -> Any:
            return func.sum(case(((c.ts > lo) & (c.ts <= hi), 1), else_=0))

        stmt = (
            select(
                c.city_code, c.kind, within(cur_start, end).label("cur"),
                within(prev_start, cur_start).label("prev"),
                within(base_start, cur_start).label("base"), func.count().label("total"),
            )
            .where(c.ts <= end)
            .group_by(c.city_code, c.kind)
            .order_by(c.city_code, c.kind)
        )  # fmt: skip
        with self.tx() as conn:
            return [
                {
                    k: (int(v) if k not in ("city_code", "kind") else v)
                    for k, v in r._mapping.items()
                }
                for r in conn.execute(stmt)
            ]

    def points(
        self, *, since: datetime | None, before: int | None, limit: int
    ) -> list[dict[str, Any]]:
        c = complaints.c
        stmt = select(
            c.seq, c.ts, c.kind, c.city_code, c.precision, c.amount_bucket
        ).order_by(c.seq.desc()).limit(limit)  # fmt: skip
        if since is not None:
            stmt = stmt.where(c.ts > since)
        if before is not None:
            stmt = stmt.where(c.seq < before)
        with self.tx() as conn:
            return [dict(r._mapping) for r in conn.execute(stmt)]
