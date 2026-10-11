"""Per-bank routing: one consumer group per bank, foreign-bank transactions dropped."""

import asyncio

import httpx
import pytest
from scam_contracts.models import CallRisk, TxnDecision
from scam_contracts.topics import Topics
from svckit.bus import InMemoryBus
from svckit.idempotency import InMemoryIdempotencyStore

from txn_guard.consumer import GROUP, group_for, run_consumers
from txn_guard.history import InMemoryHistoryStore
from txn_guard.holds import InMemoryAuditSink, InMemoryHoldStore
from txn_guard.model import Scorer
from txn_guard.service import TxnGuardService

from .conftest import T0


@pytest.fixture(scope="module")
def scorer():
    return Scorer()


def _svc(scorer, bus, bank_id):
    return TxnGuardService(
        scorer, InMemoryHistoryStore(), InMemoryHoldStore(audit=InMemoryAuditSink()), bus,
        InMemoryIdempotencyStore(), bank_id=bank_id,
    )  # fmt: skip


def _t(make_txn, bank, **kw):
    return make_txn(**kw).model_copy(update={"bank_id": bank})


async def _until(pred, n=300):
    for _ in range(n):
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


def _decided(bus):
    return {
        TxnDecision.model_validate_json(r).txn_id for _, r in bus.messages(Topics.TXN_DECISIONS)
    }


def test_group_names():
    assert group_for(None) == GROUP == "txn-guard"
    assert group_for("bank_a") == "txn-guard-bank_a"


async def test_two_banks_one_bus_each_decides_only_its_own(scorer, make_txn):
    bus = InMemoryBus()
    a, b = _svc(scorer, bus, "bank_a"), _svc(scorer, bus, "bank_b")
    tasks = run_consumers(bus, a, InMemoryIdempotencyStore()) + run_consumers(
        bus, b, InMemoryIdempotencyStore()
    )
    ta = [_t(make_txn, "bank_a", payer=f"pa{i}") for i in range(3)]
    tb = [_t(make_txn, "bank_b", payer=f"pb{i}") for i in range(2)]
    for t in [*ta, *tb]:
        await bus.publish(Topics.TXN_EVENTS, t.payer_token, t)
    await _until(lambda: a.handled == 3 and b.handled == 2)
    await _until(lambda: a.foreign_bank_events == 2 and b.foreign_bank_events == 3)
    for t in tasks:
        t.cancel()
    assert a.handled == 3 and b.handled == 2
    assert _decided(bus) == {t.txn_id for t in [*ta, *tb]}  # each exactly once, no loss
    # foreign events never reach history or the pending/hold stores
    for svc, foreign in ((a, tb), (b, ta)):
        for t in foreign:
            assert await svc.pending.get(t.txn_id) is None
            assert svc.history.context_for(t, T0).payee_n == 0


async def test_foreign_event_not_in_history_or_holds(scorer, make_txn):
    bus = InMemoryBus()
    a = _svc(scorer, bus, "bank_a")
    tasks = run_consumers(bus, a, InMemoryIdempotencyStore())
    mule = _t(make_txn, "bank_b", amount="95000", age=3, payee="p_mule")
    await bus.publish(Topics.TXN_EVENTS, mule.payer_token, mule)
    await _until(lambda: a.foreign_bank_events == 1)
    for t in tasks:
        t.cancel()
    assert await a.holds.list_open() == []
    assert await a.pending.get(mule.txn_id) is None
    assert a.history.context_for(mule, T0).payee_n == 0
    assert bus.messages(Topics.TXN_DECISIONS) == []
    assert bus.messages(Topics.TXN_EVENTS + ".dlq") == []  # dropped = acked, not dead-lettered


async def test_unset_bank_id_processes_everything_on_shared_group(scorer, make_txn):
    bus = InMemoryBus()
    s = _svc(scorer, bus, None)
    tasks = run_consumers(bus, s, InMemoryIdempotencyStore())
    ts = [_t(make_txn, "bank_a", payer="p1"), _t(make_txn, "bank_b", payer="p2")]
    for t in ts:
        await bus.publish(Topics.TXN_EVENTS, t.payer_token, t)
    await _until(lambda: s.handled == 2)
    for t in tasks:
        t.cancel()
    assert s.foreign_bank_events == 0 and _decided(bus) == {t.txn_id for t in ts}


async def test_every_bank_instance_sees_every_call_risk(scorer):
    bus = InMemoryBus()
    a, b = _svc(scorer, bus, "bank_a"), _svc(scorer, bus, "bank_b")
    tasks = run_consumers(bus, a, InMemoryIdempotencyStore()) + run_consumers(
        bus, b, InMemoryIdempotencyStore()
    )
    r = CallRisk(call_id="c1", victim_token="v1", score=0.9, reasons=[], model_version="x", ts=T0)
    await bus.publish(Topics.CALL_RISK, r.victim_token, r)
    await _until(lambda: a.handled == 1 and b.handled == 1)
    for t in tasks:
        t.cancel()


async def test_txn_topic_override_and_dlq(scorer, make_txn):
    bus = InMemoryBus()
    s = _svc(scorer, bus, "bank_a")
    tasks = run_consumers(bus, s, InMemoryIdempotencyStore(), txn_topic="txn.events.bank_a")
    t = _t(make_txn, "bank_a")
    await bus.publish("txn.events.bank_a", t.payer_token, t)
    await bus.publish(Topics.TXN_EVENTS, t.payer_token, _t(make_txn, "bank_a", payer="other"))
    await _until(lambda: s.handled == 1)
    await asyncio.sleep(0.05)
    for x in tasks:
        x.cancel()
    assert s.handled == 1  # the default topic is not consumed when overridden


async def test_metric_exported(scorer, make_txn, monkeypatch):
    from txn_guard.api import create_app

    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "1")
    monkeypatch.delenv("GATEWAY_SHARED_SECRET", raising=False)
    s = _svc(scorer, InMemoryBus(), "bank_a")
    s.foreign_bank_events = 4
    app = create_app(service=s, sweep_interval_s=0)
    h = {"X-Principal-Role": "admin", "X-Principal-Sub": "root"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        text = (await c.get("/metrics", headers=h)).text
    assert "txn_guard_foreign_bank_events_total 4" in text


def test_service_app_reads_env(monkeypatch):
    """TXN_BANK_ID and TXN_EVENTS_TOPIC reach the service and the app (not just defaults)."""
    pytest.importorskip("redis")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6399/0")
    monkeypatch.setenv("TXN_BANK_ID", "bank_z")
    monkeypatch.setenv("TXN_EVENTS_TOPIC", "txn.events.bank_z")
    monkeypatch.delenv("KAFKA_BOOTSTRAP", raising=False)
    monkeypatch.delenv("HUB_URL", raising=False)
    import txn_guard.api as api

    captured = {}
    real = api.create_app
    monkeypatch.setattr(api, "create_app", lambda **kw: captured.update(kw) or real(**kw))
    api.create_service_app()
    assert captured["service"].bank_id == "bank_z"
    assert captured["txn_topic"] == "txn.events.bank_z"
