"""Consumers: dedupe, decision events, late call risk, parity with the direct path, resilience."""

import asyncio
from datetime import datetime, timedelta

import pytest
from scam_contracts.models import CallRisk, Transaction, TxnDecision
from scam_contracts.topics import Topics
from svckit.bus import InMemoryBus
from svckit.idempotency import InMemoryIdempotencyStore

from txn_guard.consumer import GROUP, run_consumers
from txn_guard.decision import decide, make_decision
from txn_guard.features import extract_features
from txn_guard.history import InMemoryHistoryStore
from txn_guard.holds import InMemoryAuditSink, InMemoryHoldStore, RedisHoldStore
from txn_guard.model import Scorer
from txn_guard.service import TxnGuardService

from .conftest import T0

RANK = {"allow": 0, "step_up": 1, "hold_verify": 2}


@pytest.fixture(scope="module")
def scorer():
    return Scorer()


def _warm(history, make_txn, payer="payer_1"):
    for i in range(40):
        history.record_txn(
            make_txn(
                amount=str(300 + (i * 37) % 400),
                payer=payer,
                ts=T0 - timedelta(days=10) + timedelta(hours=i * 5),
            )  # fmt: skip
        )


def _risk(score=0.9, ts=None, payer="payer_1", call_id="c1"):
    return CallRisk(call_id=call_id, victim_token=payer, score=score, reasons=[],
                    model_version="x", ts=ts or T0 - timedelta(minutes=2))  # fmt: skip


def build(scorer, make_txn, holds=None, warm=True, history=None):
    bus = InMemoryBus()
    history = history or InMemoryHistoryStore()
    if warm:
        _warm(history, make_txn)
    holds = holds or InMemoryHoldStore(audit=InMemoryAuditSink())
    svc = TxnGuardService(scorer, history, holds, bus, InMemoryIdempotencyStore(),
                          hold_deadline_s=120)  # fmt: skip
    return svc, bus, holds, history


def _decisions(bus):
    return [TxnDecision.model_validate_json(raw) for _, raw in bus.messages(Topics.TXN_DECISIONS)]


# ---------------------------------------------------------------------- Review Focus 1
async def test_replayed_txn_creates_one_hold(scorer, make_txn):
    svc, bus, holds, _ = build(scorer, make_txn)
    t = make_txn(amount="95000", age=3, payee="p_mule")
    for _ in range(3):  # service-level guard even without the consume() dedupe
        await svc.handle_txn(t)
    same_id_other_key = t.model_copy(update={"idempotency_key": "other-key"})
    await svc.handle_txn(same_id_other_key)
    assert len(_decisions(bus)) == 1 and len(await holds.list_open()) == 1


async def test_replay_through_consumer_one_decision_one_hold(scorer, make_txn):
    svc, bus, holds, _ = build(scorer, make_txn)
    t = make_txn(amount="95000", age=3, payee="p_mule")
    tasks = run_consumers(bus, svc, InMemoryIdempotencyStore())
    for _ in range(3):
        await bus.publish(Topics.TXN_EVENTS, t.txn_id, t)
    await bus.publish(Topics.TXN_EVENTS, t.txn_id, t.model_copy(update={"idempotency_key": "k2"}))
    await _settle(svc, 2)  # 3 same-key copies are dropped by consume()
    for x in tasks:
        x.cancel()
    assert len(_decisions(bus)) == 1 and len(await holds.list_open()) == 1


