"""Per-call risk accumulation with time decay.

``accumulate`` is the pure update: the previous score decays with half-life ``half_life_s`` of
event time, then the new chunk score is merged by noisy-OR (chunks below ``MIN_CHUNK_SCORE``
are treated as noise so long benign calls cannot drift upwards). State lives in a store
behind a small protocol: Redis in the service, an in-memory dict in tests.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from scam_contracts.models import CallEvent, CallRisk, Reason

from .model import Scorer, no_risk_reason
from .rules import CALL_RISK_THRESHOLD

log = logging.getLogger(__name__)

HALF_LIFE_S = 300.0
MIN_CHUNK_SCORE = 0.1
SESSION_TTL_S = 6 * 3600
LOCK_TTL_MS = 5000
LOCK_RETRIES = 200
LOCK_WAIT_S = 0.02
MAX_SEEN = 256
REARM_BELOW = 0.5  # hysteresis: score must fall below this before a new crossing can arm
COOLDOWN_S = 60.0  # ... and at least this much event time must pass since the last crossing


@dataclass
class SessionState:
    score: float = 0.0
    ts: float = 0.0  # event time (epoch seconds) of last update
    reasons: dict[str, list] = field(default_factory=dict)  # code -> [weight, detail]
    above: bool = False
    crossings: int = 0  # threshold crossings so far (with hysteresis)
    published: int = 0  # crossings whose CallRisk was successfully published
    armed: bool = True  # may count a new crossing
    last_cross_ts: float = 0.0
    seen: list[str] = field(default_factory=list)  # recent event idempotency keys


def accumulate(
    state: SessionState | None,
    chunk_score: float,
    reasons: list[Reason],
    ts: float,
    half_life_s: float = HALF_LIFE_S,
    threshold: float = CALL_RISK_THRESHOLD,
    cooldown_s: float = COOLDOWN_S,
) -> SessionState:
    prev = state or SessionState(ts=ts)
    dt = max(0.0, ts - prev.ts)
    decayed = prev.score * 0.5 ** (dt / half_life_s)
    contrib = chunk_score if chunk_score >= MIN_CHUNK_SCORE else 0.0
    score = round(min(1.0, 1.0 - (1.0 - decayed) * (1.0 - contrib)), 4)
    merged = {k: list(v) for k, v in prev.reasons.items()}
    for r in reasons:
        if r.weight > 0 and (r.code not in merged or merged[r.code][0] < r.weight):
            merged[r.code] = [r.weight, r.detail]
    above = score >= threshold
    armed, crossings, last_cross = prev.armed, prev.crossings, prev.last_cross_ts
    if not armed and score < REARM_BELOW and ts - last_cross >= cooldown_s:
        armed = True  # hysteresis: dropped well below the threshold and cooled down
    if above and armed:
        crossings += 1
        armed = False
        last_cross = ts
    return SessionState(
        score=score,
        ts=max(prev.ts, ts),
        reasons=merged,
        above=above,
        crossings=crossings,
        published=prev.published,
        armed=armed,
        last_cross_ts=last_cross,
        seen=prev.seen,
    )


class SessionStore(Protocol):
    async def get(self, call_id: str) -> SessionState | None: ...
    async def put(self, call_id: str, state: SessionState) -> None: ...

    def lock(self, call_id: str) -> AbstractAsyncContextManager[None]:
        """Mutual exclusion for read-modify-write of one call's state."""
        ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._d: dict[str, SessionState] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, call_id: str) -> SessionState | None:
        return self._d.get(call_id)

    async def put(self, call_id: str, state: SessionState) -> None:
        self._d[call_id] = state

    @asynccontextmanager
    async def lock(self, call_id: str) -> AsyncIterator[None]:
        async with self._locks.setdefault(call_id, asyncio.Lock()):
            yield


class RedisSessionStore:
    def __init__(self, client: Any, prefix: str = "callguard:session:") -> None:
        self._c = client
        self._p = prefix

    async def get(self, call_id: str) -> SessionState | None:
        raw = await self._c.get(self._p + call_id)
        return SessionState(**json.loads(raw)) if raw else None

    async def put(self, call_id: str, state: SessionState) -> None:
        await self._c.set(self._p + call_id, json.dumps(asdict(state)), ex=SESSION_TTL_S)

    @asynccontextmanager
    async def lock(self, call_id: str) -> AsyncIterator[None]:
        """Per-call lock via SET NX PX with an owner token (released only by its owner)."""
        key, token = f"{self._p}lock:{call_id}", uuid.uuid4().hex
        for _ in range(LOCK_RETRIES):
            if await self._c.set(key, token, nx=True, px=LOCK_TTL_MS):
                break
            await asyncio.sleep(LOCK_WAIT_S)
        else:
            raise TimeoutError("could not lock call session")
        try:
            yield
        finally:
            if (await self._c.get(key)) in (token, token.encode()):
                await self._c.delete(key)


class SessionScorer:
    def __init__(
        self,
        store: SessionStore,
        scorer: Scorer | None = None,
        half_life_s: float = HALF_LIFE_S,
        threshold: float = CALL_RISK_THRESHOLD,
        cooldown_s: float = COOLDOWN_S,
    ) -> None:
        self._cooldown = cooldown_s
        self._store = store
        self._scorer = scorer or Scorer(None)
        self._half_life = half_life_s
        self._threshold = threshold

    async def update_with_crossing(self, event: CallEvent) -> tuple[CallRisk, bool]:
        """Fold ``event`` into its call's session (serialised per call_id).

        Returns (risk, pending): ``pending`` is True while a threshold crossing has not yet
        been published (``crossings > published``). It is derived from persisted state, so a
        retry after a failed publish still sees it; call ``mark_published`` after publishing.
        """
        async with self._store.lock(event.call_id):
            state = await self._store.get(event.call_id)
            if state is not None and event.idempotency_key in state.seen:
                return self._risk(event, state), state.crossings > state.published
            chunk_score, reasons, _ = self._scorer.score_chunk(event)
            new = accumulate(
                state,
                chunk_score,
                reasons,
                event.ts.timestamp(),
                self._half_life,
                self._threshold,
                self._cooldown,
            )
            new.seen = [*(state.seen if state else []), event.idempotency_key][-MAX_SEEN:]
            await self._store.put(event.call_id, new)
        pending = new.crossings > new.published
        log.info("call_id=%s score=%.3f pending=%s", event.call_id, new.score, pending)
        return self._risk(event, new), pending

    async def mark_published(self, call_id: str, crossing_no: int) -> None:
        async with self._store.lock(call_id):
            state = await self._store.get(call_id)
            if state is not None and state.published < crossing_no:
                state.published = crossing_no
                await self._store.put(call_id, state)

    async def update(self, event: CallEvent) -> CallRisk:
        return (await self.update_with_crossing(event))[0]

    def _risk(self, event: CallEvent, state: SessionState) -> CallRisk:
        reasons = [
            Reason(code=c, weight=v[0], detail=v[1])
            for c, v in sorted(state.reasons.items(), key=lambda kv: -kv[1][0])
        ] or [no_risk_reason()]
        return CallRisk(
            call_id=event.call_id,
            victim_token=event.victim_token,
            score=state.score,
            reasons=reasons,
            model_version=self._scorer.model_version,
            ts=event.ts,
        )

    async def crossing_no(self, call_id: str) -> int:
        state = await self._store.get(call_id)
        return state.crossings if state else 0

    @property
    def threshold(self) -> float:
        return self._threshold
