"""Consume CallEvents, update per-call sessions, publish CallRisk on threshold crossing."""

import asyncio
import logging
import os
from collections import Counter, deque
from typing import Any

from scam_contracts.models import CallEvent, CallRisk, LedgerEntryIn
from scam_contracts.topics import Topics
from svckit.bus import Bus, consume
from svckit.drain import Drainer
from svckit.idempotency import IdempotencyStore
from svckit.ledger import build_ledger_entry, build_or_placeholder, call_ref, utc_ts

from .session import LEDGER_PENDING_PREFIX, SessionScorer

log = logging.getLogger(__name__)

GROUP = "call-guard"
LEDGER_SERVICE = "call-guard"
LEDGER_ACTOR = "system:call-guard"
ALERT_EVENT = "callrisk.alert"


def alert_payload(
    risk: CallRisk, crossing: int, chunks: int, threshold: float
) -> tuple[dict[str, Any], str]:
    """Deterministic, PII-free ledger payload for one alert crossing, built only from the stored
    peak snapshot (never a wall clock). Returns (payload, ``call_ref:<16 hex>``)."""
    ref = call_ref(risk.call_id)
    payload = {
        "call_ref": ref.split(":", 1)[1], "crossing": crossing, "score": round(risk.score, 4),
        "reason_codes": sorted({r.code for r in risk.reasons}),
        "model_version": risk.model_version, "chunk_count": chunks, "threshold": threshold,
        "crossed_ts": utc_ts(risk.ts),
    }  # fmt: skip
    return payload, ref


LEDGER_DRAIN_INTERVAL_S = float(os.getenv("LEDGER_DRAIN_INTERVAL_S", "5"))
LEDGER_BATCH = 50
FALLBACK_MAX = int(os.getenv("LEDGER_FALLBACK_MAX", "10000"))
# exposed on /metrics: fallback_total (entries parked in memory after a store double fault),
# placeholder_total (entries replaced by the fixed-shape placeholder), lost_total (fallback
# overflow: the oldest entry was dropped)
AUDIT: Counter[str] = Counter()


class LedgerOutbox:
    """Best-effort, durable delivery of pending ``callrisk.alert`` entries. Entries live in the
    session store (written atomically with the crossing marker). ``kick`` is synchronous: it
    only schedules a background delivery (`svckit.drain.Drainer`: per-entry in-flight guard,
    concurrency cap, per-delivery timeout, circuit breaker), so the consumer never waits for the
    ledger. The sweeper (`drain`) retries what is left, in bounded batches. Delivery is
    at-least-once; the ledger's full-entry idempotency absorbs repeats."""

    def __init__(self, bus: Bus, scorer: SessionScorer, fallback_max: int = FALLBACK_MAX) -> None:
        self.bus, self.scorer = bus, scorer
        self.drainer = Drainer()
        # in-process buffer for entries that could not be stored (session store AND idempotency
        # store failing together); lost only if the process dies before the sweeper drains it
        self.fallback: deque[tuple[str, str]] = deque()
        self._fallback_max = fallback_max

    def add_fallback(self, key: str, raw: str) -> None:
        if len(self.fallback) >= self._fallback_max:
            self.fallback.popleft()
            AUDIT["lost_total"] += 1
            log.error("audit fallback buffer full: oldest callrisk.alert entry dropped")
        self.fallback.append((key, raw))
        AUDIT["fallback_total"] += 1

    async def _publish(self, raw: str) -> None:
        entry = LedgerEntryIn.model_validate_json(raw)
        await self.bus.publish(
            Topics.LEDGER, entry.case_refs[0] if entry.case_refs else LEDGER_SERVICE, entry
        )

    async def _one(self, key: str, raw: str) -> bool:
        await self._publish(raw)
        await self.scorer.clear_ledger(key)
        return True

    async def _drain_fallback(self, limit: int) -> int:
        items = [self.fallback.popleft() for _ in range(min(limit, len(self.fallback)))]
        sent: set[str] = set()
        by_key = dict(items)

        async def one(k: str) -> bool:
            await self._publish(by_key[k])
            sent.add(k)
            return True

        n = await self.drainer.sweep(list(by_key), one)
        for k, raw in items:  # undelivered ones go back (not counted again)
            if k not in sent:
                self.fallback.append((k, raw))
        return n

    def kick(self, key: str, raw: str) -> None:
        self.drainer.schedule(key, lambda: self._one(key, raw))

    async def drain(self, limit: int = LEDGER_BATCH) -> int:
        n = await self._drain_fallback(limit) if self.fallback else 0
        rows = dict(await self.scorer.pending_ledger(limit))
        return n + await self.drainer.sweep(list(rows), lambda k: self._one(k, rows[k]))

    async def aclose(self) -> None:
        await self.drainer.aclose()  # pending entries stay in the store


def _outbox(bus: Bus, scorer: SessionScorer) -> LedgerOutbox:
    ob = getattr(scorer, "ledger_outbox", None)
    if ob is None or ob.bus is not bus:
        ob = scorer.ledger_outbox = LedgerOutbox(bus, scorer)  # type: ignore[attr-defined]
    return ob


