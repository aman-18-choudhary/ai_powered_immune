import asyncio
import time
from datetime import timedelta

import httpx
import pytest
from svckit.bus import InMemoryBus

from graph_twin.api import create_app
from graph_twin.config import Settings
from graph_twin.graph import account_key
from graph_twin.model import AuditRow

from .conftest import T0, Clock, h, make_campaign, txn

OFFICER = {"X-Principal-Role": "officer", "X-Principal-Sub": "officer-1"}


def test_louvain_splits_two_campaigns_joined_by_a_shared_device(twin, clock):
    a = make_campaign(twin, clock, tag="A", victims=24, mules=6)
    b = make_campaign(twin, clock, tag="B", victims=24, mules=6, start=T0 + timedelta(hours=2))
    # one mule of A also paid from B's device: the components touch through a single device link
    twin.apply_txn(
        txn(a["mules"][0], h("bystander"), T0 + timedelta(hours=3), 50, device="dev_Bshared")
    )
    twin.detect_dirty()
    assert len(twin.registry.rings) == 2
    ids = {twin.registry.member_index[account_key(x["cash"])] for x in (a, b)}
    assert len(ids) == 2  # not one ring of 14 accounts


def test_settings_from_env_and_validation(monkeypatch):
    monkeypatch.setenv("GRAPH_RETENTION_DAYS", "7")
    monkeypatch.setenv("GRAPH_MAX_NODES", "5000")
    monkeypatch.setenv("GRAPH_PASSTHROUGH_MAX_S", "1800")
    monkeypatch.setenv("GRAPH_HARM_HORIZON_DAYS", "3")
    s = Settings.from_env()
    assert (s.retention_days, s.max_nodes, s.passthrough_max_us, s.harm_horizon_days) == (
        7.0, 5000, 1_800_000_000, 3.0,
    )  # fmt: skip
    monkeypatch.setenv("GRAPH_MAX_NODES", "abc")
    with pytest.raises(ValueError):
        Settings.from_env()
    monkeypatch.setenv("GRAPH_MAX_NODES", "5")
    with pytest.raises(ValueError):
        Settings.from_env()
    defaults = Settings()
    assert defaults.retention_days == 30 and defaults.max_nodes == 200_000
    assert defaults.passthrough_max_s == 3600 and defaults.harm_horizon_days == 7
    assert defaults.max_response_nodes == 500


async def test_simulation_timeout_is_503_and_the_semaphore_follows_the_thread(db_url, monkeypatch):
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "1")
    clock = Clock()
    app = create_app(
        db_url, bus=InMemoryBus(), clock=clock,
        settings=Settings(flow_timeout_s=0.05, heavy_concurrency=1), background=False,
    )  # fmt: skip
    twin, svc = app.state.twin, app.state.service
    make_campaign(twin, clock, victims=9, mules=3)
    await svc.flush()
    (rid,) = twin.published
    import graph_twin.api as api_mod

    release = __import__("threading").Event()

    def slow(fi, k, cfg):
        release.wait(2)
        return []

    monkeypatch.setattr(api_mod, "best_flow", slow)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    r = await c.post(f"/rings/{rid}/simulate", json={"k": 1}, headers=OFFICER)
    assert r.status_code == 503 and svc.heavy_timeouts == 1
    # the worker thread is still running: a second heavy job must wait, not pile up
    assert svc.heavy.locked()
    r2 = await c.post(f"/rings/{rid}/simulate", json={"k": 1}, headers=OFFICER)
    assert r2.status_code == 503
    release.set()
    for _ in range(100):
        if not svc.heavy.locked():
            break
        await asyncio.sleep(0.02)
    assert not svc.heavy.locked()
    await svc.stop()
    twin.store.close()


async def test_ring_too_large_for_simulation_is_422(db_url, monkeypatch):
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "1")
    clock = Clock()
    app = create_app(
        db_url, bus=InMemoryBus(), clock=clock, settings=Settings(max_ring_nodes=6), background=False
    )  # fmt: skip
    twin, svc = app.state.twin, app.state.service
    make_campaign(twin, clock, victims=9, mules=3)
    await svc.flush()
    (rid,) = twin.published
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    r = await c.post(f"/rings/{rid}/simulate", json={"k": 1}, headers=OFFICER)
    assert r.status_code == 422 and "too large" in r.text
    await svc.stop()
    twin.store.close()


async def test_background_audit_jobs_are_capped(db_url):
    clock = Clock()
    app = create_app(db_url, bus=InMemoryBus(), clock=clock, background=False)
    svc = app.state.service
    for i in range(300):
        svc.audit_takedown(AuditRow(dedupe=f"d{i}", msg_key="k", body="{}"))
    assert len(svc._bg) <= 100 and svc.audit_dropped >= 200
    await svc.settle()
    await svc.stop()
    app.state.twin.store.close()


async def test_audit_delivery_survives_a_ledger_outage_and_never_blocks(db_url, monkeypatch):
    """A hanging or failing ledger bus: detection and the API proceed; entries stay in the outbox
    and are delivered by the sweeper once the bus recovers (each exactly once)."""
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "1")
    from svckit.drain import Drainer

    class Flaky(InMemoryBus):
        down = True

        async def publish_raw(self, topic, key, raw):
            if self.down:
                await asyncio.sleep(5)  # hangs
            await super().publish_raw(topic, key, raw)

    bus = Flaky()
    clock = Clock()
    app = create_app(db_url, bus=bus, clock=clock, background=False)
    svc = app.state.service
    svc.drainer = Drainer(timeout_s=0.05, cooldown_s=0.05)
    twin = app.state.twin
    make_campaign(twin, clock, victims=9, mules=3)
    t0 = time.perf_counter()
    await svc.detect_once()
    assert time.perf_counter() - t0 < 0.5  # detection did not wait for the ledger
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    (rid,) = twin.published
    t1 = time.perf_counter()
    assert (
        await c.post(f"/rings/{rid}/simulate", json={"k": 1}, headers=OFFICER)
    ).status_code == 200
    assert time.perf_counter() - t1 < 0.5
    await svc.drainer.settle()
    assert twin.store.count_audit_pending() == 2 and not bus.messages("ledger.append")
    bus.down = False
    await asyncio.sleep(0.06)
    rows = twin.store.pending_audit(50)
    await svc.drainer.sweep([r["dedupe"] for r in rows], svc._deliver)
    await svc.drainer.sweep([r["dedupe"] for r in rows], svc._deliver)  # idempotent re-sweep
    assert twin.store.count_audit_pending() == 0 and len(bus.messages("ledger.append")) == 2
    await svc.stop()
    twin.store.close()
