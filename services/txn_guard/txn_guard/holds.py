"""Hold store (in-memory and Redis), resolve/upgrade rules and the transactional audit outbox.

A hold exists for every ``step_up`` / ``hold_verify`` verdict. Humans decide: a hold past its
deadline is flagged overdue (``hold.overdue`` audit event, ``overdue_total`` counter) but is never
auto-released or auto-blocked.

Audit is a transactional outbox: the audit entry is written into the hold record
(``audit_pending``) in the SAME compare-and-set as the state change, then ``drain_audit``
publishes pending entries to the ``AuditSink`` at-least-once and clears them only after a
successful publish. Each entry carries a deterministic ``payload_hash`` over (event, txn_id,
decision, decision_seq, score, actor/action, model_version, reason codes): the ledger-side dedupe
key. Drains happen right after each change, on every idempotent re-entry (repeated
create/resolve/upgrade/verify) and from the periodic ``sweep``. A failing sink never fails the
state change; the entry stays in the outbox.
"""

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel
from scam_contracts.models import LedgerEntryIn, Reason
from scam_contracts.topics import Topics
from svckit.bus import Bus

log = logging.getLogger("txn_guard")

Decision = Literal["step_up", "hold_verify"]
Action = Literal["release", "confirm_block"]
RANK = {"allow": 0, "step_up": 1, "hold_verify": 2}
SERVICE = "txn-guard"
SYSTEM_ACTOR = "system:txn-guard"
CLOSED_HOLD_TTL_S = 90 * 86400
MAX_LIST = 1000


class HoldError(Exception):
    pass


class ActorRequired(HoldError, ValueError):
    pass


class HoldNotFound(HoldError, KeyError):
    pass


class HoldConflict(HoldError):
    pass


class AuditEntry(BaseModel):
    event_type: str
    txn_id: str
    decision: str
    decision_seq: int
    score: float
    actor: str
    model_version: str
    reason_codes: list[str] = []
    payload_hash: str


def make_entry(
    event_type: str, txn_id: str, decision: str, decision_seq: int, score: float, actor: str,
    model_version: str, reasons: list[Reason],
) -> AuditEntry:  # fmt: skip
    codes = sorted(r.code for r in reasons)
    raw = json.dumps(
        [event_type, txn_id, decision, decision_seq, round(score, 6), actor, model_version, codes],
        separators=(",", ":"),
    )
    return AuditEntry(
        event_type=event_type, txn_id=txn_id, decision=decision, decision_seq=decision_seq,
        score=round(score, 6), actor=actor, model_version=model_version, reason_codes=codes,
        payload_hash=hashlib.sha256(raw.encode()).hexdigest(),
    )  # fmt: skip


class Hold(BaseModel):
    txn_id: str
    payer_token: str = ""
    decision: Decision
    score: float = 0.0
    reasons: list[Reason]
    model_version: str = ""
    decision_seq: int = 1
    state: Literal["open", "released", "blocked"] = "open"
    created_at: datetime
    deadline: datetime
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    overdue_flagged: bool = False
    audit_pending: list[AuditEntry] = []


# ------------------------------------------------------------------------------------ audit
class AuditSink(Protocol):
    async def emit(self, entry: AuditEntry) -> None: ...


