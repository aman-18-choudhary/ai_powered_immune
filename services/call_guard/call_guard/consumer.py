"""Consume CallEvents, update per-call sessions, publish CallRisk on threshold crossing."""

import logging
from typing import Any

from scam_contracts.models import CallEvent, CallRisk
from scam_contracts.topics import Topics
from svckit.bus import Bus, consume
from svckit.idempotency import IdempotencyStore
from svckit.ledger import LedgerPayloadError, call_ref, emit_ledger, utc_ts

from .session import SessionScorer

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


async def handle_event(
    event: CallEvent, scorer: SessionScorer, bus: Bus, store: IdempotencyStore
) -> None:
    """Publish the pending threshold crossing, if any.

    The decision comes from persisted session state (``crossings > published``) and the
    ``published`` marker is advanced only after ``bus.publish`` succeeds, so a failed publish
    is retried by ``consume`` and never lost, and replays after success publish nothing.

    The audit entry (``callrisk.alert`` on the ledger topic) belongs to the same crossing: it is
    published FIRST (so the ledger sees the alert before anything it triggers), then the CallRisk,
    then the marker. A failure of either step releases the claim and the whole step is retried;
    the retry re-publishes byte-identical messages (the ledger absorbs the duplicate entry, the
    CallRisk consumers dedupe on payload hash), so nothing is lost and nothing is counted twice.
    A payload the PII guard refuses (a programming error) is logged and skipped: an audit-format
    bug must never suppress a safety alert.
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
    try:
        try:
            payload, ref = alert_payload(
                risk, n, await scorer.crossing_chunks(event.call_id), scorer.threshold
            )
            await emit_ledger(
                bus, LEDGER_SERVICE, LEDGER_ACTOR, ALERT_EVENT, payload,
                model_version=risk.model_version, case_refs=[ref],
            )  # fmt: skip
        except LedgerPayloadError:
            log.error("callrisk.alert ledger payload refused (alert still published)")
        await bus.publish(Topics.CALL_RISK, risk.victim_token, risk)  # keyed by payer token
    except BaseException:
        await store.release(key)
        raise
    await scorer.mark_published(event.call_id, n)
    await store.mark(key)
    log.info("published call risk call_id=%s score=%.3f", event.call_id, risk.score)


async def run_consumer(bus: Bus, scorer: SessionScorer, store: IdempotencyStore) -> None:
    async def handler(event: CallEvent) -> None:
        await handle_event(event, scorer, bus, store)

    await consume(bus, Topics.CALL_EVENTS, GROUP, CallEvent, handler, store)
