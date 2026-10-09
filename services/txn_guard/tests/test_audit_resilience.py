"""Fix round 1: audit trouble must never delay, lose or fail a fraud-prevention action."""

import asyncio
import json
import time
from datetime import timedelta

from scam_contracts.models import LedgerEntryIn
from scam_contracts.topics import Topics
from svckit.bus import InMemoryBus
from svckit.ledger import build_ledger_entry, txn_ref

from txn_guard.holds import AuditEntry, BusAuditSink, InMemoryHoldStore

from .test_audit_payloads import PAYEE, entries
from .test_hardening import Clock, R, decs, risk, scorer, svc_for  # noqa: F401

RRN = "402312345678"  # a 12-digit UPI-RRN style transaction id


class GateBus(InMemoryBus):
    """Ledger publishes wait on `gate` (or raise while `down`); everything else is immediate."""

    def __init__(self):
        super().__init__()
        self.gate = asyncio.Event()
        self.gate.set()
        self.down = False
        self.ledger_calls = 0

    async def publish(self, topic, key, value):
        if topic == Topics.LEDGER:
            self.ledger_calls += 1
            if self.down:
                raise ConnectionError("ledger topic down")
            await self.gate.wait()
        await super().publish(topic, key, value)


async def test_numeric_txn_id_reaches_the_ledger_as_txn_ref():
    bus = InMemoryBus()
    store = InMemoryHoldStore(audit=BusAuditSink(bus), clock=Clock())
    await store.create(RRN, "hold_verify", R, Clock()() + timedelta(seconds=60), payer_token="p",
                       score=0.8, model_version="gbm-v1", payee_ref="payee_ref:" + PAYEE[:16])  # fmt: skip
    await store.resolve(RRN, "confirm_block", "analyst-7", role="analyst")
    es = entries(bus)
    ref = txn_ref(RRN)
    assert [e.event_type for e in es] == ["hold.created", "hold.resolved"]
    for e in es:
        assert e.payload["txn_id"] == ref and e.case_refs[0] == ref and "audit" not in e.payload
        build_ledger_entry("txn-guard", e.actor, e.event_type, e.payload, case_refs=e.case_refs)
    assert RRN not in json.dumps([e.model_dump() for e in es])
    assert (await store.get(RRN)).txn_id == RRN  # the hold itself keeps the real id


async def test_unbuildable_payload_becomes_a_placeholder_and_the_outbox_drains():
    bus = InMemoryBus()
    store = InMemoryHoldStore(audit=BusAuditSink(bus), clock=Clock())
    await store.create("t1", "hold_verify", R, Clock()() + timedelta(seconds=60), payer_token="p",
                       score=0.8, model_version="gbm-v1")  # fmt: skip
    await store.resolve("t1", "confirm_block", "919876543210", role="analyst")  # phone-like sub
    await store.drain_audit("t1")
    es = entries(bus)
    assert [e.event_type for e in es] == ["hold.created", "hold.resolved"]
    ph = es[1]
    assert ph.payload["audit"] == "payload_refused" and ph.actor.startswith("redacted_")
    assert ph.case_refs == ["t1"] and "919876543210" not in json.dumps(ph.model_dump())
    assert not (await store.get("t1")).audit_pending
    # later entries on the same hold are not blocked
    sink = BusAuditSink(bus)
    bad = AuditEntry(event_type="hold.upgraded", txn_id="t1", decision="hold_verify",
                     decision_seq=2, score=0.9, actor="system:txn-guard", model_version="m",
                     payload_hash="0" * 64, payload={"phone": "x"}, case_refs=["t1"])  # fmt: skip
    await sink.emit(bad)
    assert entries(bus)[-1].payload == {"txn_id": "t1", "event": "hold.upgraded",
                                         "decision_seq": 2, "audit": "payload_refused"}  # fmt: skip


async def test_decision_is_not_delayed_by_a_slow_ledger(scorer, make_txn):  # noqa: F811
    bus = GateBus()
    bus.gate.clear()
    holds = InMemoryHoldStore(audit=BusAuditSink(bus), clock=Clock())
    svc, dbus, *_ = svc_for(scorer, make_txn, holds=holds)
    t = make_txn(amount="95000", age=3, payee=PAYEE)
    t0 = time.perf_counter()
    d = await svc.handle_txn(t)
    took = time.perf_counter() - t0
    assert d.decision != "allow" and took < 0.1, took
    assert len(decs(dbus)) == 1
    bus.gate.set()  # ledger recovers: the background drain finishes, exactly one entry
    await asyncio.sleep(0.1)
    await holds.sweep()
    assert len(entries(bus)) == 1 and not (await holds.get(t.txn_id)).audit_pending


