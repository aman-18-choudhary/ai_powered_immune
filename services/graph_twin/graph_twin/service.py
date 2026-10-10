"""Async wrapper around ``GraphTwin``: consumers, detection loop, maintenance, audit delivery.

Everything blocking (SQLAlchemy, networkx) runs through ``asyncio.to_thread``; per-event work is
bounded (one transaction, O(degree) index updates); detection is debounced
(``GRAPH_DETECT_INTERVAL_S``) and runs over the touched components only. Ledger delivery uses the
shared ``svckit.drain.Drainer`` (fire-and-forget, capped, breaker) plus a periodic sweeper of the
``gt_audit_outbox`` table; no event or API path awaits the ledger.
"""

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any, TypeVar

from scam_contracts.models import Antibody, Transaction, TxnDecision
from scam_contracts.topics import Topics
from svckit.bus import Bus, consume
from svckit.drain import Drainer

from .model import AuditRow
from .takedown import CancelToken
from .twin import GraphTwin

log = logging.getLogger("graph_twin")
T = TypeVar("T")

GROUP_PREFIX = "graph-twin"
MAX_BACKGROUND_TASKS = 100
RESULT_CACHE_SIZE = 256


class HeavyBusy(Exception):
    """The heavy-operation queue (``GRAPH_HEAVY_QUEUE``) is full: the API answers 429."""


class NullIdempotencyStore:
    """The twin's own tables are the idempotency record (txn_id, (txn_id, decision_seq),
    (antibody_id, revoked, expires_at)); a second, ever-growing key set would only duplicate
    them, so the consume loop's claim/seen are no-ops here."""

    async def seen(self, key: str) -> bool:
        return False

    async def mark(self, key: str) -> None:
        return None

    async def claim(self, key: str, claim_ttl_s: float = 300) -> bool:
        return True

    async def release(self, key: str) -> None:
        return None


