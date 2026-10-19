"""Engine behaviour driven directly (no HTTP): ordering, determinism, pacing, control, bounds."""

import asyncio
import time
from collections import Counter

import pytest
import pytest_asyncio
from conftest import assert_run_prefix, assert_same_run, instant, make_settings

from demo_server.engine import Engine

IGNORED = {"wall", "run_id", "latency_ms"}


def norm(events):
    return [{k: v for k, v in e.items() if k not in IGNORED} for e in events]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def engine(tmp_path_factory):
    e = Engine(make_settings(tmp_path_factory.mktemp("eng")), sleep=instant)
    await e.start()
    yield e
    await e.aclose()


async def play(e: Engine, seed=1, mode="hero", speed=20.0, timeout=60.0):
    await e.start_run(seed, speed, mode)
    await e.join(timeout)
    return list(e.log.history)


@pytest.mark.asyncio
async def test_hero_events_ordered_and_complete(engine):
    ev = await play(engine)
    assert engine.state.status == "finished"
    assert [e["id"] for e in ev] == list(range(1, len(ev) + 1))
    kinds = Counter(e["kind"] for e in ev)
    for k in (
        "scenario.started",
        "scenario.step",
        "call.chunk",
        "call.alert",
        "txn.scored",
        "hold.created",
        "hold.resolved",
        "antibody.published",
        "antibody.applied",
        "ledger.entry",
        "ring.updated",
        "geo.point",
        "metric.tick",
        "scenario.finished",
    ):
        assert kinds[k] >= 1, k
    assert kinds["scenario.started"] == kinds["scenario.finished"] == 1
    assert ev[0]["kind"] == "scenario.started" and ev[-1]["kind"] == "scenario.finished"
    keys = [e["key"] for e in ev if e["kind"] == "scenario.step"]
    assert keys.index("call_flagged") < keys.index("transfer_held") < keys.index("antibody_shared")
    assert (
        keys.index("antibody_shared") < keys.index("bank_b_blocks") < keys.index("evidence_sealed")
    )


@pytest.mark.asyncio
async def test_same_seed_same_events_independent_of_speed(engine):
    sleeps: list[tuple[float, float]] = []

    async def rec(d: float) -> None:
        sleeps.append((d, engine.state.speed))

    engine.sleep = rec
    try:
        a = await play(engine, seed=2, speed=1.0)
        n_slow = len(sleeps)
        slow_total = sum(d for d, _ in sleeps)
        sleeps.clear()
        b = await play(engine, seed=2, speed=20.0)
        fast_total = sum(d for d, _ in sleeps)
    finally:
        engine.sleep = instant
    assert_same_run(norm(a), norm(b))  # speed never changes the events (ledger timing aside)
    assert n_slow > 100
    # wall pacing: simulated duration divided by speed
    dur = engine.state.duration
    assert slow_total == pytest.approx(dur, rel=0.02)
    assert fast_total == pytest.approx(dur / 20.0, rel=0.02)
    c = await play(engine, seed=3)
    assert [e["kind"] for e in c] != [e["kind"] for e in a] or norm(c) != norm(a)


@pytest.mark.asyncio
async def test_noise_mode_adds_traffic_but_keeps_hero_milestones(engine):
    ev = await play(engine, seed=1, mode="hero+noise")
    kinds = Counter(e["kind"] for e in ev)
    assert kinds["txn.scored"] >= 250 and kinds["call.chunk"] > 40
    hero = [e for e in ev if e["kind"] == "txn.scored" and e["hero"]]
    assert len(hero) == 5
    b = next(e for e in hero if e["victim"] == "B")
    assert b["decision"] == "hold_verify"
    assert "ANTIBODY_MATCH" in {r["code"] for r in b["reasons"]}
    assert [e["id"] for e in ev] == list(range(1, len(ev) + 1))


@pytest.mark.asyncio
async def test_restart_and_double_start_leave_exactly_one_run(engine):
    ids = await asyncio.gather(engine.start_run(1, 20, "hero"), engine.start_run(1, 20, "hero"))
    assert ids[0] != ids[1]
    await engine.join(60)
    assert engine.state.run_id in ids
    assert {e["run_id"] for e in engine.log.history} == {engine.state.run_id}
    assert engine.task is not None and engine.task.done()
    n_started = sum(1 for e in engine.log.history if e["kind"] == "scenario.started")
    assert n_started == 1
    # restarting a finished run replays from id 1
    await engine.start_run(1, 20, "hero")
    await engine.join(60)
    assert engine.log.history[0]["id"] == 1


@pytest.mark.asyncio
async def test_pause_resume_and_stop(engine):
    ticks = asyncio.Event()

    async def slow(d: float) -> None:
        await asyncio.sleep(min(d, 0.002))

    engine.sleep = slow
    try:
        await engine.start_run(1, 20, "hero")
        while engine.state.t < 50:
            await asyncio.sleep(0.005)
        st = await engine.control("pause")
        assert st["status"] == "paused"
        t_paused = engine.state.t
        await asyncio.sleep(0.2)
        assert engine.state.t == t_paused  # no progress while paused
        assert engine.state.status == "paused"
        await engine.control("resume", speed=5.0)
        assert engine.state.speed == 5.0
        while engine.state.t <= t_paused + 30:
            await asyncio.sleep(0.005)
        st = await engine.control("stop")
        assert st["status"] == "stopped"
        assert engine.log.history[-1]["kind"] == "scenario.finished"
        assert engine.log.history[-1]["reason"] == "stopped"
        # control on a stopped run is harmless
        assert (await engine.control("pause"))["status"] == "stopped"
    finally:
        engine.sleep = instant
    del ticks


