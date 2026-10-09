import asyncio
import time

from svckit.drain import Drainer


async def test_schedule_is_fire_and_forget_with_inflight_guard_and_cap():
    gate = asyncio.Event()
    calls: list[str] = []

    async def fn_for(key):
        async def fn():
            calls.append(key)
            await gate.wait()
            return True

        return fn

    d = Drainer(cap=3, timeout_s=5, cooldown_s=1)
    t0 = time.perf_counter()
    for i in range(10):
        d.schedule(f"k{i}", await fn_for(f"k{i}"))
    d.schedule("k0", await fn_for("k0"))  # in flight: no-op
    assert time.perf_counter() - t0 < 0.01 and d.live_tasks == 3
    await asyncio.sleep(0.01)
    assert sorted(calls) == ["k0", "k1", "k2"]
    gate.set()
    await d.settle()
    assert d.live_tasks == 0
    await d.aclose()


async def test_timeouts_open_the_breaker_and_sweep_probes_once():
    attempts: list[str] = []

    async def hang(key=None):
        attempts.append(key)
        await asyncio.sleep(10)
        return True

    d = Drainer(cap=8, timeout_s=0.02, cooldown_s=60, breaker_threshold=3)
    for i in range(3):
        d.schedule(f"k{i}", lambda i=i: hang(f"k{i}"))
    await d.settle()
    assert d.breaker_open
    assert d.schedule("x", lambda: hang("x")) is None  # breaker: not scheduled
    attempts.clear()
    n = await d.sweep([f"s{i}" for i in range(20)], hang)
    assert n == 0 and len(attempts) == 1  # a single probe while the breaker is open
    await d.aclose()


async def test_sweep_is_bounded_and_closes_the_breaker_on_success():
    live = peak = 0

    async def ok(key):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.005)
        live -= 1
        return True

    d = Drainer(cap=4, timeout_s=1, cooldown_s=60, breaker_threshold=1)
    d._note(False)
    assert d.breaker_open
    assert await d.sweep([f"k{i}" for i in range(20)], ok) == 20
    assert peak <= 4 and not d.breaker_open


async def test_aclose_cancels_background_tasks():
    async def hang():
        await asyncio.sleep(30)
        return True

    d = Drainer(cap=2, timeout_s=60, cooldown_s=1)
    d.schedule("a", hang)
    await asyncio.sleep(0)
    await d.aclose()
    assert d.live_tasks == 0
