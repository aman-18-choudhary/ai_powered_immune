"""Async facade: store calls via ``asyncio.to_thread``, outbox drain onto the bus, Bloom build."""

import asyncio
import hashlib
import logging
from typing import Any

from svckit.bus import Bus

from .bloom import DEFAULT_FP_RATE, BloomFilter
from .store import AntibodyStore, SubmitResult

log = logging.getLogger("antibody_hub")


class Hub:
    def __init__(self, store: AntibodyStore, bus: Bus, fp_rate: float = DEFAULT_FP_RATE) -> None:
        self.store = store
        self.bus = bus
        self.fp_rate = fp_rate
        self._drain_lock = asyncio.Lock()

    async def _run(self, fn: Any, *a: Any, **kw: Any) -> Any:
        return await asyncio.to_thread(fn, *a, **kw)

    async def submit(self, *a: Any, **kw: Any) -> SubmitResult:
        res = await self._run(self.store.submit, *a, **kw)
        await self.drain()  # also delivers anything stranded earlier (idempotent re-entry)
        return res

    async def revoke(self, ab_id: str, actor: str, reason: str) -> dict[str, Any]:
        rec = await self._run(self.store.revoke, ab_id, actor, reason)
        await self.drain()
        return rec

    async def add_protected(self, key_hash: str, actor: str, note: str | None) -> Any:
        res = await self._run(self.store.add_protected, key_hash, actor, note)
        await self.drain()
        return res

    async def sweep(self) -> int:
        n = await self._run(self.store.expire_due)
        await self.drain()
        return n

    async def drain(self) -> int:
        """Publish pending outbox rows in order, at-least-once; stop at the first failure so
        per-key ordering holds. Never raises: a failing bus leaves the rows for the next drain."""
        sent = 0
        async with self._drain_lock:
            try:
                for row_id, topic, key, body in await self._run(self.store.pending):
                    await self.bus.publish_raw(topic, key, body.encode())
                    await self._run(self.store.mark_sent, row_id)
                    sent += 1
            except Exception:
                log.warning("outbox drain interrupted after %d rows", sent, exc_info=True)
        return sent

    async def bloom(self) -> dict[str, Any]:
        hashes = await self._run(self.store.active_hashes)
        bf = BloomFilter.for_items(hashes, self.fp_rate)
        digest = hashlib.sha256()
        digest.update(f"{bf.m}:{bf.k}:{bf.fp_rate}\n".encode())
        for h in hashes:
            digest.update(h.encode())
        version = digest.hexdigest()[:32]
        return bf.to_snapshot(version=version, generated_at=self.store._clock())
