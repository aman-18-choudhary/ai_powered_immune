"""Consumer-path latency and parity under Redis stores and late call risks."""

import statistics
import time
from datetime import timedelta

import fakeredis
import fakeredis.aioredis
import numpy as np
from scam_contracts.models import CallRisk, TxnDecision
from scam_contracts.topics import Topics
from svckit.bus import InMemoryBus
from svckit.idempotency import InMemoryIdempotencyStore

from txn_guard.decision import make_decision
from txn_guard.features import extract_features
from txn_guard.history import InMemoryHistoryStore
from txn_guard.holds import InMemoryAuditSink, RedisHoldStore
from txn_guard.model import Scorer
from txn_guard.pending import RedisPendingStore
from txn_guard.redis_history import RedisHistoryStore
from txn_guard.service import TxnGuardService

from .conftest import T0
from .test_consumer import _sim_events

RANK = {"allow": 0, "step_up": 1, "hold_verify": 2}


def _redis_service(scorer):
    bus = InMemoryBus()
    ar = fakeredis.aioredis.FakeRedis()
    svc = TxnGuardService(
        scorer, RedisHistoryStore(fakeredis.FakeRedis()),
        RedisHoldStore(ar, audit=InMemoryAuditSink()), bus, InMemoryIdempotencyStore(),
        pending=RedisPendingStore(ar),
    )  # fmt: skip
    return svc, bus


async def test_consumer_path_latency_with_redis_stores(make_txn, capsys):
    scorer = Scorer()
    svc, bus = _redis_service(scorer)
    rng = np.random.default_rng(0)
    for p in range(20):
        for i in range(30):
            svc.history.record_txn(
                make_txn(
                    payer=f"p{p}",
                    amount=str(int(rng.lognormal(6, 1.2)) + 1),
                    ts=T0 - timedelta(hours=40 - i),
                )  # fmt: skip
            )
    txns = [
        make_txn(
            payer=f"p{i % 20}",
            amount=str(int(rng.lognormal(6, 1.5)) + 1),
            age=int(rng.integers(1, 2000)),
            payee=f"x{i % 90}",
            ts=T0 + timedelta(seconds=i),
        )  # fmt: skip
        for i in range(200)
    ]
    await svc.handle_txn(make_txn(payer="warm"))
    lat = []
    for t in txns:
        t0 = time.perf_counter()
        await svc.handle_txn(t)
        lat.append(time.perf_counter() - t0)
    p50, p99 = statistics.median(lat), float(np.percentile(lat, 99))
    with capsys.disabled():
        print(f"\nconsumer-path latency (fakeredis hold+history+pending, in-mem bus): "
              f"p50={p50 * 1000:.1f}ms p99={p99 * 1000:.1f}ms n=200")  # fmt: skip
    assert p99 < 0.3


async def _run(events, scorer, delay_risks: int):
    svc, bus = _redis_service(scorer)
    queue = list(events)
    # optionally hold each CallRisk back until `delay_risks` later transactions were handled
    held: list[tuple[int, CallRisk]] = []
    n_txn = 0
    upgrades = 0
    for e in queue:
        if isinstance(e, CallRisk):
            if delay_risks:
                held.append((n_txn + delay_risks, e))
            else:
                upgrades += len(await svc.handle_call_risk(e))
        else:
            await svc.handle_txn(e)
            n_txn += 1
        for item in [h for h in held if h[0] <= n_txn]:
            held.remove(item)
            upgrades += len(await svc.handle_call_risk(item[1]))
    for _, r in held:
        upgrades += len(await svc.handle_call_risk(r))
    decs = [TxnDecision.model_validate_json(r) for _, r in bus.messages(Topics.TXN_DECISIONS)]
    final: dict[str, TxnDecision] = {}
    for d in decs:
        if d.txn_id not in final or d.decision_seq > final[d.txn_id].decision_seq:
            final[d.txn_id] = d
    return final, decs, upgrades


def _reference(events, scorer):
    hist, ref = InMemoryHistoryStore(), {}
    for e in events:
        if isinstance(e, CallRisk):
            hist.record_call_risk(e)
        else:
            f = extract_features(e, hist.context_for(e, e.ts))
            ref[e.txn_id] = make_decision(e.txn_id, f, scorer, e.ts)
            hist.record_txn(e)
    return ref


async def test_parity_with_redis_stores_in_order(capsys):
    scorer = Scorer()
    events = _sim_events(n_citizens=120, days=3)
    ref = _reference(events, scorer)
    final, decs, upgrades = await _run(events, scorer, delay_risks=0)
    diffs = [
        k for k in ref if (final[k].decision, final[k].score) != (ref[k].decision, ref[k].score)
    ]
    assert diffs == [] and upgrades == 0 and all(d.decision_seq == 1 for d in decs)
    with capsys.disabled():
        print(f"\nparity redis in-order: {len(ref)} txns, 0 differences, 0 upgrades")


async def test_parity_with_late_call_risks_final_decision_equals_in_order(capsys):
    """Each CallRisk is delivered after the next 2 transactions: the highest-seq decision per txn
    must equal the in-order direct decision class, with upgrades emitted for the affected ones."""
    scorer = Scorer()
    events = _sim_events(n_citizens=120, days=3)
    ref = _reference(events, scorer)
    final, decs, upgrades = await _run(events, scorer, delay_risks=2)
    assert len(final) == len(ref)
    diffs = [k for k in ref if RANK[final[k].decision] < RANK[ref[k].decision]]
    assert diffs == [], f"{len(diffs)} txns ended weaker than the in-order verdict"
    stronger = [k for k in ref if RANK[final[k].decision] > RANK[ref[k].decision]]
    assert stronger == []  # late handling never invents a stronger verdict than in-order
    n_up = sum(d.decision_seq > 1 for d in decs)
    assert n_up == upgrades
    with capsys.disabled():
        print(f"\nparity late-risk: {len(ref)} txns, 0 downgrades vs in-order, {n_up} upgrades "
              f"published")  # fmt: skip
