"""SQLAlchemy Core persistence (SQLite in tests, Postgres in compose; one sync code path run
through ``asyncio.to_thread`` by the service).

Tables: ``gt_txns`` (one row per accepted transaction: the idempotency record, the decision level
applied to it and the source for ``takedown_timeline``), ``gt_decisions`` (latest ``decision_seq``
per txn; ``applied = false`` is the bounded pending buffer for decisions that arrive before their
transaction), ``gt_nodes`` / ``gt_edges`` (the aggregates the in-memory graph is rebuilt from),
``gt_antibodies`` (state per antibody id), ``gt_rings`` (ring snapshots) and ``gt_audit_outbox``
(ledger entries, written in the same transaction as the ring change they describe).

Money is stored as integer paise (BigInteger), times as timezone-aware DateTime read back as UTC.
There is no ``Neo4j`` here: the ``GraphStore`` surface is these methods; a graph database could
implement the same surface (README, deviations).
"""

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    bindparam,
    create_engine,
    delete,
    func,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool, StaticPool

from .graph import EdgeState, NodeState, from_us, to_us

metadata = MetaData()

txns = Table(
    "gt_txns", metadata,
    Column("txn_id", String(128), primary_key=True),
    Column("src", String(160), nullable=False),
    Column("dst", String(160), nullable=False),
    Column("device", String(160)),
    Column("amount", BigInteger, nullable=False),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("rail", String(8), nullable=False),
    Column("bank", String(64), nullable=False),
    Column("level", Integer, nullable=False, default=0),
    Column("seq", Integer, nullable=False, default=0),
    Index("ix_gt_txns_ts", "ts"),
    Index("ix_gt_txns_dst", "dst", "ts"),
)  # fmt: skip

decisions = Table(
    "gt_decisions", metadata,
    Column("txn_id", String(128), primary_key=True),
    Column("seq", Integer, nullable=False),
    Column("level", Integer, nullable=False),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("applied", Boolean, nullable=False),
    Index("ix_gt_decisions_pending", "applied", "ts"),
)  # fmt: skip

nodes = Table(
    "gt_nodes", metadata,
    Column("key", String(160), primary_key=True),
    Column("kind", String(8), nullable=False),
    Column("bank", String(64)),
    Column("first_ts", DateTime(timezone=True), nullable=False),
    Column("last_ts", DateTime(timezone=True), nullable=False),
    Column("age", Integer),
    Column("ab_until", DateTime(timezone=True)),
    Index("ix_gt_nodes_last", "last_ts"),
)  # fmt: skip

edges = Table(
    "gt_edges", metadata,
    Column("src", String(160), primary_key=True),
    Column("dst", String(160), primary_key=True),
    Column("kind", String(8), nullable=False),
    Column("cnt", BigInteger, nullable=False),
    Column("total", BigInteger, nullable=False),
    Column("first_ts", DateTime(timezone=True), nullable=False),
    Column("last_ts", DateTime(timezone=True), nullable=False),
    Column("rails", Integer, nullable=False),
    Column("susp", Integer, nullable=False),
    Index("ix_gt_edges_last", "last_ts"),
    Index("ix_gt_edges_dst", "dst"),
)  # fmt: skip

