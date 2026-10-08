"""SQLAlchemy Core store: antibodies, protected hashes and the transactional outbox.

One sync code path for SQLite (tests) and Postgres (compose); the async layer (``hub.py``) runs it
through ``asyncio.to_thread``.

Invariants
* Every state change and its outbox rows (Antibody event + ledger entry) are written in ONE
  database transaction; nothing is published from inside it.
* At most one ACTIVE antibody per (kind, key_hash): a partial unique index on rows with
  ``revoked = false``. Concurrent duplicate inserts lose with ``IntegrityError`` and are folded
  into the winner (idempotent re-entry). ``antibody_id`` = sha256("kind:key_hash:generation"), so a
  re-submission after revoke/expiry is a new generation with a new id.
* Revoke and expiry are compare-and-set on ``revoked = false`` so each antibody gets exactly one
  tombstone, whoever gets there first.
* Outbox rows carry a unique ``dedupe`` key, so replaying a change cannot enqueue it twice.
* SQLite is serialised with a process lock (its writer lock would otherwise surface as "database
  is locked" under concurrent writers); Postgres relies on the constraints.
"""

import hashlib
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from scam_contracts.models import Antibody, LedgerEntryIn
from scam_contracts.topics import Topics
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    create_engine,
    func,
    insert,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool, StaticPool

ANTIBODY_TTL_DAYS = 14
SERVICE = "antibody-hub"
EXPIRY_ACTOR = "system:expiry"
MAX_LIMIT = 1000

metadata = MetaData()

