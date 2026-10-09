"""The real service code, wired in one process (same wiring as tests/e2e/audit harness).

One ``InMemoryBus`` shared by everything; the ledger consumes only ``Topics.LEDGER``:

* call-guard: the real ``SessionScorer`` and ``handle_event`` consumer path,
* txn-guard: two ``TxnGuardService`` instances (bank_a, bank_b) with real hold stores and the
  ``BusAuditSink`` outbox; bank_b learns antibodies through the real antibody consumer,
* antibody-hub: the real ``AntibodyStore`` (SQLite file) and ``Hub`` outbox drain,
* evidence-ledger: the real app (consumer, SQLite store, signing key) driven over its HTTP API.

All blocking work (SQLite, scorers) is off the event loop: the services already use
``asyncio.to_thread`` for their stores; call-guard's classifier is pre-scored in a thread
(``PreScorer``) and the session logic then consumes the memoised result.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from collections.abc import Callable
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from antibody_hub.hub import Hub
from antibody_hub.store import AntibodyStore
from call_guard.model import Scorer as CallScorer
from call_guard.session import InMemorySessionStore, SessionScorer
from evidence_ledger.api import create_app as create_ledger_app
from evidence_ledger.keys import KeyRing, Signer
from scam_contracts.models import Antibody, CallEvent, Reason
from svckit.bus import InMemoryBus
from svckit.idempotency import InMemoryIdempotencyStore
from txn_guard.antibody_cache import AntibodyLookup, InMemoryAntibodyCache
from txn_guard.consumer import run_antibody_consumer
from txn_guard.history import InMemoryHistoryStore
from txn_guard.holds import BusAuditSink, Hold, InMemoryHoldStore
from txn_guard.model import Scorer as TxnScorer
from txn_guard.service import TxnGuardService

log = logging.getLogger("demo_server.harness")

LEDGER_BASE = "http://ledger.internal"
LEDGER_CLOCK_START = datetime(2026, 1, 1, tzinfo=UTC)


class SimClock:
    """The simulated 'now' shared by the banks, the hub and the hold stores."""

    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


class LedgerClock:
    """Deterministic ledger receipt clock: one second per call from a fixed start."""

    def __init__(self, t: datetime = LEDGER_CLOCK_START) -> None:
        self.t = t

    def __call__(self) -> datetime:
        self.t += timedelta(seconds=1)
        return self.t


class PreScorer(CallScorer):
    """call-guard ``Scorer`` whose per-chunk result can be computed off the event loop first.

    ``prescore`` runs the real classifier + rules in a worker thread and memoises the result by
    the event's idempotency key; ``score_chunk`` (called synchronously by ``SessionScorer``)
    returns the memoised result, or computes it inline if it was not prescored."""

    def __init__(self, base: CallScorer) -> None:
        self.__dict__.update(base.__dict__)
        self._base = base
        self._memo: dict[str, tuple[float, list[Reason], str]] = {}

    async def prescore(self, event: CallEvent) -> None:
        if event.idempotency_key not in self._memo:
            self._memo[event.idempotency_key] = await asyncio.to_thread(
                self._base.score_chunk, event
            )

    def score_chunk(self, event: CallEvent) -> tuple[float, list[Reason], str]:
        got = self._memo.get(event.idempotency_key)
        return got if got is not None else self._base.score_chunk(event)

    def forget(self, event: CallEvent) -> None:
        self._memo.pop(event.idempotency_key, None)


class ObservedHoldStore(InMemoryHoldStore):
    """In-memory hold store that reports lifecycle changes to a synchronous observer."""

    def __init__(self, audit: Any, clock: Callable[[], datetime], observer: Callable[..., None]):
        super().__init__(audit=audit, clock=clock)
        self._observer = observer
        self._created: set[str] = set()

    async def create(self, txn_id: str, *a: Any, **kw: Any) -> Hold:
        h = await super().create(txn_id, *a, **kw)
        if txn_id not in self._created:
            self._created.add(txn_id)
            self._observer("created", h)
        return h

    async def upgrade(self, txn_id: str, *a: Any, **kw: Any) -> Hold | None:
        h = await super().upgrade(txn_id, *a, **kw)
        if h is not None:
            self._observer("upgraded", h)
        return h

    async def resolve(self, txn_id: str, *a: Any, **kw: Any) -> Hold:
        before = await self._get(txn_id)
        h = await super().resolve(txn_id, *a, **kw)
        if before is not None and before.state == "open" and h.state != "open":
            self._observer("resolved", h)
        return h


class TimedService(TxnGuardService):
    """Records when each antibody finished being applied (real, measured latency)."""

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.applied_at: dict[str, float] = {}
        self._events: dict[str, asyncio.Event] = {}

    def applied_event(self, antibody_id: str) -> asyncio.Event:
        return self._events.setdefault(antibody_id, asyncio.Event())

    async def handle_antibody(self, ab: Antibody) -> Any:
        out = await super().handle_antibody(ab)
        if not ab.revoked:
            self.applied_at.setdefault(ab.antibody_id, time.perf_counter())
            self.applied_event(ab.antibody_id).set()
        return out


class Harness:
    """Owns every resource of one run; ``close`` releases all of them (cancel-safe)."""

    def __init__(
        self, workdir: Path, txn_scorer: TxnScorer, call_scorer: CallScorer, signer: Signer,
        gateway_secret: str, sim_start: datetime, observer: Callable[..., None],
    ) -> None:  # fmt: skip
        self.workdir = workdir
        self.bus = InMemoryBus()
        self.clock = SimClock(sim_start)
        self.signer = signer
        self.keyring = KeyRing(signer)
        self._secret = gateway_secret
        self._stack = AsyncExitStack()
        self._closed = False
        self.pre = PreScorer(call_scorer)
        self.sessions = SessionScorer(InMemorySessionStore(), self.pre)
        self.call_idem = InMemoryIdempotencyStore()
        self.hub_store = AntibodyStore(f"sqlite:///{workdir / 'hub.db'}", clock=self.clock)
        self.hub = Hub(self.hub_store, self.bus)
        self.ledger_app = create_ledger_app(
            database_url=f"sqlite:///{workdir / 'ledger.db'}", bus=self.bus,
            clock=LedgerClock(), keyring=self.keyring, checkpoint_every=50,
            maintenance_interval_s=0, consume=True,
        )  # fmt: skip
        self.ledger_store = self.ledger_app.state.store
        self.banks: dict[str, TimedService] = {}
        for slot in ("bank_a", "bank_b"):
            holds = ObservedHoldStore(
                BusAuditSink(self.bus), self.clock,
                lambda kind, hold, _s=slot: observer(_s, kind, hold),
            )  # fmt: skip
            cache = InMemoryAntibodyCache(clock=self.clock)
            self.banks[slot] = TimedService(
                txn_scorer, InMemoryHistoryStore(), holds, self.bus, InMemoryIdempotencyStore(),
                clock=self.clock, antibodies=AntibodyLookup(cache),
            )  # fmt: skip
        self._ab_task: asyncio.Task[None] | None = None
        self.ledger = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.ledger_app), base_url=LEDGER_BASE, timeout=30.0
        )

    async def open(self) -> None:
        await self._stack.enter_async_context(
            self.ledger_app.router.lifespan_context(self.ledger_app)
        )
        self._ab_task = run_antibody_consumer(
            self.bus, self.banks["bank_b"], InMemoryIdempotencyStore(), "bank_b"
        )

    def ledger_headers(self, role: str, sub: str) -> dict[str, str]:
        return {
            "X-Gateway-Secret": self._secret,
            "X-Principal-Role": role,
            "X-Principal-Sub": sub,
        }

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._ab_task is not None:
            self._ab_task.cancel()
            await asyncio.wait([self._ab_task])
        for step in (
            self.ledger.aclose(),
            self._stack.aclose(),  # ledger consumer + store close
            asyncio.to_thread(self.hub_store.close),
            *(b.holds.aclose() for b in self.banks.values()),  # type: ignore[attr-defined]
            self._close_outbox(),
        ):
            try:
                await step
            except Exception as e:  # keep closing the rest
                log.warning("harness close step failed (%s)", type(e).__name__)

    async def _close_outbox(self) -> None:
        ob = getattr(self.sessions, "ledger_outbox", None)
        if ob is not None:
            await ob.aclose()


def new_gateway_secret() -> str:
    """The shared secret between the demo server and its in-process ledger app. Set once in the
    environment (the ledger reads ``LEDGER_GATEWAY_SECRET`` per request)."""
    existing = os.environ.get("LEDGER_GATEWAY_SECRET")
    if existing:
        return existing
    s = secrets.token_hex(32)
    os.environ["LEDGER_GATEWAY_SECRET"] = s
    return s