class InMemoryAuditSink:
    """Collects entries; dedupes by ``payload_hash`` like the ledger would."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._hashes: set[str] = set()

    async def emit(self, entry: AuditEntry) -> None:
        if entry.payload_hash in self._hashes:
            return
        self._hashes.add(entry.payload_hash)
        self.events.append(entry.model_dump())


class BusAuditSink:
    """Writes ``LedgerEntryIn`` to Topics.LEDGER (the ledger service itself is a later task);
    at-least-once, so the ledger must dedupe on ``payload_hash``."""

    def __init__(self, bus: Bus) -> None:
        self._bus = bus

    async def emit(self, entry: AuditEntry) -> None:
        await self._bus.publish(
            Topics.LEDGER, entry.txn_id,
            LedgerEntryIn(
                service=SERVICE, actor=entry.actor, event_type=entry.event_type,
                payload_hash=entry.payload_hash, model_version=entry.model_version or None,
            ),
        )  # fmt: skip


# ------------------------------------------------------------------------------------ store
class HoldStore(Protocol):
    async def create(
        self, txn_id: str, decision: Decision, reasons: list[Reason], deadline: datetime, *,
        payer_token: str = "", score: float = 0.0, model_version: str = "", decision_seq: int = 1,
    ) -> Hold: ...  # fmt: skip

    async def get(self, txn_id: str) -> Hold | None: ...

    async def list_open(self, limit: int = 200) -> list[Hold]: ...

    async def resolve(
        self, txn_id: str, action: Action, actor: str, *, expect_decision: str | None = None,
        expect_seq: int | None = None,
    ) -> Hold: ...  # fmt: skip

    async def upgrade(
        self, txn_id: str, decision: Decision, reasons: list[Reason], score: float,
        model_version: str, decision_seq: int,
    ) -> Hold | None: ...  # fmt: skip

    async def drain_audit(self, txn_id: str) -> None: ...

    async def sweep(self) -> int: ...

    async def count_open(self) -> int: ...

    async def count_overdue(self) -> int: ...

    async def overdue_total(self) -> int: ...

    def is_overdue(self, hold: Hold) -> bool: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


class _BaseHoldStore:
    """Shared rules over primitives: ``_get``, ``_cas``, ``_open``, ``_pending_ids``, counters."""

    def __init__(self, audit: AuditSink | None = None, clock: Callable[[], datetime] = _utcnow):
        self._audit: AuditSink = audit if audit is not None else InMemoryAuditSink()
        self._clock = clock

    # primitives -------------------------------------------------------------------------
    async def _get(self, txn_id: str) -> Hold | None:
        raise NotImplementedError

    async def _cas(
        self, txn_id: str, expected: Hold | None, new: Hold, count_overdue: bool = False
    ) -> bool:
        raise NotImplementedError

    async def _open(self, limit: int) -> list[Hold]:
        raise NotImplementedError

    async def _pending_ids(self) -> list[str]:
        raise NotImplementedError

    async def count_open(self) -> int:
        raise NotImplementedError

    async def count_overdue(self) -> int:
        raise NotImplementedError

    async def overdue_total(self) -> int:
        raise NotImplementedError

    # rules ------------------------------------------------------------------------------
    def is_overdue(self, hold: Hold) -> bool:
        return hold.state == "open" and self._clock() > hold.deadline

    async def get(self, txn_id: str) -> Hold | None:
        return await self._get(txn_id)

    async def list_open(self, limit: int = 200) -> list[Hold]:
        limit = max(1, min(limit, MAX_LIST))
        return sorted(await self._open(limit), key=lambda h: (h.deadline, h.txn_id))[:limit]

    async def drain_audit(self, txn_id: str) -> None:
        """Publish the hold's pending audit entries (at-least-once) then clear them."""
        for _ in range(8):
            cur = await self._get(txn_id)
            if cur is None or not cur.audit_pending:
                return
            sent = list(cur.audit_pending)
            for entry in sent:
                await self._audit.emit(entry)
            done = {e.payload_hash for e in sent}
            rest = [e for e in cur.audit_pending if e.payload_hash not in done]
            if await self._cas(txn_id, cur, cur.model_copy(update={"audit_pending": rest})):
                return

    async def _drain_quietly(self, txn_id: str) -> None:
        try:
            await self.drain_audit(txn_id)
        except Exception:
            log.warning("audit drain failed txn_id=%s (kept in outbox)", txn_id, exc_info=True)

    async def create(
        self, txn_id: str, decision: Decision, reasons: list[Reason], deadline: datetime, *,
        payer_token: str = "", score: float = 0.0, model_version: str = "", decision_seq: int = 1,
    ) -> Hold:  # fmt: skip
        entry = make_entry("hold.created", txn_id, decision, decision_seq, score, SYSTEM_ACTOR,
                           model_version, reasons)  # fmt: skip
        hold = Hold(
            txn_id=txn_id, payer_token=payer_token, decision=decision, score=score,
            reasons=reasons, model_version=model_version, decision_seq=decision_seq,
            created_at=self._clock(), deadline=deadline, audit_pending=[entry],
        )  # fmt: skip
        created = await self._cas(txn_id, None, hold)
        await self._drain_quietly(txn_id)  # also drains a pending entry left by an earlier crash
        existing = await self._get(txn_id)
        assert existing is not None
        del created
        return existing

    async def resolve(
        self, txn_id: str, action: Action, actor: str, *, expect_decision: str | None = None,
        expect_seq: int | None = None,
    ) -> Hold:  # fmt: skip
        if not actor or not actor.strip():
            raise ActorRequired("resolving a hold requires a non-empty actor")
        if action not in ("release", "confirm_block"):
            raise ValueError(f"unknown action {action!r}")
        actor = actor.strip()
        state = "released" if action == "release" else "blocked"
        for _ in range(8):
            cur = await self._get(txn_id)
            if cur is None:
                raise HoldNotFound(txn_id)
            if cur.state != "open":
                if cur.state == state:
                    await self._drain_quietly(txn_id)
                    return await self._get(txn_id) or cur  # idempotent: same action again
                raise HoldConflict(f"hold already {cur.state}")
            if (expect_decision is not None and cur.decision != expect_decision) or (
                expect_seq is not None and cur.decision_seq != expect_seq
            ):
                raise HoldConflict("hold changed since it was read (decision/seq mismatch)")
            entry = make_entry(
                "hold.resolved", txn_id, f"{cur.decision}:{action}", cur.decision_seq,
                cur.score, actor, cur.model_version, cur.reasons,
            )  # fmt: skip
            new = cur.model_copy(
                update={"state": state, "resolved_by": actor, "resolved_at": self._clock(),
                        "audit_pending": [*cur.audit_pending, entry]}
            )  # fmt: skip
            if await self._cas(txn_id, cur, new):
                await self._drain_quietly(txn_id)
                return new.model_copy(update={"audit_pending": []})
        raise HoldConflict("concurrent update; retry")

    async def upgrade(
        self, txn_id: str, decision: Decision, reasons: list[Reason], score: float,
        model_version: str, decision_seq: int,
    ) -> Hold | None:  # fmt: skip
        """Raise an open hold to a stronger decision; None if missing, resolved or not stronger."""
        for _ in range(8):
            cur = await self._get(txn_id)
            if cur is None or cur.state != "open":
                return None
            if RANK[decision] <= RANK[cur.decision] or decision_seq <= cur.decision_seq:
                await self._drain_quietly(txn_id)
                return None
            entry = make_entry("hold.upgraded", txn_id, decision, decision_seq, score,
                               SYSTEM_ACTOR, model_version, reasons)  # fmt: skip
            new = cur.model_copy(
                update={"decision": decision, "reasons": reasons, "score": score,
                        "model_version": model_version, "decision_seq": decision_seq,
                        "audit_pending": [*cur.audit_pending, entry]}
            )  # fmt: skip
            if await self._cas(txn_id, cur, new):
                await self._drain_quietly(txn_id)
                return new.model_copy(update={"audit_pending": []})
        return None

    async def sweep(self) -> int:
        """Flag newly overdue holds (durable marker + one ``hold.overdue`` audit entry + counter)
        and drain every pending audit outbox. Returns the number newly flagged. Holds stay open."""
        flagged = 0
        for h in await self._open(MAX_LIST):
            if not self.is_overdue(h) or h.overdue_flagged:
                continue
            entry = make_entry("hold.overdue", h.txn_id, h.decision, h.decision_seq, h.score,
                               SYSTEM_ACTOR, h.model_version, h.reasons)  # fmt: skip
            new = h.model_copy(
                update={"overdue_flagged": True, "audit_pending": [*h.audit_pending, entry]}
            )
            if await self._cas(h.txn_id, h, new, count_overdue=True):
                flagged += 1
        for tid in await self._pending_ids():
            await self._drain_quietly(tid)
        return flagged


