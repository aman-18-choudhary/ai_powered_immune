"""Cross-bank antibody enforcement (plan Task 11)."""

import asyncio
import base64
import hashlib
import time
from datetime import timedelta

import fakeredis
import httpx
import pytest
from scam_contracts.models import Antibody, TxnDecision
from scam_contracts.topics import Topics
from svckit.bus import InMemoryBus
from svckit.idempotency import InMemoryIdempotencyStore

from txn_guard.antibody_cache import (
    AntibodyLookup,
    HttpxHubClient,
    InMemoryAntibodyCache,
    RedisAntibodyCache,
    bootstrap,
)
from txn_guard.consumer import ANTIBODY_GROUP_PREFIX, run_antibody_consumer, run_consumers
from txn_guard.history import InMemoryHistoryStore
from txn_guard.holds import InMemoryAuditSink, InMemoryHoldStore
from txn_guard.model import Scorer
from txn_guard.service import TxnGuardService

from .conftest import T0

KEY = "a" * 64
KEY2 = "b" * 64


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def ab(aid="id1", key=KEY, revoked=False, exp=None, kind="mule_account", created=None):
    return Antibody(antibody_id=aid, kind=kind, key_hash=key, source_bank="bank_a",
                    confirmed_by="analyst-1", created_at=created or T0,
                    expires_at=exp or T0 + timedelta(days=14), revoked=revoked)  # fmt: skip


@pytest.fixture(params=["memory", "redis"])
def mk_cache(request):
    def _make(clock=None, capacity=1000):
        clock = clock or Clock()
        if request.param == "memory":
            return InMemoryAntibodyCache(clock=clock, capacity=capacity), clock
        return RedisAntibodyCache(fakeredis.FakeRedis(), clock=clock, capacity=capacity), clock

    return _make


# ----------------------------------------------------------------------- merge rules
def test_apply_then_contains(mk_cache):
    c, _ = mk_cache()
    assert c.contains(KEY) is None
    c.apply(ab())
    hit = c.contains(KEY)
    assert hit is not None and hit.antibody_id == "id1" and hit.kind == "mule_account"
    assert c.contains(KEY, kind="device") is None and c.contains(KEY2) is None


def test_apply_is_idempotent(mk_cache):
    c, _ = mk_cache()
    for _ in range(3):
        c.apply(ab())
    assert c.contains(KEY).antibody_id == "id1" and c.size() == 1


def test_greatest_expires_at_wins_regardless_of_order(mk_cache):
    c, _ = mk_cache()
    short, long_ = T0 + timedelta(days=1), T0 + timedelta(days=9)
    c.apply(ab(exp=long_))
    c.apply(ab(exp=short))  # older/extend event arriving late: must not shorten
    assert c.contains(KEY).expires_at == long_
    c2, _ = mk_cache()
    c2.apply(ab(exp=short))
    c2.apply(ab(exp=long_))
    assert c2.contains(KEY).expires_at == long_


def test_tombstone_removes_hit(mk_cache):
    c, _ = mk_cache()
    c.apply(ab())
    c.apply(ab(revoked=True))
    assert c.contains(KEY) is None


def test_revoked_is_sticky_older_event_does_not_resurrect(mk_cache):
    c, _ = mk_cache()
    c.apply(ab(exp=T0 + timedelta(days=5)))
    c.apply(ab(revoked=True, exp=T0 + timedelta(days=5)))
    c.apply(ab(exp=T0 + timedelta(days=3)))  # reordered older non-revoked event, same id
    c.apply(ab(exp=T0 + timedelta(days=9)))  # even a later expiry on the same id stays dead
    assert c.contains(KEY) is None


def test_tombstone_before_any_event_blocks_later_stale_event(mk_cache):
    c, _ = mk_cache()
    c.apply(ab(revoked=True))
    c.apply(ab())
    assert c.contains(KEY) is None


def test_new_generation_after_revoke_reactivates(mk_cache):
    c, _ = mk_cache()
    c.apply(ab("gen0"))
    c.apply(ab("gen0", revoked=True))
    assert c.contains(KEY) is None
    c.apply(ab("gen1", exp=T0 + timedelta(days=14)))
    assert c.contains(KEY).antibody_id == "gen1"
    c.apply(ab("gen0"))  # the old generation is still dead
    assert c.contains(KEY).antibody_id == "gen1"


