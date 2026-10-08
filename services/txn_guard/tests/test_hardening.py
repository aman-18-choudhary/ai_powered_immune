"""Task 9 fix round 1: outbox audit, durable replay, per-payer serialisation, atomic Redis
history, TOCTOU, late-upgrade labelling, API hardening."""

import asyncio
import time
from datetime import UTC, datetime, timedelta

import fakeredis
import fakeredis.aioredis
import httpx
import pytest
from scam_contracts.models import CallRisk, Reason, TxnDecision
from scam_contracts.topics import Topics
from svckit.bus import InMemoryBus
from svckit.idempotency import InMemoryIdempotencyStore

from txn_guard.api import create_app
from txn_guard.features import extract_features
from txn_guard.history import InMemoryHistoryStore
from txn_guard.holds import (
    HoldConflict,
    InMemoryAuditSink,
    InMemoryHoldStore,
    RedisHoldStore,
)
from txn_guard.model import Scorer
from txn_guard.pending import InMemoryPendingStore
from txn_guard.redis_history import RedisHistoryStore
from txn_guard.service import TxnGuardService

from .conftest import T0

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
R = [Reason(code="NEW_PAYEE", weight=0.2, detail="First payment to this payee")]


class Clock:
    def __init__(self):
        self.t = NOW

    def __call__(self):
        return self.t


class FlakyAudit(InMemoryAuditSink):
    def __init__(self):
        super().__init__()
        self.fail = 0

    async def emit(self, entry):
        if self.fail > 0:
            self.fail -= 1
            raise ConnectionError("ledger down")
        await super().emit(entry)


@pytest.fixture(scope="module")
def scorer():
    return Scorer()


@pytest.fixture(params=["memory", "redis"])
def mk_store(request):
    def _make(audit, clock=None, shared=None):
        clock = clock or Clock()
        if request.param == "memory":
            return InMemoryHoldStore(audit=audit, clock=clock)
        return RedisHoldStore(shared or fakeredis.aioredis.FakeRedis(), audit=audit, clock=clock)

    return _make


def _types(a):
    return [e["event_type"] for e in a.events]


async def _create(store, tid="t1", decision="step_up", **kw):
    return await store.create(tid, decision, R, NOW + timedelta(seconds=60),
                              payer_token="payer_1", score=0.7, model_version="gbm-v1", **kw)  # fmt: skip


# ---------------------------------------------------------------- I1 outbox audit
async def test_audit_lost_on_create_is_delivered_once_after_retry(mk_store):
    a = FlakyAudit()
    store = mk_store(a)
    a.fail = 1
    await _create(store)  # sink fails once: state is durable, audit stays in the outbox
    assert _types(a) == [] and (await store.get("t1")).audit_pending
    await _create(store)  # idempotent re-entry drains
    await store.drain_audit("t1")
    assert _types(a) == ["hold.created"] and not (await store.get("t1")).audit_pending


async def test_audit_lost_on_resolve_is_delivered_once(mk_store):
    a = FlakyAudit()
    store = mk_store(a)
    await _create(store)
    a.fail = 1
    h = await store.resolve("t1", "release", "analyst-1")
    assert h.state == "released" and _types(a) == ["hold.created"]
    await store.resolve("t1", "release", "analyst-1")  # repeated call drains
    await store.drain_audit("t1")
    assert _types(a) == ["hold.created", "hold.resolved"]


async def test_audit_lost_on_upgrade_is_delivered_once(mk_store):
    a = FlakyAudit()
    store = mk_store(a)
    await _create(store)
    a.fail = 1
    assert await store.upgrade("t1", "hold_verify", R, 0.95, "gbm-v1", 2) is not None
    await store.drain_audit("t1")
    await store.drain_audit("t1")
    assert _types(a) == ["hold.created", "hold.upgraded"]


async def test_overdue_sweep_emits_once_durably_and_counts(mk_store):
    a, clock = FlakyAudit(), Clock()
    redis = fakeredis.aioredis.FakeRedis()
    store = mk_store(a, clock, shared=redis)
    await _create(store)
    clock.t = NOW + timedelta(seconds=90)
    a.fail = 1  # first sweep: marker + outbox persisted, delivery fails
    n1 = await store.sweep()
    n2 = await store.sweep()  # drains the pending overdue entry, does not re-flag
    n3 = await store.sweep()
    assert (n1, n2, n3) == (1, 0, 0)
    assert _types(a).count("hold.overdue") == 1
    h = await store.get("t1")
    assert h.state == "open" and h.overdue_flagged  # never auto-resolved
    assert await store.overdue_total() == 1
    assert store.is_overdue(h)


