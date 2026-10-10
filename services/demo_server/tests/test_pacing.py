import asyncio

import pytest
import pytest_asyncio
from conftest import instant, login, make_settings, wait_status
from fastapi.testclient import TestClient

from demo_server.engine import TICK_S, Engine, _shared_models
from demo_server.pacing import MAX_GAP_WALL_S, Timeline, compress_gap
from demo_server.plan import build_plan

IGNORED = {"wall", "run_id", "latency_ms", "wall_offset"}


def norm(events):
    return [{k: v for k, v in e.items() if k not in IGNORED} for e in events]


def test_compress_gap_monotone_bounded_continuous():
    gs = [i / 10 for i in range(0, 20_000)]
    ws = [compress_gap(g) for g in gs]
    assert all(b >= a for a, b in zip(ws, ws[1:], strict=False))
    assert max(ws) <= MAX_GAP_WALL_S + 1e-9
    assert compress_gap(0.3) == 0.3 and compress_gap(0.0) == 0.0
    assert abs(compress_gap(0.5) - compress_gap(0.5000001)) < 1e-6


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_total_duration_in_range_and_inverse(seed):
    world = _shared_models()[0]
    plan = build_plan(world, seed, "hero")
    tl = Timeline("cinematic", [it.t for it in plan.items], plan.duration, TICK_S)
    assert 55.0 <= tl.end_wall <= 85.0, tl.end_wall
    assert 10.0 <= tl.end_wall / 5 <= 20.0
    prev = -1.0
    for t in (0, 1, 29.9, 30, 100.5, 777, plan.duration / 2, plan.duration):
        w = tl.wall_of(t)
        assert w >= prev
        prev = w
        assert abs(tl.t_of(w) - t) < 1e-6
    assert Timeline("real").wall_of(12.5) == 12.5


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def engine(tmp_path_factory):
    e = Engine(make_settings(tmp_path_factory.mktemp("pace")), sleep=instant)
    await e.start()
    yield e
    await e.aclose()


@pytest.mark.asyncio
async def test_both_modes_same_events_and_wall_offset(engine):
    await engine.start_run(1, 20.0, "hero", "real")
    await engine.join(60)
    real = list(engine.log.history)
    await engine.start_run(1, 20.0, "hero", "cinematic")
    await engine.join(60)
    cine = list(engine.log.history)
    assert norm(real) == norm(cine)
    offs = [e["wall_offset"] for e in cine]
    assert offs == sorted(offs) and offs[-1] > 55
    assert [e["wall_offset"] for e in real] == [e["t"] for e in real]
    assert engine.state_view()["pacing"] == "cinematic"
    assert engine.state_view()["wall_duration"] == round(offs[-1], 3)


@pytest.mark.asyncio
async def test_cinematic_sleeps_total_matches_timeline(tmp_path):
    slept: list[float] = []

    async def rec(s: float) -> None:
        slept.append(s)

    e = Engine(make_settings(tmp_path), sleep=rec)
    await e.start()
    try:
        await e.start_run(1, 5.0, "hero", "cinematic")
        await e.join(60)
        assert abs(sum(slept) - e.state.wall_duration / 5.0) < 0.5
        assert 10.0 <= sum(slept) <= 20.0
    finally:
        await e.aclose()


@pytest.mark.asyncio
async def test_seek_by_wall_equals_seek_by_t(engine):
    await engine.start_run(1, 20.0, "hero", "cinematic")
    await engine.join(60)
    await engine.control("pause")
    tl = engine.run.timeline  # type: ignore[union-attr]
    t = 900.0
    st1 = await engine.control("seek", t=t)
    ev_t = list(engine.log.history)
    st2 = await engine.control("seek", wall=tl.wall_of(t))
    assert abs(st1["t"] - st2["t"]) < 0.01
    assert norm(ev_t) == norm(list(engine.log.history))


def test_api_pacing_and_seek_wall(app_factory):
    with TestClient(app_factory()) as c:
        off = login(c, "officer")
        r = c.post("/api/demo/start", json={"seed": 1, "speed": 20, "pacing": "bogus"}, headers=off)
        assert r.status_code == 422
        r = c.post("/api/demo/start", json={"seed": 1, "speed": 20, "pacing": "cinematic"},
                   headers=off)  # fmt: skip
        assert r.status_code == 200
        s = wait_status(c, off, ("finished",))
        assert s["pacing"] == "cinematic" and 55 < s["wall_duration"] < 85
        r = c.post("/api/demo/control", json={"action": "seek", "t": 5, "wall": 5}, headers=off)
        assert r.status_code == 422
        r = c.post("/api/demo/control", json={"action": "seek", "wall": 10.0}, headers=off)
        assert r.status_code == 200
        assert 0 < r.json()["t"] < 2609
        runs = c.get("/api/demo/runs", headers=off).json()["runs"]
        assert runs[-1]["pacing"] == "cinematic"
        _ = asyncio  # keep import for parity
