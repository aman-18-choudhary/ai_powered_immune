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
from datetime import UTC, datetime
from typing import Any, Protocol

from scam_contracts.models import CallEvent, CallRisk, Reason

from .model import WEAK_CODES, Scorer, no_risk_reason
from .rules import CALL_RISK_THRESHOLD

log = logging.getLogger(__name__)

HALF_LIFE_S = 300.0
MIN_CHUNK_SCORE = 0.1
SOFT_MAX = 0.6  # classifier-only evidence never reaches the 0.7 alert level
CORROBORATION_MIN = 0.3  # rule evidence needed before soft evidence may add to it
SESSION_TTL_S = 6 * 3600
LEDGER_PENDING_TTL_S = 35 * 86400  # audit entries outlive the session that produced them
LEDGER_PENDING_PREFIX = "ledger_pending:"
_RELEASE_LUA = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
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
    hard: float = 0.0  # decayed noisy-OR of chunks corroborated by a real rule cue class
    soft: float = 0.0  # same for classifier-only chunks; capped below the threshold
    peak_score: float = 0.0  # snapshot at the latest crossing, published even if it decays
    peak_reasons: dict[str, list] = field(default_factory=dict)
    cross_ts: float = 0.0
    seen: list[str] = field(default_factory=list)  # recent event idempotency keys
    chunks: int = 0  # distinct events scored so far
    peak_chunks: int = 0  # ``chunks`` at the latest crossing (audit payload)


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
    decay = 0.5 ** (dt / half_life_s)
    hard, soft = prev.hard * decay, prev.soft * decay
    contrib = chunk_score if chunk_score >= MIN_CHUNK_SCORE else 0.0
    if any(r.code not in WEAK_CODES and r.weight > 0 for r in reasons):
        hard = 1.0 - (1.0 - hard) * (1.0 - contrib)
    else:  # classifier-only evidence: a "suspicious" signal that cannot reach the alert level alone
        soft = min(SOFT_MAX, 1.0 - (1.0 - soft) * (1.0 - contrib))
    score = round(
        min(
            1.0, 1.0 - (1.0 - hard) * (1.0 - soft) if hard >= CORROBORATION_MIN else max(hard, soft)
        ),
        4,
    )
    merged = {k: list(v) for k, v in prev.reasons.items()}
    for r in reasons:
        if r.weight > 0 and (r.code not in merged or merged[r.code][0] < r.weight):
            merged[r.code] = [r.weight, r.detail]
    above = score >= threshold
    armed, crossings, last_cross = prev.armed, prev.crossings, prev.last_cross_ts
    if not armed and score < REARM_BELOW and ts - last_cross >= cooldown_s:
        armed = True  # hysteresis: dropped well below the threshold and cooled down
    peak_score, peak_reasons, cross_ts = prev.peak_score, prev.peak_reasons, prev.cross_ts
    chunks, peak_chunks = prev.chunks + 1, prev.peak_chunks
    if above and armed:
        crossings += 1
        armed = False
        last_cross = ts
        peak_score, peak_reasons, cross_ts = score, {k: list(v) for k, v in merged.items()}, ts
        peak_chunks = chunks
    return SessionState(
        score=score,
        ts=max(prev.ts, ts),
        reasons=merged,
        above=above,
        crossings=crossings,
        published=prev.published,
        armed=armed,
        last_cross_ts=last_cross,
        hard=hard,
        soft=soft,
        peak_score=peak_score,
        peak_reasons=peak_reasons,
        cross_ts=cross_ts,
        seen=prev.seen,
        chunks=chunks,
        peak_chunks=peak_chunks,
    )


