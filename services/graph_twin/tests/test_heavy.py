"""Heavy takedown operations are bounded: a queue cap (429), a cooperative deadline that stops
abandoned work, a restricted greedy pool on big rings, and caching."""

import asyncio
import threading
import time
from datetime import timedelta

import httpx
import pytest
from svckit.bus import InMemoryBus

from graph_twin.api import create_app
from graph_twin.config import Settings
from graph_twin.service import HeavyBusy, TwinService
from graph_twin.takedown import (
    Cancelled,
    CancelToken,
    FlowInput,
    best_flow,
    exhaustive_flow,
    harm_paise,
    horizon_us,
)
from graph_twin.twin import GraphTwin

from .conftest import T0, Clock, make_campaign

OFFICER = {"X-Principal-Role": "officer", "X-Principal-Sub": "officer-1"}


def big_flow(n_mules: int = 400, victims_per: int = 3, sinks: int = 5) -> FlowInput:
    edges: list[tuple[str, str, int]] = []
    roles: dict[str, str] = {}
    for s in range(sinks):
        roles[f"a:cash{s:02d}"] = "cash_out"
    for i in range(n_mules):
        m = f"a:mule{i:04d}"
        roles[m] = "mule"
        for j in range(victims_per):
            edges.append((f"a:vic{i:04d}_{j}", m, 5_000_000 + 7_919 * ((i * 31 + j) % 97)))
        edges.append((m, f"a:cash{i % sinks:02d}", 12_000_000 + 1_013 * (i % 53)))
        if i % 3 == 0 and i + 1 < n_mules:  # a second hop for a third of the mules
            edges.append((m, f"a:mule{i + 1:04d}", 2_000_000 + i))
    return FlowInput(tuple(sorted(edges)), tuple(sorted(roles.items())), 86_400 * 1_000_000)


def test_best_takedown_on_a_400_member_ring_is_fast_and_finds_the_sinks():
    fi = big_flow()
    cfg = Settings()
    t0 = time.monotonic()
    chosen = best_flow(fi, 5, cfg)
    assert time.monotonic() - t0 < 3.0
    assert len(chosen) == 5
    # cutting the five cash-outs removes all modelled harm: the restricted pool keeps terminals
    after = harm_paise(fi, frozenset(chosen), horizon_us(cfg))
    full = harm_paise(fi, frozenset(), horizon_us(cfg))
    assert after < full * 0.2


def test_small_rings_keep_the_full_pool_and_match_exhaustive():
    fi = big_flow(n_mules=8, victims_per=2, sinks=2)
    cfg = Settings()
    greedy = best_flow(fi, 2, cfg)
    best = exhaustive_flow(fi, 2, cfg)
    hz = horizon_us(cfg)
    assert harm_paise(fi, frozenset(greedy), hz) == harm_paise(fi, frozenset(best), hz)


def test_cancelled_token_stops_the_search_promptly():
    fi = big_flow()
    tok = CancelToken(60)
    t = threading.Timer(0.05, tok.cancel)
    t.start()
    t0 = time.monotonic()
    with pytest.raises(Cancelled):
        best_flow(fi, 5, Settings(takedown_pool=400), tok)
    assert time.monotonic() - t0 < 2.0
    with pytest.raises(Cancelled):
        best_flow(fi, 1, Settings(), CancelToken(0.0))


async def test_queue_cap_rejects_excess_and_abandoned_work_stops(db_url):
    cfg = Settings(heavy_concurrency=2, heavy_queue=4, flow_timeout_s=0.5)
    twin = GraphTwin(
        __import__("graph_twin.store", fromlist=["GraphStore"]).GraphStore(db_url), cfg
    )
    svc = TwinService(twin, InMemoryBus())
    live = 0
    peak = 0
    lock = threading.Lock()
    stopped_at: list[float] = []

    def job(tok: CancelToken) -> int:
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        try:
            while True:
                time.sleep(0.01)
                tok.check()
        except Cancelled:
            stopped_at.append(time.monotonic())
            raise
        finally:
            with lock:
                live -= 1

    t0 = time.monotonic()
    res = await asyncio.gather(
        *[svc.run_heavy(job, key=("k", i)) for i in range(10)], return_exceptions=True
    )
    busy = [r for r in res if isinstance(r, HeavyBusy)]
    timeouts = [r for r in res if isinstance(r, TimeoutError)]
    assert len(busy) == 4 and len(timeouts) == 6  # 2 running + 4 queued, the rest refused
    assert peak <= 2
    await asyncio.sleep(0.2)
    assert live == 0 and not svc.heavy.locked()  # threads ended near the deadline
    assert max(stopped_at) - t0 < 4 * cfg.flow_timeout_s
    twin.store.close()