antibodies = Table(
    "gt_antibodies", metadata,
    Column("antibody_id", String(128), primary_key=True),
    Column("kind", String(16), nullable=False),
    Column("key_hash", String(160), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked", Boolean, nullable=False),
    Index("ix_gt_antibodies_key", "key_hash"),
)  # fmt: skip

rings = Table(
    "gt_rings", metadata,
    Column("ring_id", String(32), primary_key=True),
    Column("version", Integer, nullable=False),
    Column("first_detected", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("last_seen", DateTime(timezone=True), nullable=False),
    Column("body", Text, nullable=False),
)  # fmt: skip

audit_outbox = Table(
    "gt_audit_outbox", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("dedupe", String(160), nullable=False, unique=True),
    Column("msg_key", String(128), nullable=False),
    Column("body", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("sent_at", DateTime(timezone=True)),
    Column("attempts", Integer, nullable=False, default=0),
    Index("ix_gt_outbox_pending", "sent_at", "id"),
)  # fmt: skip


# Hot-path statements are built once (SQLAlchemy caches their compiled form), which roughly halves
# the per-event cost compared with rebuilding them on every call.
_NODE_COLS = ("kind", "bank", "first_ts", "last_ts", "age", "ab_until")
_UPD_NODE = update(nodes).where(nodes.c.key == bindparam("p_key")).values(
    **{c: bindparam("v_" + c) for c in _NODE_COLS}
)  # fmt: skip
_INS_NODE = insert(nodes)
_EDGE_COLS = ("kind", "cnt", "total", "first_ts", "last_ts", "rails", "susp")
_UPD_EDGE = (
    update(edges)
    .where(edges.c.src == bindparam("p_src"), edges.c.dst == bindparam("p_dst"))
    .values(**{c: bindparam("v_" + c) for c in _EDGE_COLS})
)
_INS_EDGE = insert(edges)
_SEL_DECISION = select(decisions).where(decisions.c.txn_id == bindparam("p_id"))
_SEL_TXN = select(txns).where(txns.c.txn_id == bindparam("p_id"))
_INS_TXN: dict[str, Any] = {}


def aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


class GraphStore:
    def __init__(self, url: str, *, create_schema: bool = True) -> None:
        self._lock: threading.RLock | None = None
        if url.startswith("sqlite"):
            self._lock = threading.RLock()
            kw: dict[str, Any] = {"connect_args": {"check_same_thread": False, "timeout": 30}}
            if ":memory:" in url or url in ("sqlite://", "sqlite:///"):
                kw["poolclass"] = StaticPool
            else:
                kw["poolclass"] = NullPool
            self.engine: Engine = create_engine(url, **kw)
        else:
            self.engine = create_engine(url, pool_pre_ping=True)
        self.fail_next = 0  # failure injection for tests: raise on the next N transactions
        if create_schema:
            self.create_schema()

    def create_schema(self) -> None:
        with self.engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(text("SELECT pg_advisory_xact_lock(727014)"))
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

    @staticmethod
    def _insert_ignore(conn: Connection, table: Table, **values: Any) -> bool:
        if conn.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dins
        else:
            from sqlalchemy.dialects.sqlite import insert as dins
        res = conn.execute(dins(table).values(**values).on_conflict_do_nothing())
        return res.rowcount == 1

    # ------------------------------------------------------------------- txns
    def insert_txn(self, conn: Connection, **v: Any) -> bool:
        """True if new, False if the txn id was already accepted (idempotent replay)."""
        name = conn.dialect.name
        stmt = _INS_TXN.get(name)
        if stmt is None:
            if name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as dins
            else:
                from sqlalchemy.dialects.sqlite import insert as dins
            stmt = _INS_TXN[name] = dins(txns).on_conflict_do_nothing()
        return conn.execute(stmt, v).rowcount == 1

    def get_txn(self, conn: Connection, txn_id: str) -> dict[str, Any] | None:
        r = conn.execute(_SEL_TXN, {"p_id": txn_id}).first()
        return dict(r._mapping) if r else None

    def set_txn_level(self, conn: Connection, txn_id: str, level: int, seq: int) -> None:
        conn.execute(update(txns).where(txns.c.txn_id == txn_id).values(level=level, seq=seq))

    # -------------------------------------------------------------- decisions
    def get_decision(self, conn: Connection, txn_id: str) -> dict[str, Any] | None:
        r = conn.execute(_SEL_DECISION, {"p_id": txn_id}).first()
        return dict(r._mapping) if r else None

    def put_decision(
        self, conn: Connection, txn_id: str, seq: int, level: int, ts: datetime, applied: bool
    ) -> None:
        n = conn.execute(
            update(decisions)
            .where(decisions.c.txn_id == txn_id)
            .values(seq=seq, level=level, ts=ts, applied=applied)
        ).rowcount
        if n == 0:
            conn.execute(
                insert(decisions).values(
                    txn_id=txn_id, seq=seq, level=level, ts=ts, applied=applied
                )
            )

    def mark_decision_applied(self, conn: Connection, txn_id: str) -> None:
        conn.execute(update(decisions).where(decisions.c.txn_id == txn_id).values(applied=True))

    def count_pending(self, conn: Connection) -> int:
        return int(
            conn.execute(
                select(func.count()).select_from(decisions).where(decisions.c.applied.is_(False))
            ).scalar_one()
        )

    def drop_oldest_pending(self, conn: Connection, keep: int) -> int:
        """Bound the pending buffer: delete the oldest pending decisions beyond ``keep``."""
        extra = self.count_pending(conn) - keep
        if extra <= 0:
            return 0
        ids = [
            r[0]
            for r in conn.execute(
                select(decisions.c.txn_id)
                .where(decisions.c.applied.is_(False))
                .order_by(decisions.c.ts, decisions.c.txn_id)
                .limit(extra)
            )
        ]
        conn.execute(delete(decisions).where(decisions.c.txn_id.in_(ids)))
        return len(ids)

    # ---------------------------------------------------------- nodes / edges
    def upsert_node(self, conn: Connection, key: str, st: NodeState) -> None:
        vals = {
            "kind": st.kind, "bank": st.bank, "first_ts": from_us(st.first),
            "last_ts": from_us(st.last), "age": st.age,
            "ab_until": from_us(st.ab_until) if st.ab_until else None,
        }  # fmt: skip
        params = {"v_" + k: v for k, v in vals.items()}
        if conn.execute(_UPD_NODE, {"p_key": key, **params}).rowcount == 0:
            conn.execute(_INS_NODE, {"key": key, **vals})

    def upsert_edge(self, conn: Connection, a: str, b: str, e: EdgeState) -> None:
        vals = {
            "kind": e.kind, "cnt": e.count, "total": e.total, "first_ts": from_us(e.first),
            "last_ts": from_us(e.last), "rails": e.rails, "susp": e.susp,
        }  # fmt: skip
        params = {"v_" + k: v for k, v in vals.items()}
        if conn.execute(_UPD_EDGE, {"p_src": a, "p_dst": b, **params}).rowcount == 0:
            conn.execute(_INS_EDGE, {"src": a, "dst": b, **vals})

    def delete_edges_before(self, conn: Connection, cutoff: datetime) -> int:
        return conn.execute(delete(edges).where(edges.c.last_ts < cutoff)).rowcount

    def delete_nodes(self, conn: Connection, keys: list[str]) -> None:
        for i in range(0, len(keys), 500):
            chunk = keys[i : i + 500]
            conn.execute(delete(nodes).where(nodes.c.key.in_(chunk)))
            conn.execute(delete(edges).where(edges.c.src.in_(chunk)))
            conn.execute(delete(edges).where(edges.c.dst.in_(chunk)))

    def purge_old(
        self, conn: Connection, cutoff: datetime, pending_cutoff: datetime
    ) -> dict[str, int]:
        return {
            "txns": conn.execute(delete(txns).where(txns.c.ts < cutoff)).rowcount,
            "decisions": conn.execute(
                delete(decisions).where(
                    (decisions.c.applied.is_(False) & (decisions.c.ts < pending_cutoff))
                    | (decisions.c.applied.is_(True) & (decisions.c.ts < cutoff))
                )
            ).rowcount,
        }

    # ------------------------------------------------------------- antibodies
    def get_antibody(self, conn: Connection, antibody_id: str) -> dict[str, Any] | None:
        r = conn.execute(select(antibodies).where(antibodies.c.antibody_id == antibody_id)).first()
        if not r:
            return None
        d = dict(r._mapping)
        d["expires_at"] = aware(d["expires_at"])
        return d

    def put_antibody(
        self, conn: Connection, antibody_id: str, kind: str, key_hash: str,
        expires_at: datetime, revoked: bool,
    ) -> None:  # fmt: skip
        vals = {"kind": kind, "key_hash": key_hash, "expires_at": expires_at, "revoked": revoked}
        n = conn.execute(
            update(antibodies).where(antibodies.c.antibody_id == antibody_id).values(**vals)
        ).rowcount
        if n == 0:
            conn.execute(insert(antibodies).values(antibody_id=antibody_id, **vals))

    def active_antibody_until(self, conn: Connection, key_hash: str) -> datetime | None:
        rows = conn.execute(
            select(func.max(antibodies.c.expires_at)).where(
                antibodies.c.key_hash == key_hash,
                antibodies.c.kind == "mule_account",
                antibodies.c.revoked.is_(False),
            )
        ).scalar_one()
        return aware(rows)

    def purge_antibodies(self, conn: Connection, before: datetime) -> int:
        return conn.execute(delete(antibodies).where(antibodies.c.expires_at < before)).rowcount

    # ------------------------------------------------------------------ rings
    def save_ring(
        self, conn: Connection, ring_id: str, version: int, first_detected: datetime,
        updated_at: datetime, last_seen: datetime, body: dict[str, Any],
    ) -> None:  # fmt: skip
        vals = {
            "version": version, "first_detected": first_detected, "updated_at": updated_at,
            "last_seen": last_seen, "body": json.dumps(body, sort_keys=True),
        }  # fmt: skip
        if (
            conn.execute(update(rings).where(rings.c.ring_id == ring_id).values(**vals)).rowcount
            == 0
        ):
            conn.execute(insert(rings).values(ring_id=ring_id, **vals))

    def delete_rings(self, conn: Connection, ids: list[str]) -> None:
        if ids:
            conn.execute(delete(rings).where(rings.c.ring_id.in_(ids)))

    # ------------------------------------------------------------ audit outbox
    def enqueue_audit(
        self, conn: Connection, dedupe: str, msg_key: str, body: str, now: datetime
    ) -> bool:  # fmt: skip
        return self._insert_ignore(
            conn,
            audit_outbox,
            dedupe=dedupe,
            msg_key=msg_key,
            body=body,
            created_at=now,
            attempts=0,
        )

    def pending_audit(self, limit: int) -> list[dict[str, Any]]:
        with self.engine.connect() as c:
            rows = c.execute(
                select(audit_outbox).where(audit_outbox.c.sent_at.is_(None))
                .order_by(audit_outbox.c.id).limit(limit)
            )  # fmt: skip
            return [dict(r._mapping) for r in rows]

    def get_audit(self, dedupe: str) -> dict[str, Any] | None:
        with self.engine.connect() as c:
            r = c.execute(select(audit_outbox).where(audit_outbox.c.dedupe == dedupe)).first()
            return dict(r._mapping) if r else None

    def mark_audit_sent(self, dedupe: str, now: datetime) -> None:
        with self.tx() as conn:
            conn.execute(
                update(audit_outbox).where(audit_outbox.c.dedupe == dedupe)
                .values(sent_at=now, attempts=audit_outbox.c.attempts + 1)
            )  # fmt: skip

    def note_audit_attempt(self, dedupe: str) -> None:
        with self.tx() as conn:
            conn.execute(
                update(audit_outbox).where(audit_outbox.c.dedupe == dedupe)
                .values(attempts=audit_outbox.c.attempts + 1)
            )  # fmt: skip

    def count_audit_pending(self) -> int:
        with self.engine.connect() as c:
            return int(
                c.execute(
                    select(func.count()).select_from(audit_outbox)
                    .where(audit_outbox.c.sent_at.is_(None))
                ).scalar_one()
            )  # fmt: skip

    def purge_audit(self, conn: Connection, before: datetime) -> int:
        return conn.execute(
            delete(audit_outbox).where(
                audit_outbox.c.sent_at.is_not(None), audit_outbox.c.sent_at < before
            )
        ).rowcount

    # ------------------------------------------------------------------- load
    def load_all(self) -> dict[str, Any]:
        with self.engine.connect() as c:
            return {
                "nodes": [dict(r._mapping) for r in c.execute(select(nodes).order_by(nodes.c.key))],
                "edges": [
                    dict(r._mapping)
                    for r in c.execute(select(edges).order_by(edges.c.src, edges.c.dst))
                ],
                "rings": [
                    dict(r._mapping) for r in c.execute(select(rings).order_by(rings.c.ring_id))
                ],
                "pending": self.count_pending(c),
            }

    def edge_txns(self, dst_keys: list[str], before: datetime, limit: int) -> list[dict[str, Any]]:
        """Transactions into ``dst_keys`` before ``before`` (for ``takedown_timeline``)."""
        with self.engine.connect() as c:
            rows = c.execute(
                select(txns).where(txns.c.dst.in_(dst_keys), txns.c.ts < before)
                .order_by(txns.c.ts, txns.c.txn_id).limit(limit)
            )  # fmt: skip
            out = []
            for r in rows:
                d = dict(r._mapping)
                d["ts"] = aware(d["ts"])
                out.append(d)
            return out

    def terminal_txns_after(
        self, dst_keys: list[str], after: datetime, limit: int
    ) -> list[dict[str, Any]]:
        """Transactions into ``dst_keys`` at or after ``after`` (hindsight for the timeline)."""
        with self.engine.connect() as c:
            rows = c.execute(
                select(txns).where(txns.c.dst.in_(dst_keys), txns.c.ts >= after)
                .order_by(txns.c.ts, txns.c.txn_id).limit(limit)
            )  # fmt: skip
            out = []
            for r in rows:
                d = dict(r._mapping)
                d["ts"] = aware(d["ts"])
                out.append(d)
            return out


__all__ = ["GraphStore", "aware", "to_us"]