antibodies = Table(
    "antibodies", metadata,
    Column("antibody_id", String(64), primary_key=True),
    Column("kind", String(16), nullable=False),
    Column("key_hash", String(64), nullable=False),
    Column("source_bank", String(64), nullable=False),
    Column("confirmed_by", String(256), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked", Boolean, nullable=False, default=False),
    Column("revoked_by", String(256)),
    Column("revoked_at", DateTime(timezone=True)),
    Column("revoke_reason", String(500)),
    Column("generation", Integer, nullable=False),
    Column("evidence_ref", String(256)),
    Index("uq_antibody_generation", "kind", "key_hash", "generation", unique=True),
    Index(
        "uq_antibody_active", "kind", "key_hash", unique=True,
        sqlite_where=text("revoked = 0"), postgresql_where=text("revoked = false"),
    ),
    Index("ix_antibody_expiry", "revoked", "expires_at"),
    Index("ix_antibody_created", "created_at", "antibody_id"),
)  # fmt: skip

protected_hashes = Table(
    "protected_hashes", metadata,
    Column("key_hash", String(64), primary_key=True),
    Column("added_by", String(256), nullable=False),
    Column("added_at", DateTime(timezone=True), nullable=False),
    Column("note", String(500)),
)  # fmt: skip

audit_outbox = Table(
    "audit_outbox", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("topic", String(64), nullable=False),
    Column("msg_key", String(128), nullable=False),
    Column("body", Text, nullable=False),
    Column("dedupe", String(128), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("sent_at", DateTime(timezone=True)),
    Index("ix_outbox_pending", "sent_at", "id"),
)  # fmt: skip


class HubError(Exception):
    pass


class NotFound(HubError):
    pass


class Protected(HubError):
    pass


@dataclass(frozen=True)
class SubmitResult:
    record: dict[str, Any]
    created: bool
    extended: bool = False


def antibody_id(kind: str, key_hash: str, generation: int) -> str:
    return hashlib.sha256(f"{kind}:{key_hash}:{generation}".encode()).hexdigest()


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _row(r: Any) -> dict[str, Any]:
    d = dict(r._mapping)
    for k in ("created_at", "expires_at", "revoked_at"):
        d[k] = _aware(d[k])
    d["revoked"] = bool(d["revoked"])
    return d


def antibody_model(rec: dict[str, Any], *, revoked: bool | None = None) -> Antibody:
    return Antibody(
        antibody_id=rec["antibody_id"], kind=rec["kind"], key_hash=rec["key_hash"],
        source_bank=rec["source_bank"], confirmed_by=rec["confirmed_by"],
        created_at=rec["created_at"], expires_at=rec["expires_at"],
        revoked=rec["revoked"] if revoked is None else revoked,
    )  # fmt: skip


def payload_hash(rec: dict[str, Any], event: str, actor: str) -> str:
    """Ledger dedupe key binding (antibody_id, event, generation, actor, expires_at)."""
    raw = json.dumps(
        [rec["antibody_id"], event, rec["generation"], actor, rec["expires_at"].isoformat()],
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def encode_cursor(created_at: datetime, ab_id: str) -> str:
    import base64

    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{ab_id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    import base64

    try:
        ts, ab_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        dt = datetime.fromisoformat(ts)
    except Exception as e:
        raise ValueError("bad cursor") from e
    return _aware(dt), ab_id  # type: ignore[return-value]


class AntibodyStore:
    def __init__(
        self, url: str, *, ttl_days: int = ANTIBODY_TTL_DAYS, clock: Any = None,
    ) -> None:  # fmt: skip
        self.ttl = timedelta(days=ttl_days)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock: threading.RLock | None = None
        if url.startswith("sqlite"):
            self._lock = threading.RLock()
            kw: dict[str, Any] = {"connect_args": {"check_same_thread": False, "timeout": 30}}
            if ":memory:" in url or url in ("sqlite://", "sqlite:///"):
                kw["poolclass"] = StaticPool
            else:  # file DB: no pooled connections to leak when a cancelled worker thread finishes
                kw["poolclass"] = NullPool
            self.engine: Engine = create_engine(url, **kw)
        else:
            self.engine = create_engine(url, pool_pre_ping=True)
        metadata.create_all(self.engine)

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

    # ------------------------------------------------------------------ outbox helpers
    def _enqueue(
        self, conn: Connection, topic: str, key: str, body: str, dedupe: str, now: datetime
    ) -> None:  # fmt: skip
        if conn.execute(select(audit_outbox.c.id).where(audit_outbox.c.dedupe == dedupe)).first():
            return
        conn.execute(
            insert(audit_outbox).values(
                topic=topic, msg_key=key, body=body, dedupe=dedupe, created_at=now
            )
        )

    def _emit(
        self, conn: Connection, rec: dict[str, Any], event: str, actor: str, now: datetime,
        *, revoked: bool,
    ) -> None:  # fmt: skip
        ab = antibody_model(rec, revoked=revoked)
        exp = rec["expires_at"].isoformat()
        self._enqueue(
            conn, Topics.ANTIBODIES, rec["key_hash"], ab.model_dump_json(),
            f"ab:{rec['antibody_id']}:{event}:{exp}", now,
        )  # fmt: skip
        entry = LedgerEntryIn(
            service=SERVICE, actor=actor, event_type=f"antibody.{event}",
            payload_hash=payload_hash(rec, event, actor),
        )  # fmt: skip
        self._enqueue(
            conn, Topics.LEDGER, rec["antibody_id"], entry.model_dump_json(),
            f"led:{entry.payload_hash}", now,
        )  # fmt: skip

    def _revoke_row(
        self, conn: Connection, rec: dict[str, Any], actor: str, reason: str, event: str,
        now: datetime,
    ) -> bool:  # fmt: skip
        res = conn.execute(
            update(antibodies)
            .where(antibodies.c.antibody_id == rec["antibody_id"], antibodies.c.revoked.is_(False))
            .values(revoked=True, revoked_by=actor, revoked_at=now, revoke_reason=reason[:500])
        )
        if res.rowcount != 1:
            return False
        rec = rec | {"revoked": True}
        self._emit(conn, rec, event, actor, now, revoked=True)
        return True

    # ------------------------------------------------------------------ commands
    def submit(
        self, kind: str, key_hash: str, source_bank: str, confirmed_by: str,
        evidence_ref: str | None = None, extend: bool = False,
    ) -> SubmitResult:  # fmt: skip
        for attempt in range(3):
            try:
                return self._submit_once(
                    kind, key_hash, source_bank, confirmed_by, evidence_ref, extend
                )
            except IntegrityError:
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")

    def _submit_once(
        self, kind: str, key_hash: str, source_bank: str, confirmed_by: str,
        evidence_ref: str | None, extend: bool,
    ) -> SubmitResult:  # fmt: skip
        now = self._clock()
        with self._tx() as conn:
            if conn.execute(
                select(protected_hashes.c.key_hash).where(protected_hashes.c.key_hash == key_hash)
            ).first():
                raise Protected(key_hash[:8])
            q = select(antibodies).where(
                antibodies.c.kind == kind, antibodies.c.key_hash == key_hash,
                antibodies.c.revoked.is_(False),
            )  # fmt: skip
            row = conn.execute(q).first()
            if row is not None:
                rec = _row(row)
                if rec["expires_at"] <= now:  # lapsed but not yet swept: expire, then re-create
                    self._revoke_row(conn, rec, EXPIRY_ACTOR, "expired", "expired", now)
                else:
                    return self._reenter(conn, rec, extend, confirmed_by, now)
            gen = (
                conn.execute(
                    select(func.max(antibodies.c.generation)).where(
                        antibodies.c.kind == kind, antibodies.c.key_hash == key_hash
                    )
                ).scalar()
                or 0
            ) + 1
            rec = {
                "antibody_id": antibody_id(kind, key_hash, gen), "kind": kind,
                "key_hash": key_hash, "source_bank": source_bank, "confirmed_by": confirmed_by,
                "created_at": now, "expires_at": now + self.ttl, "revoked": False,
                "revoked_by": None, "revoked_at": None, "revoke_reason": None,
                "generation": gen, "evidence_ref": evidence_ref,
            }  # fmt: skip
            conn.execute(insert(antibodies).values(**rec))
            self._emit(conn, rec, "created", confirmed_by, now, revoked=False)
            return SubmitResult(rec, created=True)

    def _reenter(
        self, conn: Connection, rec: dict[str, Any], extend: bool, actor: str, now: datetime
    ) -> SubmitResult:  # fmt: skip
        if not extend:
            return SubmitResult(rec, created=False)
        new_exp = now + self.ttl
        if new_exp <= rec["expires_at"]:
            return SubmitResult(rec, created=False)
        res = conn.execute(
            update(antibodies)
            .where(
                antibodies.c.antibody_id == rec["antibody_id"],
                antibodies.c.revoked.is_(False),
                antibodies.c.expires_at < new_exp,
            )
            .values(expires_at=new_exp)
        )
        rec = rec | {"expires_at": new_exp}
        if res.rowcount == 1:
            self._emit(conn, rec, "extended", actor, now, revoked=False)
        return SubmitResult(rec, created=False, extended=res.rowcount == 1)

    def revoke(self, ab_id: str, actor: str, reason: str) -> dict[str, Any]:
        now = self._clock()
        with self._tx() as conn:
            row = conn.execute(select(antibodies).where(antibodies.c.antibody_id == ab_id)).first()
            if row is None:
                raise NotFound(ab_id)
            rec = _row(row)
            if not rec["revoked"]:
                self._revoke_row(conn, rec, actor, reason, "revoked", now)
            return _row(
                conn.execute(select(antibodies).where(antibodies.c.antibody_id == ab_id)).first()
            )

    def add_protected(self, key_hash: str, actor: str, note: str | None) -> tuple[bool, list[str]]:
        now = self._clock()
        with self._tx() as conn:
            exists = conn.execute(
                select(protected_hashes.c.key_hash).where(protected_hashes.c.key_hash == key_hash)
            ).first()
            if not exists:
                conn.execute(
                    insert(protected_hashes).values(
                        key_hash=key_hash, added_by=actor, added_at=now, note=note
                    )
                )
            revoked_ids = []
            rows = conn.execute(
                select(antibodies).where(
                    antibodies.c.key_hash == key_hash, antibodies.c.revoked.is_(False)
                )
            ).all()
            for row in rows:
                rec = _row(row)
                if self._revoke_row(conn, rec, actor, "protected hash", "revoked", now):
                    revoked_ids.append(rec["antibody_id"])
            return (not exists, revoked_ids)

    def expire_due(self) -> int:
        """Mark lapsed antibodies revoked-by-expiry; one tombstone each (CAS on revoked)."""
        now = self._clock()
        n = 0
        with self._tx() as conn:
            rows = conn.execute(
                select(antibodies).where(
                    antibodies.c.revoked.is_(False), antibodies.c.expires_at <= now
                )
            ).all()
            for row in rows:
                if self._revoke_row(conn, _row(row), EXPIRY_ACTOR, "expired", "expired", now):
                    n += 1
        return n

    # ------------------------------------------------------------------ queries
    def get(self, ab_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as c:
            row = c.execute(select(antibodies).where(antibodies.c.antibody_id == ab_id)).first()
        return _row(row) if row else None

    def listing(self, state: str, limit: int) -> list[dict[str, Any]]:
        now = self._clock()
        q = select(antibodies)
        if state == "active":
            q = q.where(antibodies.c.revoked.is_(False), antibodies.c.expires_at > now)
        q = q.order_by(antibodies.c.expires_at, antibodies.c.antibody_id).limit(
            min(limit, MAX_LIMIT)
        )
        with self.engine.connect() as c:
            return [_row(r) for r in c.execute(q)]

    def active_hashes(self) -> list[str]:
        now = self._clock()
        q = (
            select(antibodies.c.key_hash)
            .where(antibodies.c.revoked.is_(False), antibodies.c.expires_at > now)
            .distinct()
            .order_by(antibodies.c.key_hash)
        )
        with self.engine.connect() as c:
            return [r[0] for r in c.execute(q)]

    def exact_page(self, cursor: str | None, limit: int) -> tuple[list[dict[str, Any]], str | None]:
        now = self._clock()
        limit = min(limit, MAX_LIMIT)
        q = select(antibodies).where(antibodies.c.revoked.is_(False), antibodies.c.expires_at > now)
        if cursor:
            ts, ab_id = decode_cursor(cursor)
            q = q.where(
                or_(
                    antibodies.c.created_at > ts,
                    and_(antibodies.c.created_at == ts, antibodies.c.antibody_id > ab_id),
                )
            )
        q = q.order_by(antibodies.c.created_at, antibodies.c.antibody_id).limit(limit + 1)
        with self.engine.connect() as c:
            rows = [_row(r) for r in c.execute(q)]
        more = len(rows) > limit
        rows = rows[:limit]
        nxt = encode_cursor(rows[-1]["created_at"], rows[-1]["antibody_id"]) if more else None
        return rows, nxt

    def count_active(self) -> int:
        now = self._clock()
        with self.engine.connect() as c:
            return int(
                c.execute(
                    select(func.count())
                    .select_from(antibodies)
                    .where(antibodies.c.revoked.is_(False), antibodies.c.expires_at > now)
                ).scalar()
                or 0
            )

    # ------------------------------------------------------------------ outbox
    def pending(self, limit: int = 200) -> list[tuple[int, str, str, str]]:
        q = (
            select(audit_outbox.c.id, audit_outbox.c.topic, audit_outbox.c.msg_key,
                   audit_outbox.c.body)
            .where(audit_outbox.c.sent_at.is_(None))
            .order_by(audit_outbox.c.id)
            .limit(limit)
        )  # fmt: skip
        with self.engine.connect() as c:
            return [(r[0], r[1], r[2], r[3]) for r in c.execute(q)]

    def mark_sent(self, row_id: int) -> None:
        with self._tx() as conn:
            conn.execute(
                update(audit_outbox)
                .where(audit_outbox.c.id == row_id, audit_outbox.c.sent_at.is_(None))
                .values(sent_at=self._clock())
            )

    def count_pending(self) -> int:
        with self.engine.connect() as c:
            return int(
                c.execute(
                    select(func.count())
                    .select_from(audit_outbox)
                    .where(audit_outbox.c.sent_at.is_(None))
                ).scalar()
                or 0
            )
