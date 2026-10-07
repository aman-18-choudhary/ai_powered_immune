import asyncio
from datetime import UTC, datetime, timedelta

from scam_contracts.models import CallRisk
from scam_contracts.topics import Topics
from svckit.bus import InMemoryBus
from svckit.idempotency import InMemoryIdempotencyStore

from call_guard.consumer import run_consumer
from call_guard.session import InMemorySessionStore, SessionScorer

T0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
TEXTS = [
    "This is Inspector Rajesh from the CBI. A serious case is registered against you.",
    "You are under digital arrest. Do not disconnect this video call.",
    "Transfer your funds to the RBI safe account for verification right now.",
]


async def _drain(bus, events, store=None):
    sc = SessionScorer(InMemorySessionStore())
    store = store or InMemoryIdempotencyStore()
    task = asyncio.create_task(run_consumer(bus, sc, store))
    for e in events:
        await bus.publish(Topics.CALL_EVENTS, e.call_id, e)
    await asyncio.sleep(0.2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_publishes_once_when_threshold_crossed(make_event):
    bus = InMemoryBus()
    events = [make_event(t, ts=T0 + timedelta(seconds=30 * i)) for i, t in enumerate(TEXTS)]
    await _drain(bus, events + events)  # second pass = replay
    msgs = bus.messages(Topics.CALL_RISK)
    assert len(msgs) == 1
    risk = CallRisk.model_validate_json(msgs[0][1])
    assert risk.score >= 0.7 and risk.call_id == "c1" and risk.reasons
    assert msgs[0][0] == "c1"


async def test_benign_call_publishes_nothing(make_event):
    bus = InMemoryBus()
    events = [make_event(t) for t in ["Your order is downstairs.", "Which block is it?"]]
    await _drain(bus, events)
    assert bus.messages(Topics.CALL_RISK) == []


async def test_replay_with_fresh_event_keys_does_not_republish(make_event):
    bus = InMemoryBus()
    sc = SessionScorer(InMemorySessionStore())
    store = InMemoryIdempotencyStore()
    task = asyncio.create_task(run_consumer(bus, sc, store))
    for i, t in enumerate(TEXTS):
        await bus.publish(Topics.CALL_EVENTS, "c1", make_event(t, ts=T0 + timedelta(seconds=i)))
    await asyncio.sleep(0.2)
    # same call, new event keys, still above threshold: no second publish
    await bus.publish(Topics.CALL_EVENTS, "c1", make_event(TEXTS[2], ts=T0 + timedelta(seconds=5)))
    await asyncio.sleep(0.2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert len(bus.messages(Topics.CALL_RISK)) == 1
