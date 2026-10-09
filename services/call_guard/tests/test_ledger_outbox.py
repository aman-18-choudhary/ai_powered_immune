"""Fix round 1: audit trouble never delays, loses, duplicates or blocks the CallRisk alert."""

import asyncio
import time
from datetime import timedelta

import fakeredis.aioredis
import pytest
from scam_contracts.models import CallRisk, LedgerEntryIn
from scam_contracts.topics import Topics
from svckit.bus import InMemoryBus
from svckit.idempotency import InMemoryIdempotencyStore

from call_guard.consumer import drain_ledger_pending, run_consumer
from call_guard.session import InMemorySessionStore, RedisSessionStore, SessionScorer

from .test_consumer import T0, TEXTS
from .test_ledger_alert import evs, ledger


class LedgerBus(InMemoryBus):
    """Ledger publishes raise while `down`, or block on `gate` (set = pass)."""

    def __init__(self):
        super().__init__()
        self.down = False
        self.gate = asyncio.Event()
        self.gate.set()
        self.risk_at: float | None = None

    async def publish(self, topic, key, value):
        if topic == Topics.LEDGER:
            if self.down:
                raise ConnectionError("ledger down")
            await self.gate.wait()
        await super().publish(topic, key, value)
        if topic == Topics.CALL_RISK and self.risk_at is None:
            self.risk_at = time.perf_counter()


@pytest.fixture(params=["memory", "redis"])
def sessions(request):
    if request.param == "memory":
        return SessionScorer(InMemorySessionStore())
    return SessionScorer(RedisSessionStore(fakeredis.aioredis.FakeRedis()))


async def run(bus, sc, events, wait=0.3, store=None):
    task = asyncio.create_task(run_consumer(bus, sc, store or InMemoryIdempotencyStore()))
    t0 = time.perf_counter()
    for e in events:
        await bus.publish(Topics.CALL_EVENTS, e.call_id, e)
    await asyncio.sleep(wait)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return t0


async def test_persistent_ledger_outage_alert_exactly_once_entry_retained_then_drained(
    make_event, sessions
):
    bus = LedgerBus()
    bus.down = True
    t0 = await run(bus, sessions, evs(make_event))
    assert len(bus.messages(Topics.CALL_RISK)) == 1
    assert bus.messages(Topics.CALL_RISK + Topics.DLQ_SUFFIX) == []
    assert bus.messages(Topics.CALL_EVENTS + Topics.DLQ_SUFFIX) == []
    assert bus.risk_at - t0 < 0.25
    assert ledger(bus) == [] and len(await sessions.pending_ledger(10)) == 1
    assert await drain_ledger_pending(bus, sessions) == 0  # still down: retained
    bus.down = False
    assert await drain_ledger_pending(bus, sessions) == 1
    assert await drain_ledger_pending(bus, sessions) == 0
    (e,) = ledger(bus)
    assert e.event_type == "callrisk.alert" and await sessions.pending_ledger(10) == []


async def test_slow_ledger_does_not_delay_the_alert_or_stall_the_consumer(make_event, sessions):
    bus = LedgerBus()
    bus.gate.clear()
    t0 = await run(bus, sessions, evs(make_event), wait=0.9)
    assert len(bus.messages(Topics.CALL_RISK)) == 1 and bus.risk_at - t0 < 0.1
    assert ledger(bus) == [] and len(await sessions.pending_ledger(10)) == 1
    bus.gate.set()
    await asyncio.sleep(0.05)
    await drain_ledger_pending(bus, sessions)
    assert len(ledger(bus)) == 1 and await sessions.pending_ledger(10) == []


async def test_replay_three_times_one_alert_one_entry(make_event, sessions):
    bus = LedgerBus()
    events = evs(make_event)
    for _ in range(3):
        await run(bus, sessions, events, store=InMemoryIdempotencyStore())
    await drain_ledger_pending(bus, sessions)
    assert len(bus.messages(Topics.CALL_RISK)) == 1
    assert len({r for _, r in bus.messages(Topics.LEDGER)}) == 1
    assert len(ledger(bus)) == 1


async def test_pending_entry_is_written_with_the_crossing_marker_atomically(make_event, sessions):
    bus = LedgerBus()
    bus.down = True
    await run(bus, sessions, evs(make_event))
    state = await sessions._store.get("c1")
    assert state.published == state.crossings == 1  # marker advanced...
    ((key, raw),) = await sessions.pending_ledger(10)  # ...and the entry is pending with it
    assert key.startswith("ledger_pending:call_ref:") or key.startswith("call_ref:")
    assert LedgerEntryIn.model_validate_json(raw).event_type == "callrisk.alert"


