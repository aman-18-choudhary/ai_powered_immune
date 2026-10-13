"""SQLAlchemy Core store (sync). One code path for SQLite (tests) and Postgres; the API calls it
through ``asyncio.to_thread``.

Invariants
* State changes are compare-and-set (``UPDATE ... WHERE status = :expected``), so concurrent
  status changes / deletes / role changes cannot both win.
* Evidence count and complaint state are checked in the SAME transaction as the insert, with a row
  lock on the complaint (Postgres ``FOR UPDATE``; SQLite serialises writers with a process lock).
* Role/active changes serialise on one advisory lock (Postgres) so two admins cannot demote each
  other into zero active admins.
* Deletion is soft; users are deactivated, never deleted (FKs are RESTRICT).
"""

import hashlib
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Float,
    and_,
    cast,
    create_engine,
    delete,
    exists,
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

from .identity import Identity
from .schema import (
    admin_audit,
    alerts,
    analyses,
    complaint_evidence,
    complaint_status_history,
    complaints,
    idempotency_keys,
    metadata,
    users,
)

IDEMPOTENCY_TTL = timedelta(hours=24)
LAST_LOGIN_REFRESH = timedelta(minutes=5)
ADVISORY_LOCK_SCHEMA = 727201
ADVISORY_LOCK_ADMIN = 727202

TRANSITIONS: dict[str, frozenset[str]] = {
    "SUBMITTED": frozenset({"UNDER_REVIEW", "REJECTED"}),
    "UNDER_REVIEW": frozenset({"INVESTIGATING", "RESOLVED", "REJECTED"}),
    "INVESTIGATING": frozenset({"UNDER_REVIEW", "RESOLVED", "REJECTED"}),
    "RESOLVED": frozenset({"INVESTIGATING"}),
    "REJECTED": frozenset({"UNDER_REVIEW"}),
}
CLOSED_STATUSES = frozenset({"RESOLVED", "REJECTED"})
SORTS = {
    "created_at": complaints.c.created_at,
    "incident_at": complaints.c.incident_at,
    "amount": complaints.c.amount,
}


class StoreError(Exception):
    """Base for store failures; ``code`` is a stable machine-readable reason."""

    code = "store_error"


class NotFound(StoreError):
    code = "not_found"


class Conflict(StoreError):
    code = "conflict"

    def __init__(self, code: str | None = None) -> None:
        super().__init__(code or self.code)
        if code:
            self.code = code


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def new_id() -> str:
    return str(uuid.uuid4())


