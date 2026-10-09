"""Task 11 fix round 1: races, event-loop hygiene, bounded memory, bootstrap robustness."""

import asyncio
import logging
import time
from datetime import timedelta

import fakeredis
import fakeredis.aioredis
import pytest
from scam_contracts.models import Antibody, CallRisk, TxnDecision
from scam_contracts.topics import Topics
from svckit.bus import InMemoryBus
from svckit.idempotency import InMemoryIdempotencyStore

from txn_guard.antibody_cache import (
    AntibodyLookup,
    InMemoryAntibodyCache,
    RedisAntibodyCache,
    bootstrap,
)
from txn_guard.history import InMemoryHistoryStore
from txn_guard.holds import InMemoryAuditSink, InMemoryHoldStore
from txn_guard.model import Scorer
from txn_guard.pending import InMemoryPendingStore, PendingEntry, RedisPendingStore
from txn_guard.service import TxnGuardService

from .conftest import T0

MULE = "m" * 64


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def ab(aid="g0", key=MULE, revoked=False, exp=None):
    return Antibody(antibody_id=aid, kind="mule_account", key_hash=key, source_bank="bank_a",
                    confirmed_by="analyst-1", created_at=T0,
                    expires_at=exp or T0 + timedelta(days=14), revoked=revoked)  # fmt: skip


@pytest.fixture(scope="module")
def scorer():
    return Scorer()


def make_svc(scorer, make_txn, cache=None, clock=None, pending=None, **kw):
    clock = clock or Clock()
    cache = cache if cache is not None else InMemoryAntibodyCache(clock=clock)
    s = TxnGuardService(
        scorer, InMemoryHistoryStore(), InMemoryHoldStore(audit=InMemoryAuditSink(), clock=clock),
        InMemoryBus(), InMemoryIdempotencyStore(), clock=clock, pending=pending,
        antibodies=AntibodyLookup(cache), **kw,
    )  # fmt: skip
    for p in ("p1", "p2"):
        for i in range(40):
            s.history.record_txn(make_txn(amount=str(300 + (i * 37) % 400), payer=p,
                                          ts=T0 - timedelta(days=10) + timedelta(hours=i * 5)))  # fmt: skip
    return s


def decs(s):
    return [TxnDecision.model_validate_json(r) for _, r in s.bus.messages(Topics.TXN_DECISIONS)]


# -------------------------------------------------------------- 1. lost upgrade race
async def test_antibody_applied_while_txn_is_scoring_upgrades_exactly_once(scorer, make_txn):
    s = make_svc(scorer, make_txn)
    orig, started = s._score, asyncio.Event()

    async def slow(*a, **k):
        started.set()
        await asyncio.sleep(0.05)
        return await orig(*a, **k)

    s._score = slow  # type: ignore[method-assign]
    t = make_txn(amount="900", age=900, payee=MULE, payer="p1")
    task = asyncio.create_task(s.handle_txn(t))
    await started.wait()  # the txn already passed its (empty) antibody lookup
    ups = await s.handle_antibody(ab())  # applies to the cache; the txn is not in pending yet
    first = await task
    assert first.decision == "allow" and ups == []
    final = max((d for d in decs(s) if d.txn_id == t.txn_id), key=lambda d: d.decision_seq)
    assert (final.decision, final.decision_seq) == ("hold_verify", 2)
    assert "LATE_ANTIBODY_POST_SETTLEMENT" in {r.code for r in final.reasons}
    assert [d.decision_seq for d in decs(s)] == [1, 2]  # exactly one upgrade
    assert (await s.holds.get(t.txn_id)).decision == "hold_verify"
    assert await s.handle_antibody(ab()) == []  # redelivery: nothing more


async def test_converse_ordering_antibody_first_or_between_gives_one_upgrade_at_most(
    scorer, make_txn
):
    s = make_svc(scorer, make_txn)
    s.antibodies.cache.apply(ab())
    t = make_txn(amount="900", age=900, payee=MULE, payer="p1")
    d = await s.handle_txn(t)  # antibody known before scoring: held at seq 1
    assert d.decision == "hold_verify" and d.decision_seq == 1
    assert await s.handle_antibody(ab()) == []
    assert [x.decision_seq for x in decs(s)] == [1]


