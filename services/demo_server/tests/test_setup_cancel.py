import asyncio
import os
import random

import pytest
from conftest import make_settings

from demo_server.engine import Engine


async def slow(_: float) -> None:
    await asyncio.sleep(0.005)


def fd_count() -> int:
    for p in ("/dev/fd", "/proc/self/fd"):
        if os.path.isdir(p):
            return len(os.listdir(p))
    return 0


def run_dirs(e: Engine) -> list[str]:
    return [p for p in os.listdir(e.work_dir) if p.startswith("run-")]


@pytest.mark.asyncio
async def test_rapid_start_stop_seek_leaves_no_dirs_or_handles(tmp_path):
    e = Engine(make_settings(tmp_path), sleep=slow)
    await e.start()
    try:
        await e.start_run(1, 20.0, "hero")  # warm: threads, imports, sqlite
        await e.control("stop")
        base = fd_count()
        # 8 concurrent starts
        await asyncio.gather(*(e.start_run(1, 20.0, "hero") for _ in range(8)))
        assert len(run_dirs(e)) <= 1, run_dirs(e)
        rnd = random.Random(7)
        for _ in range(20):
            op = rnd.choice(["start_stop", "start_seek", "start_start", "seek_seek"])
            if op == "start_stop":
                s = asyncio.create_task(e.start_run(1, 20.0, "hero"))
                await asyncio.sleep(rnd.random() * 0.02)
                await asyncio.gather(s, e.control("stop"), return_exceptions=True)
            elif op == "start_seek":
                s = asyncio.create_task(e.start_run(1, 20.0, "hero"))
                await asyncio.sleep(rnd.random() * 0.02)
                await asyncio.gather(s, e.control("seek", t=100.0), return_exceptions=True)
            elif op == "start_start":
                await asyncio.gather(
                    e.start_run(2, 20.0, "hero"),
                    e.start_run(1, 20.0, "hero"),
                    return_exceptions=True,
                )
            else:
                await e.start_run(1, 20.0, "hero")
                await asyncio.gather(
                    e.control("seek", t=50.0), e.control("seek", t=300.0), return_exceptions=True
                )
        await e.control("stop")
        await asyncio.sleep(0.2)
        assert len(run_dirs(e)) <= 1, run_dirs(e)
        if base:
            assert fd_count() <= base + 12, (base, fd_count())
    finally:
        await e.aclose()
    assert not os.path.exists(e.work_dir)
