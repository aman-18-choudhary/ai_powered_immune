"""Ingestion (complaints topic) and the maintenance loop; all store work runs in a worker thread.

Ingest rules, applied in this order: the location resolves to a table row (a city code, else the
state's anchor city with ``precision="state"``); an event older than the retention window behind
the newest accepted event, or further than ``future_skew_s`` ahead of the clock, is dropped and
counted (never retried); a repeated ``complaint_id`` is a no-op (first writer wins). A store
failure raises, so ``svckit.consume`` retries and then dead-letters the message.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from scam_contracts.models import Complaint
from scam_contracts.topics import Topics
from svckit.bus import Bus, consume

from .config import Settings
from .store import GeoStore
from .table import BY_CODE, CITY_CODE, STATE_ANCHOR

log = logging.getLogger("geo_intel")
GROUP = "geo-intel"


class NullIdempotencyStore:
    """The ``complaint_id`` unique key is the idempotency record; no second key set."""

    async def seen(self, key: str) -> bool:
        return False

    async def mark(self, key: str) -> None:
        return None

    async def claim(self, key: str, claim_ttl_s: float = 300) -> bool:
        return True

    async def release(self, key: str) -> None:
        return None


class GeoService:
    def __init__(
        self, store: GeoStore, cfg: Settings, bus: Bus | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:  # fmt: skip
        self.store, self.cfg, self.bus = store, cfg, bus
        self.clock = clock or (lambda: datetime.now(UTC))
        self.tasks: list[asyncio.Task[None]] = []
        self.counters = {
            "ingested": 0, "duplicates": 0, "dropped_old": 0, "dropped_future": 0,
            "dropped_location": 0, "pruned": 0, "prune_errors": 0, "store_errors": 0,
        }  # fmt: skip
        self._watermark: datetime | None = None

    # -------------------------------------------------------------------- lifecycle
    def start(self, *, consumers: bool = True, loops: bool = True) -> None:
        if consumers and self.bus is not None:
            self.tasks.append(
                asyncio.create_task(
                    consume(
                        self.bus,
                        Topics.COMPLAINTS,
                        GROUP,
                        Complaint,
                        self.on_complaint,
                        NullIdempotencyStore(),
                        self.cfg.max_retries,
                        self.cfg.retry_backoff_s,
                    )
                )
            )
        if loops:
            self.tasks.append(asyncio.create_task(self._prune_loop()))

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()

    # ----------------------------------------------------------------------- ingest
    async def on_complaint(self, c: Complaint) -> None:
        try:
            await asyncio.to_thread(self.ingest, c)
        except Exception:
            self.counters["store_errors"] += 1
            raise

    def ingest(self, c: Complaint) -> str:
        """Synchronous core (runs in a worker thread). Returns the outcome label."""
        if c.city_code is not None and c.city_code in BY_CODE:
            code, precision = c.city_code, "city"
        elif c.state_code is not None and c.state_code in STATE_ANCHOR:
            code, precision = CITY_CODE[STATE_ANCHOR[c.state_code][0]], "state"
        else:
            self.counters["dropped_location"] += 1
            return "dropped_location"
        ts = c.ts.astimezone(UTC)
        if ts > self.clock() + timedelta(seconds=self.cfg.future_skew_s):
            self.counters["dropped_future"] += 1
            return "dropped_future"
        mark = self.watermark()
        if mark is not None and ts < mark - timedelta(seconds=self.cfg.retention_s):
            self.counters["dropped_old"] += 1
            return "dropped_old"
        new = self.store.insert(
            complaint_id=c.complaint_id, ts=ts, city_code=code, precision=precision,
            kind=c.kind, amount_bucket=c.amount_bucket, case_ref=c.case_ref,
        )  # fmt: skip
        if not new:
            self.counters["duplicates"] += 1
            return "duplicate"
        if self._watermark is None or ts > self._watermark:
            self._watermark = ts
        self.counters["ingested"] += 1
        return "ingested"

    def watermark(self) -> datetime | None:
        """Newest accepted event time (cached; read from the store after a restart)."""
        if self._watermark is None:
            self._watermark = self.store.max_ts()
        return self._watermark

    # -------------------------------------------------------------------- maintenance
    def prune_once(self) -> int:
        mark = self.watermark()
        if mark is None:
            return 0
        n = self.store.prune(mark - timedelta(seconds=self.cfg.retention_s), self.cfg.prune_batch)
        self.counters["pruned"] += n
        return n

    async def _prune_loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.prune_once)
            except Exception:
                self.counters["prune_errors"] += 1
                log.warning("prune failed")
            await asyncio.sleep(self.cfg.prune_interval_s)


__all__ = ["GROUP", "GeoService", "NullIdempotencyStore"]