async def test_audit_hash_binds_txn_decision_seq_score_actor_model():
    a = InMemoryAuditSink()
    store = InMemoryHoldStore(audit=a, clock=Clock())
    await _create(store)
    await store.upgrade("t1", "hold_verify", R, 0.95, "gbm-v1", 2)
    await store.resolve("t1", "confirm_block", "analyst-1")
    hashes = [e["payload_hash"] for e in a.events]
    assert len(set(hashes)) == 3 and all(len(h) == 64 for h in hashes)
    up = a.events[1]
    assert up["decision_seq"] == 2 and up["score"] == 0.95 and up["txn_id"] == "t1"
    # deterministic: same inputs -> same hash
    b = InMemoryAuditSink()
    store2 = InMemoryHoldStore(audit=b, clock=Clock())
    await _create(store2)
    assert b.events[0]["payload_hash"] == a.events[0]["payload_hash"]


async def test_list_open_limit_and_resolved_key_gets_ttl():
    redis = fakeredis.aioredis.FakeRedis()
    store = RedisHoldStore(redis, audit=InMemoryAuditSink(), clock=Clock())
    for i in range(5):
        await store.create(f"t{i}", "step_up", R, NOW + timedelta(seconds=10 * (5 - i)),
                           payer_token="p")  # fmt: skip
    assert [h.txn_id for h in await store.list_open(limit=2)] == ["t4", "t3"]
    await store.resolve("t4", "release", "a")
    assert 0 < await redis.ttl(store.KEY + "t4") <= 90 * 86400
    assert await redis.ttl(store.KEY + "t3") == -1  # open holds never expire
    assert await store.count_open() == 4


# ---------------------------------------------------------------- I5 TOCTOU
async def test_resolve_enforces_expected_decision_and_seq(mk_store):
    store = mk_store(InMemoryAuditSink())
    await _create(store)
    await store.upgrade("t1", "hold_verify", R, 0.95, "gbm-v1", 2)
    with pytest.raises(HoldConflict):
        await store.resolve("t1", "release", "citizen", expect_decision="step_up", expect_seq=1)
    with pytest.raises(HoldConflict):
        await store.resolve("t1", "release", "analyst", expect_seq=1)
    assert (await store.get("t1")).state == "open"
    ok = await store.resolve("t1", "release", "analyst", expect_seq=2)
    assert ok.state == "released"


# ---------------------------------------------------------------- service fixtures
def warm(h, make_txn, payer="payer_1"):
    for i in range(40):
        h.record_txn(make_txn(amount=str(300 + (i * 37) % 400), payer=payer,
                              ts=T0 - timedelta(days=10) + timedelta(hours=i * 5)))  # fmt: skip


def risk(score=0.9, ts=None, payer="payer_1", cid="c1"):
    return CallRisk(call_id=cid, victim_token=payer, score=score, reasons=[], model_version="x",
                    ts=ts or T0 - timedelta(minutes=2))  # fmt: skip


def decs(bus):
    return [TxnDecision.model_validate_json(r) for _, r in bus.messages(Topics.TXN_DECISIONS)]


def svc_for(scorer, make_txn, history=None, holds=None, bus=None, pending=None, idem=None, **kw):
    history = history or InMemoryHistoryStore()
    if not getattr(history, "_warmed", False):
        warm(history, make_txn)
        history._warmed = True
    holds = holds or InMemoryHoldStore(audit=InMemoryAuditSink(), clock=Clock())
    bus = bus or InMemoryBus()
    return TxnGuardService(scorer, history, holds, bus, idem or InMemoryIdempotencyStore(),
                           pending=pending or InMemoryPendingStore(), **kw), bus, holds, history  # fmt: skip


# ---------------------------------------------------------------- I2 durable replay
async def test_restart_replay_republishes_identical_bytes_never_a_new_score(scorer, make_txn):
    svc, bus, holds, history = svc_for(scorer, make_txn)
    t = make_txn(amount="95000", age=3, payee="p_mule")
    first = await svc.handle_txn(t)
    svc2 = TxnGuardService(
        scorer, history, holds, bus, InMemoryIdempotencyStore(), pending=svc.pending
    )  # fmt: skip  (fresh idempotency store)
    again = await svc2.handle_txn(t)
    raws = [raw for _, raw in bus.messages(Topics.TXN_DECISIONS)]
    assert len(set(raws)) == 1  # identical bytes only (consumers dedupe on txn_id + seq)
    assert again.score == first.score and again.decision == first.decision
    assert len(await holds.list_open()) == 1