def hash_key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class ComplaintStore:
    def __init__(self, url: str, *, create_schema: bool = True) -> None:
        self._lock: threading.RLock | None = None
        if url.startswith("sqlite"):
            self._lock = threading.RLock()
            kw: dict[str, Any] = {"connect_args": {"check_same_thread": False, "timeout": 30}}
            kw["poolclass"] = (
                StaticPool if ":memory:" in url or url in ("sqlite://", "sqlite:///") else NullPool
            )
            self.engine: Engine = create_engine(url, **kw)
            from sqlalchemy import event

            @event.listens_for(self.engine, "connect")
            def _fk_on(dbapi_conn: Any, _: Any) -> None:
                dbapi_conn.execute("PRAGMA foreign_keys=ON")

        else:
            self.engine = create_engine(url, pool_pre_ping=True)
        if create_schema:
            self.create_schema()

    # ------------------------------------------------------------------ infra
    def create_schema(self) -> None:
        """Idempotent DDL; on Postgres concurrent starters serialise on an advisory lock."""
        with self.engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_SCHEMA})
            metadata.create_all(conn)

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

    @staticmethod
    def _admin_lock(conn: Connection) -> None:
        if conn.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_ADMIN})

    @staticmethod
    def _audit(
        conn: Connection, actor: str | None, action: str, target: str | None,
        before: dict[str, Any], after: dict[str, Any], now: datetime,
    ) -> None:  # fmt: skip
        conn.execute(
            insert(admin_audit).values(
                id=new_id(), actor_user_id=actor, action=action, target_user_id=target,
                before=before, after=after, created_at=now,
            )
        )  # fmt: skip

    @staticmethod
    def _user(row: Any) -> dict[str, Any]:
        d = dict(row._mapping)
        d["email_verified"] = bool(d["email_verified"])
        d["is_active"] = bool(d["is_active"])
        return d

    # ------------------------------------------------------------------ users
    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as c:
            row = c.execute(select(users).where(users.c.id == user_id)).first()
        return self._user(row) if row else None

    def upsert_user(
        self, ident: Identity, now: datetime, bootstrap_emails: frozenset[str]
    ) -> dict[str, Any]:
        """Create the internal user on first sight; keep email/name current; apply the admin
        bootstrap (verified email in the env list, once per user, never after a deliberate
        demotion)."""
        for attempt in (1, 2):
            try:
                return self._upsert_user(ident, now, bootstrap_emails)
            except IntegrityError:
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    def _upsert_user(
        self, ident: Identity, now: datetime, bootstrap_emails: frozenset[str]
    ) -> dict[str, Any]:
        eligible = (
            ident.email_verified and ident.email is not None and ident.email in bootstrap_emails
        )
        with self._tx() as conn:
            row = conn.execute(select(users).where(users.c.idp_subject == ident.subject)).first()
            if row is None:
                uid = new_id()
                role = "ADMIN" if eligible else "CITIZEN"
                conn.execute(
                    insert(users).values(
                        id=uid, idp_subject=ident.subject, email=ident.email,
                        email_verified=ident.email_verified, display_name=ident.display_name,
                        role=role, is_active=True, created_at=now, last_login_at=now,
                    )
                )  # fmt: skip
                if eligible:
                    self._audit(conn, uid, "admin_bootstrap", uid, {"role": "CITIZEN"},
                                {"role": "ADMIN"}, now)  # fmt: skip
                return self._user(conn.execute(select(users).where(users.c.id == uid)).one())
            user = self._user(row)
            changes: dict[str, Any] = {}
            if user["email"] != ident.email:
                changes["email"] = ident.email
            if user["email_verified"] != ident.email_verified:
                changes["email_verified"] = ident.email_verified
            if ident.display_name and user["display_name"] != ident.display_name:
                changes["display_name"] = ident.display_name
            last = _aware(user["last_login_at"])
            if last is None or now - last >= LAST_LOGIN_REFRESH:
                changes["last_login_at"] = now
            if (
                eligible
                and user["is_active"]
                and user["role"] != "ADMIN"
                and not conn.execute(
                    select(admin_audit.c.id).where(
                        admin_audit.c.target_user_id == user["id"],
                        admin_audit.c.action == "admin_bootstrap",
                    )
                ).first()
            ):
                changes["role"] = "ADMIN"
                self._audit(conn, user["id"], "admin_bootstrap", user["id"],
                            {"role": user["role"]}, {"role": "ADMIN"}, now)  # fmt: skip
            if changes:
                conn.execute(update(users).where(users.c.id == user["id"]).values(**changes))
                user.update(changes)
            return user

    def grant_investigator(self, user_id: str, now: datetime) -> tuple[dict[str, Any], bool]:
        """Citizen -> investigator through the entry flow (the caller has verified eligibility).
        Returns (user, changed). Never lowers or replaces ADMIN."""
        with self._tx() as conn:
            self._admin_lock(conn)
            user = self._user(conn.execute(select(users).where(users.c.id == user_id)).one())
            if user["role"] != "CITIZEN":
                return user, False
            conn.execute(
                update(users).where(and_(users.c.id == user_id, users.c.role == "CITIZEN"))
                .values(role="INVESTIGATOR")
            )  # fmt: skip
            self._audit(conn, user_id, "investigator_entry", user_id, {"role": "CITIZEN"},
                        {"role": "INVESTIGATOR"}, now)  # fmt: skip
            user["role"] = "INVESTIGATOR"
            return user, True

    def list_users(
        self, limit: int, offset: int, role: str | None = None, active: bool | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        cond = []
        if role:
            cond.append(users.c.role == role)
        if active is not None:
            cond.append(users.c.is_active == active)
        with self.engine.connect() as c:
            total = c.execute(select(func.count()).select_from(users).where(*cond)).scalar_one()
            rows = c.execute(
                select(users).where(*cond).order_by(users.c.created_at, users.c.id)
                .limit(limit).offset(offset)
            ).all()  # fmt: skip
        return [self._user(r) for r in rows], int(total)

    def _count_active_admins(self, conn: Connection) -> int:
        return int(
            conn.execute(
                select(func.count()).select_from(users)
                .where(users.c.role == "ADMIN", users.c.is_active.is_(True))
            ).scalar_one()
        )  # fmt: skip

    def set_role(
        self, actor_id: str, target_id: str, new_role: str, expected_role: str, now: datetime
    ) -> dict[str, Any]:
        with self._tx() as conn:
            self._admin_lock(conn)
            row = conn.execute(select(users).where(users.c.id == target_id)).first()
            if row is None:
                raise NotFound()
            target = self._user(row)
            if target["role"] != expected_role:
                raise Conflict("role_conflict")
            if new_role == target["role"]:
                return target
            if new_role in ("INVESTIGATOR", "ADMIN") and not (
                target["email_verified"] and target["email"]
            ):
                raise Conflict("target_email_not_verified")
            if (
                target["role"] == "ADMIN"
                and target["is_active"]
                and self._count_active_admins(conn) <= 1
            ):
                raise Conflict("last_admin")
            res = conn.execute(
                update(users)
                .where(and_(users.c.id == target_id, users.c.role == expected_role))
                .values(role=new_role)
            )
            if res.rowcount != 1:
                raise Conflict("role_conflict")
            self._audit(conn, actor_id, "role_change", target_id, {"role": target["role"]},
                        {"role": new_role}, now)  # fmt: skip
            target["role"] = new_role
            return target

    def set_active(
        self, actor_id: str, target_id: str, active: bool, now: datetime
    ) -> dict[str, Any]:
        with self._tx() as conn:
            self._admin_lock(conn)
            row = conn.execute(select(users).where(users.c.id == target_id)).first()
            if row is None:
                raise NotFound()
            target = self._user(row)
            if target["is_active"] == active:
                return target
            if not active:
                if target["id"] == actor_id and target["role"] == "ADMIN":
                    if self._count_active_admins(conn) <= 1:
                        raise Conflict("last_admin")
                elif (
                    target["role"] == "ADMIN"
                    and target["is_active"]
                    and self._count_active_admins(conn) <= 1
                ):
                    raise Conflict("last_admin")
            conn.execute(update(users).where(users.c.id == target_id).values(is_active=active))
            self._audit(conn, actor_id, "activate" if active else "deactivate", target_id,
                        {"is_active": target["is_active"]}, {"is_active": active}, now)  # fmt: skip
            target["is_active"] = active
            return target

    def list_audit(self, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        with self.engine.connect() as c:
            total = c.execute(select(func.count()).select_from(admin_audit)).scalar_one()
            rows = c.execute(
                select(admin_audit)
                .order_by(admin_audit.c.created_at.desc(), admin_audit.c.id)
                .limit(limit).offset(offset)
            ).all()  # fmt: skip
        out = []
        for r in rows:
            d = dict(r._mapping)
            out.append(d)
        return out, int(total)

    # ------------------------------------------------------------------ complaints
    def create_complaint(
        self, owner_id: str, fields: dict[str, Any], now: datetime,
        idempotency_key: str | None, request_hash: str,
    ) -> tuple[dict[str, Any], bool]:  # fmt: skip
        """Insert a complaint (+ initial history row). With an idempotency key the key row is
        written in the same transaction; a replay returns the original (created=False)."""
        for attempt in (1, 2):
            try:
                return self._create_complaint(owner_id, fields, now, idempotency_key, request_hash)
            except IntegrityError:
                if attempt == 2 or not idempotency_key:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    def _create_complaint(
        self, owner_id: str, fields: dict[str, Any], now: datetime,
        idempotency_key: str | None, request_hash: str,
    ) -> tuple[dict[str, Any], bool]:  # fmt: skip
        with self._tx() as conn:
            khash = hash_key(idempotency_key) if idempotency_key else None
            if khash:
                prev = conn.execute(
                    select(idempotency_keys).where(
                        idempotency_keys.c.user_id == owner_id, idempotency_keys.c.key_hash == khash
                    )
                ).first()
                if prev is not None:
                    created = _aware(prev.created_at)
                    assert created is not None
                    if now - created < IDEMPOTENCY_TTL:
                        if prev.request_hash != request_hash:
                            raise Conflict("idempotency_key_reuse")
                        got = self._get_complaint(conn, prev.complaint_id)
                        if got is None:
                            raise Conflict("idempotent_target_deleted")
                        return got, False
                    conn.execute(delete(idempotency_keys).where(idempotency_keys.c.id == prev.id))
            cid = new_id()
            conn.execute(
                insert(complaints).values(
                    id=cid, owner_user_id=owner_id, status="SUBMITTED", created_at=now,
                    updated_at=now, **fields,
                )
            )  # fmt: skip
            conn.execute(
                insert(complaint_status_history).values(
                    id=new_id(), complaint_id=cid, from_status=None, to_status="SUBMITTED",
                    actor_user_id=owner_id, note=None, created_at=now,
                )
            )  # fmt: skip
            if khash:
                conn.execute(
                    insert(idempotency_keys).values(
                        id=new_id(), user_id=owner_id, key_hash=khash, request_hash=request_hash,
                        complaint_id=cid, created_at=now,
                    )
                )  # fmt: skip
            got = self._get_complaint(conn, cid)
            assert got is not None
            return got, True

    @staticmethod
    def _complaint_row(row: Any) -> dict[str, Any]:
        d = dict(row._mapping)
        for k in ("incident_at", "reported_at", "created_at", "updated_at", "deleted_at"):
            d[k] = _aware(d[k])
        return d

    def _get_complaint(
        self, conn: Connection, complaint_id: str, *, lock: bool = False
    ) -> dict[str, Any] | None:
        q = select(complaints).where(
            complaints.c.id == complaint_id, complaints.c.deleted_at.is_(None)
        )
        if lock:
            q = q.with_for_update()
        row = conn.execute(q).first()
        return self._complaint_row(row) if row else None

    def get_complaint(self, complaint_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as c:
            return self._get_complaint(c, complaint_id)

    def complaint_history(self, complaint_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as c:
            rows = c.execute(
                select(complaint_status_history)
                .where(complaint_status_history.c.complaint_id == complaint_id)
                .order_by(complaint_status_history.c.created_at, complaint_status_history.c.id)
            ).all()
        return [{**dict(r._mapping), "created_at": _aware(r.created_at)} for r in rows]

    def list_complaints(
        self, *, owner_id: str | None, filters: dict[str, Any], sort: str, limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        cond: list[Any] = [complaints.c.deleted_at.is_(None)]
        if owner_id:
            cond.append(complaints.c.owner_user_id == owner_id)
        f = filters
        if f.get("status"):
            cond.append(complaints.c.status == f["status"])
        if f.get("category"):
            cond.append(complaints.c.category == f["category"])
        if f.get("city"):
            cond.append(func.lower(complaints.c.city) == f["city"].casefold())
        if f.get("state"):
            cond.append(func.lower(complaints.c.state) == f["state"].casefold())
        if f.get("incident_from"):
            cond.append(complaints.c.incident_at >= f["incident_from"])
        if f.get("incident_to"):
            cond.append(complaints.c.incident_at <= f["incident_to"])
        if f.get("created_from"):
            cond.append(complaints.c.created_at >= f["created_from"])
        if f.get("created_to"):
            cond.append(complaints.c.created_at <= f["created_to"])
        if f.get("q"):
            pat = f"%{_like_escape(f['q'].casefold())}%"
            cond.append(
                or_(
                    func.lower(complaints.c.title).like(pat, escape="\\"),
                    func.lower(complaints.c.description).like(pat, escape="\\"),
                )
            )
        stored_ev = exists().where(
            complaint_evidence.c.complaint_id == complaints.c.id,
            complaint_evidence.c.status == "stored",
        )
        if f.get("has_evidence") is True:
            cond.append(stored_ev)
        elif f.get("has_evidence") is False:
            cond.append(~stored_ev)
        if f.get("risk_level"):
            latest = (
                select(analyses.c.complaint_id, func.max(analyses.c.version).label("v"))
                .group_by(analyses.c.complaint_id)
                .subquery()
            )
            ids = (
                select(analyses.c.complaint_id)
                .join(
                    latest,
                    and_(
                        analyses.c.complaint_id == latest.c.complaint_id,
                        analyses.c.version == latest.c.v,
                    ),
                )
                .where(analyses.c.risk_level == f["risk_level"])
            )
            cond.append(complaints.c.id.in_(ids))
        desc = sort.startswith("-")
        col = SORTS[sort.lstrip("-")]
        if sort.lstrip("-") == "amount":
            col = cast(col, Float)  # SQLite stores money as exact text; order numerically
        order = [(col.desc() if desc else col.asc()).nulls_last(), complaints.c.id]
        with self.engine.connect() as c:
            total = c.execute(
                select(func.count()).select_from(complaints).where(*cond)
            ).scalar_one()
            rows = c.execute(
                select(complaints).where(*cond).order_by(*order).limit(limit).offset(offset)
            ).all()
            items = [self._complaint_row(r) for r in rows]
            if items:
                ids_ = [i["id"] for i in items]
                counts = dict(
                    c.execute(
                        select(complaint_evidence.c.complaint_id, func.count())
                        .where(
                            complaint_evidence.c.complaint_id.in_(ids_),
                            complaint_evidence.c.status == "stored",
                        )
                        .group_by(complaint_evidence.c.complaint_id)
                    ).all()
                )
                for i in items:
                    i["evidence_count"] = int(counts.get(i["id"], 0))
                    i["latest_analysis"] = self._latest_analysis(c, i["id"], summary=True)
        return items, int(total)

    def change_status(
        self, complaint_id: str, to_status: str, actor_id: str, note: str | None, now: datetime
    ) -> dict[str, Any]:
        with self._tx() as conn:
            cur = self._get_complaint(conn, complaint_id, lock=True)
            if cur is None:
                raise NotFound()
            if to_status not in TRANSITIONS[cur["status"]]:
                raise Conflict("invalid_transition")
            res = conn.execute(
                update(complaints)
                .where(
                    and_(
                        complaints.c.id == complaint_id,
                        complaints.c.status == cur["status"],
                        complaints.c.deleted_at.is_(None),
                    )
                )
                .values(status=to_status, updated_at=now)
            )
            if res.rowcount != 1:
                raise Conflict("status_conflict")
            conn.execute(
                insert(complaint_status_history).values(
                    id=new_id(), complaint_id=complaint_id, from_status=cur["status"],
                    to_status=to_status, actor_user_id=actor_id, note=note, created_at=now,
                )
            )  # fmt: skip
            self._alert(
                conn, cur["owner_user_id"], complaint_id, "STATUS_CHANGED",
                f"Your complaint status changed to {to_status}.", now,
            )  # fmt: skip
            cur.update(status=to_status, updated_at=now)
            return cur

    def soft_delete(self, complaint_id: str, owner_id: str, now: datetime) -> None:
        with self._tx() as conn:
            cur = self._get_complaint(conn, complaint_id, lock=True)
            if cur is None or cur["owner_user_id"] != owner_id:
                raise NotFound()
            res = conn.execute(
                update(complaints)
                .where(
                    and_(
                        complaints.c.id == complaint_id,
                        complaints.c.status == "SUBMITTED",
                        complaints.c.deleted_at.is_(None),
                    )
                )
                .values(deleted_at=now, updated_at=now)
            )
            if res.rowcount != 1:
                raise Conflict("not_deletable")
            self._audit(conn, owner_id, "complaint_delete", owner_id, {"complaint_id": complaint_id,
                        "status": "SUBMITTED"}, {"deleted": True}, now)  # fmt: skip

    # ------------------------------------------------------------------ evidence
    def add_evidence(
        self, complaint_id: str, uploader_id: str, rec: dict[str, Any], max_count: int,
        now: datetime,
    ) -> tuple[dict[str, Any], bool]:  # fmt: skip
        """Insert a stored-evidence row. Returns (row, created); re-sending the same bytes (same
        sha256) for the complaint returns the existing row with created=False (retry-safe)."""
        with self._tx() as conn:
            cur = self._get_complaint(conn, complaint_id, lock=True)
            if cur is None or cur["owner_user_id"] != uploader_id:
                raise NotFound()
            same = conn.execute(
                select(complaint_evidence).where(
                    complaint_evidence.c.complaint_id == complaint_id,
                    complaint_evidence.c.status == "stored",
                    complaint_evidence.c.sha256 == rec["sha256"],
                )
            ).first()
            if same is not None:
                return {**dict(same._mapping), "created_at": _aware(same.created_at)}, False
            if cur["status"] in CLOSED_STATUSES:
                raise Conflict("complaint_closed")
            n = conn.execute(
                select(func.count())
                .select_from(complaint_evidence)
                .where(
                    complaint_evidence.c.complaint_id == complaint_id,
                    complaint_evidence.c.status == "stored",
                )
            ).scalar_one()
            if n >= max_count:
                raise Conflict("evidence_limit_reached")
            eid = new_id()
            conn.execute(
                insert(complaint_evidence).values(
                    id=eid, complaint_id=complaint_id, uploader_user_id=uploader_id,
                    status="stored", created_at=now, **rec,
                )
            )  # fmt: skip
            row = conn.execute(
                select(complaint_evidence).where(complaint_evidence.c.id == eid)
            ).one()
            return {**dict(row._mapping), "created_at": _aware(row.created_at)}, True

    def list_evidence(self, complaint_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as c:
            rows = c.execute(
                select(complaint_evidence)
                .where(complaint_evidence.c.complaint_id == complaint_id,
                       complaint_evidence.c.status == "stored")
                .order_by(complaint_evidence.c.created_at, complaint_evidence.c.id)
            ).all()  # fmt: skip
        return [{**dict(r._mapping), "created_at": _aware(r.created_at)} for r in rows]

    def get_evidence(self, complaint_id: str, evidence_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as c:
            row = c.execute(
                select(complaint_evidence).where(
                    complaint_evidence.c.id == evidence_id,
                    complaint_evidence.c.complaint_id == complaint_id,
                    complaint_evidence.c.status == "stored",
                )
            ).first()
        return {**dict(row._mapping), "created_at": _aware(row.created_at)} if row else None

    # ------------------------------------------------------------------ analyses
    @staticmethod
    def _analysis_row(row: Any, summary: bool = False) -> dict[str, Any]:
        d = dict(row._mapping)
        d["created_at"] = _aware(d["created_at"])
        if summary:
            return {
                k: d[k] for k in ("version", "created_at", "status", "risk_score", "risk_level")
            }
        return d

    def _latest_analysis(
        self, conn: Connection, complaint_id: str, summary: bool = False
    ) -> dict[str, Any] | None:
        row = conn.execute(
            select(analyses).where(analyses.c.complaint_id == complaint_id)
            .order_by(analyses.c.version.desc()).limit(1)
        ).first()  # fmt: skip
        return self._analysis_row(row, summary) if row else None

    def latest_analysis(self, complaint_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as c:
            return self._latest_analysis(c, complaint_id)

    def analysis_history(self, complaint_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as c:
            rows = c.execute(
                select(analyses).where(analyses.c.complaint_id == complaint_id)
                .order_by(analyses.c.version.desc())
            ).all()  # fmt: skip
        return [self._analysis_row(r, summary=True) for r in rows]

    def add_analysis(self, complaint_id: str, rec: dict[str, Any], now: datetime) -> dict[str, Any]:
        for attempt in (1, 2, 3):
            try:
                return self._add_analysis(complaint_id, rec, now)
            except IntegrityError:
                if attempt == 3:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    def _add_analysis(
        self, complaint_id: str, rec: dict[str, Any], now: datetime
    ) -> dict[str, Any]:
        with self._tx() as conn:
            cur = self._get_complaint(conn, complaint_id, lock=True)
            if cur is None:
                raise NotFound()
            version = (
                conn.execute(
                    select(func.coalesce(func.max(analyses.c.version), 0)).where(
                        analyses.c.complaint_id == complaint_id
                    )
                ).scalar_one()
                + 1
            )
            aid = new_id()
            conn.execute(
                insert(analyses).values(
                    id=aid, complaint_id=complaint_id, version=version, created_at=now, **rec
                )
            )
            # the owner is alerted for a HIGH result (also when an investigator ran it), once per
            # version, in the same transaction as the analysis row
            if rec.get("risk_level") == "HIGH" and rec.get("status") != "UNAVAILABLE":
                self._alert(
                    conn, cur["owner_user_id"], complaint_id, "HIGH_RISK_ANALYSIS",
                    f"Analysis {version} of your complaint found strong scam indicators. "
                    "Open the complaint for recommended safety steps.", now,
                )  # fmt: skip
            row = conn.execute(select(analyses).where(analyses.c.id == aid)).one()
            return self._analysis_row(row)

    # ------------------------------------------------------------------ alerts / stats
    @staticmethod
    def _alert(
        conn: Connection, user_id: str, complaint_id: str | None, kind: str, message: str,
        now: datetime,
    ) -> None:  # fmt: skip
        conn.execute(
            insert(alerts).values(
                id=new_id(), user_id=user_id, complaint_id=complaint_id, kind=kind,
                message=message, created_at=now,
            )
        )  # fmt: skip

    def list_alerts(
        self, user_id: str, unread_only: bool, limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int, int]:
        base = [alerts.c.user_id == user_id]
        cond = [*base, alerts.c.read_at.is_(None)] if unread_only else base
        with self.engine.connect() as c:
            total = c.execute(select(func.count()).select_from(alerts).where(*cond)).scalar_one()
            unread = c.execute(
                select(func.count()).select_from(alerts)
                .where(*base, alerts.c.read_at.is_(None))
            ).scalar_one()  # fmt: skip
            rows = c.execute(
                select(alerts).where(*cond).order_by(alerts.c.created_at.desc(), alerts.c.id)
                .limit(limit).offset(offset)
            ).all()  # fmt: skip
        items = [
            {**dict(r._mapping), "created_at": _aware(r.created_at), "read_at": _aware(r.read_at)}
            for r in rows
        ]
        return items, int(total), int(unread)

    def mark_alert_read(self, user_id: str, alert_id: str, now: datetime) -> dict[str, Any]:
        with self._tx() as conn:
            row = conn.execute(
                select(alerts).where(alerts.c.id == alert_id, alerts.c.user_id == user_id)
            ).first()
            if row is None:
                raise NotFound()
            if row.read_at is None:
                conn.execute(
                    update(alerts)
                    .where(and_(alerts.c.id == alert_id, alerts.c.read_at.is_(None)))
                    .values(read_at=now)
                )
            row = conn.execute(select(alerts).where(alerts.c.id == alert_id)).one()
            return {**dict(row._mapping), "created_at": _aware(row.created_at),
                    "read_at": _aware(row.read_at)}  # fmt: skip

    def stats(self, user_id: str) -> dict[str, Any]:
        own = [complaints.c.owner_user_id == user_id, complaints.c.deleted_at.is_(None)]
        with self.engine.connect() as c:
            by_status = dict(
                c.execute(
                    select(complaints.c.status, func.count())
                    .where(*own)
                    .group_by(complaints.c.status)
                ).all()  # fmt: skip
            )
            by_cat = dict(
                c.execute(
                    select(complaints.c.category, func.count())
                    .where(*own)
                    .group_by(complaints.c.category)
                ).all()  # fmt: skip
            )
            last = c.execute(select(func.max(complaints.c.created_at)).where(*own)).scalar_one()
            unread = c.execute(
                select(func.count()).select_from(alerts)
                .where(alerts.c.user_id == user_id, alerts.c.read_at.is_(None))
            ).scalar_one()  # fmt: skip
        return {
            "by_status": {k: int(v) for k, v in by_status.items()},
            "by_category": {k: int(v) for k, v in by_cat.items()},
            "last_complaint_at": _aware(last) if isinstance(last, datetime) else _parse(last),
            "unread_alerts": int(unread),
        }


def _parse(value: Any) -> datetime | None:
    # func.max over a TypeDecorator column returns the raw driver value on SQLite (a string)
    if value is None:
        return None
    if isinstance(value, str):
        return _aware(datetime.fromisoformat(value))
    return None


__all__ = [
    "CLOSED_STATUSES", "TRANSITIONS", "ComplaintStore", "Conflict", "Decimal", "NotFound",
    "StoreError",
]  # fmt: skip
