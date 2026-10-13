"""Confirm-to-hub across the microservice seams.

Two txn-guard services (bank_a, bank_b) share one in-memory bus. The REAL antibody-hub app (ASGI,
SQLite, its real outbox drain onto the bus) is reached ONLY through txn-guard's ``HttpxHubClient``
over an httpx ASGI transport, with the hub's gateway secret enforced. An analyst resolves victim
A's held transfer through txn-guard's HTTP API; nothing calls ``hub.submit`` directly. Then
victim B's transfer to the same mule at bank B must be held with ANTIBODY_MATCH.
"""

import asyncio
import hashlib
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from antibody_hub.api import create_app as create_hub_app
from call_guard.consumer import handle_event
from call_guard.model import Scorer as CallScorer
from call_guard.model import load_classifier
from call_guard.session import InMemorySessionStore, SessionScorer
from evidence_ledger.api import create_app as create_ledger_app
from evidence_ledger.keys import KeyRing, Signer
from scam_contracts.models import CallRisk
from scam_contracts.topics import Topics
from sim_engine.replay import build_hero_scenario
from sim_engine.world import build_world
from svckit.bus import InMemoryBus
from svckit.idempotency import InMemoryIdempotencyStore
from txn_guard.antibody_cache import AntibodyLookup, HttpxHubClient, InMemoryAntibodyCache
from txn_guard.api import create_app as create_txn_app
from txn_guard.consumer import run_antibody_consumer
from txn_guard.history import InMemoryHistoryStore
from txn_guard.holds import BusAuditSink, InMemoryHoldStore
from txn_guard.hub_submit import HubSubmitConfig
from txn_guard.model import Scorer
from txn_guard.service import TxnGuardService

from scam_audit.harness import CONFIRM_DELAY, Clock, LedgerClock, _warm

SECRET = "e2e-hub-gateway-secret-not-for-production"
ANALYST = {"X-Principal-Role": "analyst", "X-Principal-Sub": "analyst-a1"}
ADMIN = {"X-Principal-Role": "admin", "X-Principal-Sub": "admin-1", "X-Gateway-Secret": SECRET}


