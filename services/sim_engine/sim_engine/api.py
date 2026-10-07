"""HTTP surface of the sim engine: trigger the hero scenario."""

import asyncio
import os

from fastapi import FastAPI
from svckit.bus import Bus, KafkaBus
from svckit.health import make_health_router

from .replay import HeroMetadata, SleepFn, build_hero_scenario, replay
from .world import World, build_world


def create_app(
    bus: Bus | None = None,
    world: World | None = None,
    speed: float | None = None,
    seed: int | None = None,
    sleep: SleepFn = asyncio.sleep,
) -> FastAPI:
    seed = int(os.getenv("SIM_SEED", "1")) if seed is None else seed
    speed = float(os.getenv("SIM_SPEED", "60")) if speed is None else speed
    app = FastAPI(title="sim-engine")
    app.state.tasks = set()

    the_bus: Bus = bus or KafkaBus(os.getenv("KAFKA_BOOTSTRAP", "kafka:9092"))
    the_world = world or build_world(seed, int(os.getenv("SIM_CITIZENS", "2000")))

    async def ready() -> bool:
        return True

    app.include_router(make_health_router(ready))

    @app.post("/scenarios/hero", status_code=202, response_model=HeroMetadata)
    async def run_hero() -> HeroMetadata:
        scenario = build_hero_scenario(the_world, seed)
        task = asyncio.create_task(replay(the_bus, the_world, [scenario], speed, sleep))
        app.state.tasks.add(task)
        task.add_done_callback(app.state.tasks.discard)
        assert scenario.metadata is not None
        return scenario.metadata

    async def wait_idle() -> None:
        while app.state.tasks:
            await asyncio.gather(*list(app.state.tasks))

    app.state.wait_idle = wait_idle
    return app
