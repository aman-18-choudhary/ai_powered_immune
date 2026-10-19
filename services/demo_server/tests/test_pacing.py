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


def split_ledger(events):
    """(non-ledger events without ids or ledger counters, ledger entries by seq and type)."""
    other, ledger = [], []
    for e in norm(events):
        if e["kind"] == "ledger.entry":
            ledger.append((e["seq"], e["event_type"]))
            continue
        e = {k: v for k, v in e.items() if k != "id"}
        if "counters" in e:
            e["counters"] = {k: v for k, v in e["counters"].items() if k != "ledger_entries"}
        other.append(e)
    return other, ledger


def assert_same_run(a_events, b_events):
    """The engine's documented determinism contract: everything except ledger.entry events is
    identical; ledger entries wait at most LEDGER_WAIT_S for the ledger consumer and otherwise
    appear later (shifting ids and ledger counters after them), so they must be the same
    sequence, one run possibly lagging at the end."""
    a, b = split_ledger(a_events), split_ledger(b_events)
    assert a[0] == b[0]
    shorter, longer = sorted((a[1], b[1]), key=len)
    assert longer[: len(shorter)] == shorter


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
    assert_same_run(real, cine)
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
    # The engine's documented contract: ledger.entry events wait at most LEDGER_WAIT_S for the
    # ledger consumer and otherwise appear later, so under load their position (and the ids and
    # ledger counters after them) can differ. Everything else must be identical, and the ledger
    # entries must be the same sequence (one replay may lag at the end).
    assert_same_run(ev_t, list(engine.log.history))


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


@pytest.mark.asyncio
async def test_ledger_lag_in_one_run_keeps_the_contract(tmp_path, monkeypatch):
    """Regression (CI run #38/#39, 2026-10-19): on a slow runner one run's ledger entries missed
    the LEDGER_WAIT_S window and appeared after a metric tick. Forcing that lag in one run: the
    exact sequences differ, the documented contract still holds."""
    import demo_server.engine as engine_mod

    e = Engine(make_settings(tmp_path), sleep=instant)
    await e.start()
    try:
        await e.start_run(1, 20.0, "hero", "real")
        await e.join(60)
        real = list(e.log.history)
        monkeypatch.setattr(engine_mod, "LEDGER_WAIT_S", 0.0)  # a ledger consumer that lags
        await e.start_run(1, 20.0, "hero", "cinematic")
        await e.join(60)
        cine = list(e.log.history)
    finally:
        await e.aclose()
    assert norm(real) != norm(cine)  # the lag really changed the positions
    assert_same_run(real, cine)
    assert any(x["kind"] == "ledger.entry" for x in cine)
