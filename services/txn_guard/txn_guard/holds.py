"""Hold store (in-memory and Redis), resolve/upgrade rules and the transactional audit outbox.

A hold exists for every ``step_up`` / ``hold_verify`` verdict. Humans decide: a hold past its
deadline is flagged overdue (``hold.overdue`` audit event, ``overdue_total`` counter) but is never
auto-released or auto-blocked.

Audit is a transactional outbox: the audit entry is written into the hold record
(``audit_pending``) in the SAME compare-and-set as the state change, then ``drain_audit``
publishes pending entries to the ``AuditSink`` at-least-once and clears them only after a
successful publish. Each entry carries a structured, PII-free ``payload`` (txn_id, decision,
decision_seq, score to 4 dp, sorted reason codes, model_version, rail, amount BUCKET, deadline;
upgrade/resolution fields per event) and ``payload_hash = sha256(canonical_json(payload))``. The
payload holds only the hold's own timestamps (never a wall clock read at drain time), so a retry
re-emits byte-identical content, which the ledger absorbs (its idempotency key covers the hash,
actor, model version and case refs). ``case_refs`` = txn id, ``payee_ref:<16 hex>`` (the keyed
payee hash: the same mule account across banks) and, when a call risk drove the decision,
``call_ref:<16 hex>``. No amounts, account numbers, phones or payer tokens are emitted.
Drains happen right after each change, on every idempotent re-entry (repeated
create/resolve/upgrade/verify) and from the periodic ``sweep``. A failing sink never fails the
state change; the entry stays in the outbox.
"""

import asyncio
import hashlib
import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import BaseModel
from scam_contracts.canonical import payload_hash as canonical_payload_hash
from scam_contracts.models import LedgerEntryIn, Reason
from scam_contracts.topics import Topics
from svckit.bus import Bus
from svckit.drain import Drainer
from svckit.ledger import build_or_placeholder, safe_actor, txn_ref, utc_ts

from .hub_submit import (
    MAX_HUB_PENDING_PER_HOLD,
    RESULTS,
    HubOutcome,
    HubSubmission,
    HubSubmitConfig,
    evidence_ref_for,
    skip_reason,
)

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


AMOUNT_BUCKETS: tuple[tuple[int, str], ...] = (
    (1_000, "<1k"), (10_000, "1k-10k"), (100_000, "10k-100k"), (1_000_000, "100k-1m"),
)  # fmt: skip


def amount_bucket(amount: Decimal | float | int) -> str:
    """Coarse amount class for the audit trail (the amount itself is never emitted)."""
    for limit, label in AMOUNT_BUCKETS:
        if amount < limit:
            return label
    return ">=1m"


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
    payload: dict[str, Any] = {}  # empty only for entries queued by an older version
    case_refs: list[str] = []


def _bare(ref: str) -> str | None:
    return ref.split(":", 1)[1] if ref else None