def test_expired_antibody_ignored_and_purged(mk_cache):
    clock = Clock()
    c, _ = mk_cache(clock)
    c.apply(ab(exp=T0 + timedelta(hours=1)))
    assert c.contains(KEY) is not None
    clock.t = T0 + timedelta(hours=1)  # expires_at <= now is expired
    assert c.contains(KEY) is None
    assert c.sweep() >= 0
    if isinstance(c, InMemoryAntibodyCache):  # Redis expiry follows real TTLs, not the test clock
        assert c.size() == 0
    c.apply(ab("late", exp=T0 + timedelta(minutes=5)))  # already expired on arrival
    assert c.contains(KEY) is None


def test_other_generation_with_later_expiry_wins(mk_cache):
    c, _ = mk_cache()
    c.apply(ab("g0", exp=T0 + timedelta(days=2)))
    c.apply(ab("g1", exp=T0 + timedelta(days=10)))
    assert c.contains(KEY).antibody_id == "g1"
    c.apply(ab("g0", exp=T0 + timedelta(days=2)))
    assert c.contains(KEY).antibody_id == "g1"


def test_capacity_is_bounded_and_evicts_soonest_expiry():
    clock = Clock()
    c = InMemoryAntibodyCache(clock=clock, capacity=3)
    for i in range(5):
        c.apply(ab(f"i{i}", key=f"{i:064x}", exp=T0 + timedelta(days=1 + i)))
    assert c.size() <= 3
    assert c.contains(f"{4:064x}") is not None and c.contains(f"{0:064x}") is None


# ----------------------------------------------------------------- bootstrap / bloom / client
class FakeHub:
    def __init__(self, pages, bloom=None, fail=False):
        self.pages, self.bloom_snap, self.fail, self.calls = pages, bloom, fail, []

    async def exact_page(self, bank_id, since=None, limit=500):
        self.calls.append(since)
        if self.fail:
            raise ConnectionError("hub down")
        i = 0 if since is None else int(since)
        return self.pages[i]

    async def bloom(self, bank_id, etag=None):
        if self.fail:
            raise ConnectionError("hub down")
        return self.bloom_snap


def _item(i, key=None, exp=None):
    return {"antibody_id": f"b{i}", "kind": "mule_account", "key_hash": key or f"{i:064x}",
            "expires_at": (exp or T0 + timedelta(days=10)).isoformat()}  # fmt: skip


async def test_bootstrap_loads_all_pages(mk_cache):
    c, clock = mk_cache()
    hub = FakeHub([{"items": [_item(1), _item(2)], "next_cursor": "1"},
                   {"items": [_item(3)], "next_cursor": None}])  # fmt: skip
    n = await bootstrap(c, hub, "bank_a", clock=clock)
    assert n == 3 and hub.calls == [None, "1"]
    assert all(c.contains(f"{i:064x}") for i in (1, 2, 3))


async def test_bootstrap_does_not_resurrect_tombstoned_and_never_crashes(caplog):
    clock = Clock()
    c = InMemoryAntibodyCache(clock=clock)
    c.apply(ab("b1", key=f"{1:064x}", revoked=True, exp=T0 + timedelta(days=10)))
    hub = FakeHub([{"items": [_item(1)], "next_cursor": None}])
    await bootstrap(c, hub, "bank_a", clock=clock)
    assert c.contains(f"{1:064x}") is None
    look = AntibodyLookup(c)
    with caplog.at_level("WARNING", logger="txn_guard"):
        n = await bootstrap(c, FakeHub([], fail=True), "bank_a", clock=clock, stats=look.stats)
    assert n == 0 and look.stats["bootstrap_failed"] == 1
    assert any("antibody bootstrap failed" in r.getMessage() for r in caplog.records)
    assert KEY not in caplog.text  # no key hashes in logs


async def test_httpx_hub_client_sends_trust_headers_and_paginates():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.path, dict(req.url.params), req.headers))
        return httpx.Response(200, json={"items": [], "next_cursor": None})

    client = HttpxHubClient("http://hub", "bank_a", gateway_secret="s3cret",
                            transport=httpx.MockTransport(handler))  # fmt: skip
    await client.exact_page("bank_a", "abc", 50)
    path, params, headers = seen[0]
    assert path == "/antibodies/exact" and params == {
        "bank_id": "bank_a",
        "since": "abc",
        "limit": "50",
    }
    assert headers["x-principal-role"] == "bank" and headers["x-principal-bank"] == "bank_a"
    assert headers["x-gateway-secret"] == "s3cret"


