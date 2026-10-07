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
    "This is Inspector Rajesh from the CBI, a case is registered against you under PMLA.",
    "You are under digital arrest. Do not disconnect this video call.",
    "Transfer your funds to the RBI safe account for verification right now.",
]


class FlakyBus(InMemoryBus):
    """First `fail` publishes to CALL_RISK raise."""

    def __init__(self, fail: int = 1) -> None:
        super().__init__()
        self.fail = fail
        self.attempts = 0

    async def publish(self, topic, key, value):
        if topic == Topics.CALL_RISK:
            self.attempts += 1
            if self.attempts <= self.fail:
                raise ConnectionError("broker down")
        await super().publish(topic, key, value)


async def _drain(bus, events, store=None):
    sc = SessionScorer(InMemorySessionStore())
    store = store or InMemoryIdempotencyStore()
    task = asyncio.create_task(run_consumer(bus, sc, store))
    for e in events:
        await bus.publish(Topics.CALL_EVENTS, e.call_id, e)
    await asyncio.sleep(0.3)
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


async def test_publish_failure_is_retried_not_lost(make_event):
    bus = FlakyBus(fail=1)
    events = [make_event(t, ts=T0 + timedelta(seconds=30 * i)) for i, t in enumerate(TEXTS[:2])]
    await _drain(bus, events)
    assert bus.attempts == 2  # failed once, retried by consume
    assert len(bus.messages(Topics.CALL_RISK)) == 1
    assert bus.messages(Topics.CALL_RISK + Topics.DLQ_SUFFIX) == []


async def test_replays_after_success_publish_nothing(make_event):
    bus = FlakyBus(fail=1)
    sc = SessionScorer(InMemorySessionStore())
    store = InMemoryIdempotencyStore()
    task = asyncio.create_task(run_consumer(bus, sc, store))
    events = [make_event(t, ts=T0 + timedelta(seconds=30 * i)) for i, t in enumerate(TEXTS)]
    for e in events:
        await bus.publish(Topics.CALL_EVENTS, e.call_id, e)
    await asyncio.sleep(0.3)
    # replay with brand-new idempotency keys and a fresh idempotency store (e.g. after a flush)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    task = asyncio.create_task(run_consumer(bus, sc, InMemoryIdempotencyStore()))
    for i, t in enumerate(TEXTS):
        await bus.publish(
            Topics.CALL_EVENTS, "c1", make_event(t, ts=T0 + timedelta(seconds=100 + i))
        )
    await asyncio.sleep(0.3)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert len(bus.messages(Topics.CALL_RISK)) == 1


async def test_benign_call_publishes_nothing(make_event):
    bus = InMemoryBus()
    events = [make_event(t) for t in ["Your order is downstairs.", "Which block is it?"]]
    await _drain(bus, events)
    assert bus.messages(Topics.CALL_RISK) == []


async def test_new_events_while_above_threshold_do_not_republish(make_event):
    bus = InMemoryBus()
    events = [make_event(t, ts=T0 + timedelta(seconds=i)) for i, t in enumerate(TEXTS)]
    events.append(make_event(TEXTS[2], ts=T0 + timedelta(seconds=5)))
    await _drain(bus, events)
    assert len(bus.messages(Topics.CALL_RISK)) == 1


class DownBus(InMemoryBus):
    """CALL_RISK publishes fail while ``down`` is set."""

    down = True

    async def publish(self, topic, key, value):
        if topic == Topics.CALL_RISK and self.down:
            raise ConnectionError("broker down")
        await super().publish(topic, key, value)


async def test_pending_crossing_published_after_decay_with_peak_score(make_event):
    bus = DownBus()
    sc = SessionScorer(InMemorySessionStore())
    store = InMemoryIdempotencyStore()
    task = asyncio.create_task(run_consumer(bus, sc, store))
    first = [make_event(t, ts=T0 + timedelta(seconds=30 * i)) for i, t in enumerate(TEXTS)]
    for e in first:
        await bus.publish(Topics.CALL_EVENTS, e.call_id, e)
    await asyncio.sleep(0.3)
    peak = (await sc._store.get("c1")).peak_score
    assert bus.messages(Topics.CALL_RISK) == [] and peak >= 0.7  # outage: events went to the DLQ
    bus.down = False
    late = make_event(
        "Thanks, see you soon, bye.", ts=T0 + timedelta(seconds=900)
    )  # score long decayed
    await bus.publish(Topics.CALL_EVENTS, "c1", late)
    await asyncio.sleep(0.3)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    msgs = bus.messages(Topics.CALL_RISK)
    assert len(msgs) == 1
    risk = CallRisk.model_validate_json(msgs[0][1])
    assert risk.score == peak and risk.reasons and risk.score >= 0.7
