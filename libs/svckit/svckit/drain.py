"""Fire-and-forget delivery of outbox entries with bounded concurrency and a circuit breaker.

The hot path (a fraud decision, a CallRisk alert) must contain NO await on the ledger. It calls
``schedule`` (synchronous, returns at once); the delivery runs in a background task. Guards:

* per-key in-flight set (a second ``schedule`` for the same key is a no-op),
* a global cap on concurrent deliveries (default ``LEDGER_DRAIN_CONCURRENCY`` = 8); the rest is
  left for the periodic sweeper,
* every delivery has a timeout (``LEDGER_EMIT_TIMEOUT_S`` = 0.5 s); timeouts and errors count as
  failures,
* a breaker: after ``breaker_threshold`` consecutive failures nothing is scheduled for
  ``LEDGER_BREAKER_COOLDOWN_S`` (10 s); the sweeper still probes with ONE attempt per cycle and
  closes the breaker on the first success,
* strong references to tasks; ``aclose`` cancels and awaits them (pending entries stay in the
  outbox store, so cancelling loses nothing).
Delivery functions return True on success; they must be idempotent (at-least-once).
"""

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable, Sequence

log = logging.getLogger("svckit.drain")


class Drainer:
    def __init__(
        self,
        *,
        cap: int | None = None,
        timeout_s: float | None = None,
        cooldown_s: float | None = None,
        breaker_threshold: int = 5,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cap = cap if cap is not None else int(os.getenv("LEDGER_DRAIN_CONCURRENCY", "8"))
        self.timeout_s = (
            timeout_s if timeout_s is not None else float(os.getenv("LEDGER_EMIT_TIMEOUT_S", "0.5"))
        )
        self.cooldown_s = (
            cooldown_s
            if cooldown_s is not None
            else float(os.getenv("LEDGER_BREAKER_COOLDOWN_S", "10"))
        )
        self.threshold = breaker_threshold
        self._clock = clock
        self._failures = 0
        self._open_until = 0.0
        self._inflight: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------ state
    @property
    def live_tasks(self) -> int:
        return len(self._tasks)

    @property
    def breaker_open(self) -> bool:
        return self._failures >= self.threshold

    def _note(self, ok: bool) -> None:
        if ok:
            self._failures = 0
            self._open_until = 0.0
            return
        self._failures += 1
        if self._failures >= self.threshold:
            self._open_until = self._clock() + self.cooldown_s

    def _cooling(self) -> bool:
        return self.breaker_open and self._clock() < self._open_until

    # --------------------------------------------------------------- delivery
    async def _attempt(self, key: str, fn: Callable[[], Awaitable[bool]]) -> bool:
        self._inflight.add(key)
        ok = False
        try:
            ok = bool(await asyncio.wait_for(fn(), self.timeout_s))
        except TimeoutError:
            log.warning("ledger delivery timed out")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("ledger delivery failed (%s)", type(e).__name__)
        finally:
            self._inflight.discard(key)
        self._note(ok)
        return ok

    def schedule(self, key: str, fn: Callable[[], Awaitable[bool]]) -> "asyncio.Task[None] | None":
        """Start a background delivery unless the key is in flight, the cap is reached or the
        breaker is cooling down. Never awaits; never raises."""
        if key in self._inflight or len(self._tasks) >= self.cap or self._cooling():
            return None
        self._inflight.add(key)  # claim before the task starts (a second schedule is a no-op)

        async def run() -> None:
            await self._attempt(key, fn)

        task = asyncio.ensure_future(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def sweep(self, keys: Sequence[str], fn: Callable[[str], Awaitable[bool]]) -> int:
        """Deliver ``keys`` (the sweeper's cycle) with at most ``cap`` concurrent attempts; while
        the breaker is open make a single probe first. Returns the number delivered."""
        todo = [k for k in keys if k not in self._inflight]
        done = 0
        if todo and self.breaker_open:
            first = todo.pop(0)
            if not await self._attempt(first, lambda: fn(first)):
                return 0
            done += 1
        sem = asyncio.Semaphore(self.cap)

        async def one(k: str) -> bool:
            async with sem:
                if k in self._inflight:
                    return False
                return await self._attempt(k, lambda: fn(k))

        return done + sum(await asyncio.gather(*(one(k) for k in todo)))

    async def settle(self) -> None:
        """Wait for scheduled deliveries (tests, graceful shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def aclose(self) -> None:
        tasks = list(self._tasks)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