@pytest.mark.asyncio
async def test_seek_replays_deterministically_then_continues(engine):
    full = norm(await play(engine, seed=1))
    st = await engine.control("seek", t=1000.0)
    assert st["status"] == "paused" and st["epoch"] == 1  # was finished: stays paused at t
    assert 995.0 <= engine.state.t <= 1000.0
    hist = norm(engine.log.history)
    assert_run_prefix(hist, full)  # the replay is the original prefix (ledger timing aside)
    assert hist[-1]["t"] <= 1000.0 and len(hist) < len(full)
    snap = engine.snapshot()
    assert snap["kind"] == "scenario.state" and snap["id"] == hist[-1]["id"]
    assert snap["counters"]["ledger_entries"] >= 1
    await engine.control("resume")
    await engine.join(60)
    assert_same_run(norm(engine.log.history), full)  # continuing reproduces the original run


@pytest.mark.asyncio
async def test_seek_while_running_keeps_running(engine):
    async def slow(d: float) -> None:
        await asyncio.sleep(min(d, 0.001))

    engine.sleep = slow
    try:
        await engine.start_run(1, 20, "hero")
        while engine.state.t < 300:
            await asyncio.sleep(0.005)
        st = await engine.control("seek", t=100.0)
        assert st["epoch"] == 1
        await engine.join(60)
        assert engine.state.status == "finished"
        ids = [e["id"] for e in engine.log.history]
        assert ids == list(range(1, len(ids) + 1))
    finally:
        engine.sleep = instant


@pytest.mark.asyncio
async def test_control_without_run_and_bad_input():
    e = Engine(make_settings(__import__("pathlib").Path("/tmp/demo-x-unused")), sleep=instant)
    with pytest.raises(LookupError):
        await e.control("pause")
    await e.aclose()


@pytest.mark.asyncio
async def test_event_history_is_a_bounded_ring_buffer(tmp_path):
    e = Engine(make_settings(tmp_path, history_size=100), sleep=instant)
    await e.start()
    try:
        await e.start_run(1, 20, "hero")
        await e.join(60)
        assert len(e.log.history) == 100
        ids = [x["id"] for x in e.log.history]
        assert ids == list(range(ids[0], ids[0] + 100)) and ids[0] > 1
        assert e.snapshot()["events"][-1]["id"] == e.last_id()
    finally:
        await e.aclose()


@pytest.mark.asyncio
async def test_runs_list_and_work_dirs_are_bounded(tmp_path):
    e = Engine(make_settings(tmp_path, runs_kept=3), sleep=instant)
    await e.start()
    try:
        for s in range(1, 6):
            await e.start_run(s, 20, "hero")
            await e.join(60)
        assert len(e.runs) == 3
        assert [r["seed"] for r in e.runs] == [3, 4, 5]
        dirs = [p for p in tmp_path.iterdir() if p.is_dir()]
        assert len(dirs) <= 1  # only the current run keeps its files
    finally:
        await e.aclose()
    assert not tmp_path.exists() or not any(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_event_loop_is_not_blocked(engine, monkeypatch):
    """Heartbeat: no synchronous stall over ~0.35 s while a noisy run executes."""
    from demo_server import plan as plan_mod

    monkeypatch.setattr(plan_mod, "N_NOISE_TXNS", 60)
    gaps: list[float] = []
    stop = asyncio.Event()

    async def beat() -> None:
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    hb = asyncio.create_task(beat())
    await play(engine, seed=1, mode="hero+noise")
    stop.set()
    await hb
    assert gaps and max(gaps) < 0.35, max(gaps)


@pytest.mark.asyncio
async def test_ledger_outage_does_not_block_decisions(engine, monkeypatch):
    """A slow ledger only defers ``ledger.entry`` events; scoring and holds are unaffected."""
    from evidence_ledger.store import LedgerStore

    from demo_server import engine as eng_mod

    real = LedgerStore.append

    def slow_append(self, entry):
        time.sleep(0.25)
        return real(self, entry)

    monkeypatch.setattr(LedgerStore, "append", slow_append)
    monkeypatch.setattr(eng_mod, "LEDGER_WAIT_S", 0.05)
    t0 = time.perf_counter()
    ev = await play(engine, seed=1)
    took = time.perf_counter() - t0
    kinds = Counter(e["kind"] for e in ev)
    assert kinds["txn.scored"] == 5 and kinds["hold.created"] == 5
    assert kinds["antibody.applied"] == 1 and kinds["scenario.finished"] == 1
    assert took < 12, took
    # entries still arrive eventually (the ledger itself is not broken)
    assert engine.run is not None and engine.run.harness is not None
    deadline = time.perf_counter() + 20
    while time.perf_counter() < deadline:
        if (await asyncio.to_thread(engine.run.harness.ledger_store.head)).seq >= 8:
            break
        await asyncio.sleep(0.1)
    assert (await asyncio.to_thread(engine.run.harness.ledger_store.head)).seq >= 8