class TwinService:
    def __init__(
        self,
        twin: GraphTwin,
        bus: Bus,
        *,
        drainer: Drainer | None = None,
        max_retries: int = 3,
        backoff_s: float = 0.0,
    ) -> None:
        self.twin = twin
        self.bus = bus
        self.cfg = twin.cfg
        self.drainer = drainer or Drainer()
        self.max_retries, self.backoff_s = max_retries, backoff_s
        self.tasks: list[asyncio.Task[None]] = []
        self._bg: set[asyncio.Task[Any]] = set()
        self._wake = asyncio.Event()
        self.heavy = asyncio.Semaphore(self.cfg.heavy_concurrency)
        self.heavy_timeouts = 0
        self.heavy_rejected = 0
        self._heavy_waiting = 0
        self._inflight: dict[Hashable, asyncio.Future[Any]] = {}
        self._results: OrderedDict[Hashable, Any] = OrderedDict()
        self.audit_dropped = 0
        self.audit_sent = 0
        self.detect_errors = 0
        self.last_detect_ms = 0.0

    # ------------------------------------------------------------------ lifecycle
    def start(self, *, consumers: bool = True, loops: bool = True) -> None:
        idem = NullIdempotencyStore()
        if consumers:
            for topic, role, model, handler in (
                (Topics.TXN_EVENTS, "txn", Transaction, self.on_txn),
                (Topics.TXN_DECISIONS, "decisions", TxnDecision, self.on_decision),
                (Topics.ANTIBODIES, "antibody", Antibody, self.on_antibody),
            ):
                self.tasks.append(
                    asyncio.create_task(
                        consume(
                            self.bus,
                            topic,
                            f"{GROUP_PREFIX}-{role}",
                            model,
                            handler,
                            idem,
                            self.max_retries,
                            self.backoff_s,
                        )
                    )
                )
        if loops:
            self.tasks.append(asyncio.create_task(self._detect_loop()))
            self.tasks.append(asyncio.create_task(self._maintenance_loop()))
            self.tasks.append(asyncio.create_task(self._audit_sweeper()))

    async def stop(self) -> None:
        for t in [*self.tasks, *self._bg]:
            t.cancel()
        await asyncio.gather(*self.tasks, *self._bg, return_exceptions=True)
        self.tasks.clear()
        self._bg.clear()
        await self.drainer.aclose()

    # -------------------------------------------------------------------- handlers
    async def on_txn(self, t: Transaction) -> None:
        if await asyncio.to_thread(self.twin.apply_txn, t):
            self._wake.set()

    async def on_decision(self, d: TxnDecision) -> None:
        if await asyncio.to_thread(self.twin.apply_decision, d):
            self._wake.set()

    async def on_antibody(self, ab: Antibody) -> None:
        if await asyncio.to_thread(self.twin.apply_antibody, ab):
            self._wake.set()

    # ------------------------------------------------------------------- detection
    async def detect_once(self) -> int:
        """One incremental detection pass (also callable from tests / ``flush``)."""
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        res = await asyncio.to_thread(self.twin.detect_dirty)
        self.last_detect_ms = (loop.time() - t0) * 1000
        self.schedule_audit(res.audit)
        return len(res.touched_rings)

    async def _detect_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.cfg.detect_interval_s * 5)
            except TimeoutError:
                pass
            self._wake.clear()
            try:
                if self.twin.dirty or self.twin._carry[0] or self.twin._carry[1]:
                    await self.detect_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.detect_errors += 1
                log.warning("detection failed (%s)", type(e).__name__)
            await asyncio.sleep(self.cfg.detect_interval_s)

    async def flush(self) -> None:
        """Run detection now and wait for scheduled ledger deliveries (tests, shutdown)."""
        await self.detect_once()
        await self.drainer.settle()

    async def _maintenance_loop(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.maintenance_interval_s)
            try:
                await asyncio.to_thread(self.twin.sweep)
                self.schedule_audit(self.twin.pending_audit_rows)
                self._wake.set()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("maintenance failed (%s)", type(e).__name__)

    # --------------------------------------------------------------------- heavy
    async def run_heavy(
        self, fn: Callable[[CancelToken], T], key: Hashable | None = None
    ) -> T:  # fmt: skip
        """Run a CPU-heavy function off-loop under the heavy-concurrency semaphore, a bounded wait
        queue and a deadline (``GRAPH_FLOW_TIMEOUT_S``).

        * At most ``GRAPH_HEAVY_CONCURRENCY`` run and ``GRAPH_HEAVY_QUEUE`` wait; further requests
          raise ``HeavyBusy`` at once (no unbounded pile-up).
        * ``fn`` receives a ``CancelToken`` whose deadline is the flow timeout; on timeout the
          token is cancelled, so an abandoned thread stops at its next check. The semaphore is
          released only when the thread has really ended.
        * ``key`` (ring id, ring version, request) enables a small LRU result cache and single
          flight: identical concurrent requests share one job.
        """
        timeout = self.cfg.flow_timeout_s
        if key is not None:
            if key in self._results:
                self._results.move_to_end(key)
                return self._results[key]  # type: ignore[no-any-return]
            shared = self._inflight.get(key)
            if shared is not None:
                try:
                    return await asyncio.wait_for(asyncio.shield(shared), timeout)  # type: ignore[no-any-return]
                except TimeoutError:
                    self.heavy_timeouts += 1
                    raise
        if self.heavy.locked() and self._heavy_waiting >= self.cfg.heavy_queue:
            self.heavy_rejected += 1
            raise HeavyBusy
        self._heavy_waiting += 1
        try:
            await asyncio.wait_for(self.heavy.acquire(), timeout)
        except TimeoutError:
            self.heavy_timeouts += 1
            raise
        finally:
            self._heavy_waiting -= 1
        token = CancelToken(timeout)
        fut: asyncio.Future[T] = asyncio.ensure_future(asyncio.to_thread(fn, token))
        if key is not None:
            self._inflight[key] = fut

        def _done(f: "asyncio.Future[T]") -> None:
            self.heavy.release()
            if key is not None:
                self._inflight.pop(key, None)
            if f.cancelled():
                return
            if f.exception() is None and key is not None:  # retrieved either way
                self._results[key] = f.result()
                while len(self._results) > RESULT_CACHE_SIZE:
                    self._results.popitem(last=False)

        fut.add_done_callback(_done)
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout)
        except TimeoutError:
            token.cancel()
            self.heavy_timeouts += 1
            raise

    # ----------------------------------------------------------------------- audit
    def schedule_audit(self, rows: list[AuditRow]) -> None:
        for r in rows:
            self.drainer.schedule(r.dedupe, lambda d=r.dedupe: self._deliver(d))

    async def _deliver(self, dedupe: str) -> bool:
        row = await asyncio.to_thread(self.twin.store.get_audit, dedupe)
        if row is None or row["sent_at"] is not None:
            return True
        await self.bus.publish_raw(Topics.LEDGER, row["msg_key"], row["body"].encode())
        await asyncio.to_thread(self.twin.store.mark_audit_sent, dedupe, self.twin.clock())
        self.audit_sent += 1
        return True

    async def _audit_sweeper(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.audit_sweep_interval_s)
            try:
                rows = await asyncio.to_thread(
                    self.twin.store.pending_audit, self.cfg.audit_sweep_batch
                )
                await self.drainer.sweep([r["dedupe"] for r in rows], self._deliver)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("audit sweep failed (%s)", type(e).__name__)

    def audit_takedown(self, row: AuditRow) -> None:
        """Fire-and-forget: persist (off-loop) and deliver one ``takedown.simulated`` entry. The
        API response never waits for it; the set of in-flight jobs is capped."""
        if len(self._bg) >= MAX_BACKGROUND_TASKS:
            self.audit_dropped += 1
            return

        async def job() -> None:
            try:
                await asyncio.to_thread(self.twin.enqueue_audit_row, row)
                self.schedule_audit([row])
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.audit_dropped += 1
                log.warning("takedown audit not stored (%s)", type(e).__name__)

        task = asyncio.ensure_future(job())
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def settle(self) -> None:
        while self._bg:
            await asyncio.gather(*list(self._bg), return_exceptions=True)
        await self.drainer.settle()


__all__ = ["NullIdempotencyStore", "TwinService"]
