"""Idempotency stores."""

import time
from typing import Any, Protocol


class IdempotencyStore(Protocol):
    async def seen(self, key: str) -> bool: ...

    async def mark(self, key: str) -> None: ...

    async def claim(self, key: str, claim_ttl_s: float = 300) -> bool:
        """Atomically claim `key` for `claim_ttl_s`; False if already claimed or marked."""
        ...

    async def release(self, key: str) -> None:
        """Drop a claim so the message can be retried later."""
        ...


class InMemoryIdempotencyStore:
    def __init__(self) -> None:
        self._done: set[str] = set()
        self._claims: dict[str, float] = {}

    async def seen(self, key: str) -> bool:
        return key in self._done

    async def mark(self, key: str) -> None:
        self._done.add(key)
        self._claims.pop(key, None)

    async def claim(self, key: str, claim_ttl_s: float = 300) -> bool:
        now = time.monotonic()
        if key in self._done or self._claims.get(key, 0.0) > now:
            return False
        self._claims[key] = now + claim_ttl_s
        return True

    async def release(self, key: str) -> None:
        self._claims.pop(key, None)


class RedisIdempotencyStore:
    def __init__(self, client: Any, prefix: str = "idem:", ttl_seconds: int = 7 * 86400) -> None:
        self._client = client
        self._prefix = prefix
        self._ttl = ttl_seconds

    async def seen(self, key: str) -> bool:
        return bool(await self._client.exists(self._prefix + key))

    async def mark(self, key: str) -> None:
        await self._client.set(self._prefix + key, "done", ex=self._ttl)

    async def claim(self, key: str, claim_ttl_s: float = 300) -> bool:
        px = max(1, int(claim_ttl_s * 1000))
        return bool(await self._client.set(self._prefix + key, "claim", nx=True, px=px))

    async def release(self, key: str) -> None:
        await self._client.delete(self._prefix + key)
