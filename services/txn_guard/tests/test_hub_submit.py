"""Confirm-to-hub: a ``confirm_block`` resolve durably queues an antibody submission that a
background submitter delivers to the hub at-least-once; the resolve never waits for the hub."""

import asyncio
import json
import logging
import re
import time
from datetime import UTC, datetime, timedelta

import fakeredis
import fakeredis.aioredis
import httpx
import pytest
from scam_contracts.models import Reason

from txn_guard.antibody_cache import HttpxHubClient
from txn_guard.api import create_app
from txn_guard.holds import (
    HoldConflict,
    InMemoryAuditSink,
    InMemoryHoldStore,
    RedisHoldStore,
)
from txn_guard.hub_submit import (
    HubSubmitConfig,
    evidence_ref_for,
    hub_config_from_env,
)

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
R = [Reason(code="NEW_PAYEE", weight=0.2, detail="First payment to this payee")]
KEY = "ab" * 32
SECRET = "s3cr3t-hub-gateway-value-XYZ"
RRN = "402312345678"


class Clock:
    def __init__(self):
        self.t = NOW

    def __call__(self):
        return self.t


class Mono:
    """Injected monotonic clock for backoff and the breaker."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeHub:
    """An httpx MockTransport handler: scripted statuses, then ``default``."""

    def __init__(self, default=201, script=None, sleep=0.0, raise_exc=None):
        self.default, self.script = default, list(script or [])
        self.sleep, self.raise_exc = sleep, raise_exc
        self.calls: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.sleep:
            await asyncio.sleep(self.sleep)
        if self.raise_exc is not None:
            raise self.raise_exc
        status = self.script.pop(0) if self.script else self.default
        return httpx.Response(status, json={"antibody_id": "0" * 8 + "-aaaa", "detail": "x"})

    @property
    def n(self) -> int:
        return len(self.calls)


def make(kind: str, hub: FakeHub, *, bank="bank_sbi", redis=None, mono=None, clock=None,
         audit=None, **cfg):  # fmt: skip
    clock = clock or Clock()
    mono = mono or Mono()
    client = HttpxHubClient(
        "http://hub", bank, SECRET, transport=httpx.MockTransport(hub)
    )  # fmt: skip
    conf = HubSubmitConfig(bank_id=bank, **cfg)
    audit = audit if audit is not None else InMemoryAuditSink()
    if kind == "memory":
        store = InMemoryHoldStore(
            audit=audit, clock=clock, hub_submit=conf, hub_client=client, mono=mono
        )  # fmt: skip
    else:
        store = RedisHoldStore(
            redis or fakeredis.aioredis.FakeRedis(), audit=audit, clock=clock,
            hub_submit=conf, hub_client=client, mono=mono,
        )  # fmt: skip
    return store, client, audit, clock, mono


async def held(store, clock, txn_id="t1", payee_hash=KEY, decision="hold_verify"):
    await store.create(
        txn_id, decision, R, clock() + timedelta(seconds=120), payer_token="p", score=0.9,
        model_version="gbm-v1", payee_hash=payee_hash,
        payee_ref="payee_ref:" + KEY[:16],
    )  # fmt: skip
    await store.hub_settle()


KINDS = pytest.mark.parametrize("kind", ["memory", "redis"])


def event_types(audit):
    return [e["event_type"] for e in audit.events]


# ------------------------------------------------------------------------- recording
@KINDS
async def test_confirm_block_records_pending_in_the_same_cas(kind):
    hub = FakeHub(sleep=60)  # never answers in this test
    store, client, audit, clock, _ = make(kind, hub)
    await held(store, clock)
    out = await store.resolve("t1", "confirm_block", "analyst-7", role="analyst")
    cur = await store.get("t1")
    assert cur.state == "blocked" and cur.resolved_by == "analyst-7"
    assert len(cur.hub_pending) == 1
    s = cur.hub_pending[0]
    assert (s.kind, s.key_hash, s.source_bank, s.actor) == (
        "mule_account", KEY, "bank_sbi", "analyst-7",
    )  # fmt: skip
    assert re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", s.evidence_ref)
    assert out.state == "blocked"
    await store.aclose()
    await client.aclose()


@KINDS
async def test_release_and_conflicts_never_record(kind):
    hub = FakeHub()
    store, client, audit, clock, _ = make(kind, hub)
    await held(store, clock)
    await store.resolve("t1", "release", "analyst-7", role="analyst")
    await store.hub_settle()
    cur = await store.get("t1")
    assert cur.hub_pending == [] and cur.hub_result is None
    with pytest.raises(HoldConflict):
        await store.resolve("t1", "confirm_block", "analyst-7", role="analyst")
    await store.hub_settle()
    assert hub.n == 0 and store.hub_stats.get("ok", 0) == 0
    await store.aclose()
    await client.aclose()


@KINDS
async def test_repeat_confirm_makes_one_submission_and_block_then_release_is_409(kind):
    hub = FakeHub()
    store, client, audit, clock, _ = make(kind, hub)
    await held(store, clock)
    await store.resolve("t1", "confirm_block", "analyst-7", role="analyst")
    await store.hub_settle()
    await store.resolve("t1", "confirm_block", "analyst-8", role="admin")
    await store.hub_settle()
    with pytest.raises(HoldConflict):
        await store.resolve("t1", "release", "analyst-7", role="analyst")
    assert hub.n == 1
    assert event_types(audit).count("hold.antibody_submitted") == 1
    await store.aclose()
    await client.aclose()


@KINDS
async def test_concurrent_resolves_submit_exactly_once(kind):
    hub = FakeHub(sleep=0.05)
    store, client, audit, clock, _ = make(kind, hub)
    await held(store, clock)
    outs = await asyncio.gather(
        *(store.resolve("t1", "confirm_block", f"analyst-{i}", role="analyst") for i in range(20)),
        return_exceptions=True,
    )
    assert all(not isinstance(o, Exception) for o in outs)
    await store.hub_settle()
    assert hub.n == 1
    assert (await store.get("t1")).hub_pending == []
    await store.aclose()
    await client.aclose()


# ------------------------------------------------------------------------- delivery
@KINDS
async def test_success_clears_pending_and_audits(kind):
    hub = FakeHub(201)
    store, client, audit, clock, _ = make(kind, hub)
    await held(store, clock, txn_id=RRN)
    await store.resolve(RRN, "confirm_block", "analyst-7", role="analyst")
    await store.hub_settle()
    cur = await store.get(RRN)
    assert cur.hub_pending == [] and cur.hub_result == "ok"
    assert store.hub_stats["ok"] == 1
    req = hub.calls[0]
    assert req.method == "POST" and req.url.path == "/antibodies"
    assert req.headers["x-principal-role"] == "analyst"
    assert req.headers["x-principal-sub"] == "analyst-7"
    assert req.headers["x-principal-bank"] == "bank_sbi"
    assert req.headers["x-gateway-secret"] == SECRET
    body = json.loads(req.content)
    assert set(body) == {"kind", "key_hash", "source_bank", "evidence_ref", "extend"}
    assert body["key_hash"] == KEY and body["source_bank"] == "bank_sbi"
    assert body["extend"] is False and RRN not in json.dumps(body)
    await store.drain_audit(RRN)
    sub = [e for e in audit.events if e["event_type"] == "hold.antibody_submitted"]
    assert len(sub) == 1
    p = sub[0]["payload"]
    assert p["result"] == "ok" and p["attempts"] == 1 and p["key_prefix"] == KEY[:8]
    assert KEY not in json.dumps(sub[0]) and RRN not in json.dumps(sub[0])
    assert any(r.startswith("payee_ref:") for r in sub[0]["case_refs"])
    await store.aclose()
    await client.aclose()


@KINDS
async def test_200_is_duplicate_success(kind):
    hub = FakeHub(200)
    store, client, audit, clock, _ = make(kind, hub)
    await held(store, clock)
    await store.resolve("t1", "confirm_block", "analyst-7", role="analyst")
    await store.hub_settle()
    cur = await store.get("t1")
    assert cur.hub_pending == [] and cur.hub_result == "duplicate"
    assert store.hub_stats["duplicate"] == 1
    await store.aclose()
    await client.aclose()


@KINDS
async def test_protected_is_terminal_success_with_flag(kind):
    hub = FakeHub(409)
    store, client, audit, clock, mono = make(kind, hub)
    await held(store, clock)
    await store.resolve("t1", "confirm_block", "analyst-7", role="analyst")
    await store.hub_settle()
    mono.t += 600
    await store.sweep()
    await store.hub_settle()
    cur = await store.get("t1")
    assert hub.n == 1 and cur.hub_pending == [] and cur.hub_result == "protected"
    assert store.hub_stats["protected"] == 1
    p = [e for e in audit.events if e["event_type"] == "hold.antibody_submitted"]
    assert p and p[0]["payload"]["result"] == "protected"
    await store.aclose()
    await client.aclose()


@KINDS
async def test_422_is_terminal_failure(kind):
    hub = FakeHub(422)
    store, client, audit, clock, mono = make(kind, hub)
    await held(store, clock)
    await store.resolve("t1", "confirm_block", "analyst-7", role="analyst")
    await store.hub_settle()
    mono.t += 600
    await store.sweep()
    await store.hub_settle()
    cur = await store.get("t1")
    assert hub.n == 1 and cur.hub_pending == [] and cur.hub_result == "rejected"
    assert store.hub_stats["rejected"] == 1
    f = [e for e in audit.events if e["event_type"] == "hold.antibody_submit_failed"]
    assert len(f) == 1 and f[0]["payload"]["result"] == "rejected"
    await store.aclose()
    await client.aclose()


@KINDS
async def test_401_is_loud_kept_pending_and_backed_off(kind, caplog):
    caplog.set_level(logging.DEBUG)
    hub = FakeHub(script=[401, 403])
    store, client, audit, clock, mono = make(kind, hub)
    await held(store, clock)
    await store.resolve("t1", "confirm_block", "analyst-7", role="analyst")
    await store.hub_settle()
    cur = await store.get("t1")
    assert hub.n == 1 and len(cur.hub_pending) == 1 and cur.hub_result is None
    assert store.hub_stats["auth_failures"] == 1
    assert any(r.levelno >= logging.WARNING and "auth" in r.getMessage().lower()
               for r in caplog.records)  # fmt: skip
    await store.sweep()  # still inside the backoff window: no new attempt
    await store.hub_settle()
    assert hub.n == 1
    mono.t += 61
    await store.sweep()
    await store.hub_settle()
    assert hub.n == 2 and store.hub_stats["auth_failures"] == 2
    mono.t += 61
    await store.sweep()  # third attempt: 201 default
    await store.hub_settle()
    cur = await store.get("t1")
    assert cur.hub_pending == [] and cur.hub_result == "ok"
    assert SECRET not in caplog.text
    await store.aclose()
    await client.aclose()


@KINDS
async def test_5xx_and_network_errors_retry_with_backoff_then_succeed(kind):
    hub = FakeHub(script=[503, 500])
    store, client, audit, clock, mono = make(kind, hub)
    await held(store, clock)
    await store.resolve("t1", "confirm_block", "analyst-7", role="analyst")
    await store.hub_settle()
    assert hub.n == 1 and store.hub_stats["retried"] == 1
    await store.sweep()
    await store.hub_settle()
    assert hub.n == 1  # inside the backoff window
    mono.t += 5
    await store.sweep()
    await store.hub_settle()
    assert hub.n == 2 and store.hub_stats["retried"] == 2
    mono.t += 5
    await store.sweep()
    await store.hub_settle()
    assert hub.n == 2  # second backoff is longer than the first
    mono.t += 60
    await store.sweep()
    await store.hub_settle()
    cur = await store.get("t1")
    assert hub.n == 3 and cur.hub_pending == [] and cur.hub_result == "ok"
    await store.drain_audit("t1")
    p = [e for e in audit.events if e["event_type"] == "hold.antibody_submitted"]
    assert p[0]["payload"]["attempts"] == 3
    assert store.hub_last_error == "HTTP5xx"
    await store.aclose()
    await client.aclose()


@KINDS
async def test_timeout_and_connection_error_classes_are_counted(kind):
    hub = FakeHub(raise_exc=httpx.ConnectError("boom: 402312345678"))
    store, client, audit, clock, mono = make(kind, hub)
    await held(store, clock)
    await store.resolve("t1", "confirm_block", "analyst-7", role="analyst")
    await store.hub_settle()
    assert store.hub_stats["retried"] == 1 and store.hub_last_error == "ConnectError"
    assert (await store.get("t1")).hub_pending
    await store.aclose()
    await client.aclose()


async def test_breaker_stops_scheduling_after_repeated_failures_and_sweeper_probes_once():
    hub = FakeHub(default=503)
    store, client, audit, clock, mono = make("memory", hub)
    for i in range(7):
        await held(store, clock, txn_id=f"t{i}")
    for i in range(5):
        await store.resolve(f"t{i}", "confirm_block", "analyst-7", role="analyst")
        await store.hub_settle()
    assert hub.n == 5 and store.hub_breaker_open
    await store.resolve("t5", "confirm_block", "analyst-7", role="analyst")
    await store.hub_settle()
    assert hub.n == 5  # the breaker is cooling down: nothing was scheduled
    mono.t += 61  # past every backoff and the cooldown
    await store.sweep()
    await store.hub_settle()
    assert hub.n == 6  # one probe while the breaker is open, which failed
    hub.default = 201
    mono.t += 61
    await store.sweep()
    await store.hub_settle()
    assert all(not (await store.get(f"t{i}")).hub_pending for i in range(6))
    await store.aclose()
    await client.aclose()


async def test_pending_survives_a_restart_and_sweeper_delivers_once():
    redis = fakeredis.aioredis.FakeRedis()
    down = FakeHub(default=503)
    s1, c1, audit, clock, mono = make("redis", down, redis=redis)
    await held(s1, clock)
    await s1.resolve("t1", "confirm_block", "analyst-7", role="analyst")
    await s1.hub_settle()
    await s1.aclose()
    await c1.aclose()
    assert (await RedisHoldStore(redis).get("t1")).hub_pending  # still durable
    up = FakeHub(201)
    s2, c2, _, _, _ = make("redis", up, redis=redis, audit=audit, clock=clock)
    await s2.sweep()
    await s2.hub_settle()
    await s2.sweep()
    await s2.hub_settle()
    assert up.n == 1
    cur = await s2.get("t1")
    assert cur.hub_pending == [] and cur.hub_result == "ok"
    await s2.aclose()
    await c2.aclose()


async def test_resolve_does_not_wait_for_a_hung_hub():
    hub = FakeHub(sleep=5.0)
    store, client, audit, clock, _ = make("memory", hub)
    await held(store, clock)
    t0 = time.perf_counter()
    await store.resolve("t1", "confirm_block", "analyst-7", role="analyst")
    assert time.perf_counter() - t0 < 0.1
    await store.aclose()
    await client.aclose()


# ----------------------------------------------------------------------------- skips
@KINDS
async def test_non_hex_payee_hash_is_skipped_with_counter_and_audit(kind):
    hub = FakeHub()
    store, client, audit, clock, _ = make(kind, hub)
    await held(store, clock, payee_hash="payee_known")
    out = await store.resolve("t1", "confirm_block", "analyst-7", role="analyst")
    await store.hub_settle()
    assert out.state == "blocked" and hub.n == 0
    cur = await store.get("t1")
    assert cur.hub_pending == [] and cur.hub_result == "skipped"
    assert cur.hub_reason == "invalid_key_hash"
    assert store.hub_stats["skipped"] == 1
    await store.drain_audit("t1")
    f = [e for e in audit.events if e["event_type"] == "hold.antibody_submit_failed"]
    assert len(f) == 1 and f[0]["payload"]["result"] == "skipped"
    assert "payee_known" not in json.dumps(audit.events)
    await store.aclose()
    await client.aclose()


async def test_missing_or_invalid_bank_id_is_skipped():
    for bank in ("", "Bank A!"):
        hub = FakeHub()
        store, client, audit, clock, _ = make("memory", hub, bank=bank or "x")
        store._hub_cfg = HubSubmitConfig(bank_id=bank)
        await held(store, clock)
        await store.resolve("t1", "confirm_block", "analyst-7", role="analyst")
        await store.hub_settle()
        cur = await store.get("t1")
        assert hub.n == 0 and cur.hub_result == "skipped"
        assert cur.hub_reason in {"no_bank_id", "invalid_bank_id"}
        await store.aclose()
        await client.aclose()


async def test_global_pending_cap_skips_with_metric_but_still_resolves():
    hub = FakeHub(sleep=60)
    store, client, audit, clock, _ = make("memory", hub, max_pending=1)
    await held(store, clock, txn_id="t1")
    await held(store, clock, txn_id="t2")
    await store.resolve("t1", "confirm_block", "analyst-7", role="analyst")
    out = await store.resolve("t2", "confirm_block", "analyst-7", role="analyst")
    assert out.state == "blocked"
    cur = await store.get("t2")
    assert cur.hub_result == "skipped" and cur.hub_reason == "pending_cap"
    assert store.hub_stats["skipped"] == 1
    assert await store.hub_pending_count() == 1
    await store.aclose()
    await client.aclose()


async def test_store_without_hub_config_records_nothing():
    store = InMemoryHoldStore(audit=InMemoryAuditSink(), clock=Clock())
    await store.create("t1", "hold_verify", R, NOW + timedelta(minutes=2), payee_hash=KEY)
    await store.resolve("t1", "confirm_block", "analyst-7", role="analyst")
    cur = await store.get("t1")
    assert cur.hub_pending == [] and cur.hub_result is None and cur.payee_hash == KEY


# ------------------------------------------------------------------------- hygiene
async def test_nothing_sensitive_reaches_logs(caplog):
    caplog.set_level(logging.DEBUG)
    hub = FakeHub(script=[401, 503, 422, 201])
    store, client, audit, clock, mono = make("memory", hub)
    for t in (RRN, "t2"):
        await held(store, clock, txn_id=t)
        await store.resolve(t, "confirm_block", "analyst-7", role="analyst")
        await store.hub_settle()
    for _ in range(4):
        mono.t += 70
        await store.sweep()
        await store.hub_settle()
    text = caplog.text
    assert SECRET not in text and KEY not in text and RRN not in text
    await store.aclose()
    await client.aclose()


def test_evidence_ref_is_opaque_digit_free_and_deterministic():
    a = evidence_ref_for(RRN)
    assert a == evidence_ref_for(RRN) and a != evidence_ref_for("402312345679")
    assert re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", a)
    assert not re.search(r"\d", a) and RRN not in a
    assert evidence_ref_for("txn_0123456789abcdef") != evidence_ref_for("txn_0123456789abcdee")


def test_config_from_env():
    f = hub_config_from_env
    assert f({"HUB_URL": "http://h", "TXN_BANK_ID": "Bank_A "}).bank_id == "bank_a"
    assert f({"HUB_URL": "http://h"}) is None  # no bank id: default off
    assert f({"TXN_BANK_ID": "bank_a"}) is None  # no hub
    assert f({"HUB_URL": "http://h", "TXN_BANK_ID": "bank_a", "TXN_HUB_SUBMIT": "0"}) is None
    assert f({"TXN_HUB_SUBMIT": "1"}) is None  # nowhere to send
    c = f({"HUB_URL": "http://h", "TXN_BANK_ID": "a", "HUB_SUBMIT_TIMEOUT_S": "0.5",
           "HUB_SUBMIT_MAX_ATTEMPTS": "7"})  # fmt: skip
    assert c.timeout_s == 0.5 and c.max_attempts == 7 and c.max_backoff_s == 60.0
    d = f({"HUB_URL": "http://h", "TXN_BANK_ID": "a"})
    assert d.timeout_s == 2.0 and d.max_attempts is None


async def test_max_attempts_makes_failure_terminal():
    hub = FakeHub(default=503)
    store, client, audit, clock, mono = make("memory", hub, max_attempts=2)
    await held(store, clock)
    await store.resolve("t1", "confirm_block", "analyst-7", role="analyst")
    for _ in range(3):
        await store.hub_settle()
        mono.t += 70
        await store.sweep()
    await store.hub_settle()
    cur = await store.get("t1")
    assert hub.n == 2 and cur.hub_pending == [] and cur.hub_result == "exhausted"
    assert store.hub_stats["exhausted"] == 1
    await store.aclose()
    await client.aclose()


# ---------------------------------------------------------------------------- api
async def test_api_metrics_readyz_and_view(monkeypatch):
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "1")
    monkeypatch.setenv("METRICS_TOKEN", "tok")
    hub = FakeHub(201)
    store, client, audit, clock, mono = make("memory", hub)
    await held(store, clock, txn_id=RRN)
    app = create_app(holds=store, clock=clock, sweep_interval_s=0, closers=[client.aclose])
    an = {"X-Principal-Role": "analyst", "X-Principal-Sub": "analyst-7"}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            t0 = time.perf_counter()
            r = await c.post(f"/holds/{RRN}/resolve", headers=an, json={"action": "confirm_block"})
            assert r.status_code == 200 and time.perf_counter() - t0 < 0.5
            body = r.json()
            assert body["state"] == "blocked" and "payee_hash" not in body
            assert "hub_pending" in body and KEY not in r.text
            await store.hub_settle()
            m = await c.get("/metrics", headers={"X-Metrics-Token": "tok"})
            assert 'txn_guard_hub_submissions_total{result="ok"} 1' in m.text
            for res in ("duplicate", "protected", "rejected", "retried", "skipped"):
                assert f'txn_guard_hub_submissions_total{{result="{res}"}} 0' in m.text
            assert "txn_guard_hub_pending 0" in m.text
            assert "txn_guard_hub_submit_auth_failures_total 0" in m.text
            assert KEY not in m.text and RRN not in m.text and SECRET not in m.text
            rz = await c.get("/readyz")
            assert rz.status_code == 200
            assert rz.json()["hub_submit"] == "enabled" and rz.json()["hub_reachable"] == "true"
    assert store.hub_drainer.live_tasks == 0
    assert client._client.is_closed  # noqa: SLF001


async def test_readyz_stays_ready_when_hub_is_down(monkeypatch):
    hub = FakeHub(default=503)
    store, client, audit, clock, mono = make("memory", hub)
    await held(store, clock)
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "1")
    app = create_app(holds=store, clock=clock, sweep_interval_s=0)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            an = {"X-Principal-Role": "analyst", "X-Principal-Sub": "analyst-7"}
            await c.post("/holds/t1/resolve", headers=an, json={"action": "confirm_block"})
            await store.hub_settle()
            rz = await c.get("/readyz")
            assert rz.status_code == 200 and rz.json()["hub_reachable"] == "false"
    await client.aclose()
