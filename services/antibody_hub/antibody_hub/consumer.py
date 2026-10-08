"""Background maintenance: periodic expiry sweep + outbox drain (at-least-once delivery)."""

import asyncio
import logging

from .hub import Hub

log = logging.getLogger("antibody_hub")


async def run_maintenance(hub: Hub, interval_s: float) -> None:
    while True:
        try:
            await hub.sweep()  # expires lapsed antibodies, then drains the outbox
        except Exception:
            log.warning("antibody maintenance failed", exc_info=True)
        await asyncio.sleep(interval_s)