# -------------------------------------------------------------- 2. event loop stays free
class SlowCache(InMemoryAntibodyCache):
    def contains(self, key_hash, kind="mule_account"):
        time.sleep(0.05)  # a blocking Redis round trip
        return super().contains(key_hash, kind)


async def test_slow_cache_lookup_does_not_block_the_event_loop(scorer, make_txn):
    s = make_svc(scorer, make_txn, cache=SlowCache(clock=Clock()))
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    task = asyncio.create_task(ticker())
    await s.handle_txn(make_txn(amount="900", age=900, payee="q" * 64, payer="p1"))
    task.cancel()
    assert ticks >= 8  # ~50 ms+ of lookups with the loop still ticking every 5 ms


# -------------------------------------------------------------- 3. bounded payee index
def _entry(i, ts, payee=MULE):
    return PendingEntry(txn_id=f"t{i}", payer_token=f"p{i % 7}", ts=ts, decision="allow", score=0.0,
                        seq=1, model_version="m", features={}, payee_hash=payee)  # fmt: skip


@pytest.mark.parametrize("kind", ["memory", "redis"])
async def test_payee_index_stays_bounded_and_scan_is_capped(kind):
    store = (InMemoryPendingStore() if kind == "memory"
             else RedisPendingStore(fakeredis.aioredis.FakeRedis()))  # fmt: skip
    for i in range(10_000):
        await store.add(_entry(i, T0 + timedelta(seconds=i)))  # 10,000 s of one hot payee
    rows = await store.recent_by_payee(MULE, T0 - timedelta(days=1), limit=500)
    assert len(rows) == 500 and rows[0].ts >= rows[-1].ts  # newest first, capped
    window = await store.recent_by_payee(MULE, T0 - timedelta(days=1), limit=100_000)
    assert len(window) <= 40 * 60  # only ~30 minutes of the index is retained, not 10,000 entries


async def test_antibody_scan_cap_logs_and_counts(scorer, make_txn, caplog):
    s = make_svc(scorer, make_txn, max_pending_scan=5)
    for i in range(20):
        await s.handle_txn(make_txn(amount="450", age=900, payee=MULE, payer=f"p{1 + i % 2}",
                                    ts=T0 - timedelta(minutes=1, seconds=i)))  # fmt: skip
    with caplog.at_level(logging.WARNING, logger="txn_guard"):
        ups = await s.handle_antibody(ab())
    assert len(ups) == 5 and s.antibody_scan_truncated == 1
    assert any("truncated" in r.getMessage() for r in caplog.records)
    assert MULE not in caplog.text


# -------------------------------------------------------------- minors in the service
async def test_mixed_case_payee_hash_matches(scorer, make_txn):
    s = make_svc(scorer, make_txn)
    s.antibodies.cache.apply(ab())
    d = await s.handle_txn(make_txn(amount="900", age=900, payee=MULE.upper(), payer="p1"))
    assert d.decision == "hold_verify" and "ANTIBODY_MATCH" in {r.code for r in d.reasons}


async def test_call_risk_rescore_after_tombstone_does_not_keep_antibody_feature(scorer, make_txn):
    s = make_svc(scorer, make_txn)
    t = make_txn(amount="900", age=900, payee=MULE, payer="p1")
    await s.handle_txn(t)  # allow
    await s.handle_antibody(ab())  # -> hold_verify seq 2
    await s.handle_antibody(ab(revoked=True))
    entry = await s.pending.get(t.txn_id)
    assert entry.features["payee_in_antibody"] == 1.0  # stored at the upgrade
    await s.handle_call_risk(CallRisk(call_id="c", victim_token="p1", score=0.9, reasons=[],
                                      model_version="x", ts=t.ts))  # fmt: skip
    entry = await s.pending.get(t.txn_id)
    assert entry.features["payee_in_antibody"] == 0.0  # recomputed from the (tombstoned) cache


