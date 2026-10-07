"""Per-role fixed-window rate limiting backed by Redis. Fails open if Redis is unavailable."""

import logging
import time
from collections.abc import Callable

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

DEFAULT_LIMITS: dict[str, int] = {"citizen": 30, "analyst": 300, "officer": 300, "admin": 600}
WINDOW_SECONDS = 60


class RateLimiter:
    def __init__(
        self,
        redis: Redis,
        limits: dict[str, int] | None = None,
        window: int = WINDOW_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._redis = redis
        self._limits = {**DEFAULT_LIMITS, **(limits or {})}
        self._window = window
        self._clock = clock

    async def check(self, role: str, sub: str) -> int | None:
        """Count a request. Returns None if allowed, else seconds until the window resets."""
        now = self._clock()
        window_id = int(now // self._window)
        key = f"gw:rl:{role}:{sub}:{window_id}"
        try:
            count = await self._redis.incr(key)
            if count == 1:
                await self._redis.expire(key, self._window * 2)
        except Exception:
            logger.warning("rate limit store unavailable; allowing request", exc_info=False)
            return None
        if count > self._limits[role]:
            return max(1, int((window_id + 1) * self._window - now) + 1)
        return None