class SessionStore(Protocol):
    async def get(self, call_id: str) -> SessionState | None: ...
    async def put(self, call_id: str, state: SessionState) -> None: ...

    async def put_with_pending(
        self, call_id: str, state: SessionState, pending_key: str, entry_json: str
    ) -> None:
        """ONE atomic update: the session state (crossing marker) AND a pending ledger entry."""
        ...

    async def pending_ledger(self, limit: int) -> list[tuple[str, str]]: ...

    async def clear_ledger(self, pending_key: str) -> None: ...

    def lock(self, call_id: str) -> AbstractAsyncContextManager[None]:
        """Mutual exclusion for read-modify-write of one call's state."""
        ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._d: dict[str, SessionState] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._pending: dict[str, str] = {}

    async def get(self, call_id: str) -> SessionState | None:
        return self._d.get(call_id)

    async def put(self, call_id: str, state: SessionState) -> None:
        self._d[call_id] = state

    async def put_with_pending(
        self, call_id: str, state: SessionState, pending_key: str, entry_json: str
    ) -> None:
        self._d[call_id] = state  # no await between the two writes: atomic on the event loop
        self._pending[pending_key] = entry_json

    async def pending_ledger(self, limit: int) -> list[tuple[str, str]]:
        return list(self._pending.items())[:limit]

    async def clear_ledger(self, pending_key: str) -> None:
        self._pending.pop(pending_key, None)

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

    async def put_with_pending(
        self, call_id: str, state: SessionState, pending_key: str, entry_json: str
    ) -> None:
        """MULTI/EXEC: session state, the pending entry (35-day TTL) and its index together."""
        async with self._c.pipeline(transaction=True) as pipe:
            pipe.set(self._p + call_id, json.dumps(asdict(state)), ex=SESSION_TTL_S)
            pipe.set(self._p + pending_key, entry_json, ex=LEDGER_PENDING_TTL_S)
            pipe.sadd(self._p + "ledger_index", pending_key)
            await pipe.execute()

    async def pending_ledger(self, limit: int) -> list[tuple[str, str]]:
        keys = sorted(
            k.decode() if isinstance(k, bytes) else k
            for k in await self._c.smembers(self._p + "ledger_index")
        )[:limit]
        out = []
        for k in keys:
            raw = await self._c.get(self._p + k)
            if raw is None:  # expired: drop the index entry
                await self._c.srem(self._p + "ledger_index", k)
                continue
            out.append((k, raw.decode() if isinstance(raw, bytes) else raw))
        return out

    async def clear_ledger(self, pending_key: str) -> None:
        async with self._c.pipeline(transaction=True) as pipe:
            pipe.delete(self._p + pending_key)
            pipe.srem(self._p + "ledger_index", pending_key)
            await pipe.execute()

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
            await self._c.eval(_RELEASE_LUA, 1, key, token)  # atomic compare-and-delete


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

    async def crossing_risk(self, event: CallEvent) -> CallRisk | None:
        """CallRisk for the pending crossing, built from the stored peak snapshot."""
        state = await self._store.get(event.call_id)
        if state is None or state.crossings <= state.published:
            return None
        reasons = [
            Reason(code=c, weight=v[0], detail=v[1])
            for c, v in sorted(state.peak_reasons.items(), key=lambda kv: -kv[1][0])
        ] or [no_risk_reason()]
        return CallRisk(
            call_id=event.call_id,
            victim_token=event.victim_token,
            score=state.peak_score,
            reasons=reasons,
            model_version=self._scorer.model_version,
            ts=datetime.fromtimestamp(state.cross_ts, tz=UTC),
        )

    async def mark_published(
        self, call_id: str, crossing_no: int, ledger: tuple[str, str] | None = None
    ) -> None:
        """Advance the crossing marker; with ``ledger=(key, entry_json)`` the pending audit entry
        is stored in the SAME atomic update, so the alert's audit entry cannot be lost between
        the marker and its outbox row."""
        async with self._store.lock(call_id):
            state = await self._store.get(call_id)
            if state is not None and state.published < crossing_no:
                state.published = crossing_no
                if ledger is not None:
                    await self._store.put_with_pending(call_id, state, *ledger)
                else:
                    await self._store.put(call_id, state)

    async def pending_ledger(self, limit: int = 50) -> list[tuple[str, str]]:
        return await self._store.pending_ledger(limit)

    async def clear_ledger(self, pending_key: str) -> None:
        await self._store.clear_ledger(pending_key)

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

    async def crossing_chunks(self, call_id: str) -> int:
        """Events scored when the latest crossing happened (audit payload ``chunk_count``)."""
        state = await self._store.get(call_id)
        return state.peak_chunks if state else 0

    @property
    def threshold(self) -> float:
        return self._threshold
