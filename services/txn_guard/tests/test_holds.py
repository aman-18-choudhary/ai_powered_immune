"""HoldStore, audit sink and HTTP API."""

import json
from datetime import UTC, datetime, timedelta

import fakeredis.aioredis
import httpx
import pytest
from scam_contracts.models import LedgerEntryIn, Reason
from scam_contracts.topics import Topics
from svckit.bus import InMemoryBus

from txn_guard.api import create_app
from txn_guard.holds import (
    ActorRequired,
    BusAuditSink,
    HoldConflict,
    HoldNotFound,
    InMemoryAuditSink,
    InMemoryHoldStore,
    RedisHoldStore,
)

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
REASONS = [Reason(code="NEW_PAYEE", weight=0.2, detail="First payment to this payee")]


class Clock:
    def __init__(self) -> None:
        self.t = NOW

    def __call__(self) -> datetime:
        return self.t


@pytest.fixture(params=["memory", "redis"])
def make_store(request):
    def _make(audit=None, clock=None):
        audit = audit if audit is not None else InMemoryAuditSink()
        clock = clock or Clock()
        if request.param == "memory":
            return InMemoryHoldStore(audit=audit, clock=clock), audit, clock
        r = fakeredis.aioredis.FakeRedis()
        return RedisHoldStore(r, audit=audit, clock=clock), audit, clock

    return _make


async def _create(store, txn_id="t1", decision="hold_verify", payer="payer_1", deadline_s=120):
    return await store.create(
        txn_id, decision, REASONS, NOW + timedelta(seconds=deadline_s),
        payer_token=payer, score=0.9, model_version="gbm-v1",
    )  # fmt: skip


async def test_create_is_idempotent_one_hold(make_store):
    store, audit, _ = make_store()
    a = await _create(store)
    b = await _create(store)
    assert a.txn_id == b.txn_id and len(await store.list_open()) == 1
    assert [e["event_type"] for e in audit.events] == ["hold.created"]


async def test_resolve_requires_actor(make_store):
    store, audit, _ = make_store()
    await _create(store)
    for bad in ("", "   "):
        with pytest.raises(ActorRequired):
            await store.resolve("t1", "release", bad)
    assert (await store.get("t1")).state == "open"
    assert [e["event_type"] for e in audit.events] == ["hold.created"]


async def test_resolve_records_resolver_and_is_idempotent(make_store):
    store, audit, clock = make_store()
    await _create(store)
    clock.t = NOW + timedelta(seconds=30)
    h = await store.resolve("t1", "release", "analyst-7")
    assert h.state == "released" and h.resolved_by == "analyst-7" and h.resolved_at == clock.t
    clock.t = NOW + timedelta(seconds=99)
    again = await store.resolve("t1", "release", "analyst-9")
    assert again.resolved_by == "analyst-7" and again.resolved_at == NOW + timedelta(seconds=30)
    assert [e["event_type"] for e in audit.events] == ["hold.created", "hold.resolved"]
    assert await store.list_open() == []


async def test_conflicting_second_action_rejected(make_store):
    store, _, _ = make_store()
    await _create(store)
    await store.resolve("t1", "confirm_block", "analyst-7")
    with pytest.raises(HoldConflict):
        await store.resolve("t1", "release", "analyst-8")
    assert (await store.get("t1")).state == "blocked"
    with pytest.raises(HoldNotFound):
        await store.resolve("nope", "release", "analyst-8")


async def test_list_open_sorted_by_deadline_and_overdue_stays_open(make_store):
    store, _, clock = make_store()
    await _create(store, "late", deadline_s=300)
    await _create(store, "soon", deadline_s=60)
    await _create(store, "mid", deadline_s=120)
    assert [h.txn_id for h in await store.list_open()] == ["soon", "mid", "late"]
    clock.t = NOW + timedelta(seconds=200)  # past two deadlines: never auto-released/blocked
    open_ = await store.list_open()
    assert [h.txn_id for h in open_] == ["soon", "mid", "late"]
    assert [store.is_overdue(h) for h in open_] == [True, True, False]


async def test_upgrade_never_downgrades_and_bumps_seq(make_store):
    store, audit, _ = make_store()
    await _create(store, decision="step_up")
    up = await store.upgrade("t1", "hold_verify", REASONS, 0.95, "gbm-v1", 2)
    assert up is not None and up.decision == "hold_verify" and up.decision_seq == 2
    assert await store.upgrade("t1", "step_up", REASONS, 0.6, "gbm-v1", 3) is None
    assert (await store.get("t1")).decision == "hold_verify"
    assert [e["event_type"] for e in audit.events] == ["hold.created", "hold.upgraded"]


async def test_audit_payloads_have_no_raw_pii(make_store):
    store, audit, _ = make_store()
    await _create(store, payer="payer_secret_tok")
    await store.resolve("t1", "release", "analyst-7")
    blob = json.dumps(audit.events)
    assert "payer_secret_tok" not in blob and "First payment" not in blob
    assert all(set(e) <= {"event_type", "txn_id", "decision", "decision_seq", "score", "actor",
                          "model_version", "reason_codes", "payload_hash"} for e in audit.events)  # fmt: skip


