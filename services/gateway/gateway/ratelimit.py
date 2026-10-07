"""Redis fixed-window limiters (per-role request limits, failed-login limits).

Every method fails open (allows, logs a warning) if Redis errors.
"""

import hashlib
import logging
import math
import time
from collections.abc import Callable

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

DEFAULT_LIMITS: dict[str, int] = {"citizen": 30, "analyst": 300, "officer": 300, "admin": 600}
WINDOW_SECONDS = 60
LOGIN_FAILURE_LIMIT = 10


class RateLimiter:
    def __init__(
        self,
        redis: Redis,
        limits: dict[str, int] | None = None,
        window: int = WINDOW_SECONDS,
        clock: Callable[[], float] = time.time,
        login_failure_limit: int = LOGIN_FAILURE_LIMIT,
    ) -> None:
        self._redis = redis
        self._limits = {**DEFAULT_LIMITS, **(limits or {})}
        self._window = window
        self._clock = clock
        self._login_limit = login_failure_limit

    def _key(self, base: str) -> tuple[str, float]:
        now = self._clock()
        return f"{base}:{int(now // self._window)}", now

    def _retry_after(self, now: float) -> int:
        reset = (int(now // self._window) + 1) * self._window
        return min(self._window, max(1, math.ceil(reset - now)))

    async def _incr(self, key: str) -> int:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, self._window * 2)
            count, _ = await pipe.execute()
        return int(count)

    async def check(self, role: str, sub: str) -> int | None:
        """Count a request. Returns None if allowed, else seconds until the window resets."""
        key, now = self._key(f"gw:rl:{role}:{sub}")
        try:
            count = await self._incr(key)
        except Exception:
            logger.warning("rate limit store unavailable; allowing request")
            return None
        return self._retry_after(now) if count > self._limits[role] else None

    @staticmethod
    def _login_keys(ip: str, username: str) -> list[str]:
        user = hashlib.sha256(username.encode()).hexdigest()[:16]
        return [f"gw:rl:login:ip:{ip}", f"gw:rl:login:user:{user}"]

    async def login_blocked(self, ip: str, username: str) -> int | None:
        """Seconds to wait if this IP or username has too many recent failures."""
        try:
            for base in self._login_keys(ip, username):
                key, now = self._key(base)
                raw = await self._redis.get(key)
                if raw is not None and int(raw) >= self._login_limit:
                    return self._retry_after(now)
        except Exception:
            logger.warning("login limiter unavailable; allowing attempt")
        return None

    async def login_failed(self, ip: str, username: str) -> None:
        try:
            for base in self._login_keys(ip, username):
                await self._incr(self._key(base)[0])
        except Exception:
            logger.warning("login limiter unavailable; failure not recorded")