async def test_ledger_down_decision_published_entry_pending_then_drains_once(scorer, make_txn):  # noqa: F811
    bus = GateBus()
    bus.down = True
    holds = InMemoryHoldStore(audit=BusAuditSink(bus), clock=Clock())
    svc, dbus, *_ = svc_for(scorer, make_txn, holds=holds)
    t = make_txn(amount="95000", age=3, payee=PAYEE)
    t0 = time.perf_counter()
    d = await svc.handle_txn(t)
    assert d.decision != "allow" and time.perf_counter() - t0 < 0.1
    assert len(decs(dbus)) == 1 and len((await holds.get(t.txn_id)).audit_pending) == 1
    assert entries(bus) == []
    await holds.sweep()  # still down: stays pending, no crash
    assert len((await holds.get(t.txn_id)).audit_pending) == 1
    bus.down = False
    await holds.sweep()
    await holds.sweep()
    assert len(entries(bus)) == 1 and not (await holds.get(t.txn_id)).audit_pending


async def test_case_selection_by_txn_ref_matches_the_entries():
    bus = InMemoryBus()
    store = InMemoryHoldStore(audit=BusAuditSink(bus), clock=Clock())
    await store.create(RRN, "step_up", R, Clock()() + timedelta(seconds=60), payer_token="p")
    (e,) = entries(bus)
    assert txn_ref(RRN) in e.case_refs and RRN not in e.case_refs
    assert isinstance(e, LedgerEntryIn)


# ---------------------------------------------------------------- fix round 2
from svckit.drain import Drainer  # noqa: E402

from txn_guard.holds import InMemoryAuditSink  # noqa: E402


def fast_drainer(**kw):
    return Drainer(cap=8, timeout_s=kw.pop("timeout_s", 0.05), cooldown_s=60, **kw)


async def make_holds(n, store):
    lat = []
    for i in range(n):
        t0 = time.perf_counter()
        await store.create(f"h{i}", "hold_verify", R, Clock()() + timedelta(seconds=60),
                           payer_token="p", score=0.8, model_version="gbm-v1")  # fmt: skip
        lat.append(time.perf_counter() - t0)
    return lat


async def test_200_holds_with_a_hung_ledger_are_not_throttled():
    base = InMemoryHoldStore(audit=InMemoryAuditSink(), clock=Clock())
    t0 = time.perf_counter()
    await make_holds(200, base)
    base_s = time.perf_counter() - t0
    bus = GateBus()
    bus.gate.clear()
    store = InMemoryHoldStore(audit=BusAuditSink(bus), clock=Clock())
    store.drainer = fast_drainer(timeout_s=5)
    t0 = time.perf_counter()
    lat = await make_holds(200, store)
    total = time.perf_counter() - t0
    assert total < base_s + 0.2 and max(lat) < 0.1, (total, base_s, max(lat))
    assert store.drainer.live_tasks <= 8
    await store.drainer.aclose()


async def test_sweeps_with_a_hung_ledger_keep_tasks_and_publishes_bounded():
    bus = GateBus()
    bus.gate.clear()
    store = InMemoryHoldStore(audit=BusAuditSink(bus), clock=Clock())
    store.drainer = fast_drainer(breaker_threshold=5)
    await make_holds(40, store)
    peak = 0
    for _ in range(5):
        await store.sweep()
        peak = max(peak, store.drainer.live_tasks)
    assert peak <= 8 and store.drainer.live_tasks <= 8
    assert bus.ledger_calls <= 40 + 5  # one try per hold at most, plus one probe per sweep
    bus.gate.set()
    await store.drainer.settle()
    for _ in range(3):
        await store.sweep()
    es = entries(bus)
    assert len(es) == 40 and len({e.case_refs[0] for e in es}) == 40
    assert len(bus.messages(Topics.LEDGER)) <= 40 + 8  # in-flight guard: no per-sweep respawn
    assert await store.count_open() == 40 and not [
        h for h in [await store.get("h0")] if h.audit_pending
    ]


async def test_second_drain_of_the_same_hold_is_a_noop_while_one_is_in_flight():
    bus = GateBus()
    bus.gate.clear()
    store = InMemoryHoldStore(audit=BusAuditSink(bus), clock=Clock())
    store.drainer = fast_drainer(timeout_s=5)
    await make_holds(1, store)
    for _ in range(5):
        await store._drain_quietly("h0")
    await asyncio.sleep(0.02)
    assert bus.ledger_calls == 1 and store.drainer.live_tasks == 1
    bus.gate.set()
    await store.drainer.settle()
    assert len(entries(bus)) == 1
