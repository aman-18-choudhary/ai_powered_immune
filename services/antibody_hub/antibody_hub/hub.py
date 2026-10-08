"""Async facade: store calls via ``asyncio.to_thread``, outbox drain onto the bus, Bloom build."""

import asyncio
import logging
from typing import Any

from svckit.bus import Bus

from .bloom import DEFAULT_FP_RATE, BloomFilter
from .store import AntibodyStore, SubmitResult

log = logging.getLogger("antibody_hub")


class Hub:
    def __init__(
        self, store: AntibodyStore, bus: Bus, fp_rate: float = DEFAULT_FP_RATE,
        retention_days: float = 7.0,
    ) -> None:  # fmt: skip
        self.retention_days = retention_days
        self._bloom_cache: tuple[str, dict[str, Any]] | None = None
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

    async def revoke(
        self, ab_id: str, actor: str, reason: str, bank: str | None = None
    ) -> dict[str, Any]:
        rec = await self._run(self.store.revoke, ab_id, actor, reason, bank)
        await self.drain()
        return rec

    async def add_protected(self, key_hash: str, actor: str, note: str | None) -> Any:
        res = await self._run(self.store.add_protected, key_hash, actor, note)
        await self.drain()
        return res

    async def remove_protected(self, key_hash: str, actor: str) -> bool:
        res = await self._run(self.store.remove_protected, key_hash, actor)
        await self.drain()
        return res

    async def sweep(self) -> int:
        n = await self._run(self.store.expire_due)
        await self.drain()
        await self._run(self.store.purge_sent, self.retention_days)
        return n

    async def drain(self) -> int:
        """Publish pending outbox rows in id order, at-least-once. A failing row is retried on
        the next drain; later rows with the SAME key are held back in this pass (per-key order),
        rows for other keys continue. After ``max_attempts`` failures the row is parked (counter
        ``antibody_hub_outbox_parked``) and its key stays held until an operator intervenes.
        Never raises."""
        sent = 0
        async with self._drain_lock:
            try:
                blocked: set[str] = set()
                for row_id, topic, key, body in await self._run(self.store.pending):
                    if key in blocked:
                        continue
                    try:
                        await self.bus.publish_raw(topic, key, body.encode())
                    except Exception:
                        blocked.add(key)
                        parked = await self._run(self.store.record_failure, row_id)
                        log.warning("outbox publish failed (row %d, parked=%s)", row_id, parked)
                        continue
                    await self._run(self.store.mark_sent, row_id)
                    sent += 1
            except Exception:
                log.warning("outbox drain interrupted after %d rows", sent, exc_info=True)
        return sent

    async def bloom_version(self) -> str:
        return await self._run(self.store.bloom_version)

    async def bloom(self, version: str | None = None) -> dict[str, Any]:
        """Snapshot for ``version`` (built off-thread, cached by version)."""
        version = version or await self.bloom_version()
        if self._bloom_cache is not None and self._bloom_cache[0] == version:
            return self._bloom_cache[1]
        snap = await self._run(self._build_bloom, version)
        self._bloom_cache = (version, snap)
        return snap

    def _build_bloom(self, version: str) -> dict[str, Any]:
        hashes = self.store.active_hashes()
        bf = BloomFilter.for_items(hashes, self.fp_rate)
        return bf.to_snapshot(version=version, generated_at=self.store._clock())
