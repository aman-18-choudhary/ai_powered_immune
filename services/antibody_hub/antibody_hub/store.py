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
* Ledger entries carry a structured, PII-free payload (``antibody_id, event, generation, kind,
  key_hash_prefix (8 hex), expires_at, actor_role, actor_bank?``) built from the record only (no
  wall clock), ``payload_hash = sha256(canonical_json(payload))`` and ``case_refs =
  [antibody_id, <kind>_ref:<16 hex of key_hash>]`` (``payee_ref:`` for mule accounts, which is the
  same ref txn-guard puts on its holds for that payee). The outbox dedupe key binds the hash AND
  the actor, so two actors' corroborations stay distinct entries.
* SQLite is serialised with a process lock (its writer lock would otherwise surface as "database
  is locked" under concurrent writers); Postgres relies on the constraints.
"""

import hashlib
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
    delete,
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
from svckit.ledger import (
    LedgerPayloadError,
    build_or_placeholder,
    hash_ref,
    redacted_ref,
    safe_actor,
    utc_ts,
)
from svckit.pii import string_has_identifier

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
    Column("evidence_ref", String(64)),
    Column("revoked_by_bank", String(64)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
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
    Column("attempts", Integer, nullable=False, default=0),
    Column("parked", Boolean, nullable=False, default=False),
    Column("first_failed_at", DateTime(timezone=True)),
    Index("ix_outbox_pending", "sent_at", "id"),
)  # fmt: skip

corroborations = Table(
    "corroborations", metadata,
    Column("antibody_id", String(64), primary_key=True),
    Column("bank_id", String(64), primary_key=True),
    Column("actor", String(256), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
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
    corroborated: bool = False


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


def _kind_ref(kind: str) -> str:
    return "payee_ref" if kind == "mule_account" else f"{kind}_ref"


def _safe_slug(value: str | None) -> str | None:
    """Bank ids / roles in audit payloads: the value if the guard accepts it, else a stable
    ``redacted_<16 hex>`` stand-in (a gateway header is not under our validation)."""
    if not value:
        return None
    return value if not string_has_identifier(value) and len(value) <= 64 else redacted_ref(value)


def ledger_entry(
    rec: dict[str, Any], event: str, actor: str, role: str | None, bank: str | None
) -> LedgerEntryIn:
    """The structured ledger entry for one antibody lifecycle event. Deterministic in (rec, event,
    actor, role, bank): replays produce byte-identical entries. Never raises: values the guard
    refuses are replaced by ``redacted_<hash>`` stand-ins, and if the payload is still refused a
    fixed-shape placeholder is returned, so an audit problem cannot fail the analyst's action."""
    payload: dict[str, Any] = {
        "antibody_id": rec["antibody_id"], "event": event, "generation": rec["generation"],
        "kind": rec["kind"], "key_hash_prefix": rec["key_hash"][:8],
        "expires_at": utc_ts(rec["expires_at"]), "actor_role": _safe_slug(role) or "unspecified",
    }  # fmt: skip
    safe_bank = _safe_slug(bank)
    if safe_bank:
        payload["actor_bank"] = safe_bank
    try:
        pref = hash_ref(_kind_ref(rec["kind"]), rec["key_hash"])
    except LedgerPayloadError:
        pref = ""
    refs = [rec["antibody_id"], *([pref] if pref else [])]
    entry, refused = build_or_placeholder(
        SERVICE, safe_actor(actor), f"antibody.{event}", payload, case_refs=refs,
        placeholder={"antibody_id": rec["antibody_id"], "event": event,
                     "generation": rec["generation"], "kind": rec["kind"]},
        placeholder_refs=refs,
    )  # fmt: skip
    return entry


def _role_of(actor: str, role: str | None) -> str:
    return "system" if actor == EXPIRY_ACTOR else (role or "unspecified")


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
        create_schema: bool = True, park_attempts: int = 10, park_after_s: float = 900.0,
    ) -> None:  # fmt: skip
        self.park_attempts = park_attempts
        self.park_after_s = park_after_s
        self.ttl = timedelta(days=ttl_days)
        # normalise to UTC: SQLite drops tzinfo, so a non-UTC clock would be read back mislabelled
        raw = clock or (lambda: datetime.now(UTC))
        self._clock = lambda: raw().astimezone(UTC)
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
        if create_schema:
            self.create_schema()

    def create_schema(self) -> None:
        """Idempotent DDL. On Postgres replicas starting together serialise on an advisory lock;
        production deploys may instead run ``python -m antibody_hub.migrate`` once."""
        with self.engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(text("SELECT pg_advisory_xact_lock(727001)"))
            metadata.create_all(conn)

    def _lock_hash(self, conn: Connection, key_hash: str) -> None:
        """Serialise submit/protect on one hash across processes (SQLite: process lock)."""
        if conn.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:h))"), {"h": key_hash})

    def _insert_ignore(self, conn: Connection, table: Table, **values: Any) -> bool:
        if conn.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dins
        else:
            from sqlalchemy.dialects.sqlite import insert as dins
        res = conn.execute(dins(table).values(**values).on_conflict_do_nothing())
        return res.rowcount == 1

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
        *, revoked: bool, ledger_event: str | None = None, role: str | None = None,
        bank: str | None = None,
    ) -> None:  # fmt: skip
        ab = antibody_model(rec, revoked=revoked)
        exp = rec["expires_at"].isoformat()
        self._enqueue(
            conn, Topics.ANTIBODIES, rec["key_hash"], ab.model_dump_json(),
            f"ab:{rec['antibody_id']}:{event}:{exp}", now,
        )  # fmt: skip
        self._enqueue_ledger(
            conn, ledger_entry(rec, ledger_event or event, actor, _role_of(actor, role), bank),
            now,
        )  # fmt: skip

    def _enqueue_ledger(self, conn: Connection, entry: LedgerEntryIn, now: datetime) -> None:
        key = entry.case_refs[0] if entry.case_refs else SERVICE
        dedupe = hashlib.sha256(f"{entry.actor}|{entry.payload_hash}".encode()).hexdigest()
        self._enqueue(conn, Topics.LEDGER, key, entry.model_dump_json(), f"led:{dedupe}", now)

    def _revoke_row(
        self, conn: Connection, rec: dict[str, Any], actor: str, reason: str, event: str,
        now: datetime, bank: str | None = None, role: str | None = None,
    ) -> bool:  # fmt: skip
        res = conn.execute(
            update(antibodies)
            .where(antibodies.c.antibody_id == rec["antibody_id"], antibodies.c.revoked.is_(False))
            .values(
                revoked=True,
                revoked_by=actor,
                revoked_at=now,
                revoke_reason=reason[:500],
                revoked_by_bank=bank,
                updated_at=now,
            )
        )
        if res.rowcount != 1:
            return False
        rec = rec | {"revoked": True}
        cross = event == "revoked" and bank is not None and bank != rec["source_bank"]
        self._emit(
            conn, rec, event, actor, now, revoked=True,
            ledger_event="revoked.cross_bank" if cross else None, role=role, bank=bank,
        )  # fmt: skip
        return True

    # ------------------------------------------------------------------ commands
    def submit(
        self, kind: str, key_hash: str, source_bank: str, confirmed_by: str,
        evidence_ref: str | None = None, extend: bool = False, role: str | None = None,
    ) -> SubmitResult:  # fmt: skip
        for attempt in range(3):
            try:
                return self._submit_once(
                    kind, key_hash, source_bank, confirmed_by, evidence_ref, extend, role
                )
            except IntegrityError:
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")

    def _submit_once(
        self, kind: str, key_hash: str, source_bank: str, confirmed_by: str,
        evidence_ref: str | None, extend: bool, role: str | None = None,
    ) -> SubmitResult:  # fmt: skip
        now = self._clock()
        with self._tx() as conn:
            self._lock_hash(conn, key_hash)  # then re-check protected: races add_protected safely
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
                    return self._reenter(conn, rec, extend, confirmed_by, source_bank, now, role)
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
                "revoked_by_bank": None, "updated_at": now,
            }  # fmt: skip
            conn.execute(insert(antibodies).values(**rec))
            self._emit(
                conn, rec, "created", confirmed_by, now, revoked=False, role=role, bank=source_bank
            )
            return SubmitResult(rec, created=True)

    def _reenter(
        self, conn: Connection, rec: dict[str, Any], extend: bool, actor: str, bank: str,
        now: datetime, role: str | None = None,
    ) -> SubmitResult:  # fmt: skip
        corroborated = False
        if bank != rec["source_bank"]:
            # a second bank independently confirms: record it (audit only, no new bus event)
            corroborated = self._insert_ignore(
                conn, corroborations, antibody_id=rec["antibody_id"], bank_id=bank, actor=actor,
                created_at=now,
            )  # fmt: skip
            if corroborated:
                self._ledger_only(conn, rec, "corroborated", f"{actor}@{bank}", now, role, bank)
        if not extend:
            return SubmitResult(rec, created=False, corroborated=corroborated)
        new_exp = now + self.ttl
        if new_exp <= rec["expires_at"]:
            return SubmitResult(rec, created=False, corroborated=corroborated)
        res = conn.execute(
            update(antibodies)
            .where(
                antibodies.c.antibody_id == rec["antibody_id"],
                antibodies.c.revoked.is_(False),
                antibodies.c.expires_at < new_exp,
            )
            .values(expires_at=new_exp, updated_at=now)
        )
        rec = rec | {"expires_at": new_exp}
        if res.rowcount == 1:
            self._emit(conn, rec, "extended", actor, now, revoked=False, role=role, bank=bank)
        return SubmitResult(
            rec, created=False, extended=res.rowcount == 1, corroborated=corroborated
        )

    def _ledger_only(
        self, conn: Connection, rec: dict[str, Any], event: str, actor: str, now: datetime,
        role: str | None = None, bank: str | None = None,
    ) -> None:  # fmt: skip
        self._enqueue_ledger(
            conn, ledger_entry(rec, event, actor, _role_of(actor, role), bank), now
        )

    def revoke(
        self, ab_id: str, actor: str, reason: str, bank: str | None = None,
        role: str | None = None,
    ) -> dict[str, Any]:  # fmt: skip
        now = self._clock()
        with self._tx() as conn:
            row = conn.execute(select(antibodies).where(antibodies.c.antibody_id == ab_id)).first()
            if row is None:
                raise NotFound(ab_id)
            rec = _row(row)
            if not rec["revoked"]:
                self._revoke_row(conn, rec, actor, reason, "revoked", now, bank, role)
            return _row(
                conn.execute(select(antibodies).where(antibodies.c.antibody_id == ab_id)).first()
            )

    def add_protected(
        self, key_hash: str, actor: str, note: str | None, role: str | None = None
    ) -> tuple[bool, list[str]]:
        now = self._clock()
        with self._tx() as conn:
            self._lock_hash(conn, key_hash)
            created = self._insert_ignore(
                conn, protected_hashes, key_hash=key_hash, added_by=actor, added_at=now, note=note
            )
            if created:
                self._protected_audit(conn, "protected.added", key_hash, actor, now, role)
            revoked_ids = []
            rows = conn.execute(
                select(antibodies).where(
                    antibodies.c.key_hash == key_hash, antibodies.c.revoked.is_(False)
                )
            ).all()
            for row in rows:
                rec = _row(row)
                if self._revoke_row(conn, rec, actor, "protected hash", "revoked", now, role=role):
                    revoked_ids.append(rec["antibody_id"])
            return (created, revoked_ids)

    def remove_protected(self, key_hash: str, actor: str, role: str | None = None) -> bool:
        now = self._clock()
        with self._tx() as conn:
            self._lock_hash(conn, key_hash)
            res = conn.execute(
                delete(protected_hashes).where(protected_hashes.c.key_hash == key_hash)
            )
            if res.rowcount == 1:
                self._protected_audit(conn, "protected.removed", key_hash, actor, now, role)
            return res.rowcount == 1

    def _protected_audit(
        self, conn: Connection, event: str, key_hash: str, actor: str, now: datetime,
        role: str | None = None,
    ) -> None:  # fmt: skip
        """Payload: event, key_hash_prefix, actor_role and the change time (microseconds, so an
        add / remove / add sequence stays three distinct entries)."""
        payload = {
            "event": event, "key_hash_prefix": key_hash[:8],
            "actor_role": _safe_slug(role) or "unspecified", "at": utc_ts(now, micros=True),
        }  # fmt: skip
        refs = [hash_ref("payee_ref", key_hash)]
        entry, _ = build_or_placeholder(
            SERVICE, safe_actor(actor), event, payload, case_refs=refs,
            placeholder={"event": event}, placeholder_refs=refs,
        )  # fmt: skip
        self._enqueue_ledger(conn, entry, now)

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
        """Unsent, unparked rows in id order. Keys held back by a parked row are filtered out
        BEFORE the limit, so a backlog of held rows cannot starve other keys."""
        held = audit_outbox.alias("held")
        held_keys = select(held.c.msg_key).where(held.c.parked.is_(True), held.c.sent_at.is_(None))
        q = (
            select(audit_outbox.c.id, audit_outbox.c.topic, audit_outbox.c.msg_key,
                   audit_outbox.c.body)
            .where(
                audit_outbox.c.sent_at.is_(None),
                audit_outbox.c.parked.is_(False),
                audit_outbox.c.msg_key.not_in(held_keys),
            )
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

    def record_failure(self, row_id: int) -> bool:
        """Count a failed publish. A row is parked only after ``park_attempts`` failures AND
        ``park_after_s`` since its first failure, so a short broker blip never parks anything.
        Returns True if parked."""
        now = self._clock()
        with self._tx() as conn:
            row = conn.execute(
                select(audit_outbox.c.attempts, audit_outbox.c.first_failed_at).where(
                    audit_outbox.c.id == row_id
                )
            ).first()
            if row is None:
                return False
            attempts = (row[0] or 0) + 1
            first = _aware(row[1]) or now
            park = (
                attempts >= self.park_attempts
                and (now - first).total_seconds() >= self.park_after_s
            )
            conn.execute(
                update(audit_outbox)
                .where(audit_outbox.c.id == row_id)
                .values(attempts=attempts, first_failed_at=first, parked=park)
            )
            return park

    def unpark(self) -> int:
        with self._tx() as conn:
            res = conn.execute(
                update(audit_outbox)
                .where(audit_outbox.c.parked.is_(True), audit_outbox.c.sent_at.is_(None))
                .values(parked=False, attempts=0, first_failed_at=None)
            )
            return int(res.rowcount)

    def purge_sent(self, retention_days: float) -> int:
        cutoff = self._clock() - timedelta(days=retention_days)
        with self._tx() as conn:
            res = conn.execute(
                delete(audit_outbox).where(
                    audit_outbox.c.sent_at.is_not(None), audit_outbox.c.sent_at < cutoff
                )
            )
            return int(res.rowcount)

    def count_pending(self) -> int:
        return self._count_outbox(audit_outbox.c.parked.is_(False))

    def count_parked(self) -> int:
        return self._count_outbox(audit_outbox.c.parked.is_(True))

    def _count_outbox(self, cond: Any) -> int:
        with self.engine.connect() as c:
            return int(
                c.execute(
                    select(func.count())
                    .select_from(audit_outbox)
                    .where(audit_outbox.c.sent_at.is_(None), cond)
                ).scalar()
                or 0
            )

    def bloom_version(self) -> str:
        """Cheap change detector: (active count, total row count, newest updated_at)."""
        now = self._clock()
        with self.engine.connect() as c:
            active = c.execute(
                select(func.count())
                .select_from(antibodies)
                .where(antibodies.c.revoked.is_(False), antibodies.c.expires_at > now)
            ).scalar()
            total = c.execute(select(func.count()).select_from(antibodies)).scalar()
            newest = c.execute(select(func.max(antibodies.c.updated_at))).scalar()
        if isinstance(newest, str):
            newest = datetime.fromisoformat(newest)
        newest = _aware(newest)
        stamp = newest.isoformat() if newest else ""
        return hashlib.sha256(f"{active}:{total}:{stamp}".encode()).hexdigest()[:32]