async def test_antibody_and_call_risk_each_class_change_bumps_seq_once(scorer, make_txn):
    s = make_svc(scorer, make_txn)
    t = make_txn(amount="40000", payee="pn", age=900, payer="p1")  # allow alone
    assert (await s.handle_txn(t)).decision == "allow"
    ups1 = await s.handle_call_risk(CallRisk(call_id="c", victim_token="p1", score=0.9, reasons=[],
                                             model_version="x", ts=t.ts - timedelta(minutes=1)))  # fmt: skip
    assert [(u.decision, u.decision_seq) for u in ups1] == [("hold_verify", 2)]
    s.antibodies.cache.apply(ab(key="pn"))
    ups2 = await s.handle_antibody(ab(key="pn"))
    assert ups2 == []  # already hold_verify: the antibody adds no class change, no new event
    assert await s.handle_antibody(ab(key="pn")) == []
    assert [d.decision_seq for d in decs(s)] == [1, 2]


async def test_protected_merchant_tombstone_differs_from_established_payee(scorer, make_txn):
    """A wrongly published merchant: new customers are blocked until the tombstone; an
    established customer is only stepped up, and after the tombstone both are allowed."""
    s = make_svc(scorer, make_txn)
    for i in range(4):
        s.history.record_txn(make_txn(amount="900", payee="shop", payer="p2",
                                      ts=T0 - timedelta(days=12) + timedelta(days=3 * i)))  # fmt: skip
    s.antibodies.cache.apply(ab("wrong", key="shop"))
    newbie = await s.handle_txn(make_txn(amount="900", payee="shop", payer="p1", age=900))
    loyal = await s.handle_txn(make_txn(amount="900", payee="shop", payer="p2", age=900))
    assert newbie.decision == "hold_verify" and loyal.decision == "step_up"
    s.antibodies.cache.apply(ab("wrong", key="shop", revoked=True))
    after = [await s.handle_txn(make_txn(amount="900", payee="shop", payer=p, age=900))
             for p in ("p1", "p2")]  # fmt: skip
    assert [d.decision for d in after] == ["allow", "allow"]
    assert (await s.holds.get(newbie.txn_id)).state == "open"


# -------------------------------------------------------------- 4. memory bounds
def test_in_memory_eviction_keeps_newest_counts_and_warns(caplog):
    c = InMemoryAntibodyCache(clock=Clock(), capacity=3)
    for i in range(3):
        c.apply(ab(f"a{i}", key=f"{i:064x}", exp=T0 + timedelta(days=10 + i)))
    with caplog.at_level(logging.WARNING, logger="txn_guard"):
        # the new entry has the SOONEST expiry: it must still be kept, an older one is evicted
        c.apply(ab("new", key=f"{9:064x}", exp=T0 + timedelta(days=1)))
    assert c.contains(f"{9:064x}") is not None and c.size() == 3 and c.evictions() == 1
    assert c.contains(f"{0:064x}") is None  # the soonest-expiring OLD entry went
    assert "evicted" in caplog.text


def test_tombstones_and_delta_are_bounded_and_delta_only_with_bloom():
    c = InMemoryAntibodyCache(clock=Clock(), capacity=4)
    for i in range(50):
        c.apply(ab(f"t{i}", key=f"{i:064x}", revoked=True, exp=T0 + timedelta(days=1 + i)))
    assert len(c._tombs) <= 4
    assert c.delta_keys() == set()  # not tracked without Bloom
    AntibodyLookup(c, use_bloom=True)
    for i in range(50, 60):
        c.apply(ab(f"d{i}", key=f"{i:064x}"))
    assert len(c.delta_keys()) <= 4 or c.delta_overflow()


def test_redis_cache_enforces_capacity_counts_evictions_and_size_is_cheap():
    clock = Clock()
    c = RedisAntibodyCache(fakeredis.FakeRedis(), clock=clock, capacity=3)
    for i in range(5):
        c.apply(ab(f"a{i}", key=f"{i:064x}", exp=T0 + timedelta(days=10 + i)))
    assert c.size() == 3 and c.evictions() == 2
    assert c.contains(f"{4:064x}") is not None and c.contains(f"{0:064x}") is None
    c.apply(ab("n", key=f"{9:064x}", exp=T0 + timedelta(days=1)))  # soonest expiry, newest: kept
    assert c.contains(f"{9:064x}") is not None