async def test_bloom_prefilter_is_never_the_blocker():
    from hashlib import sha256

    def snap(items, m=8192, k=4):
        bits = bytearray(m // 8)
        for it in items:
            d = sha256(it.encode()).digest()
            h1, h2 = int.from_bytes(d[:8], "big"), int.from_bytes(d[8:16], "big") | 1
            for i in range(k):
                p = (h1 + i * h2) % m
                bits[p >> 3] |= 1 << (p & 7)
        return {"version": "v1", "n": 1024, "m": m, "k": k, "fp_rate": 1e-6, "count": len(items),
                "bits": base64.b64encode(bytes(bits)).decode(), "generated_at": T0.isoformat()}  # fmt: skip

    clock = Clock()
    c = InMemoryAntibodyCache(clock=clock)
    look = AntibodyLookup(c, use_bloom=True)
    c.apply(ab("x1", key=KEY))
    look.load_bloom(snap([KEY, KEY2]))  # KEY2 is in Bloom but NOT in the exact cache
    assert look.lookup(KEY).antibody_id == "x1"
    assert look.lookup(KEY2) is None  # Bloom hit alone never blocks
    assert look.stats["bloom_unconfirmed"] == 1
    late = "c" * 64
    c.apply(ab("x2", key=late))  # newer than the snapshot: negative Bloom must not hide it
    assert look.lookup(late).antibody_id == "x2"


# ------------------------------------------------------------------------- consumer
async def test_antibody_consumer_applies_and_malformed_goes_to_dlq():
    bus = InMemoryBus()
    cache = InMemoryAntibodyCache(clock=Clock())
    svc = _svc(Scorer(), bus, cache=cache)
    task = run_antibody_consumer(bus, svc, InMemoryIdempotencyStore(), "bank_x")
    await bus.publish(Topics.ANTIBODIES, KEY, ab())
    await bus.publish_raw(Topics.ANTIBODIES, "bad", b"{not json")
    for _ in range(300):
        if bus.messages(Topics.ANTIBODIES + Topics.DLQ_SUFFIX) and cache.contains(KEY):
            break
        await asyncio.sleep(0.01)
    task.cancel()
    assert cache.contains(KEY) is not None
    assert len(bus.messages(Topics.ANTIBODIES + Topics.DLQ_SUFFIX)) == 1
    assert ANTIBODY_GROUP_PREFIX.startswith("txn-guard")


# ------------------------------------------------------------------ service fixtures
def _warm(h, make_txn, payer):
    for i in range(40):
        h.record_txn(make_txn(amount=str(300 + (i * 37) % 400), payer=payer,
                              ts=T0 - timedelta(days=10) + timedelta(hours=i * 5)))  # fmt: skip


def _svc(scorer, bus, cache=None, history=None, holds=None, clock=None, use_cache=True, **kw):
    clock = clock or Clock()
    cache = cache if cache is not None else InMemoryAntibodyCache(clock=clock)
    return TxnGuardService(
        scorer, history or InMemoryHistoryStore(),
        holds or InMemoryHoldStore(audit=InMemoryAuditSink(), clock=clock), bus,
        InMemoryIdempotencyStore(), clock=clock,
        antibodies=AntibodyLookup(cache) if use_cache else None, **kw,
    )  # fmt: skip


def _decs(bus):
    return [TxnDecision.model_validate_json(r) for _, r in bus.messages(Topics.TXN_DECISIONS)]


@pytest.fixture(scope="module")
def scorer():
    return Scorer()


MULE = "m" * 64
AID = hashlib.sha256(b"ab-mule-1").hexdigest()


# ----------------------------------------------------------- the plan's acceptance test
async def test_published_antibody_blocks_other_bank_transfer_within_5_seconds(scorer, make_txn):
    bus = InMemoryBus()  # shared by both banks (and the hub's topic)
    clock = Clock()
    a = _svc(scorer, bus, history=InMemoryHistoryStore(), clock=clock)
    b = _svc(scorer, bus, history=InMemoryHistoryStore(), clock=clock)
    _warm(a.history, make_txn, "victim_a")
    _warm(b.history, make_txn, "victim_b")
    tasks = [run_antibody_consumer(bus, s, InMemoryIdempotencyStore(), bank)
             for s, bank in ((a, "bank_a"), (b, "bank_b"))]  # fmt: skip
    try:
        # bank_a: victim A pays the mule (young payee, large) and is held
        held = await a.handle_txn(make_txn(amount="95000", age=3, payee=MULE, payer="victim_a"))
        assert held.decision == "hold_verify"
        # control: bank_b has not heard of the mule; a modest transfer to an old payee is allowed
        control_txn = make_txn(amount="3000", age=900, payee=MULE, payer="victim_b")
        control = await b.handle_txn(control_txn)
        assert control.decision in ("allow", "step_up") and "ANTIBODY_MATCH" not in {
            r.code for r in control.reasons}  # fmt: skip
        # analyst at bank_a confirms; the hub publishes the antibody
        t_pub = time.perf_counter()
        await bus.publish(Topics.ANTIBODIES, MULE, ab(AID, key=MULE, exp=T0 + timedelta(days=14)))
        while b.antibodies.lookup(MULE) is None:
            assert time.perf_counter() - t_pub < 5.0, "antibody not applied at bank_b in 5 s"
            await asyncio.sleep(0.005)
        applied_in = time.perf_counter() - t_pub
        assert applied_in < 5.0
        # bank_b's next victim transfer to the same payee_hash is stopped
        second = await b.handle_txn(make_txn(amount="3000", age=900, payee=MULE, payer="victim_b2"))
        assert second.decision == "hold_verify"
        assert "ANTIBODY_MATCH" in {r.code for r in second.reasons} and second.score >= 0.9
        detail = next(r.detail for r in second.reasons if r.code == "ANTIBODY_MATCH")
        assert (
            AID[:8] in detail
            and AID not in detail
            and "analyst" not in detail
            and "bank_a" not in detail
        )
        assert MULE not in detail
    finally:
        for t in tasks:
            t.cancel()
    print(f"\nantibody applied at bank_b in {applied_in * 1000:.1f} ms")


# ------------------------------------------------------------ policy behaviour
async def test_established_payee_is_step_up_only(scorer, make_txn):
    bus = InMemoryBus()
    s = _svc(scorer, bus)
    _warm(s.history, make_txn, "loyal")
    for i in range(4):  # 4 payments over 12 days: established
        s.history.record_txn(make_txn(amount="900", payee="friendly", payer="loyal",
                                      ts=T0 - timedelta(days=12) + timedelta(days=3 * i)))  # fmt: skip
    s.antibodies.cache.apply(ab("wrong1", key="friendly", exp=T0 + timedelta(days=14)))
    d = await s.handle_txn(make_txn(amount="900", payee="friendly", payer="loyal"))
    codes = {r.code for r in d.reasons}
    assert d.decision == "step_up" and "ANTIBODY_MATCH_KNOWN_PAYEE" in codes
    assert "ANTIBODY_MATCH" not in codes
    s.antibodies.cache.apply(
        ab("wrong1", key="friendly", revoked=True, exp=T0 + timedelta(days=14))
    )
    d2 = await s.handle_txn(make_txn(amount="900", payee="friendly", payer="loyal"))
    assert d2.decision == "allow"  # tombstone stops it immediately


async def test_tombstone_stops_blocking_new_transactions_and_keeps_existing_holds(scorer, make_txn):
    bus = InMemoryBus()
    s = _svc(scorer, bus)
    _warm(s.history, make_txn, "p1")
    _warm(s.history, make_txn, "p2")
    s.antibodies.cache.apply(ab("m1", key=MULE, exp=T0 + timedelta(days=14)))
    first = await s.handle_txn(make_txn(amount="900", age=900, payee=MULE, payer="p1"))
    assert first.decision == "hold_verify"
    await s.handle_antibody(ab("m1", key=MULE, revoked=True, exp=T0 + timedelta(days=14)))
    assert (await s.holds.get(first.txn_id)).state == "open"  # humans resolve existing holds
    later = await s.handle_txn(make_txn(amount="900", age=900, payee=MULE, payer="p2"))
    assert later.decision == "allow"


async def test_expired_antibody_is_ignored_by_scoring(scorer, make_txn):
    clock = Clock()
    s = _svc(scorer, InMemoryBus(), clock=clock)
    _warm(s.history, make_txn, "p1")
    s.antibodies.cache.apply(ab("m1", key=MULE, exp=T0 + timedelta(hours=1)))
    clock.t = T0 + timedelta(hours=2)
    d = await s.handle_txn(make_txn(amount="900", age=900, payee=MULE, payer="p1",
                                    ts=T0 + timedelta(hours=2)))  # fmt: skip
    assert d.decision == "allow"


async def test_non_member_payee_decisions_unchanged_vs_no_cache(scorer, make_txn):
    txns = [make_txn(amount=a, age=g, payee=f"p{i}", payer="px", ts=T0 + timedelta(minutes=i))
            for i, (a, g) in enumerate([("450", 900), ("95000", 3), ("40000", 900), ("6000", 20)])]  # fmt: skip
    out = []
    for use_cache in (True, False):
        s = _svc(scorer, InMemoryBus(), use_cache=use_cache)
        _warm(s.history, make_txn, "px")
        out.append([(d.decision, d.score) for d in [await s.handle_txn(t) for t in txns]])
    assert out[0] == out[1]


# --------------------------------------------------------------- late antibody upgrade
async def test_late_antibody_upgrades_pending_allow_to_hold_with_label(scorer, make_txn):
    bus = InMemoryBus()
    s = _svc(scorer, bus)
    _warm(s.history, make_txn, "p1")
    t = make_txn(amount="900", age=900, payee=MULE, payer="p1", ts=T0 - timedelta(minutes=5))
    assert (await s.handle_txn(t)).decision == "allow"
    ev = ab("m1", key=MULE, exp=T0 + timedelta(days=14))
    ups = await s.handle_antibody(ev)
    assert [(u.decision, u.decision_seq) for u in ups] == [("hold_verify", 2)]
    codes = [r.code for r in ups[0].reasons]
    assert "ANTIBODY_MATCH" in codes and "LATE_ANTIBODY_POST_SETTLEMENT" in codes
    assert (await s.holds.get(t.txn_id)).decision == "hold_verify"
    assert await s.handle_antibody(ev) == []  # redelivery: no second upgrade
    assert [d.decision_seq for d in _decs(bus)] == [1, 2]


async def test_late_antibody_upgrades_step_up_without_settlement_label(scorer, make_txn):
    bus = InMemoryBus()
    s = _svc(scorer, bus)
    _warm(s.history, make_txn, "p1")
    t = make_txn(amount="6000", age=20, payee=MULE, payer="p1", ts=T0 - timedelta(minutes=2))
    first = await s.handle_txn(t)
    assert first.decision == "step_up"
    (up,) = await s.handle_antibody(ab("m1", key=MULE, exp=T0 + timedelta(days=14)))
    assert up.decision == "hold_verify" and up.decision_seq == 2
    assert "LATE_ANTIBODY_POST_SETTLEMENT" not in [r.code for r in up.reasons]


async def test_late_antibody_ignores_old_resolved_and_other_payees(scorer, make_txn):
    bus = InMemoryBus()
    s = _svc(scorer, bus)
    _warm(s.history, make_txn, "p1")
    old = make_txn(amount="900", age=900, payee=MULE, payer="p1", ts=T0 - timedelta(minutes=40))
    other = make_txn(
        amount="900", age=900, payee="elsewhere", payer="p1", ts=T0 - timedelta(minutes=3)
    )
    await s.handle_txn(old)
    await s.handle_txn(other)
    assert await s.handle_antibody(ab("m1", key=MULE, exp=T0 + timedelta(days=14))) == []
    assert len(_decs(bus)) == 2


async def test_late_antibody_never_downgrades_and_established_stays_put(scorer, make_txn):
    bus = InMemoryBus()
    s = _svc(scorer, bus)
    _warm(s.history, make_txn, "p1")
    t = make_txn(amount="95000", age=3, payee=MULE, payer="p1", ts=T0 - timedelta(minutes=1))
    assert (await s.handle_txn(t)).decision == "hold_verify"
    assert await s.handle_antibody(ab("m1", key=MULE, exp=T0 + timedelta(days=14))) == []
    assert (await s.holds.get(t.txn_id)).decision == "hold_verify"


# ------------------------------------------------------ two instances over run_consumers
async def test_run_consumers_unchanged_signature(scorer, make_txn):
    bus = InMemoryBus()
    s = _svc(scorer, bus)
    tasks = run_consumers(bus, s, InMemoryIdempotencyStore())
    assert len(tasks) >= 2
    for t in tasks:
        t.cancel()


async def test_metrics_export_antibody_stats_and_admin_rebootstrap(monkeypatch, scorer):
    from txn_guard.api import create_app

    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "1")
    monkeypatch.delenv("GATEWAY_SHARED_SECRET", raising=False)
    s = _svc(scorer, InMemoryBus())
    hub = FakeHub([{"items": [_item(1)], "next_cursor": None}])

    async def boot() -> int:
        return await bootstrap(
            s.antibodies.cache, hub, "bank_a", clock=Clock(), stats=s.antibodies.stats
        )

    app = create_app(service=s, bootstrap_fn=boot, sweep_interval_s=0)
    h = {"X-Principal-Role": "admin", "X-Principal-Sub": "root"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.post("/admin/antibodies/bootstrap", headers=h)).json() == {"loaded": 1}
        text = (await c.get("/metrics", headers=h)).text
        analyst = await c.post(
            "/admin/antibodies/bootstrap", headers=h | {"X-Principal-Role": "analyst"}
        )
    assert (
        "txn_guard_antibody_cache_size 1" in text
        and "txn_guard_antibody_bootstrap_failed 0" in text
    )
    assert analyst.status_code == 403