async def _settle(svc, n, timeout=20.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while svc.handled < n and loop.time() < end:
        await asyncio.sleep(0.01)
    assert svc.handled >= n


async def test_decision_event_published(scorer, make_txn):
    svc, bus, _, _ = build(scorer, make_txn)
    t = make_txn(amount="95000", age=3, payee="p_mule")
    await svc.handle_txn(t)
    ((key, raw),) = bus.messages(Topics.TXN_DECISIONS)
    d = TxnDecision.model_validate_json(raw)
    assert key == t.txn_id and d.txn_id == t.txn_id and d.decision == "hold_verify"
    assert d.model_version == "gbm-v1" and d.reasons and d.decision_seq == 1 and d.ts == t.ts


async def test_allow_creates_no_hold(scorer, make_txn):
    svc, bus, holds, _ = build(scorer, make_txn)
    await svc.handle_txn(make_txn(amount="450"))
    assert _decisions(bus)[0].decision == "allow" and await holds.list_open() == []


# ---------------------------------------------------------------------- Review Focus 2
async def test_late_call_risk_upgrades_pending_txn(scorer, make_txn):
    svc, bus, holds, _ = build(scorer, make_txn)
    t = make_txn(amount="40000")  # known payee, z large: allow without a call
    first = await svc.handle_txn(t)
    assert first.decision == "allow"
    ups = await svc.handle_call_risk(_risk())
    assert [(u.decision, u.decision_seq) for u in ups] == [("step_up", 2)]
    assert [d.decision_seq for d in _decisions(bus)] == [1, 2]
    assert (await holds.get(t.txn_id)).decision == "step_up"
    # an identical or weaker risk event changes nothing and emits nothing new
    assert await svc.handle_call_risk(_risk(0.75, call_id="c2")) == []
    assert len(_decisions(bus)) == 2
    # a stronger signal on the same txn (new payee case) upgrades again, once
    t2 = make_txn(amount="99999", age=1200, payee="p_far", ts=T0 + timedelta(hours=1))
    d2 = await svc.handle_txn(t2)
    assert d2.decision in ("allow", "step_up")
    ups2 = await svc.handle_call_risk(
        _risk(0.95, ts=T0 + timedelta(hours=1, minutes=1), call_id="c3")
    )
    assert any(u.txn_id == t2.txn_id and u.decision == "hold_verify" for u in ups2)
    assert (await holds.get(t2.txn_id)).decision == "hold_verify"


@pytest.mark.parametrize("risk_first", [True, False])
async def test_both_orders_same_final_class(risk_first, scorer, make_txn):
    svc, bus, _, _ = build(scorer, make_txn)
    t = make_txn(amount="99999", age=1200, payee="p_far")
    r = _risk(0.9)
    if risk_first:
        await svc.handle_call_risk(r)
        await svc.handle_txn(t)
    else:
        await svc.handle_txn(t)
        await svc.handle_call_risk(r)
    final = [d for d in _decisions(bus) if d.txn_id == t.txn_id][-1]
    ref_hist = InMemoryHistoryStore()
    _warm(ref_hist, make_txn)
    ref_hist.record_call_risk(r)
    ref = make_decision(t.txn_id, extract_features(t, ref_hist.context_for(t, t.ts)), scorer, t.ts)
    assert final.decision == ref.decision == "hold_verify"


async def test_late_call_risk_with_no_pending_txn_does_not_crash(scorer, make_txn):
    svc, bus, _, _ = build(scorer, make_txn)
    assert await svc.handle_call_risk(_risk(payer="ghost")) == []
    assert _decisions(bus) == []


async def test_resolved_and_old_txns_are_not_upgraded_and_never_downgraded(scorer, make_txn):
    svc, bus, holds, _ = build(scorer, make_txn)
    t = make_txn(amount="99999", age=1200, payee="p_far")
    d = await svc.handle_txn(t)  # no call: >= step_up (extreme amount), maybe more
    if d.decision != "allow":
        await holds.resolve(t.txn_id, "release", "analyst-1")
    n = len(_decisions(bus))
    ups = await svc.handle_call_risk(_risk(0.95))
    assert ups == [] and len(_decisions(bus)) == n  # resolved hold: left alone
    old = make_txn(amount="40000", ts=T0 - timedelta(minutes=40), payee="payee_known")
    svc2, bus2, _, _ = build(scorer, make_txn)
    await svc2.handle_txn(old)
    assert await svc2.handle_call_risk(_risk(0.9, ts=T0)) == []  # outside the 15-minute window
    # a weaker later risk never downgrades
    t3 = make_txn(amount="99999", age=1200, payee="p_far3", ts=T0 + timedelta(hours=1))
    svc3, bus3, h3, _ = build(scorer, make_txn)
    await svc3.handle_call_risk(_risk(0.95, ts=t3.ts - timedelta(minutes=1), call_id="x"))
    d3 = await svc3.handle_txn(t3)
    assert d3.decision == "hold_verify"
    assert (
        await svc3.handle_call_risk(_risk(0.7, ts=t3.ts - timedelta(minutes=1), call_id="y")) == []
    )
    assert (await h3.get(t3.txn_id)).decision == "hold_verify"


# ------------------------------------------------------------------------------ parity
def _sim_events(n_citizens=250, days=4):
    from sim_engine.benign import gen_benign_txns
    from sim_engine.scam import gen_scam_campaign
    from sim_engine.world import build_world

    world = build_world(4242, n_citizens)
    benign = list(gen_benign_txns(world, days, 4242))
    camp = gen_scam_campaign(
        world, "parity", 4, 4242, start_ts=world.start + timedelta(days=2, hours=10)
    )
    events: list[tuple[datetime, int, object]] = [(t.ts, 1, t) for t in [*benign, *camp.txns]]
    by_victim: dict[str, datetime] = {}
    for e in camp.calls:
        by_victim[e.victim_token] = max(by_victim.get(e.victim_token, e.ts), e.ts)
    for i, (tok, ts) in enumerate(by_victim.items()):
        rts = ts + timedelta(seconds=60)
        events.append((rts, 0, CallRisk(call_id=f"cc{i}", victim_token=tok, score=0.9, reasons=[],
                                        model_version="sim", ts=rts)))  # fmt: skip
    events.sort(key=lambda e: (e[0], e[1]))
    return [e[2] for e in events]


async def test_decision_parity_with_direct_make_decision(scorer):
    events = _sim_events(n_citizens=160, days=3)
    n_txn = sum(isinstance(e, Transaction) for e in events)
    assert n_txn > 400
    # reference: the benchmark pipeline order (risk -> history; txn -> score -> record)
    ref_hist, ref = InMemoryHistoryStore(), {}
    for e in events:
        if isinstance(e, CallRisk):
            ref_hist.record_call_risk(e)
        else:
            f = extract_features(e, ref_hist.context_for(e, e.ts))
            ref[e.txn_id] = make_decision(e.txn_id, f, scorer, e.ts)
            ref_hist.record_txn(e)
    # service path through the consumers, one event at a time to fix the cross-topic order
    svc, bus, holds, _ = build(scorer, None, warm=False)
    tasks = run_consumers(bus, svc, InMemoryIdempotencyStore())
    for i, e in enumerate(events, start=1):
        topic = Topics.CALL_RISK if isinstance(e, CallRisk) else Topics.TXN_EVENTS
        await bus.publish(topic, getattr(e, "txn_id", None) or e.call_id, e)
        await _settle(svc, i)
    for x in tasks:
        x.cancel()
    got = {d.txn_id: d for d in _decisions(bus)}
    assert len(got) == n_txn == len(ref)
    diffs = [k for k in ref if (got[k].decision, got[k].score) != (ref[k].decision, ref[k].score)]
    assert diffs == []
    assert all(d.decision_seq == 1 for d in _decisions(bus))
    flagged = sum(d.decision != "allow" for d in got.values())
    assert flagged > 0 and len(await holds.list_open()) == flagged
    print(f"\nparity: {n_txn} txns, {flagged} non-allow, 0 differences")


# -------------------------------------------------------------------------- resilience
class DownHolds(InMemoryHoldStore):
    async def create(self, *a, **k):
        raise ConnectionError("hold store down")


async def test_hold_store_down_retries_then_dlq_without_losing_or_recording(scorer, make_txn):
    holds = DownHolds(audit=InMemoryAuditSink())
    svc, bus, _, history = build(scorer, make_txn, holds=holds)
    tasks = run_consumers(bus, svc, InMemoryIdempotencyStore())
    t = make_txn(amount="95000", age=3, payee="p_mule")
    await bus.publish(Topics.TXN_EVENTS, t.txn_id, t)
    for _ in range(300):
        if bus.messages(Topics.TXN_EVENTS + Topics.DLQ_SUFFIX):
            break
        await asyncio.sleep(0.01)
    for x in tasks:
        x.cancel()
    assert len(bus.messages(Topics.TXN_EVENTS + Topics.DLQ_SUFFIX)) == 1
    assert _decisions(bus) == []  # no verdict without a hold behind it
    ctx = history.context_for(make_txn(payee="p_mule"), T0)
    assert ctx.payee_n == 0  # history not recorded for the failed txn


async def test_model_failure_publishes_rules_fallback_decisions(make_txn, tmp_path):
    bad = Scorer(path=tmp_path / "missing.joblib")
    svc, bus, holds, _ = build(bad, make_txn)
    d = await svc.handle_txn(make_txn(amount="95000", age=3, payee="p_mule"))
    assert d.model_version == "rules-fallback-v1" and d.decision == "hold_verify"
    assert len(await holds.list_open()) == 1


async def test_redis_backed_stores_end_to_end(scorer, make_txn):
    import fakeredis

    from txn_guard.redis_history import RedisHistoryStore

    history = RedisHistoryStore(fakeredis.FakeRedis())
    holds = RedisHoldStore(fakeredis.aioredis.FakeRedis(), audit=InMemoryAuditSink())
    svc, bus, _, _ = build(scorer, make_txn, holds=holds, history=history)
    t = make_txn(amount="95000", age=3, payee="p_mule")
    for _ in range(2):
        await svc.handle_txn(t)
    assert len(_decisions(bus)) == 1 and len(await holds.list_open()) == 1
    ups = await svc.handle_call_risk(_risk())
    assert ups == [] or ups[0].decision_seq == 2


def test_group_name():
    assert GROUP == "txn-guard" and decide(0.5) == "step_up"