async def test_metrics_export_evictions(scorer, make_txn, monkeypatch):
    import httpx

    from txn_guard.api import create_app

    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "1")
    monkeypatch.delenv("GATEWAY_SHARED_SECRET", raising=False)
    s = make_svc(scorer, make_txn, cache=InMemoryAntibodyCache(clock=Clock(), capacity=1))
    s.antibodies.cache.apply(ab("a", key="a" * 64))
    s.antibodies.cache.apply(ab("b", key="b" * 64, exp=T0 + timedelta(days=20)))
    app = create_app(service=s, sweep_interval_s=0)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        text = (await c.get("/metrics", headers={"X-Principal-Role": "admin",
                                                 "X-Principal-Sub": "r"})).text  # fmt: skip
    assert "txn_guard_antibody_cache_evictions_total 1" in text


async def test_lifespan_sweeps_the_antibody_cache(scorer, make_txn):
    from txn_guard.api import create_app

    clock = Clock()
    s = make_svc(scorer, make_txn, cache=InMemoryAntibodyCache(clock=clock), clock=clock)
    s.antibodies.cache.apply(ab("a", key="a" * 64, exp=T0 + timedelta(hours=1)))
    clock.t = T0 + timedelta(hours=2)
    app = create_app(service=s, sweep_interval_s=0.01)
    async with app.router.lifespan_context(app):
        for _ in range(100):
            if s.antibodies.cache.size() == 0:
                break
            await asyncio.sleep(0.01)
    assert s.antibodies.cache.size() == 0


# -------------------------------------------------------------- bootstrap robustness
class Hub:
    def __init__(self, pages, delay=0.0):
        self.pages, self.delay = pages, delay

    async def exact_page(self, bank_id, since=None, limit=500):
        await asyncio.sleep(self.delay)
        return self.pages[0 if since is None else int(since)]

    async def bloom(self, bank_id, etag=None):
        return None


def item(i, exp=None, **kw):
    return {"antibody_id": f"b{i}", "kind": "mule_account", "key_hash": f"{i:064x}",
            "expires_at": exp if exp is not None else (T0 + timedelta(days=9)).isoformat()} | kw  # fmt: skip


async def test_bootstrap_skips_bad_items_handles_naive_dates_and_resets_failure():
    clock = Clock()
    c = InMemoryAntibodyCache(clock=clock)
    stats = {"bootstrap_failed": 1}
    page = {"items": [item(1), {"antibody_id": "x"}, item(2, exp="2026-03-20T00:00:00"),
                      item(3, exp="not-a-date"), item(4, kind="alien")], "next_cursor": None}  # fmt: skip
    n = await bootstrap(c, Hub([page]), "bank_a", clock=clock, stats=stats)
    assert n == 2 and c.contains(f"{1:064x}") and c.contains(f"{2:064x}")  # naive -> UTC
    assert stats["bootstrap_skipped"] == 3 and stats["bootstrap_failed"] == 0


async def test_bootstrap_total_deadline_is_bounded():
    clock = Clock()
    c = InMemoryAntibodyCache(clock=clock)
    pages = [{"items": [item(i)], "next_cursor": str(i + 1)} for i in range(50)]
    stats: dict[str, float] = {}
    t0 = time.perf_counter()
    n = await bootstrap(
        c, Hub(pages, delay=0.05), "bank_a", clock=clock, stats=stats, deadline_s=0.2
    )
    assert time.perf_counter() - t0 < 1.0 and n < 50 and stats["bootstrap_failed"] == 1


async def test_periodic_rebootstrap_is_merge_safe(scorer, make_txn):
    import httpx

    from txn_guard.api import create_app

    clock = Clock()
    s = make_svc(scorer, make_txn, clock=clock)
    s.antibodies.cache.apply(ab("b1", key=f"{1:064x}", revoked=True, exp=T0 + timedelta(days=9)))
    calls = []

    async def boot() -> int:
        calls.append(1)
        return await bootstrap(s.antibodies.cache, Hub([{"items": [item(1), item(2)],
                               "next_cursor": None}]), "bank_a", clock=clock)  # fmt: skip

    app = create_app(service=s, bootstrap_fn=boot, rebootstrap_interval_s=0.02, sweep_interval_s=0)
    async with app.router.lifespan_context(app):
        for _ in range(100):
            if len(calls) >= 3:
                break
            await asyncio.sleep(0.01)
    assert len(calls) >= 3
    assert (
        s.antibodies.cache.contains(f"{1:064x}") is None
    )  # sticky tombstone survives re-bootstrap
    assert s.antibodies.cache.contains(f"{2:064x}") is not None
    del httpx