class InMemoryHoldStore(_BaseHoldStore):
    def __init__(self, audit: AuditSink | None = None, clock: Callable[[], datetime] = _utcnow):
        super().__init__(audit, clock)
        self._holds: dict[str, Hold] = {}
        self._overdue_total = 0

    async def _get(self, txn_id: str) -> Hold | None:
        return self._holds.get(txn_id)

    async def _cas(
        self, txn_id: str, expected: Hold | None, new: Hold, count_overdue: bool = False
    ) -> bool:
        if self._holds.get(txn_id) != expected:
            return False
        self._holds[txn_id] = new
        self._overdue_total += int(count_overdue)
        return True

    async def _open(self, limit: int) -> list[Hold]:
        return [h for h in self._holds.values() if h.state == "open"]

    async def _pending_ids(self) -> list[str]:
        return [t for t, h in self._holds.items() if h.audit_pending]

    async def count_open(self) -> int:
        return sum(h.state == "open" for h in self._holds.values())

    async def count_overdue(self) -> int:
        return sum(self.is_overdue(h) for h in self._holds.values())

    async def overdue_total(self) -> int:
        return self._overdue_total


class RedisHoldStore(_BaseHoldStore):
    """Holds as JSON under ``hold:{txn_id}``; open holds indexed in a sorted set by deadline, holds
    with pending audit in a set. Writes are compare-and-set via WATCH/MULTI so concurrent
    resolvers cannot both win. Closed holds expire after 90 days; open holds never expire."""

    KEY, INDEX = "txnguard:hold:", "txnguard:holds:open"
    AUDIT_SET, OVERDUE_KEY = "txnguard:holds:audit", "txnguard:overdue_total"

    def __init__(
        self, client: Any, audit: AuditSink | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:  # fmt: skip
        super().__init__(audit, clock)
        self._r = client

    async def _get(self, txn_id: str) -> Hold | None:
        raw = await self._r.get(self.KEY + txn_id)
        return Hold.model_validate_json(raw) if raw else None

    async def _cas(
        self, txn_id: str, expected: Hold | None, new: Hold, count_overdue: bool = False
    ) -> bool:
        from redis.exceptions import WatchError

        key = self.KEY + txn_id
        async with self._r.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(key)
                raw = await pipe.get(key)
                cur = Hold.model_validate_json(raw) if raw else None
                if cur != expected:
                    await pipe.reset()
                    return False
                pipe.multi()
                pipe.set(key, new.model_dump_json())
                if new.state == "open":
                    pipe.zadd(self.INDEX, {txn_id: new.deadline.timestamp()})
                else:
                    pipe.zrem(self.INDEX, txn_id)
                    pipe.expire(key, CLOSED_HOLD_TTL_S)
                if new.audit_pending:
                    pipe.sadd(self.AUDIT_SET, txn_id)
                else:
                    pipe.srem(self.AUDIT_SET, txn_id)
                if count_overdue:
                    pipe.incr(self.OVERDUE_KEY)
                await pipe.execute()
                return True
            except WatchError:
                return False

    async def _open(self, limit: int) -> list[Hold]:
        ids = await self._r.zrange(self.INDEX, 0, limit - 1)
        if not ids:
            return []
        raws = await self._r.mget(
            [self.KEY + (i.decode() if isinstance(i, bytes) else i) for i in ids]
        )
        holds = [Hold.model_validate_json(r) for r in raws if r]
        return [h for h in holds if h.state == "open"]

    async def _pending_ids(self) -> list[str]:
        return [
            i.decode() if isinstance(i, bytes) else i
            for i in await self._r.smembers(self.AUDIT_SET)
        ]

    async def count_open(self) -> int:
        return int(await self._r.zcard(self.INDEX))

    async def count_overdue(self) -> int:
        return int(await self._r.zcount(self.INDEX, "-inf", f"({self._clock().timestamp()}"))

    async def overdue_total(self) -> int:
        return int(await self._r.get(self.OVERDUE_KEY) or 0)