async def test_unbuildable_payload_becomes_a_placeholder_entry(make_event, monkeypatch):
    from call_guard import consumer

    monkeypatch.setattr(
        consumer, "alert_payload", lambda *a, **k: ({"phone": "x"}, "call_ref:0123456789abcdef")
    )  # noqa: E501
    bus = LedgerBus()
    sc = SessionScorer(InMemorySessionStore())
    await run(bus, sc, evs(make_event))
    await drain_ledger_pending(bus, sc)
    (e,) = ledger(bus)
    assert e.payload["audit"] == "payload_refused" and e.payload["call_ref"]
    assert len(bus.messages(Topics.CALL_RISK)) == 1
    assert CallRisk.model_validate_json(bus.messages(Topics.CALL_RISK)[0][1]).score >= 0.7


# ---------------------------------------------------------------- fix round 2
def burst(make_event, n, start=0):
    out = []
    for c in range(start, start + n):
        for i, t in enumerate(TEXTS):
            out.append(make_event(t, call_id=f"burst-{c}", ts=T0 + timedelta(seconds=30 * i)))
    return out


async def feed(bus, sc, events, wait):
    task = asyncio.create_task(run_consumer(bus, sc, InMemoryIdempotencyStore()))
    t0 = time.perf_counter()
    for e in events:
        await bus.publish(Topics.CALL_EVENTS, e.call_id, e)
    await asyncio.sleep(wait)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return t0


class StampBus(LedgerBus):
    def __init__(self):
        super().__init__()
        self.risk_times: list[float] = []

    async def publish(self, topic, key, value):
        await super().publish(topic, key, value)
        if topic == Topics.CALL_RISK:
            self.risk_times.append(time.perf_counter())


async def test_hung_ledger_20_simultaneous_crossings_all_alerts_prompt(make_event):
    bus = StampBus()
    bus.gate.clear()  # every ledger publish hangs
    sc = SessionScorer(InMemorySessionStore())
    t0 = await feed(bus, sc, burst(make_event, 20), wait=1.5)
    assert len(bus.messages(Topics.CALL_RISK)) == 20
    assert max(bus.risk_times) - t0 < 0.5  # no per-crossing 0.5 s ledger wait on the consumer
    assert bus.messages(Topics.CALL_EVENTS + Topics.DLQ_SUFFIX) == []
    assert len(await sc.pending_ledger(100)) == 20
    bus.gate.set()
    await sc.ledger_outbox.drainer.aclose()


async def test_ledger_down_200_crossings_bounded_tasks_then_recovery_drains_200_once(make_event):
    bus = StampBus()
    bus.down = True
    sc = SessionScorer(InMemorySessionStore())
    task = asyncio.create_task(run_consumer(bus, sc, InMemoryIdempotencyStore()))
    peak = 0
    for e in burst(make_event, 200):
        await bus.publish(Topics.CALL_EVENTS, e.call_id, e)
        ob = getattr(sc, "ledger_outbox", None)
        peak = max(peak, ob.drainer.live_tasks if ob else 0)
    await asyncio.sleep(1.0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert len(bus.messages(Topics.CALL_RISK)) == 200 and peak <= 8
    assert len(await sc.pending_ledger(1000)) == 200
    bus.down = False
    await sc.ledger_outbox.drainer.aclose()
    n = 0
    for _ in range(10):  # sweeps in batches of 50
        n += await drain_ledger_pending(bus, sc)
    assert n == 200 and len(ledger(bus)) == 200 and await sc.pending_ledger(10) == []
    assert len({r for _, r in bus.messages(Topics.LEDGER)}) == 200


async def test_mark_published_failure_is_retried_alert_at_least_once_entry_once(make_event):
    class Blip(InMemorySessionStore):
        fail = 1

        async def put_with_pending(self, *a):
            if self.fail:
                self.fail -= 1
                raise ConnectionError("redis blip")
            await super().put_with_pending(*a)

    bus = LedgerBus()
    sc = SessionScorer(Blip())
    await feed(bus, sc, evs(make_event), wait=0.5)
    risks = {r for _, r in bus.messages(Topics.CALL_RISK)}
    assert len(risks) == 1 and len(bus.messages(Topics.CALL_RISK)) == 2  # duplicate, same bytes
    await drain_ledger_pending(bus, sc)
    assert len({r for _, r in bus.messages(Topics.LEDGER)}) == 1 and len(ledger(bus)) == 1
    state = await sc._store.get("c1")
    assert state.published == state.crossings == 1
    assert bus.messages(Topics.CALL_EVENTS + Topics.DLQ_SUFFIX) == []