class Mono:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class SwitchTransport(httpx.AsyncBaseTransport):
    """Delegates to the hub's ASGI transport unless the hub is 'down' (connection refused)."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner, self.down, self.requests = inner, False, 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if self.down:
            raise httpx.ConnectError("hub unreachable")
        return await self.inner.handle_async_request(request)


@pytest.fixture(scope="module")
def world_scorer():
    return build_world(5, 600), Scorer()


@dataclass
class Stack:
    meta: Any
    a_txns: list
    b_first: Any
    bank_a: TxnGuardService
    bank_b: TxnGuardService
    api_a: httpx.AsyncClient
    hub_api: httpx.AsyncClient
    switch: SwitchTransport
    clock: Clock
    mono: Mono
    bus: InMemoryBus
    ledger_store: Any
    shared_hold: tuple[datetime, str]


async def _wait(cond, what: str, timeout: float = 10.0) -> None:
    t0 = asyncio.get_running_loop().time()
    while not cond():
        if asyncio.get_running_loop().time() - t0 > timeout:
            raise TimeoutError(what)
        await asyncio.sleep(0.01)


@pytest.fixture
async def stack(tmp_path: Path, world_scorer, monkeypatch):
    world, scorer = world_scorer
    sc = build_hero_scenario(world, seed=1)
    meta = sc.metadata
    vt = sorted(
        (t for t in sc.txns if sc.campaign.txn_roles[t.txn_id] == "victim_transfer"),
        key=lambda t: t.ts,
    )
    a_txns = [t for t in vt if t.payer_token == meta.victim_a_token]
    b_first = next(t for t in vt if t.payer_token == meta.victim_b_token)
    bus = InMemoryBus()
    clock = Clock(min(e.ts for e in sc.calls))
    mono = Mono()
    monkeypatch.setenv("HUB_GATEWAY_SECRET", SECRET)  # the hub trust gate under test
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "1")  # txn-guard / ledger behind the gateway
    monkeypatch.delenv("GATEWAY_SHARED_SECRET", raising=False)
    monkeypatch.delenv("LEDGER_GATEWAY_SECRET", raising=False)

    keyring = KeyRing(Signer.from_seed(hashlib.sha256(b"confirm-to-hub-e2e").digest()))
    ledger_app = create_ledger_app(
        database_url=f"sqlite:///{tmp_path / 'ledger.db'}", bus=bus,
        clock=LedgerClock(datetime(2026, 1, 1, tzinfo=UTC)), keyring=keyring,
        checkpoint_every=50, maintenance_interval_s=0, consume=True,
    )  # fmt: skip
    hub_app = create_hub_app(
        database_url=f"sqlite:///{tmp_path / 'hub.db'}", bus=bus, clock=clock,
        banks=["bank_a", "bank_b"], drain_interval_s=0,
    )  # fmt: skip
    switch = SwitchTransport(httpx.ASGITransport(app=hub_app))

    def build(bank: str) -> tuple[TxnGuardService, Any, HttpxHubClient]:
        client = HttpxHubClient("http://hub", bank, SECRET, transport=switch, timeout=2.0)
        holds = InMemoryHoldStore(
            audit=BusAuditSink(bus), clock=clock, hub_submit=HubSubmitConfig(bank_id=bank),
            hub_client=client, mono=mono,
        )  # fmt: skip
        svc = TxnGuardService(
            scorer, InMemoryHistoryStore(), holds, bus, InMemoryIdempotencyStore(), clock=clock,
            antibodies=AntibodyLookup(InMemoryAntibodyCache(clock=clock)),
        )  # fmt: skip
        app = create_txn_app(service=svc, clock=clock, sweep_interval_s=0, closers=[client.aclose])
        return svc, app, client

    bank_a, app_a, _ = build("bank_a")
    bank_b, app_b, _ = build("bank_b")
    ab_task = None
    async with AsyncExitStack() as st:
        await st.enter_async_context(ledger_app.router.lifespan_context(ledger_app))
        await st.enter_async_context(hub_app.router.lifespan_context(hub_app))
        await st.enter_async_context(app_a.router.lifespan_context(app_a))
        await st.enter_async_context(app_b.router.lifespan_context(app_b))
        api_a = await st.enter_async_context(
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app_a), base_url="http://a")
        )
        hub_api = await st.enter_async_context(
            httpx.AsyncClient(transport=httpx.ASGITransport(app=hub_app), base_url="http://hub")
        )
        try:
            sessions = SessionScorer(InMemorySessionStore(), CallScorer(load_classifier()))
            cg_idem = InMemoryIdempotencyStore()
            for e in sorted(sc.calls, key=lambda e: e.ts):
                await handle_event(e, sessions, bus, cg_idem)
            risks = [CallRisk.model_validate_json(r) for _, r in bus.messages(Topics.CALL_RISK)]
            a_risks = [r for r in risks if r.victim_token == meta.victim_a_token]
            assert a_risks
            clock.t = a_txns[0].ts
            _warm(bank_a.history, meta.victim_a_token, a_txns[0].device_id_token, a_txns[0].ts)
            _warm(bank_b.history, meta.victim_b_token, b_first.device_id_token, b_first.ts)
            ab_task = run_antibody_consumer(bus, bank_b, InMemoryIdempotencyStore(), "bank_b")
            events = sorted(
                [(r.ts, 0, r) for r in a_risks] + [(t.ts, 1, t) for t in a_txns],
                key=lambda x: (x[0], x[1]),
            )
            shared: tuple[datetime, str] | None = None
            for ts, kind, obj in events:
                clock.t = ts
                if kind == 0:
                    await bank_a.handle_call_risk(obj)
                else:
                    d = await bank_a.handle_txn(obj)
                    if (
                        d.decision == "hold_verify"
                        and obj.payee_hash == meta.shared_mule_payee_hash
                        and shared is None
                    ):
                        shared = (obj.ts, obj.txn_id)
            assert shared is not None
            yield Stack(
                meta, a_txns, b_first, bank_a, bank_b, api_a, hub_api, switch, clock, mono, bus,
                ledger_app.state.store, shared,
            )  # fmt: skip
        finally:
            if ab_task is not None:
                ab_task.cancel()
                await asyncio.gather(ab_task, return_exceptions=True)
            os.environ.pop("HUB_GATEWAY_SECRET", None)


async def _ledger_types(s: Stack) -> list[str]:
    want = len({raw for _, raw in s.bus.messages(Topics.LEDGER)})
    await _wait(lambda: s.ledger_store.head().seq >= want, "ledger consumed every entry")
    head = s.ledger_store.head().seq
    return [e.event_type for e in s.ledger_store.page(1, head + 1)]


async def _hub_antibodies(s: Stack) -> list[dict]:
    r = await s.hub_api.get("/antibodies?state=all", headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


async def test_analyst_confirm_at_bank_a_blocks_the_mule_at_bank_b(stack: Stack):
    s = stack
    s.clock.t = s.shared_hold[0] + CONFIRM_DELAY
    r = await s.api_a.post(
        f"/holds/{s.shared_hold[1]}/resolve", headers=ANALYST, json={"action": "confirm_block"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "blocked" and body["resolved_by"] == "analyst-a1"
    assert s.meta.shared_mule_payee_hash not in r.text  # the API never returns the keyed hash

    await _wait(
        lambda: s.bank_b.antibodies.lookup(s.meta.shared_mule_payee_hash) is not None,
        "antibody.applied at bank_b",
    )
    abs_ = await _hub_antibodies(s)
    assert len(abs_) == 1
    assert abs_[0]["key_hash"] == s.meta.shared_mule_payee_hash
    assert abs_[0]["source_bank"] == "bank_a" and abs_[0]["confirmed_by"] == "analyst-a1"

    s.clock.t = s.b_first.ts
    d_b = await s.bank_b.handle_txn(s.b_first)
    assert d_b.decision == "hold_verify"
    assert "ANTIBODY_MATCH" in {x.code for x in d_b.reasons}

    # at-least-once, idempotent: the same confirm again and a sweep add no second antibody
    r2 = await s.api_a.post(
        f"/holds/{s.shared_hold[1]}/resolve", headers=ANALYST, json={"action": "confirm_block"}
    )
    assert r2.status_code == 200
    s.mono.t += 120
    await s.bank_a.holds.sweep()
    await s.bank_a.holds.hub_settle()
    assert len(await _hub_antibodies(s)) == 1
    hold = await s.bank_a.holds.get(s.shared_hold[1])
    assert hold.hub_pending == [] and hold.hub_result == "ok"

    types = await _ledger_types(s)
    assert "hold.antibody_submitted" in types
    assert "antibody.created" in types
    assert types.count("hold.antibody_submitted") == 1
    assert types.count("antibody.created") == 1
    m = await s.api_a.get("/metrics", headers=ANALYST)
    assert 'txn_guard_hub_submissions_total{result="ok"} 1' in m.text


async def test_control_without_confirm_bank_b_is_not_blocked(stack: Stack):
    s = stack
    s.clock.t = s.b_first.ts
    d_b = await s.bank_b.handle_txn(s.b_first)
    assert "ANTIBODY_MATCH" not in {x.code for x in d_b.reasons}
    assert await _hub_antibodies(s) == []
    assert s.switch.requests == 0  # nothing was submitted without an analyst confirmation


async def test_release_never_creates_an_antibody(stack: Stack):
    s = stack
    r = await s.api_a.post(
        f"/holds/{s.shared_hold[1]}/resolve", headers=ANALYST, json={"action": "release"}
    )
    assert r.status_code == 200 and r.json()["state"] == "released"
    await s.bank_a.holds.hub_settle()
    assert s.switch.requests == 0 and await _hub_antibodies(s) == []


async def test_hub_outage_delays_the_antibody_but_the_resolve_succeeds(stack: Stack):
    s = stack
    s.switch.down = True
    s.clock.t = s.shared_hold[0] + CONFIRM_DELAY
    r = await s.api_a.post(
        f"/holds/{s.shared_hold[1]}/resolve", headers=ANALYST, json={"action": "confirm_block"}
    )
    assert r.status_code == 200 and r.json()["state"] == "blocked"
    await s.bank_a.holds.hub_settle()
    assert r.json()["hub_pending"] is True
    # other banks are NOT protected while the submission is undelivered
    s.clock.t = s.b_first.ts
    d_b = await s.bank_b.handle_txn(s.b_first)
    assert "ANTIBODY_MATCH" not in {x.code for x in d_b.reasons}
    assert await s.bank_a.holds.hub_pending_count() == 1

    s.switch.down = False  # the hub comes back; the periodic sweeper delivers
    s.mono.t += 120
    await s.bank_a.holds.sweep()
    await s.bank_a.holds.hub_settle()
    await _wait(
        lambda: s.bank_b.antibodies.lookup(s.meta.shared_mule_payee_hash) is not None,
        "antibody applied after the outage",
    )
    assert await s.bank_a.holds.hub_pending_count() == 0
    assert len(await _hub_antibodies(s)) == 1


async def test_wrong_gateway_secret_is_loud_and_kept_pending(stack: Stack, caplog):
    s = stack
    # a mis-set secret on txn-guard: rebuild bank_a's client with a wrong one
    client = HttpxHubClient("http://hub", "bank_a", "wrong-secret", transport=s.switch)
    s.bank_a.holds._hub_client = client  # noqa: SLF001
    s.clock.t = s.shared_hold[0] + CONFIRM_DELAY
    r = await s.api_a.post(
        f"/holds/{s.shared_hold[1]}/resolve", headers=ANALYST, json={"action": "confirm_block"}
    )
    assert r.status_code == 200
    await s.bank_a.holds.hub_settle()
    assert s.bank_a.holds.hub_stats["auth_failures"] == 1
    assert (await s.bank_a.holds.get(s.shared_hold[1])).hub_pending
    assert "wrong-secret" not in caplog.text and SECRET not in caplog.text
    await client.aclose()