async def drain_ledger_pending(bus: Bus, scorer: SessionScorer, limit: int = LEDGER_BATCH) -> int:
    """Publish pending audit entries (the service lifespan calls this every
    LEDGER_DRAIN_INTERVAL_S). Returns how many were delivered."""
    return await _outbox(bus, scorer).drain(limit)


async def run_ledger_sweeper(
    bus: Bus, scorer: SessionScorer, interval_s: float = LEDGER_DRAIN_INTERVAL_S
) -> None:
    while True:
        try:
            await drain_ledger_pending(bus, scorer)
        except Exception as e:
            log.warning("ledger sweep failed (%s)", type(e).__name__)
        await asyncio.sleep(interval_s)


async def handle_event(
    event: CallEvent, scorer: SessionScorer, bus: Bus, store: IdempotencyStore
) -> None:
    """Publish the pending threshold crossing, if any.

    The decision comes from persisted session state (``crossings > published``) and the
    ``published`` marker is advanced only after ``bus.publish`` succeeds, so a failed publish
    is retried by ``consume`` and never lost, and replays after success publish nothing.

    The alert comes FIRST and never waits for the ledger: the consumer path contains no await on
    it. The audit entry (``callrisk.alert``) is built before the publish and stored in the session
    store in the SAME atomic update that advances the marker; delivery is scheduled in the
    background (cap, in-flight guard, timeout, breaker) and retried by the sweeper. Guarantees:
    alerts are at-least-once (a duplicate is byte-identical and consumers dedupe on it); audit
    entries are at-least-once and durable in the session store; one can be lost only by losing
    that store, or by a simultaneous failure of the session store AND the idempotency store
    followed by process death before the in-memory fallback buffer drains (logged at ERROR and
    counted in AUDIT). If building the entry fails a placeholder entry is stored (also counted). If storing the marker/entry fails after the alert went out, the
    claim is released and the retry re-publishes the alert (duplicate) and stores the entry. A
    payload the PII guard refuses becomes a fixed-shape placeholder entry.
    """
    _, pending = await scorer.update_with_crossing(event)
    if not pending:
        return
    risk = await scorer.crossing_risk(event)  # peak snapshot: still published after decay
    if risk is None:
        return
    n = await scorer.crossing_no(event.call_id)
    key = f"callrisk:{event.call_id}:x{n}"  # cross-instance guard on (call, crossing)
    if await store.seen(key) or not await store.claim(key):
        return
    pending_entry = None
    ref = call_ref(risk.call_id)
    try:
        payload, ref = alert_payload(
            risk, n, await scorer.crossing_chunks(event.call_id), scorer.threshold
        )
        entry, refused = build_or_placeholder(
            LEDGER_SERVICE, LEDGER_ACTOR, ALERT_EVENT, payload, model_version=risk.model_version,
            case_refs=[ref],
            placeholder={"call_ref": ref.split(":", 1)[1], "crossing": n}, placeholder_refs=[ref],
        )  # fmt: skip
        if refused:
            AUDIT["placeholder_total"] += 1
            log.error("callrisk.alert ledger payload refused; placeholder entry stored")
    except Exception as e:  # an audit-format bug must not touch the alert: store a placeholder
        AUDIT["placeholder_total"] += 1
        log.error("callrisk.alert entry not built (%s); placeholder entry stored", type(e).__name__)
        entry = build_ledger_entry(
            LEDGER_SERVICE, LEDGER_ACTOR, ALERT_EVENT,
            {"call_ref": ref.split(":", 1)[1], "crossing": n, "audit": "payload_refused"},
            case_refs=[ref],
        )  # fmt: skip
    pending_entry = (f"{LEDGER_PENDING_PREFIX}{ref}:x{n}", entry.model_dump_json())
    try:
        await bus.publish(Topics.CALL_RISK, risk.victim_token, risk)  # keyed by payer token
    except BaseException:
        await store.release(key)
        raise
    try:
        # marker + audit entry, atomic
        await scorer.mark_published(event.call_id, n, pending_entry)
    except BaseException:
        # the alert is out but its marker/audit entry were not stored: release the claim so the
        # retry re-publishes the (byte-identical) alert and stores the entry. At-least-once.
        try:
            await store.release(key)
        except Exception as e:
            # double fault: the retry would no-op on the held claim. Park the entry in memory so
            # the sweeper still delivers it, and leave an ERROR trace.
            _outbox(bus, scorer).add_fallback(*pending_entry)
            log.error(
                "callrisk.alert entry parked in memory after store AND claim-release failure "
                "call_ref=%s crossing=%d (%s)", ref, n, type(e).__name__,
            )  # fmt: skip
        raise
    await store.mark(key)
    log.info("published call risk call_id=%s score=%.3f", event.call_id, risk.score)
    if pending_entry is not None:
        _outbox(bus, scorer).kick(*pending_entry)  # schedules only: no await on the ledger


async def run_consumer(bus: Bus, scorer: SessionScorer, store: IdempotencyStore) -> None:
    async def handler(event: CallEvent) -> None:
        await handle_event(event, scorer, bus, store)

    await consume(bus, Topics.CALL_EVENTS, GROUP, CallEvent, handler, store)