# -------------------------------------------------------------- env wiring
def test_create_service_app_requires_bank_id_when_hub_or_kafka_configured(monkeypatch):
    from txn_guard.api import create_service_app

    monkeypatch.setenv("REDIS_URL", "redis://localhost:6399/0")
    monkeypatch.delenv("TXN_BANK_ID", raising=False)
    monkeypatch.setenv("HUB_URL", "http://hub:8000")
    with pytest.raises(RuntimeError, match="TXN_BANK_ID"):
        create_service_app()
    monkeypatch.delenv("HUB_URL")
    monkeypatch.setenv("KAFKA_BOOTSTRAP", "kafka:9092")
    with pytest.raises(RuntimeError, match="TXN_BANK_ID"):
        create_service_app()


def test_create_service_app_wires_cache_capacity_and_bank_prefix(monkeypatch):
    from txn_guard.api import create_service_app

    monkeypatch.setenv("REDIS_URL", "redis://localhost:6399/0")
    monkeypatch.setenv("TXN_BANK_ID", "bank_b")
    monkeypatch.setenv("ANTIBODY_CACHE_CAPACITY", "1234")
    monkeypatch.setenv("ANTIBODY_BLOOM", "1")
    monkeypatch.delenv("HUB_URL", raising=False)
    monkeypatch.delenv("KAFKA_BOOTSTRAP", raising=False)
    app = create_service_app()
    cache = app.state.service.antibodies.cache
    assert cache.capacity == 1234 and "bank_b" in cache._p
    assert app.state.service.antibodies.use_bloom is True


# -------------------------------------------------------------- bootstrap off the event loop
class SlowApplyCache(InMemoryAntibodyCache):
    def apply(self, event):
        time.sleep(0.005)  # a blocking Redis EVAL per item
        super().apply(event)


async def test_bootstrap_does_not_stall_the_event_loop():
    clock = Clock()
    c = SlowApplyCache(clock=clock)
    pages = [{"items": [item(p * 100 + i) for i in range(100)], "next_cursor": str(p + 1) if p < 1 else None}
             for p in range(2)]  # fmt: skip
    gaps, stop = [], False

    async def ticker():
        last = time.perf_counter()
        while not stop:
            await asyncio.sleep(0.002)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    task = asyncio.create_task(ticker())
    t0 = time.perf_counter()
    n = await bootstrap(c, Hub(pages), "bank_a", clock=clock)
    total = time.perf_counter() - t0
    stop = True
    await task
    assert n == 200 and total >= 0.9  # 200 items x 5 ms of blocking work
    assert max(gaps) < 0.1 and max(gaps) < total / 5  # the loop kept ticking during each page


async def test_bootstrap_deadline_is_checked_between_pages():
    clock = Clock()
    c = SlowApplyCache(clock=clock)
    pages = [{"items": [item(p * 50 + i) for i in range(50)], "next_cursor": str(p + 1)}
             for p in range(20)]  # fmt: skip
    stats: dict[str, float] = {}
    t0 = time.perf_counter()
    n = await bootstrap(c, Hub(pages), "bank_a", clock=clock, stats=stats, deadline_s=0.5)
    assert time.perf_counter() - t0 < 1.5 and 0 < n < 1000
    assert n % 50 == 0  # whole pages only: it stopped between pages
    assert stats["bootstrap_failed"] == 1


async def test_threaded_bootstrap_keeps_sticky_tombstones():
    clock = Clock()
    c = InMemoryAntibodyCache(clock=clock)
    c.apply(ab("b1", key=f"{1:064x}", revoked=True, exp=T0 + timedelta(days=9)))
    await bootstrap(
        c, Hub([{"items": [item(1), item(2)], "next_cursor": None}]), "bank_a", clock=clock
    )
    assert c.contains(f"{1:064x}") is None and c.contains(f"{2:064x}") is not None