async def test_replay_of_allow_after_idem_loss_same_score(scorer, make_txn):
    svc, bus, holds, history = svc_for(scorer, make_txn)
    t = make_txn(amount="450")
    a = await svc.handle_txn(t)
    svc2 = TxnGuardService(scorer, history, holds, bus, InMemoryIdempotencyStore(),
                           pending=svc.pending)  # fmt: skip
    b = await svc2.handle_txn(t)
    assert (a.decision, a.score) == (b.decision, b.score)
    assert len({raw for _, raw in bus.messages(Topics.TXN_DECISIONS)}) == 1


# ---------------------------------------------------------------- I3 / I3b
class RecordingHistory(InMemoryHistoryStore):
    blocking = True

    def __init__(self):
        super().__init__()
        self.log: list[tuple[str, str]] = []

    def context_for(self, txn, now=None):
        self.log.append(("ctx-start", txn.txn_id))
        c = super().context_for(txn, now)  # snapshot first, then the slow "round trip"
        time.sleep(0.03)
        self.log.append(("ctx-end", txn.txn_id))
        return c

    def record_txn(self, txn):
        self.log.append(("record", txn.txn_id))
        super().record_txn(txn)


async def test_same_payer_processing_is_serialised(scorer, make_txn):
    h = RecordingHistory()
    svc, bus, holds, _ = svc_for(scorer, make_txn, history=h)
    a = make_txn(amount="30000", payee="p_new", age=5, ts=T0)
    b = make_txn(amount="30000", payee="p_new", age=5, ts=T0 + timedelta(minutes=2))
    other = make_txn(amount="500", payer="payer_2", ts=T0)
    warm(h, make_txn, "payer_2")
    h.log.clear()
    await asyncio.gather(svc.handle_txn(a), svc.handle_txn(b), svc.handle_txn(other))
    mine = [e for e in h.log if e[1] in (a.txn_id, b.txn_id)]

    def one(t):  # the last ctx pair is the gap re-check
        return [("ctx-start", t), ("ctx-end", t), ("record", t), ("ctx-start", t), ("ctx-end", t)]

    assert mine == one(a.txn_id) + one(b.txn_id)  # no interleaving of the same payer
    assert len(svc._locks) <= svc.MAX_LOCKS


async def test_lock_dict_is_bounded(scorer, make_txn):
    svc, *_ = svc_for(scorer, make_txn)
    svc.MAX_LOCKS = 8
    for i in range(50):
        async with svc._lock(f"payer_{i}"):
            pass
    assert len(svc._locks) <= 8


async def test_concurrent_txn_and_risk_same_instance_serialised(scorer, make_txn):
    h = RecordingHistory()
    svc, bus, holds, _ = svc_for(scorer, make_txn, history=h)
    t = make_txn(amount="40000")
    await asyncio.gather(svc.handle_txn(t), _late(svc))
    final = max((d for d in decs(bus) if d.txn_id == t.txn_id), key=lambda d: d.decision_seq)
    assert final.decision == "step_up"


async def _late(svc):
    await asyncio.sleep(0.01)
    await svc.handle_call_risk(risk())


async def test_cross_instance_gap_race_is_rescored_after_pending_add(scorer, make_txn):
    """Instance A reads the context before the risk is recorded; instance B records the risk and
    scans pending (still empty). A must re-read after pending.add and upgrade."""
    h = RecordingHistory()
    pending, holds, bus = (
        InMemoryPendingStore(),
        InMemoryHoldStore(audit=InMemoryAuditSink()),
        InMemoryBus(),
    )
    a, *_ = svc_for(scorer, make_txn, history=h, holds=holds, bus=bus, pending=pending)
    b = TxnGuardService(scorer, h, holds, bus, InMemoryIdempotencyStore(), pending=pending)
    t = make_txn(amount="40000")
    task = asyncio.create_task(a.handle_txn(t))
    await asyncio.sleep(0.01)  # A is inside context_for (risk not visible yet)
    ups = await b.handle_call_risk(risk())
    await task
    assert ups == []  # B saw nothing pending
    final = max((d for d in decs(bus) if d.txn_id == t.txn_id), key=lambda d: d.decision_seq)
    assert final.decision == "step_up" and final.decision_seq == 2
    assert (await holds.get(t.txn_id)).decision == "step_up"


