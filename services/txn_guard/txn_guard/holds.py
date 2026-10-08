"""Hold store (in-memory and Redis), resolve/upgrade rules and the audit sink.

A hold exists for every ``step_up`` / ``hold_verify`` verdict. Humans decide: a hold past its
deadline is flagged overdue (see ``is_overdue``) but is never auto-released or auto-blocked.
Every state change is an audit event carrying only ids/tokens, never raw PII.
"""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel
from scam_contracts.models import LedgerEntryIn, Reason
from scam_contracts.topics import Topics
from svckit.bus import Bus

Decision = Literal["step_up", "hold_verify"]
Action = Literal["release", "confirm_block"]
RANK = {"allow": 0, "step_up": 1, "hold_verify": 2}
SERVICE = "txn-guard"
SYSTEM_ACTOR = "system:txn-guard"


class HoldError(Exception):
    pass


class ActorRequired(HoldError, ValueError):
    pass


class HoldNotFound(HoldError, KeyError):
    pass


class HoldConflict(HoldError):
    pass


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


# ------------------------------------------------------------------------------------ audit
class AuditSink(Protocol):
    async def emit(
        self, event_type: str, txn_id: str, decision: str, actor: str, model_version: str
    ) -> None: ...


def payload_hash(
    event_type: str, txn_id: str, decision: str, actor: str, model_version: str
) -> str:
    raw = json.dumps([event_type, txn_id, decision, actor, model_version], separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.events: list[dict[str, str]] = []

    async def emit(
        self, event_type: str, txn_id: str, decision: str, actor: str, model_version: str
    ) -> None:
        self.events.append(
            {"event_type": event_type, "txn_id": txn_id, "decision": decision, "actor": actor,
             "model_version": model_version,
             "payload_hash": payload_hash(event_type, txn_id, decision, actor, model_version)}
        )  # fmt: skip


class BusAuditSink:
    """Writes ``LedgerEntryIn`` to Topics.LEDGER (the ledger service itself is a later task)."""

    def __init__(self, bus: Bus) -> None:
        self._bus = bus

    async def emit(
        self, event_type: str, txn_id: str, decision: str, actor: str, model_version: str
    ) -> None:
        entry = LedgerEntryIn(
            service=SERVICE, actor=actor, event_type=event_type,
            payload_hash=payload_hash(event_type, txn_id, decision, actor, model_version),
            model_version=model_version or None,
        )  # fmt: skip
        await self._bus.publish(Topics.LEDGER, txn_id, entry)


# ------------------------------------------------------------------------------------ store
class HoldStore(Protocol):
    async def create(
        self, txn_id: str, decision: Decision, reasons: list[Reason], deadline: datetime, *,
        payer_token: str = "", score: float = 0.0, model_version: str = "", decision_seq: int = 1,
    ) -> Hold: ...  # fmt: skip

    async def get(self, txn_id: str) -> Hold | None: ...

    async def list_open(self) -> list[Hold]: ...

    async def resolve(self, txn_id: str, action: Action, actor: str) -> Hold: ...

    async def upgrade(
        self, txn_id: str, decision: Decision, reasons: list[Reason], score: float,
        model_version: str, decision_seq: int,
    ) -> Hold | None: ...  # fmt: skip

    def is_overdue(self, hold: Hold) -> bool: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


class _BaseHoldStore:
    """Shared rules over three primitives: ``_get``, ``_cas`` (compare-and-set) and ``_open``."""

    def __init__(self, audit: AuditSink | None = None, clock: Callable[[], datetime] = _utcnow):
        self._audit: AuditSink = audit if audit is not None else InMemoryAuditSink()
        self._clock = clock

    # primitives -------------------------------------------------------------------------
    async def _get(self, txn_id: str) -> Hold | None:
        raise NotImplementedError

    async def _cas(self, txn_id: str, expected: Hold | None, new: Hold) -> bool:
        raise NotImplementedError

    async def _open(self) -> list[Hold]:
        raise NotImplementedError

    # rules ------------------------------------------------------------------------------
    def is_overdue(self, hold: Hold) -> bool:
        return hold.state == "open" and self._clock() > hold.deadline

    async def get(self, txn_id: str) -> Hold | None:
        return await self._get(txn_id)

    async def list_open(self) -> list[Hold]:
        return sorted(await self._open(), key=lambda h: (h.deadline, h.txn_id))

    async def create(
        self, txn_id: str, decision: Decision, reasons: list[Reason], deadline: datetime, *,
        payer_token: str = "", score: float = 0.0, model_version: str = "", decision_seq: int = 1,
    ) -> Hold:  # fmt: skip
        hold = Hold(
            txn_id=txn_id, payer_token=payer_token, decision=decision, score=score,
            reasons=reasons, model_version=model_version, decision_seq=decision_seq,
            created_at=self._clock(), deadline=deadline,
        )  # fmt: skip
        if await self._cas(txn_id, None, hold):
            await self._audit.emit("hold.created", txn_id, decision, SYSTEM_ACTOR, model_version)
            return hold
        existing = await self._get(txn_id)
        assert existing is not None
        return existing

    async def resolve(self, txn_id: str, action: Action, actor: str) -> Hold:
        if not actor or not actor.strip():
            raise ActorRequired("resolving a hold requires a non-empty actor")
        if action not in ("release", "confirm_block"):
            raise ValueError(f"unknown action {action!r}")
        state = "released" if action == "release" else "blocked"
        for _ in range(8):
            cur = await self._get(txn_id)
            if cur is None:
                raise HoldNotFound(txn_id)
            if cur.state != "open":
                if cur.state == state:
                    return cur  # idempotent: same action again
                raise HoldConflict(f"hold already {cur.state}")
            new = cur.model_copy(
                update={"state": state, "resolved_by": actor.strip(), "resolved_at": self._clock()}
            )
            if await self._cas(txn_id, cur, new):
                await self._audit.emit(
                    "hold.resolved", txn_id, f"{cur.decision}:{action}", actor.strip(),
                    cur.model_version,
                )  # fmt: skip
                return new
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
                return None
            new = cur.model_copy(
                update={"decision": decision, "reasons": reasons, "score": score,
                        "model_version": model_version, "decision_seq": decision_seq}
            )  # fmt: skip
            if await self._cas(txn_id, cur, new):
                await self._audit.emit(
                    "hold.upgraded", txn_id, decision, SYSTEM_ACTOR, model_version
                )  # fmt: skip
                return new
        return None


class InMemoryHoldStore(_BaseHoldStore):
    def __init__(self, audit: AuditSink | None = None, clock: Callable[[], datetime] = _utcnow):
        super().__init__(audit, clock)
        self._holds: dict[str, Hold] = {}

    async def _get(self, txn_id: str) -> Hold | None:
        return self._holds.get(txn_id)

    async def _cas(self, txn_id: str, expected: Hold | None, new: Hold) -> bool:
        if self._holds.get(txn_id) != expected:
            return False
        self._holds[txn_id] = new
        return True

    async def _open(self) -> list[Hold]:
        return [h for h in self._holds.values() if h.state == "open"]


class RedisHoldStore(_BaseHoldStore):
    """Holds as JSON under ``hold:{txn_id}``; open holds indexed in a sorted set by deadline.
    Writes are compare-and-set via WATCH/MULTI so concurrent resolvers cannot both win."""

    KEY, INDEX = "txnguard:hold:", "txnguard:holds:open"

    def __init__(
        self, client: Any, audit: AuditSink | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:  # fmt: skip
        super().__init__(audit, clock)
        self._r = client

    async def _get(self, txn_id: str) -> Hold | None:
        raw = await self._r.get(self.KEY + txn_id)
        return Hold.model_validate_json(raw) if raw else None

    async def _cas(self, txn_id: str, expected: Hold | None, new: Hold) -> bool:
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
                await pipe.execute()
                return True
            except WatchError:
                return False

    async def _open(self) -> list[Hold]:
        ids = await self._r.zrange(self.INDEX, 0, -1)
        out = []
        for i in ids:
            h = await self._get(i.decode() if isinstance(i, bytes) else i)
            if h is not None and h.state == "open":
                out.append(h)
        return out