def make_entry(
    event_type: str, txn_id: str, decision: str, decision_seq: int, score: float, actor: str,
    model_version: str, reasons: list[Reason], *, deadline: datetime | None = None,
    rail: str = "", amount_bucket: str = "", payee_ref: str = "", call_ref: str = "",
    extra: dict[str, Any] | None = None,
) -> AuditEntry:  # fmt: skip
    """Build one outbox entry. Everything in the payload derives from the hold's own state."""
    codes = sorted({r.code for r in reasons})
    ref = txn_ref(txn_id)  # the guard refuses numeric ids (UPI RRNs): payloads carry txn_<hash>
    fields: dict[str, Any] = {
        "txn_id": ref, "decision": decision, "decision_seq": decision_seq,
        "score": round(score, 4), "reason_codes": codes, "model_version": model_version or None,
        "rail": rail or None, "amount_bucket": amount_bucket or None,
        "deadline_ts": utc_ts(deadline) if deadline else None, "payee_ref": _bare(payee_ref),
        "call_ref": _bare(call_ref),
    }  # fmt: skip
    payload = {k: v for k, v in (fields | (extra or {})).items() if v is not None}
    refs = [ref, *(r for r in (payee_ref, call_ref) if r)]
    return AuditEntry(
        event_type=event_type, txn_id=txn_id, decision=decision, decision_seq=decision_seq,
        score=round(score, 4), actor=actor, model_version=model_version, reason_codes=codes,
        payload_hash=canonical_payload_hash(payload), payload=payload, case_refs=refs,
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
    rail: str = ""
    amount_bucket: str = ""
    payee_ref: str = ""  # "payee_ref:<16 hex>" from the keyed payee hash (audit linking only)
    call_ref: str = ""  # "call_ref:<16 hex>" of the call risk that influenced the decision
    audit_pending: list[AuditEntry] = []
    # confirm-to-hub (additive; older records load with the defaults). ``payee_hash`` is the keyed
    # payee hash (never returned by the API); ``hub_pending`` is the durable submission outbox,
    # written in the same compare-and-set as the ``blocked`` state; ``hub_result`` / ``hub_reason``
    # record the terminal outcome (ok, duplicate, protected, rejected, exhausted, skipped).
    payee_hash: str = ""
    hub_pending: list[HubSubmission] = []
    hub_result: str | None = None
    hub_reason: str | None = None


# ------------------------------------------------------------------------------------ audit
class AuditSink(Protocol):
    async def emit(self, entry: AuditEntry) -> None: ...


class InMemoryAuditSink:
    """Collects entries; dedupes by ``payload_hash`` (a stricter, test-only stand-in: the ledger
    itself dedupes on the full idempotency key)."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._hashes: set[str] = set()

    async def emit(self, entry: AuditEntry) -> None:
        if entry.payload_hash in self._hashes:
            return
        self._hashes.add(entry.payload_hash)
        self.events.append(entry.model_dump())


class BusAuditSink:
    """Publishes structured, PII-checked ``LedgerEntryIn`` entries to Topics.LEDGER
    (at-least-once; the ledger absorbs exact repeats). An entry queued by an older version (no
    payload) is sent hash-only. A payload the PII guard refuses is replaced by a fixed-shape
    placeholder ``{txn_id, event, decision_seq, audit: payload_refused}`` so the outbox always
    drains and the chain still records that this kind of change happened; only the hash of the
    hold id and the error class are logged."""

    def __init__(self, bus: Bus) -> None:
        self._bus = bus

    async def emit(self, entry: AuditEntry) -> None:
        if not entry.payload:
            await self._bus.publish(
                Topics.LEDGER, entry.txn_id,
                LedgerEntryIn(
                    service=SERVICE, actor=entry.actor, event_type=entry.event_type,
                    payload_hash=entry.payload_hash, model_version=entry.model_version or None,
                ),
            )  # fmt: skip
            return
        ref = txn_ref(entry.txn_id)
        out, refused = build_or_placeholder(
            SERVICE, entry.actor, entry.event_type, entry.payload,
            model_version=entry.model_version or None, case_refs=entry.case_refs,
            placeholder={"txn_id": ref, "event": entry.event_type,
                         "decision_seq": entry.decision_seq},
            placeholder_refs=[ref],
        )  # fmt: skip
        if refused:
            log.warning(
                "audit payload refused by the PII guard; placeholder sent hold=%s",
                hashlib.sha256(entry.txn_id.encode()).hexdigest()[:12],
            )
        await self._bus.publish(Topics.LEDGER, out.case_refs[0] if out.case_refs else SERVICE, out)


# ------------------------------------------------------------------------------------ store
class HoldStore(Protocol):
    async def create(
        self, txn_id: str, decision: Decision, reasons: list[Reason], deadline: datetime, *,
        payer_token: str = "", score: float = 0.0, model_version: str = "", decision_seq: int = 1,
        rail: str = "", amount_bucket: str = "", payee_ref: str = "", call_ref: str = "",
        trigger: str | None = None, payee_hash: str = "",
    ) -> Hold: ...  # fmt: skip

    async def get(self, txn_id: str) -> Hold | None: ...

    async def list_open(self, limit: int = 200) -> list[Hold]: ...

    async def resolve(
        self, txn_id: str, action: Action, actor: str, *, expect_decision: str | None = None,
        expect_seq: int | None = None, role: str | None = None,
    ) -> Hold: ...  # fmt: skip

    async def upgrade(
        self, txn_id: str, decision: Decision, reasons: list[Reason], score: float,
        model_version: str, decision_seq: int, *, trigger: str = "call_risk",
        call_ref: str | None = None,
    ) -> Hold | None: ...  # fmt: skip

    async def drain_audit(self, txn_id: str) -> None: ...

    async def drain_hub(self, txn_id: str) -> bool: ...

    async def hub_pending_count(self) -> int: ...

    async def sweep(self) -> int: ...

    async def count_open(self) -> int: ...

    async def count_overdue(self) -> int: ...

    async def overdue_total(self) -> int: ...

    def is_overdue(self, hold: Hold) -> bool: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


class _BaseHoldStore:
    """Shared rules over primitives: ``_get``, ``_cas``, ``_open``, ``_pending_ids``, counters."""

    def __init__(
        self,
        audit: AuditSink | None = None,
        clock: Callable[[], datetime] = _utcnow,
        drainer: Drainer | None = None,
        *,
        hub_submit: HubSubmitConfig | None = None,
        hub_client: Any = None,
        mono: Callable[[], float] = time.monotonic,
    ):
        self._audit: AuditSink = audit if audit is not None else InMemoryAuditSink()
        self._clock = clock
        self._mono = mono
        # confirm-to-hub: only active with both a config and a client (otherwise a confirm_block
        # records nothing and the store behaves exactly as before)
        self._hub_cfg = hub_submit
        self._hub_client = hub_client
        self.hub_stats: dict[str, int] = dict.fromkeys(RESULTS, 0) | {"auth_failures": 0}
        self.hub_last_error: str | None = None
        self._hub_reachable: bool | None = None
        self._hub_next: dict[str, float] = {}  # txn_id -> monotonic time of the next attempt
        self.hub_drainer = Drainer(
            cap=int(os.getenv("HUB_SUBMIT_CONCURRENCY", "8")),
            timeout_s=(hub_submit.timeout_s if hub_submit else 2.0) + 1.0,
            cooldown_s=float(os.getenv("HUB_SUBMIT_BREAKER_COOLDOWN_S", "10")),
            clock=mono, name="hub submission",
        )  # fmt: skip
        # audit delivery is fire-and-forget (cap, in-flight guard, timeout, breaker): the hot path
        # never awaits the ledger; the periodic ``sweep`` retries what is left
        self.drainer = drainer or Drainer()
        self.sweep_batch = int(os.getenv("LEDGER_SWEEP_BATCH", "200"))

    async def aclose(self) -> None:
        # pending audit entries and hub submissions stay in the hold records
        await self.drainer.aclose()
        await self.hub_drainer.aclose()

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

    async def _hub_ids(self) -> list[str]:
        raise NotImplementedError

    async def hub_pending_count(self) -> int:
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
        """Schedule a background drain of the hold's audit outbox; never awaits the ledger. The
        caller only yields a few loop turns (not time) so a fast sink has normally finished."""
        task = self.drainer.schedule(txn_id, lambda: self._drain_ok(txn_id))
        for _ in range(8):
            if task is None or task.done():
                break
            await asyncio.sleep(0)

    async def _drain_ok(self, txn_id: str) -> bool:
        await self.drain_audit(txn_id)  # errors/timeouts are counted and logged by the drainer
        return True

    async def create(
        self, txn_id: str, decision: Decision, reasons: list[Reason], deadline: datetime, *,
        payer_token: str = "", score: float = 0.0, model_version: str = "", decision_seq: int = 1,
        rail: str = "", amount_bucket: str = "", payee_ref: str = "", call_ref: str = "",
        trigger: str | None = None, payee_hash: str = "",
    ) -> Hold:  # fmt: skip
        extra = {"from_decision": "allow", "trigger": trigger} if trigger else None
        entry = make_entry(
            "hold.created", txn_id, decision, decision_seq, score, SYSTEM_ACTOR, model_version,
            reasons, deadline=deadline, rail=rail, amount_bucket=amount_bucket,
            payee_ref=payee_ref, call_ref=call_ref, extra=extra,
        )  # fmt: skip
        hold = Hold(
            txn_id=txn_id, payer_token=payer_token, decision=decision, score=score,
            reasons=reasons, model_version=model_version, decision_seq=decision_seq,
            created_at=self._clock(), deadline=deadline, rail=rail, amount_bucket=amount_bucket,
            payee_ref=payee_ref, call_ref=call_ref, audit_pending=[entry],
            payee_hash=payee_hash.strip().lower(),
        )  # fmt: skip
        created = await self._cas(txn_id, None, hold)
        await self._drain_quietly(txn_id)  # also drains a pending entry left by an earlier crash
        existing = await self._get(txn_id)
        assert existing is not None
        del created
        return existing

    async def resolve(
        self, txn_id: str, action: Action, actor: str, *, expect_decision: str | None = None,
        expect_seq: int | None = None, role: str | None = None,
    ) -> Hold:  # fmt: skip
        if not actor or not actor.strip():
            raise ActorRequired("resolving a hold requires a non-empty actor")
        if action not in ("release", "confirm_block"):
            raise ValueError(f"unknown action {action!r}")
        actor = actor.strip()
        state = "released" if action == "release" else "blocked"
        over_cap = (
            action == "confirm_block"
            and self._hub_active()
            and await self.hub_pending_count() >= self._hub_cfg.max_pending  # type: ignore[union-attr]
        )
        for _ in range(8):
            cur = await self._get(txn_id)
            if cur is None:
                raise HoldNotFound(txn_id)
            if cur.state != "open":
                if cur.state == state:
                    await self._drain_quietly(txn_id)
                    self._hub_kick(txn_id, cur)  # idempotent: no second submission is queued
                    return await self._get(txn_id) or cur  # idempotent: same action again
                raise HoldConflict(f"hold already {cur.state}")
            if (expect_decision is not None and cur.decision != expect_decision) or (
                expect_seq is not None and cur.decision_seq != expect_seq
            ):
                raise HoldConflict("hold changed since it was read (decision/seq mismatch)")
            resolved_at = self._clock()
            entry = make_entry(
                "hold.resolved", txn_id, cur.decision, cur.decision_seq, cur.score, actor,
                cur.model_version, cur.reasons, deadline=cur.deadline, rail=cur.rail,
                amount_bucket=cur.amount_bucket, payee_ref=cur.payee_ref, call_ref=cur.call_ref,
                extra={"action": action, "resolver_role": role or "unspecified",
                       "resolver_ref": actor, "resolved_ts": utc_ts(resolved_at)},
            )  # fmt: skip
            update: dict[str, Any] = {
                "state": state, "resolved_by": actor, "resolved_at": resolved_at,
                "audit_pending": [*cur.audit_pending, entry],
            }  # fmt: skip
            skipped: str | None = None
            if action == "confirm_block" and self._hub_active():
                skipped = await self._hub_plan(cur, actor, update, over_cap)
            new = cur.model_copy(update=update)
            if await self._cas(txn_id, cur, new):
                if skipped is not None:
                    self.hub_stats["skipped"] += 1
                    log.warning("antibody submission skipped (%s)", skipped)
                await self._drain_quietly(txn_id)
                self._hub_kick(txn_id, new)
                return new.model_copy(update={"audit_pending": []})
        raise HoldConflict("concurrent update; retry")

    # ---------------------------------------------------------------- confirm-to-hub
    def _hub_active(self) -> bool:
        return self._hub_cfg is not None and self._hub_client is not None

    def hub_info(self) -> dict[str, str]:
        """Informational (never a readiness failure): is submission on, did the last attempt reach
        the hub."""
        reach = {None: "unknown", True: "true", False: "false"}[self._hub_reachable]
        return {"hub_submit": "enabled" if self._hub_active() else "disabled",
                "hub_reachable": reach}  # fmt: skip

    @property
    def hub_breaker_open(self) -> bool:
        return self.hub_drainer.breaker_open

    async def _hub_plan(
        self, cur: Hold, actor: str, update: dict[str, Any], over_cap: bool
    ) -> str | None:
        """Extend the resolve CAS ``update``: the durable submission (returns None) or a recorded
        skip reason with its audit entry. Pure over the hold record."""
        cfg = self._hub_cfg
        assert cfg is not None
        reason = skip_reason(cur.payee_hash, cfg.bank_id) or ("pending_cap" if over_cap else None)
        if reason is None:
            sub = HubSubmission(
                key_hash=cur.payee_hash, source_bank=cfg.bank_id, actor=safe_actor(actor),
                evidence_ref=evidence_ref_for(cur.txn_id),
            )  # fmt: skip
            update["hub_pending"] = [*cur.hub_pending, sub][-MAX_HUB_PENDING_PER_HOLD:]
            return None
        extra: dict[str, Any] = {"result": "skipped", "reason": reason, "attempts": 0}
        update["hub_result"], update["hub_reason"] = "skipped", reason
        update["audit_pending"] = [
            *update["audit_pending"],
            self._hub_entry("hold.antibody_submit_failed", cur, SYSTEM_ACTOR, extra),
        ]  # fmt: skip
        return reason

    @staticmethod
    def _hub_entry(event_type: str, h: Hold, actor: str, extra: dict[str, Any]) -> AuditEntry:
        return make_entry(
            event_type, h.txn_id, h.decision, h.decision_seq, h.score, actor, h.model_version,
            h.reasons, deadline=h.deadline, rail=h.rail, amount_bucket=h.amount_bucket,
            payee_ref=h.payee_ref, call_ref=h.call_ref, extra=extra,
        )  # fmt: skip

    def _hub_due(self, txn_id: str) -> bool:
        return self._hub_next.get(txn_id, 0.0) <= self._mono()

    def _hub_kick(self, txn_id: str, hold: Hold) -> None:
        """Schedule the background delivery (synchronous, never awaits the hub)."""
        if not hold.hub_pending or not self._hub_active() or not self._hub_due(txn_id):
            return
        self.hub_drainer.schedule(txn_id, lambda: self.drain_hub(txn_id))

    async def hub_settle(self) -> None:
        """Wait for scheduled hub deliveries (tests, graceful shutdown)."""
        await self.hub_drainer.settle()

    async def drain_hub(self, txn_id: str) -> bool:
        """One delivery attempt for the hold's first pending submission. True when there is
        nothing left to retry now (delivered, terminal outcome, or no work); False asks the
        breaker/sweeper to try again later. At-least-once: the hub dedupes identical antibodies."""
        cfg, client = self._hub_cfg, self._hub_client
        if cfg is None or client is None:
            return True
        for _ in range(8):
            cur = await self._get(txn_id)
            if cur is None or not cur.hub_pending:
                self._hub_next.pop(txn_id, None)
                return True
            sub = cur.hub_pending[0]
            try:
                out: HubOutcome = await client.submit_antibody(sub, cfg.timeout_s)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                out = HubOutcome("retry", None, type(e).__name__)
            for _ in range(8):
                done = await self._hub_apply(cur, sub, out, cfg)
                if done is not None:
                    return done
                # Lost a CAS race (for example a concurrent audit flush on the same hold): record
                # the outcome already received on the fresh record instead of calling the hub
                # again, which would send a duplicate submission.
                cur = await self._get(txn_id)
                if cur is None or sub not in cur.hub_pending:
                    break  # someone else settled this submission; re-evaluate the hold
            else:
                return False
        return False

    async def _hub_apply(
        self, cur: Hold, sub: HubSubmission, out: HubOutcome, cfg: HubSubmitConfig
    ) -> bool | None:
        """Record one outcome in a CAS (None: lost the race, caller re-reads and retries)."""
        txn_id = cur.txn_id
        self._hub_reachable = out.status is not None and out.status < 500
        attempts = sub.attempts + 1
        rest = [s for s in cur.hub_pending if s is not sub and s != sub]
        base = {"attempts": attempts, "key_prefix": sub.key_hash[:8], "resolver_ref": sub.actor}
        result = out.result
        if result == "retry" and cfg.max_attempts is not None and attempts >= cfg.max_attempts:
            result = "exhausted"  # type: ignore[assignment]
        if result in ("ok", "duplicate", "protected"):
            event, extra = "hold.antibody_submitted", {**base, "result": result}
            update: dict[str, Any] = {"hub_pending": rest, "hub_result": result}
        elif result in ("rejected", "exhausted"):
            event = "hold.antibody_submit_failed"
            extra = {**base, "result": result}
            if out.status is not None:
                extra["http_status"] = out.status
            update = {"hub_pending": rest, "hub_result": result}
        else:  # retry / misconfig: keep it pending, count the attempt, back off in memory
            kept = sub.model_copy(update={"attempts": attempts, "last_error": out.error_class})
            new = cur.model_copy(
                update={"hub_pending": [kept if s == sub else s for s in cur.hub_pending]}
            )
            if not await self._cas(txn_id, cur, new):
                return None
            self.hub_last_error = out.error_class
            self._hub_next[txn_id] = self._mono() + cfg.backoff_s(attempts)
            if result == "misconfig":
                self.hub_stats["auth_failures"] += 1
                log.warning(
                    "antibody hub refused the submission (HTTP %s, auth/route misconfiguration): "
                    "check HUB_GATEWAY_SECRET, HUB_URL and that this bank is listed in the hub's "
                    "HUB_BANKS; the submission stays pending and is retried with backoff",
                    out.status,
                )  # fmt: skip
            else:
                self.hub_stats["retried"] += 1
            return False
        entry = self._hub_entry(event, cur, SYSTEM_ACTOR, extra)
        update["audit_pending"] = [*cur.audit_pending, entry]
        if not await self._cas(txn_id, cur, cur.model_copy(update=update)):
            return None
        self.hub_stats[result] += 1
        if result in ("rejected", "exhausted"):
            self.hub_last_error = out.error_class
            log.warning("antibody submission failed terminally (%s)", result)
        self._hub_next.pop(txn_id, None)
        await self._drain_quietly(txn_id)
        return True

    async def _hub_ok(self, txn_id: str) -> bool:
        return await self.drain_hub(txn_id)

    async def upgrade(
        self, txn_id: str, decision: Decision, reasons: list[Reason], score: float,
        model_version: str, decision_seq: int, *, trigger: str = "call_risk",
        call_ref: str | None = None,
    ) -> Hold | None:  # fmt: skip
        """Raise an open hold to a stronger decision; None if missing, resolved or not stronger."""
        for _ in range(8):
            cur = await self._get(txn_id)
            if cur is None or cur.state != "open":
                return None
            if RANK[decision] <= RANK[cur.decision] or decision_seq <= cur.decision_seq:
                await self._drain_quietly(txn_id)
                return None
            ref = call_ref or cur.call_ref
            entry = make_entry(
                "hold.upgraded", txn_id, decision, decision_seq, score, SYSTEM_ACTOR,
                model_version, reasons, deadline=cur.deadline, rail=cur.rail,
                amount_bucket=cur.amount_bucket, payee_ref=cur.payee_ref, call_ref=ref,
                extra={"from_decision": cur.decision, "to_decision": decision,
                       "trigger": trigger},
            )  # fmt: skip
            new = cur.model_copy(
                update={"decision": decision, "reasons": reasons, "score": score,
                        "model_version": model_version, "decision_seq": decision_seq,
                        "call_ref": ref, "audit_pending": [*cur.audit_pending, entry]}
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
            entry = make_entry(
                "hold.overdue", h.txn_id, h.decision, h.decision_seq, h.score, SYSTEM_ACTOR,
                h.model_version, h.reasons, deadline=h.deadline, rail=h.rail,
                amount_bucket=h.amount_bucket, payee_ref=h.payee_ref, call_ref=h.call_ref,
                extra={"overdue": True},
            )  # fmt: skip
            new = h.model_copy(
                update={"overdue_flagged": True, "audit_pending": [*h.audit_pending, entry]}
            )
            if await self._cas(h.txn_id, h, new, count_overdue=True):
                flagged += 1
        ids = sorted(await self._pending_ids())[: self.sweep_batch]
        await self.drainer.sweep(ids, self._drain_ok)
        if self._hub_active():
            batch = self._hub_cfg.sweep_batch  # type: ignore[union-attr]
            due = [t for t in sorted(await self._hub_ids()) if self._hub_due(t)][:batch]
            await self.hub_drainer.sweep(due, self._hub_ok)
        return flagged


class InMemoryHoldStore(_BaseHoldStore):
    def __init__(
        self, audit: AuditSink | None = None, clock: Callable[[], datetime] = _utcnow,
        *, hub_submit: HubSubmitConfig | None = None, hub_client: Any = None,
        mono: Callable[[], float] = time.monotonic,
    ) -> None:  # fmt: skip
        super().__init__(
            audit, clock, hub_submit=hub_submit, hub_client=hub_client, mono=mono
        )  # fmt: skip
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

    async def _hub_ids(self) -> list[str]:
        return [t for t, h in self._holds.items() if h.hub_pending]

    async def hub_pending_count(self) -> int:
        return sum(bool(h.hub_pending) for h in self._holds.values())

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
    HUB_SET = "txnguard:holds:hub"  # holds with a pending antibody submission

    def __init__(
        self, client: Any, audit: AuditSink | None = None,
        clock: Callable[[], datetime] = _utcnow, *, hub_submit: HubSubmitConfig | None = None,
        hub_client: Any = None, mono: Callable[[], float] = time.monotonic,
    ) -> None:  # fmt: skip
        super().__init__(
            audit, clock, hub_submit=hub_submit, hub_client=hub_client, mono=mono
        )  # fmt: skip
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
                    if new.hub_pending:
                        pipe.persist(key)  # an undelivered antibody submission must not expire
                    else:
                        pipe.expire(key, CLOSED_HOLD_TTL_S)
                if new.audit_pending:
                    pipe.sadd(self.AUDIT_SET, txn_id)
                else:
                    pipe.srem(self.AUDIT_SET, txn_id)
                if new.hub_pending:
                    pipe.sadd(self.HUB_SET, txn_id)
                else:
                    pipe.srem(self.HUB_SET, txn_id)
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

    async def _hub_ids(self) -> list[str]:
        return [
            i.decode() if isinstance(i, bytes) else i for i in await self._r.smembers(self.HUB_SET)
        ]

    async def hub_pending_count(self) -> int:
        return int(await self._r.scard(self.HUB_SET))

    async def count_open(self) -> int:
        return int(await self._r.zcard(self.INDEX))

    async def count_overdue(self) -> int:
        return int(await self._r.zcount(self.INDEX, "-inf", f"({self._clock().timestamp()}"))

    async def overdue_total(self) -> int:
        return int(await self._r.get(self.OVERDUE_KEY) or 0)
