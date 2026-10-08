"""Consume CallEvents, update per-call sessions, publish CallRisk on threshold crossing."""

import logging

from scam_contracts.models import CallEvent
from scam_contracts.topics import Topics
from svckit.bus import Bus, consume
from svckit.idempotency import IdempotencyStore

from .session import SessionScorer

log = logging.getLogger(__name__)

GROUP = "call-guard"


async def handle_event(
    event: CallEvent, scorer: SessionScorer, bus: Bus, store: IdempotencyStore
) -> None:
    """Publish the pending threshold crossing, if any.

    The decision comes from persisted session state (``crossings > published``) and the
    ``published`` marker is advanced only after ``bus.publish`` succeeds, so a failed publish
    is retried by ``consume`` and never lost, and replays after success publish nothing.
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