# ---------------------------------------------------------------- I6 / M5
async def test_post_settlement_upgrade_is_labelled(scorer, make_txn):
    svc, bus, holds, _ = svc_for(scorer, make_txn)
    t = make_txn(amount="40000")
    assert (await svc.handle_txn(t)).decision == "allow"
    (up,) = await svc.handle_call_risk(risk())
    codes = [r.code for r in up.reasons]
    assert "LATE_CALL_RISK_POST_SETTLEMENT" in codes
    assert "LATE_CALL_RISK_POST_SETTLEMENT" in [r.code for r in (await holds.get(t.txn_id)).reasons]
    det = next(r for r in up.reasons if r.code == "LATE_CALL_RISK_POST_SETTLEMENT").detail
    assert "already" in det and "recall" in det
    audit = holds._audit.events
    assert "LATE_CALL_RISK_POST_SETTLEMENT" in audit[-1]["reason_codes"]
    # a step_up -> hold_verify upgrade of a still-open hold is not post-settlement
    t2 = make_txn(amount="6000", age=20, payee="p_young", ts=T0 + timedelta(hours=2))
    d2 = await svc.handle_txn(t2)
    assert d2.decision == "step_up"
    (up2,) = await svc.handle_call_risk(risk(0.95, ts=t2.ts + timedelta(minutes=1), cid="c9"))
    assert up2.decision == "hold_verify"
    assert "LATE_CALL_RISK_POST_SETTLEMENT" not in [r.code for r in up2.reasons]


@pytest.mark.parametrize("delta_min", [-2, 5, 14])
async def test_risk_ts_before_or_after_txn_same_rule_both_orders(delta_min, scorer, make_txn):
    t = make_txn(amount="99999", age=1200, payee="p_far")
    r = risk(0.9, ts=t.ts + timedelta(minutes=delta_min))
    finals = {}
    for order in ("risk_first", "txn_first"):
        svc, bus, *_ = svc_for(scorer, make_txn)
        if order == "risk_first":
            await svc.handle_call_risk(r)
            await svc.handle_txn(t)
        else:
            await svc.handle_txn(t)
            await svc.handle_call_risk(r)
        finals[order] = max(decs(bus), key=lambda d: d.decision_seq).decision
    assert finals["risk_first"] == finals["txn_first"] == "hold_verify"
    far = risk(0.9, ts=t.ts + timedelta(minutes=16), cid="far")
    svc, bus, *_ = svc_for(scorer, make_txn)
    await svc.handle_txn(t)
    assert await svc.handle_call_risk(far) == []  # outside the shared 15-minute window


def test_features_window_matches_late_path(make_txn):
    from txn_guard.history import Context

    t = make_txn()
    for dm, expected in ((-14, 0.9), (14, 0.9), (-16, 0.0), (16, 0.0)):
        ctx = Context(now=T0, call_risks=((t.ts + timedelta(minutes=dm), 0.9),))
        assert extract_features(t, ctx)["active_call_risk"] == expected


# ---------------------------------------------------------------- I4 atomic Redis history
def test_redis_record_txn_failure_mid_update_then_retry_equals_clean_run(make_txn):
    def feed(store, boom=False):
        for i in range(3):
            store.record_txn(make_txn(amount="500", ts=T0 - timedelta(hours=3 - i)))
        t = make_txn(amount="900", payee="p9", ts=T0)
        if boom:
            r = store._r
            real = r.evalsha, r.eval

            def fail(*a, **k):
                r.evalsha, r.eval = real
                raise ConnectionError("blip")

            r.evalsha = r.eval = fail
            with pytest.raises(ConnectionError):
                store.record_txn(t)
            r.evalsha, r.eval = real
        store.record_txn(t)
        return store.context_for(make_txn(payee="p9", ts=T0 + timedelta(minutes=1)), T0)

    clean = feed(RedisHistoryStore(fakeredis.FakeRedis()))
    retried = feed(RedisHistoryStore(fakeredis.FakeRedis()), boom=True)
    assert retried == clean
    assert (retried.payer_n, retried.payee_n, len(retried.recent)) == (4, 1, 4)


# ---------------------------------------------------------------- API hardening
def H(role, sub="u1"):
    return {"X-Principal-Role": role, "X-Principal-Sub": sub}


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                             base_url="http://t")  # fmt: skip


@pytest.fixture
def trust(monkeypatch):
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "1")
    monkeypatch.delenv("GATEWAY_SHARED_SECRET", raising=False)
    monkeypatch.delenv("METRICS_TOKEN", raising=False)


async def test_verify_loses_race_with_upgrade(trust):
    holds = InMemoryHoldStore(audit=InMemoryAuditSink(), clock=Clock())
    await holds.create("tA", "step_up", R, NOW + timedelta(hours=1), payer_token="alice")
    orig = holds.get
    fired = {"v": False}

    async def get_then_upgrade(tid):
        h = await orig(tid)
        if tid == "tA" and not fired["v"] and h and h.decision == "step_up":
            fired["v"] = True
            await holds.upgrade("tA", "hold_verify", R, 0.95, "gbm-v1", 2)
        return h

    holds.get = get_then_upgrade  # type: ignore[method-assign]
    async with _client(create_app(holds=holds)) as c:
        r = await c.post("/holds/tA/verify", headers=H("citizen", "alice"))
    assert r.status_code == 409
    cur = await orig("tA")
    assert cur.state == "open" and cur.decision == "hold_verify"


