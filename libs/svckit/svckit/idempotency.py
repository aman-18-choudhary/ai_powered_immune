"""Idempotency stores."""

from typing import Any, Protocol


class IdempotencyStore(Protocol):
    async def seen(self, key: str) -> bool: ...

    async def mark(self, key: str) -> None: ...


class InMemoryIdempotencyStore:
    def __init__(self) -> None:
        self._keys: set[str] = set()

    async def seen(self, key: str) -> bool:
        return key in self._keys

    async def mark(self, key: str) -> None:
        self._keys.add(key)


class RedisIdempotencyStore:
    def __init__(self, client: Any, prefix: str = "idem:", ttl_seconds: int = 7 * 86400) -> None:
        self._client = client
        self._prefix = prefix
        self._ttl = ttl_seconds

    async def seen(self, key: str) -> bool:
        return bool(await self._client.exists(self._prefix + key))

    async def mark(self, key: str) -> None:
        await self._client.set(self._prefix + key, "1", ex=self._ttl)
