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
    risk, crossed = await scorer.update_with_crossing(event)
    if not crossed or risk.score < scorer.threshold:
        return
    # one publish per (call, threshold crossing); survives replays and consumer restarts
    state_key = f"callrisk:{event.call_id}:x{await scorer.crossing_no(event.call_id)}"
    if await store.seen(state_key) or not await store.claim(state_key):
        return
    await bus.publish(Topics.CALL_RISK, event.call_id, risk)
    await store.mark(state_key)
    log.info("published call risk call_id=%s score=%.3f", event.call_id, risk.score)


async def run_consumer(bus: Bus, scorer: SessionScorer, store: IdempotencyStore) -> None:
    async def handler(event: CallEvent) -> None:
        await handle_event(event, scorer, bus, store)

    await consume(bus, Topics.CALL_EVENTS, GROUP, CallEvent, handler, store)