async def test_bus_audit_sink_publishes_ledger_entry_in():
    bus = InMemoryBus()
    store = InMemoryHoldStore(audit=BusAuditSink(bus), clock=Clock())
    await _create(store)
    await store.resolve("t1", "confirm_block", "analyst-7")
    msgs = bus.messages(Topics.LEDGER)
    entries = [LedgerEntryIn.model_validate_json(raw) for _, raw in msgs]
    assert [e.event_type for e in entries] == ["hold.created", "hold.resolved"]
    assert all(e.service == "txn-guard" and len(e.payload_hash) == 64 for e in entries)
    assert entries[1].actor == "analyst-7" and entries[0].model_version == "gbm-v1"


# ------------------------------------------------------------------------------------- API
def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def H(role, sub="u1"):
    return {"X-Principal-Role": role, "X-Principal-Sub": sub}


@pytest.fixture
async def api(monkeypatch):
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "1")
    clock = Clock()
    store = InMemoryHoldStore(audit=InMemoryAuditSink(), clock=clock)
    await _create(store, "t_hold", "hold_verify", "payer_1", 60)
    await _create(store, "t_step", "step_up", "payer_2", 30)
    app = create_app(holds=store, clock=clock)
    async with _client(app) as c:
        c.store, c.clock = store, clock  # type: ignore[attr-defined]
        yield c


async def test_headers_rejected_unless_trusted(monkeypatch):
    monkeypatch.delenv("TRUST_GATEWAY_HEADERS", raising=False)
    monkeypatch.delenv("GATEWAY_SHARED_SECRET", raising=False)
    async with _client(create_app(holds=InMemoryHoldStore(audit=InMemoryAuditSink()))) as c:
        assert (await c.get("/holds", headers=H("analyst"))).status_code == 401
        assert (await c.get("/healthz")).status_code == 200


async def test_shared_secret_enables_trust(monkeypatch):
    monkeypatch.delenv("TRUST_GATEWAY_HEADERS", raising=False)
    monkeypatch.setenv("GATEWAY_SHARED_SECRET", "s3cret-s3cret-s3cret-s3cret-0000")
    async with _client(create_app(holds=InMemoryHoldStore(audit=InMemoryAuditSink()))) as c:
        assert (await c.get("/holds", headers=H("analyst"))).status_code == 401
        bad = H("analyst") | {"X-Gateway-Secret": "wrong"}
        assert (await c.get("/holds", headers=bad)).status_code == 401
        ok = H("analyst") | {"X-Gateway-Secret": "s3cret-s3cret-s3cret-s3cret-0000"}
        assert (await c.get("/holds", headers=ok)).status_code == 200


async def test_get_holds_roles_and_order(api):
    for role in ("analyst", "officer", "admin"):
        r = await api.get("/holds", headers=H(role))
        assert r.status_code == 200
        assert [h["txn_id"] for h in r.json()] == ["t_step", "t_hold"]
    assert (await api.get("/holds", headers=H("citizen"))).status_code == 403
    assert (await api.get("/holds")).status_code == 401


async def test_overdue_flag_and_metric_not_auto_resolved(api):
    api.clock.t = NOW + timedelta(seconds=45)
    body = (await api.get("/holds", headers=H("analyst"))).json()
    assert {h["txn_id"]: h["overdue"] for h in body} == {"t_step": True, "t_hold": False}
    assert all(h["state"] == "open" for h in body)
    assert "txn_guard_holds_overdue 1" in (await api.get("/metrics", headers=H("analyst"))).text


async def test_get_one_hold_and_404(api):
    r = await api.get("/holds/t_hold", headers=H("officer"))
    assert r.status_code == 200 and r.json()["decision"] == "hold_verify"
    assert (await api.get("/holds/zzz", headers=H("officer"))).status_code == 404


async def test_resolve_roles_actor_and_conflict(api):
    url = "/holds/t_hold/resolve"
    assert (
        await api.post(url, json={"action": "release"}, headers=H("officer"))
    ).status_code == 403
    assert (await api.post(url, json={"action": "release"}, headers=H("citizen", "payer_1"))
            ).status_code == 403  # fmt: skip
    r = await api.post(url, json={"action": "confirm_block"}, headers=H("analyst", "an-1"))
    assert r.status_code == 200 and r.json()["resolved_by"] == "an-1"
    again = await api.post(url, json={"action": "confirm_block"}, headers=H("admin", "ad-1"))
    assert again.status_code == 200 and again.json()["resolved_by"] == "an-1"
    clash = await api.post(url, json={"action": "release"}, headers=H("admin", "ad-1"))
    assert clash.status_code == 409
    bad = await api.post(url, json={"action": "explode"}, headers=H("admin", "ad-1"))
    assert bad.status_code == 422
    blank = await api.post("/holds/t_step/resolve", json={"action": "release"},
                           headers=H("analyst", "  "))  # fmt: skip
    assert blank.status_code in (400, 401, 403)


async def test_citizen_verifies_only_own_step_up(api):
    ok = await api.post("/holds/t_step/verify", headers=H("citizen", "payer_2"))
    assert ok.status_code == 200 and ok.json()["state"] == "released"
    assert ok.json()["resolved_by"] == "payer_2"
    assert (
        await api.post("/holds/t_step/verify", headers=H("citizen", "payer_2"))
    ).status_code == 200
    other = await api.post("/holds/t_hold/verify", headers=H("citizen", "payer_9"))
    assert other.status_code == 403
    own_hold = await api.post("/holds/t_hold/verify", headers=H("citizen", "payer_1"))
    assert own_hold.status_code == 403  # a hold_verify needs an analyst, not self-verification
    assert (await api.post("/holds/t_hold/verify", headers=H("analyst"))).status_code == 403