async def test_resolve_endpoint_enforces_decision_seq_when_given(trust):
    holds = InMemoryHoldStore(audit=InMemoryAuditSink(), clock=Clock())
    await holds.create("tA", "step_up", R, NOW + timedelta(hours=1), payer_token="alice")
    await holds.upgrade("tA", "hold_verify", R, 0.95, "gbm-v1", 2)
    async with _client(create_app(holds=holds)) as c:
        bad = await c.post("/holds/tA/resolve", json={"action": "release", "decision_seq": 1},
                           headers=H("analyst", "a1"))  # fmt: skip
        ok = await c.post("/holds/tA/resolve", json={"action": "release", "decision_seq": 2},
                          headers=H("analyst", "a1"))  # fmt: skip
    assert bad.status_code == 409 and ok.status_code == 200


async def test_non_ascii_gateway_secret_is_401_not_500(monkeypatch):
    monkeypatch.delenv("TRUST_GATEWAY_HEADERS", raising=False)
    monkeypatch.setenv("GATEWAY_SHARED_SECRET", "s3cret-s3cret-s3cret-s3cret-0000")
    async with _client(create_app(holds=InMemoryHoldStore(audit=InMemoryAuditSink()))) as c:
        r = await c.get(
            "/holds", headers=H("admin") | {"X-Gateway-Secret": "é-secret".encode("latin-1")}
        )
        assert r.status_code == 401


async def test_citizen_other_or_missing_hold_both_404(trust):
    holds = InMemoryHoldStore(audit=InMemoryAuditSink(), clock=Clock())
    await holds.create("tA", "step_up", R, NOW + timedelta(hours=1), payer_token="alice")
    async with _client(create_app(holds=holds)) as c:
        other = await c.get("/holds/tA", headers=H("citizen", "mallory"))
        missing = await c.get("/holds/zzz", headers=H("citizen", "mallory"))
        own = await c.get("/holds/tA", headers=H("citizen", "alice"))
    assert (other.status_code, missing.status_code, own.status_code) == (404, 404, 200)


async def test_holds_limit_param(trust):
    holds = InMemoryHoldStore(audit=InMemoryAuditSink(), clock=Clock())
    for i in range(5):
        await holds.create(f"t{i}", "step_up", R, NOW + timedelta(seconds=i + 1), payer_token="p")
    async with _client(create_app(holds=holds)) as c:
        two = await c.get("/holds?limit=2", headers=H("analyst"))
        huge = await c.get("/holds?limit=100000", headers=H("analyst"))
    assert [h["txn_id"] for h in two.json()] == ["t0", "t1"]
    assert huge.status_code == 422 or len(huge.json()) == 5


async def test_metrics_not_world_readable(monkeypatch):
    monkeypatch.delenv("TRUST_GATEWAY_HEADERS", raising=False)
    monkeypatch.delenv("GATEWAY_SHARED_SECRET", raising=False)
    monkeypatch.setenv("METRICS_TOKEN", "tok-123")
    async with _client(create_app(holds=InMemoryHoldStore(audit=InMemoryAuditSink()))) as c:
        assert (await c.get("/metrics")).status_code == 401
        assert (await c.get("/metrics", headers={"X-Metrics-Token": "nope"})).status_code == 401
        ok = await c.get("/metrics", headers={"X-Metrics-Token": "tok-123"})
        assert ok.status_code == 200 and "txn_guard_holds_overdue" in ok.text
        assert (await c.get("/healthz")).status_code == 200


async def test_lifespan_closes_clients_and_runs_sweeper(trust):
    closed = []

    async def closer():
        closed.append(1)

    a = InMemoryAuditSink()
    holds = InMemoryHoldStore(audit=a, clock=Clock())
    await holds.create("tA", "step_up", R, NOW + timedelta(seconds=10), payer_token="alice")
    clock = holds._clock
    clock.t = NOW + timedelta(seconds=30)
    app = create_app(holds=holds, closers=[closer], sweep_interval_s=0.01)
    async with app.router.lifespan_context(app):
        for _ in range(100):
            if "hold.overdue" in [e["event_type"] for e in a.events]:
                break
            await asyncio.sleep(0.01)
    assert closed == [1]
    assert [e["event_type"] for e in a.events].count("hold.overdue") == 1
