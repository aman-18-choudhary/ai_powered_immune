"""Task 13: one ``callrisk.alert`` ledger entry per CallRisk crossing, with the crossing's durability."""

import hashlib
import json
from datetime import timedelta

from scam_contracts.canonical import payload_hash
from scam_contracts.models import CallRisk, LedgerEntryIn
from scam_contracts.topics import Topics
from svckit.bus import InMemoryBus
from svckit.idempotency import InMemoryIdempotencyStore
from svckit.ledger import build_ledger_entry

from call_guard import consumer
from call_guard.rules import CALL_RISK_THRESHOLD

from .test_consumer import T0, TEXTS, FlakyBus, _drain


class OrderBus(InMemoryBus):
    """Records the global publish order and fails the first `fail_ledger` ledger publishes."""

    def __init__(self, fail_ledger: int = 0) -> None:
        super().__init__()
        self.order: list[str] = []
        self.fail_ledger = fail_ledger

    async def publish(self, topic, key, value):
        if topic == Topics.LEDGER and self.fail_ledger > 0:
            self.fail_ledger -= 1
            raise ConnectionError("ledger topic down")
        self.order.append(topic)
        await super().publish(topic, key, value)


def ledger(bus):
    return [LedgerEntryIn.model_validate_json(r) for _, r in bus.messages(Topics.LEDGER)]


def evs(make_event, n=3):
    return [make_event(t, ts=T0 + timedelta(seconds=30 * i)) for i, t in enumerate(TEXTS[:n])]


async def test_one_alert_entry_per_crossing_with_expected_shape(make_event):
    bus = OrderBus()
    await _drain(bus, evs(make_event) * 2)  # second pass = replay
    (e,) = ledger(bus)
    (risk_raw,) = [r for _, r in bus.messages(Topics.CALL_RISK)]
    risk = CallRisk.model_validate_json(risk_raw)
    ref = "call_ref:" + hashlib.sha256(b"c1").hexdigest()[:16]
    assert (e.service, e.actor, e.event_type) == (
        "call-guard",
        "system:call-guard",
        "callrisk.alert",
    )
    assert e.case_refs == [ref] and e.model_version == risk.model_version
    p = e.payload
    assert p["call_ref"] == ref.split(":")[1] and p["score"] == round(risk.score, 4)
    assert p["reason_codes"] == sorted({r.code for r in risk.reasons})
    assert p["threshold"] == CALL_RISK_THRESHOLD and p["chunk_count"] >= 1 and p["crossing"] == 1
    assert e.payload_hash == payload_hash(p)
    blob = json.dumps(e.model_dump())
    assert "c1" not in {v for v in p.values() if isinstance(v, str)} and "call_id" not in p
    assert "call_id" not in blob and f'"{risk.victim_token}"' not in blob
    for r in risk.reasons:
        assert r.detail not in blob  # free-text detail is not emitted
    build_ledger_entry(e.service, e.actor, e.event_type, p, model_version=e.model_version,
                       case_refs=e.case_refs)  # fmt: skip
    assert bus.order.index(Topics.CALL_RISK) < bus.order.index(Topics.LEDGER)  # alert first


async def test_benign_call_emits_nothing(make_event):
    bus = OrderBus()
    await _drain(bus, [make_event("Hi, your parcel arrives tomorrow.", ts=T0)])
    assert ledger(bus) == [] and bus.messages(Topics.CALL_RISK) == []


async def test_callrisk_publish_failure_retry_cannot_lose_or_double_the_entry(make_event):
    bus = FlakyBus(fail=1)  # first CALL_RISK publish raises
    await _drain(bus, evs(make_event, 2))
    assert len(bus.messages(Topics.CALL_RISK)) == 1
    assert len(ledger(bus)) == 1  # the entry is built and delivered once, after the alert


async def test_ledger_publish_failure_never_blocks_the_alert_and_is_retried_later(make_event):
    from call_guard.consumer import drain_ledger_pending
    from call_guard.session import InMemorySessionStore, SessionScorer

    bus, sc = OrderBus(fail_ledger=1), SessionScorer(InMemorySessionStore())
    await _drain_with(bus, sc, evs(make_event, 2))
    assert len(bus.messages(Topics.CALL_RISK)) == 1 and ledger(bus) == []
    assert bus.messages(Topics.CALL_RISK + Topics.DLQ_SUFFIX) == []
    assert await drain_ledger_pending(bus, sc) == 1 and len(ledger(bus)) == 1


async def _drain_with(bus, sc, events):
    import asyncio

    from call_guard.consumer import run_consumer

    task = asyncio.create_task(run_consumer(bus, sc, InMemoryIdempotencyStore()))
    for e in events:
        await bus.publish(Topics.CALL_EVENTS, e.call_id, e)
    await asyncio.sleep(0.3)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_replay_with_fresh_idempotency_store_adds_nothing(make_event):
    import asyncio

    from scam_contracts.topics import Topics as T

    from call_guard.consumer import run_consumer
    from call_guard.session import InMemorySessionStore, SessionScorer

    bus, sc = OrderBus(), SessionScorer(InMemorySessionStore())
    events = evs(make_event)
    for store in (InMemoryIdempotencyStore(), InMemoryIdempotencyStore()):
        task = asyncio.create_task(run_consumer(bus, sc, store))
        for e in events:
            await bus.publish(T.CALL_EVENTS, e.call_id, e)
        await asyncio.sleep(0.3)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert len(ledger(bus)) == 1 and len(bus.messages(T.CALL_RISK)) == 1


async def test_payload_refusal_never_suppresses_the_alert(make_event, monkeypatch):
    def boom(*a, **k):
        from svckit.ledger import LedgerPayloadError

        raise LedgerPayloadError("x")

    monkeypatch.setattr(consumer, "alert_payload", boom)
    bus = OrderBus()
    await _drain(bus, evs(make_event, 2))
    assert len(bus.messages(Topics.CALL_RISK)) == 1 and ledger(bus) == []