async def test_results_are_cached_per_ring_version_and_request(db_url):
    twin = GraphTwin(__import__("graph_twin.store", fromlist=["GraphStore"]).GraphStore(db_url))
    svc = TwinService(twin, InMemoryBus())
    calls = 0

    def job(tok: CancelToken) -> int:
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return 7

    out = await asyncio.gather(*[svc.run_heavy(job, key=("sim", "r", 1, 3)) for _ in range(5)])
    assert out == [7] * 5 and calls == 1  # single flight
    assert await svc.run_heavy(job, key=("sim", "r", 1, 3)) == 7 and calls == 1  # cached
    assert await svc.run_heavy(job, key=("sim", "r", 2, 3)) == 7 and calls == 2  # new version
    twin.store.close()


async def test_api_answers_429_with_retry_after_when_the_queue_is_full(db_url, monkeypatch):
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "1")
    clock = Clock()
    app = create_app(
        db_url, bus=InMemoryBus(), clock=clock, background=False,
        settings=Settings(heavy_concurrency=1, heavy_queue=1, flow_timeout_s=2.0),
    )  # fmt: skip
    twin, svc = app.state.twin, app.state.service
    make_campaign(twin, clock, victims=9, mules=3)
    await svc.flush()
    (rid,) = twin.published
    import graph_twin.api as api_mod

    def slow(fi, remove, cfg, token=None):
        time.sleep(0.4)
        return api_mod_real(fi, remove, cfg, token)

    api_mod_real = api_mod.simulate_flow
    monkeypatch.setattr(api_mod, "simulate_flow", slow)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    g = (await c.get(f"/rings/{rid}/graph", headers=OFFICER)).json()
    ids = [n["id"] for n in g["nodes"]][:6]
    t0 = time.monotonic()
    rs = await asyncio.gather(
        *[c.post(f"/rings/{rid}/simulate", json={"remove": [i]}, headers=OFFICER) for i in ids]
    )
    codes = sorted(r.status_code for r in rs)
    assert codes.count(200) == 2 and codes.count(429) == 4
    assert all(r.headers.get("retry-after") for r in rs if r.status_code == 429)
    assert time.monotonic() - t0 < 5
    await svc.stop()
    twin.store.close()


async def test_exact_best_takedown_is_bounded(db_url, monkeypatch):
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "1")
    clock = Clock()
    app = create_app(
        db_url, bus=InMemoryBus(), clock=clock, background=False,
        settings=Settings(louvain_min_nodes=200),
    )  # fmt: skip
    twin, svc = app.state.twin, app.state.service
    make_campaign(twin, clock, victims=9, mules=3)
    await svc.flush()
    (rid,) = twin.published
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    ok = await c.get(f"/rings/{rid}/best-takedown?k=2&exact=true", headers=OFFICER)
    assert ok.status_code == 200 and ok.json()["mode"] == "k"
    greedy = await c.get(f"/rings/{rid}/best-takedown?k=2", headers=OFFICER)
    assert ok.json()["reduction_pct"] >= greedy.json()["reduction_pct"]
    assert (
        await c.get(f"/rings/{rid}/best-takedown?k=4&exact=true", headers=OFFICER)
    ).status_code == 422
    # a ring of more than 12 members: 422
    make_campaign(twin, clock, victims=26, mules=13, tag="big", start=T0 + timedelta(hours=1))
    await svc.flush()
    big = max(twin.published.values(), key=lambda r: len(r.members))
    r = await c.get(f"/rings/{big.ring_id}/best-takedown?k=1&exact=true", headers=OFFICER)
    assert r.status_code == 422
    await svc.stop()
    twin.store.close()
