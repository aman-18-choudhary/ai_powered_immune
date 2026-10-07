"""HTTP surface of the sim engine: trigger the hero scenario."""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from svckit.bus import Bus, KafkaBus
from svckit.health import make_health_router

from .replay import HeroMetadata, SleepFn, build_hero_scenario, replay
from .world import World, build_world

logger = logging.getLogger(__name__)


class HeroRequest(BaseModel):
    seed: int | None = None  # defaults to SIM_SEED; a new seed gives new ids and keys


def create_app(
    bus: Bus | None = None,
    world: World | None = None,
    speed: float | None = None,
    seed: int | None = None,
    sleep: SleepFn = asyncio.sleep,
) -> FastAPI:
    seed = int(os.getenv("SIM_SEED", "1")) if seed is None else seed
    speed = float(os.getenv("SIM_SPEED", "60")) if speed is None else speed
    the_bus: Bus = bus or KafkaBus(os.getenv("KAFKA_BOOTSTRAP", "kafka:9092"))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        close = getattr(the_bus, "close", None)
        if close is not None:
            await close()

    app = FastAPI(title="sim-engine", lifespan=lifespan)
    app.state.tasks = set()

    the_world = world or build_world(seed, int(os.getenv("SIM_CITIZENS", "2000")))

    async def ready() -> bool:
        return True

    app.include_router(make_health_router(ready))

    def _finished(task: asyncio.Task[None]) -> None:
        app.state.tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("hero scenario replay failed", exc_info=task.exception())

    @app.post("/scenarios/hero", status_code=202, response_model=HeroMetadata)
    async def run_hero(body: HeroRequest | None = None) -> HeroMetadata:
        """Start the hero scenario in the background and return its metadata.

        Only one run at a time (409 while one is active). Re-running with the same seed
        re-emits identical idempotency keys, which idempotent consumers will dedupe, so a
        repeat demo needs a different `seed` in the body (default: SIM_SEED).
        """
        if app.state.tasks:
            raise HTTPException(409, "a hero scenario run is already in progress")
        run_seed = body.seed if body and body.seed is not None else seed
        scenario = build_hero_scenario(the_world, run_seed)
        task = asyncio.create_task(replay(the_bus, the_world, [scenario], speed, sleep))
        app.state.tasks.add(task)
        task.add_done_callback(_finished)
        assert scenario.metadata is not None
        return scenario.metadata

    async def wait_idle() -> None:
        while app.state.tasks:
            await asyncio.gather(*list(app.state.tasks), return_exceptions=True)

    app.state.wait_idle = wait_idle
    return app
