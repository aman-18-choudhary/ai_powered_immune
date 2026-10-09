"""SQLAlchemy Core store for the append-only evidence ledger.

One sync code path for SQLite (tests, single process) and Postgres (production); the async layer
runs it through ``asyncio.to_thread``.

Invariants
* Appends are serialised: a process lock on SQLite, ``pg_advisory_xact_lock`` on Postgres. Under
  the lock the idempotency lookup, the head read, the chain insert, the case-ref rows and (every
  ``checkpoint_every`` entries) the signed checkpoint all happen in ONE transaction, so ``seq`` is
  gap-free and two replicas racing the same event yield exactly one row. The unique constraint on
  idem_key (sha256 over every stored input field) is the backstop.
* ``ts`` is the ledger's RECEIPT time (UTC). Chain order is receipt order, not event order.
* ``ledger_entries``, ``entry_refs``, ``checkpoints`` and ``cases`` have triggers that RAISE on
  UPDATE and DELETE (and TRUNCATE on Postgres). A database superuser can drop the triggers; the
  hash chain and the signed checkpoints are what then expose the change.
"""

import base64
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from scam_contracts.models import LedgerEntry, LedgerEntryIn
from sqlalchemy import (
    Column,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool, StaticPool

from .chain import GENESIS, EntryRejected, build_entry, idempotency_key, to_model, validate_entry
from .keys import Signer
from .verify import canonical_json, checkpoint_message, ts_str

MAX_PAGE = 1000
ADVISORY_LOCK_ID = 727012
SCHEMA_LOCK_ID = 727013
APPEND_ONLY_TABLES = ("ledger_entries", "entry_refs", "checkpoints", "cases")

metadata = MetaData()

ledger_entries = Table(
    "ledger_entries", metadata,
    Column("seq", Integer, primary_key=True, autoincrement=False),
    Column("ts", String(32), nullable=False),
    Column("service", String(128), nullable=False),
    Column("actor", String(128), nullable=False),
    Column("event_type", String(128), nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("prev_hash", String(64), nullable=False),
    Column("entry_hash", String(64), nullable=False, unique=True),
    Column("model_version", String(128)),
    Column("payload", Text),
    Column("case_refs", Text, nullable=False),
    Column("idem_key", String(64), nullable=False, unique=True),
)  # fmt: skip

entry_refs = Table(
    "entry_refs", metadata,
    Column("seq", Integer, primary_key=True, autoincrement=False),
    Column("ref", String(64), primary_key=True),
    Index("ix_entry_refs_ref", "ref", "seq"),
)  # fmt: skip

checkpoints = Table(
    "checkpoints", metadata,
    Column("checkpoint_id", String(64), primary_key=True),
    Column("seq", Integer, nullable=False, unique=True),
    Column("entry_hash", String(64), nullable=False),
    Column("count", Integer, nullable=False),
    Column("ts", String(32), nullable=False),
    Column("key_id", String(16), nullable=False),
    Column("signature_b64", String(128), nullable=False),
)  # fmt: skip

cases = Table(
    "cases", metadata,
    Column("case_id", String(64), primary_key=True),
    Column("title", String(120), nullable=False),
    Column("created_by", String(256), nullable=False),
    Column("created_at", String(32), nullable=False),
    Column("refs", Text, nullable=False),
    Column("seqs", Text, nullable=False),
)  # fmt: skip


class CaseConflict(Exception):
    """A case with this id exists with different content (cases are immutable)."""


@dataclass(frozen=True)
class AppendResult:
    entry: LedgerEntry
    created: bool


@dataclass(frozen=True)
class Head:
    seq: int
    entry_hash: str
    count: int


class Counters:
    NAMES = ("appended", "duplicates", "rejected", "dlq", "checkpoints")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._v = dict.fromkeys(self.NAMES, 0)

    def inc(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._v[name] += n

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._v)


def _row_to_entry(r: Any) -> LedgerEntry:
    return to_model(
        {
            "seq": r.seq, "ts": r.ts, "service": r.service, "actor": r.actor,
            "event_type": r.event_type, "payload_hash": r.payload_hash, "prev_hash": r.prev_hash,
            "entry_hash": r.entry_hash, "model_version": r.model_version,
            "payload": json.loads(r.payload) if r.payload is not None else None,
            "case_refs": json.loads(r.case_refs),
        }
    )  # fmt: skip


def _cp(r: Any) -> dict[str, Any]:
    return {
        "checkpoint_id": r.checkpoint_id, "seq": r.seq, "entry_hash": r.entry_hash,
        "count": r.count, "ts": r.ts, "key_id": r.key_id, "signature_b64": r.signature_b64,
    }  # fmt: skip


def _trigger_ddl(dialect: str) -> list[str]:
    stmts: list[str] = []
    if dialect == "postgresql":
        stmts.append(
            "CREATE OR REPLACE FUNCTION ledger_append_only() RETURNS trigger LANGUAGE plpgsql AS "
            "$$ BEGIN RAISE EXCEPTION 'table % is append-only', TG_TABLE_NAME "
            "USING ERRCODE = 'integrity_constraint_violation'; END $$"
        )
        for t in APPEND_ONLY_TABLES:
            stmts += [
                f"DROP TRIGGER IF EXISTS trg_{t}_no_change ON {t}",
                f"CREATE TRIGGER trg_{t}_no_change BEFORE UPDATE OR DELETE ON {t} "
                "FOR EACH ROW EXECUTE FUNCTION ledger_append_only()",
                f"DROP TRIGGER IF EXISTS trg_{t}_no_truncate ON {t}",
                f"CREATE TRIGGER trg_{t}_no_truncate BEFORE TRUNCATE ON {t} "
                "FOR EACH STATEMENT EXECUTE FUNCTION ledger_append_only()",
            ]
    else:
        for t in APPEND_ONLY_TABLES:
            for op in ("update", "delete"):
                stmts.append(
                    f"CREATE TRIGGER IF NOT EXISTS trg_{t}_no_{op} BEFORE {op.upper()} ON {t} "
                    "BEGIN SELECT RAISE(ABORT, 'ledger tables are append-only'); END"
                )
    return stmts


class LedgerStore:
    def __init__(
        self, url: str, *, signer: Signer | None = None, checkpoint_every: int = 100,
        clock: Any = None, create_schema: bool = True,
    ) -> None:  # fmt: skip
        self.signer = signer
        self.checkpoint_every = checkpoint_every
        self._clock = clock or (lambda: datetime.now(UTC))
        self.counters = Counters()
        self._lock: threading.RLock | None = None
        if url.startswith("sqlite"):
            self._lock = threading.RLock()
            kw: dict[str, Any] = {"connect_args": {"check_same_thread": False, "timeout": 30}}
            in_mem = ":memory:" in url or url in ("sqlite://", "sqlite:///")
            kw["poolclass"] = StaticPool if in_mem else NullPool
            self.engine: Engine = create_engine(url, **kw)
        else:
            self.engine = create_engine(url, pool_pre_ping=True)
        if create_schema:
            self.create_schema()

    # ------------------------------------------------------------------ plumbing
    def create_schema(self) -> None:
        """Idempotent DDL + immutability triggers; serialised by an advisory lock on Postgres."""
        with self.engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(text("SELECT pg_advisory_xact_lock(:i)"), {"i": SCHEMA_LOCK_ID})
            metadata.create_all(conn)
            for stmt in _trigger_ddl(conn.dialect.name):
                conn.execute(text(stmt))

    def close(self) -> None:
        self.engine.dispose()

    def ping(self) -> bool:
        with self.engine.connect() as c:
            c.execute(select(1))
        return True

    @contextmanager
    def _tx(self) -> Iterator[Connection]:
        if self._lock:
            self._lock.acquire()
        try:
            with self.engine.begin() as conn:
                yield conn
        finally:
            if self._lock:
                self._lock.release()

    @contextmanager
    def _append_tx(self) -> Iterator[Connection]:
        with self._tx() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(text("SELECT pg_advisory_xact_lock(:i)"), {"i": ADVISORY_LOCK_ID})
            yield conn

    def _now(self) -> datetime:
        return self._clock()

    # ------------------------------------------------------------------ append
    def _head(self, conn: Connection) -> Head:
        r = conn.execute(
            select(ledger_entries.c.seq, ledger_entries.c.entry_hash)
            .order_by(ledger_entries.c.seq.desc()).limit(1)
        ).first()  # fmt: skip
        return Head(0, GENESIS, 0) if r is None else Head(r.seq, r.entry_hash, r.seq)

    def append(self, entry_in: LedgerEntryIn) -> AppendResult:
        """Idempotent on the exact entry (``chain.idempotency_key``): a repeat returns the
        existing entry and writes nothing. Raises ``EntryRejected`` for permanent problems (nothing is written)."""
        try:
            validate_entry(entry_in)
        except EntryRejected:
            self.counters.inc("rejected")
            raise
        with self._append_tx() as conn:
            c = ledger_entries.c
            idem = idempotency_key(entry_in)
            existing = conn.execute(select(ledger_entries).where(c.idem_key == idem)).first()
            if existing is not None:
                self.counters.inc("duplicates")
                return AppendResult(_row_to_entry(existing), False)
            head = self._head(conn)
            d = build_entry(entry_in, head.seq + 1, self._now(), head.entry_hash)
            conn.execute(
                ledger_entries.insert().values(
                    seq=d["seq"],
                    ts=d["ts"],
                    service=d["service"],
                    actor=d["actor"],
                    event_type=d["event_type"],
                    payload_hash=d["payload_hash"],
                    prev_hash=d["prev_hash"],
                    entry_hash=d["entry_hash"],
                    idem_key=idem,
                    model_version=d["model_version"],
                    payload=canonical_json(d["payload"]).decode()
                    if d["payload"] is not None
                    else None,
                    case_refs=canonical_json(d["case_refs"]).decode(),
                )  # fmt: skip
            )
            refs = sorted(set(d["case_refs"]))
            if refs:
                conn.execute(entry_refs.insert(), [{"seq": d["seq"], "ref": r} for r in refs])
            if self.signer is not None and d["seq"] % self.checkpoint_every == 0:
                self._checkpoint(conn, Head(d["seq"], d["entry_hash"], d["seq"]))
            entry = to_model(d)
        self.counters.inc("appended")
        return AppendResult(entry, True)

    # ------------------------------------------------------------------ checkpoints
    def _checkpoint(self, conn: Connection, head: Head) -> dict[str, Any] | None:
        assert self.signer is not None
        cp: dict[str, Any] = {
            "checkpoint_id": f"cp-{head.seq:010d}", "seq": head.seq, "entry_hash": head.entry_hash,
            "count": head.count, "ts": ts_str(self._now()), "key_id": self.signer.key_id,
        }  # fmt: skip
        cp["signature_b64"] = base64.b64encode(self.signer.sign(checkpoint_message(cp))).decode()
        res = conn.execute(self._insert_ignore(conn, checkpoints, cp))
        if res.rowcount == 1:
            self.counters.inc("checkpoints")
            return cp
        row = conn.execute(select(checkpoints).where(checkpoints.c.seq == head.seq)).first()
        return _cp(row) if row is not None else None

    @staticmethod
    def _insert_ignore(conn: Connection, table: Table, values: dict[str, Any]) -> Any:
        if conn.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dins
        else:
            from sqlalchemy.dialects.sqlite import insert as dins
        return dins(table).values(**values).on_conflict_do_nothing()

    def checkpoint_if_due(self, interval_s: float) -> bool:
        """Sign a checkpoint at the head if there are unsigned entries and the oldest of them is
        at least ``interval_s`` old. Returns whether one was written."""
        if self.signer is None:
            return False
        with self._append_tx() as conn:
            head = self._head(conn)
            if head.seq == 0:
                return False
            last = conn.execute(select(func.max(checkpoints.c.seq))).scalar() or 0
            if last >= head.seq:
                return False
            first_unsigned = conn.execute(
                select(ledger_entries.c.ts).where(ledger_entries.c.seq == last + 1)
            ).scalar_one()
            age = (self._now() - datetime.fromisoformat(first_unsigned)).total_seconds()
            if age < interval_s:
                return False
            return self._checkpoint(conn, head) is not None

    def checkpoint_covering(self, seq: int) -> dict[str, Any] | None:
        """The first checkpoint with checkpoint.seq >= seq; if none exists and the head is at
        least ``seq``, sign one at the head now. None if ``seq`` is beyond the head."""
        with self._append_tx() as conn:
            row = conn.execute(
                select(checkpoints).where(checkpoints.c.seq >= seq)
                .order_by(checkpoints.c.seq).limit(1)
            ).first()  # fmt: skip
            if row is not None:
                return _cp(row)
            head = self._head(conn)
            if head.seq < seq or head.seq == 0 or self.signer is None:
                return None
            return self._checkpoint(conn, head)

    def checkpoints(self, from_seq: int, limit: int) -> list[dict[str, Any]]:
        limit = max(1, min(limit, MAX_PAGE))
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(checkpoints).where(checkpoints.c.seq >= from_seq)
                .order_by(checkpoints.c.seq).limit(limit)
            ).all()  # fmt: skip
        return [_cp(r) for r in rows]

    def latest_checkpoint(self) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(checkpoints).order_by(checkpoints.c.seq.desc()).limit(1)
            ).first()
        return _cp(row) if row is not None else None

    # ------------------------------------------------------------------ reads
    def head(self) -> Head:
        with self.engine.connect() as conn:
            return self._head(conn)

    def last_non_audit_seq(self, audit_event: str) -> int:
        """Highest seq that is not an export-audit entry (keeps repeated exports identical)."""
        with self.engine.connect() as conn:
            return (
                conn.execute(
                    select(func.max(ledger_entries.c.seq)).where(
                        ledger_entries.c.event_type != audit_event
                    )
                ).scalar()
                or 0
            )

    def get(self, seq: int) -> LedgerEntry | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(ledger_entries).where(ledger_entries.c.seq == seq)).first()
        return _row_to_entry(r) if r is not None else None

    def page(self, from_seq: int, limit: int) -> list[LedgerEntry]:
        limit = max(1, min(limit, MAX_PAGE))
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(ledger_entries).where(ledger_entries.c.seq >= from_seq)
                .order_by(ledger_entries.c.seq).limit(limit)
            ).all()  # fmt: skip
        return [_row_to_entry(r) for r in rows]

    def segment(self, from_seq: int, to_seq: int) -> list[LedgerEntry]:
        """All entries in [from_seq, to_seq], read in bounded pages (caller bounds the span)."""
        out: list[LedgerEntry] = []
        cur = from_seq
        while cur <= to_seq:
            chunk = self.page(cur, min(MAX_PAGE, to_seq - cur + 1))
            chunk = [e for e in chunk if e.seq <= to_seq]
            if not chunk:
                break
            out.extend(chunk)
            cur = chunk[-1].seq + 1
        return out

    def select_seqs(self, refs: list[str], seqs: list[int], *, limit: int) -> list[int]:
        """Sorted seqs of entries carrying any of ``refs`` plus the explicit ``seqs``. At most
        ``limit + 1`` are returned (more than ``limit`` means "too many"). Unknown explicit seqs
        raise LookupError."""
        found: set[int] = set()
        with self.engine.connect() as conn:
            if seqs:
                have = {
                    r.seq for r in conn.execute(
                        select(ledger_entries.c.seq).where(ledger_entries.c.seq.in_(seqs))
                    )
                }  # fmt: skip
                if have != set(seqs):
                    raise LookupError("unknown seq")
                found |= have
            if refs:
                rows = conn.execute(
                    select(entry_refs.c.seq).where(entry_refs.c.ref.in_(refs))
                    .distinct().order_by(entry_refs.c.seq).limit(limit + 1)
                )  # fmt: skip
                found |= {r.seq for r in rows}
        return sorted(found)[: limit + 1]

    # ------------------------------------------------------------------ cases
    def put_case(
        self, case_id: str, title: str, created_by: str, refs: list[str], seqs: list[int]
    ) -> tuple[dict[str, Any], bool]:
        refs, seqs = sorted(set(refs)), sorted(set(seqs))
        with self._tx() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:h))"), {"h": case_id})
            vals = {
                "case_id": case_id, "title": title, "created_by": created_by,
                "created_at": ts_str(self._now()), "refs": json.dumps(refs),
                "seqs": json.dumps(seqs),
            }  # fmt: skip
            res = conn.execute(self._insert_ignore(conn, cases, vals))
            row = conn.execute(select(cases).where(cases.c.case_id == case_id)).one()
            rec = self._case(row)
            if res.rowcount != 1 and (
                rec["title"] != title or rec["case_refs"] != refs or rec["seqs"] != seqs
            ):
                raise CaseConflict(case_id)
            return rec, res.rowcount == 1

    @staticmethod
    def _case(r: Any) -> dict[str, Any]:
        return {
            "case_id": r.case_id, "title": r.title, "created_by": r.created_by,
            "created_at": r.created_at, "case_refs": json.loads(r.refs),
            "seqs": json.loads(r.seqs),
        }  # fmt: skip

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(cases).where(cases.c.case_id == case_id)).first()
        if r is None:
            return None
        rec = self._case(r)
        return rec
