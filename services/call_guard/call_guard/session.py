"""Per-call risk accumulation with time decay.

``accumulate`` is the pure update: the previous score decays with half-life ``half_life_s`` of
event time, then the new chunk score is merged by noisy-OR (chunks below ``MIN_CHUNK_SCORE``
are treated as noise so long benign calls cannot drift upwards). State lives in a store
behind a small protocol: Redis in the service, an in-memory dict in tests.
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from scam_contracts.models import CallEvent, CallRisk, Reason

from .model import Scorer, no_risk_reason
from .rules import CALL_RISK_THRESHOLD

log = logging.getLogger(__name__)

HALF_LIFE_S = 300.0
MIN_CHUNK_SCORE = 0.1
SESSION_TTL_S = 6 * 3600
MAX_SEEN = 256


@dataclass
class SessionState:
    score: float = 0.0
    ts: float = 0.0  # event time (epoch seconds) of last update
    reasons: dict[str, list] = field(default_factory=dict)  # code -> [weight, detail]
    above: bool = False
    crossings: int = 0
    seen: list[str] = field(default_factory=list)  # recent event idempotency keys


def accumulate(
    state: SessionState | None,
    chunk_score: float,
    reasons: list[Reason],
    ts: float,
    half_life_s: float = HALF_LIFE_S,
    threshold: float = CALL_RISK_THRESHOLD,
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
    return SessionState(
        score=score,
        ts=max(prev.ts, ts),
        reasons=merged,
        above=above,
        crossings=prev.crossings + (1 if above and not prev.above else 0),
        seen=prev.seen,
    )


class SessionStore(Protocol):
    async def get(self, call_id: str) -> SessionState | None: ...
    async def put(self, call_id: str, state: SessionState) -> None: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._d: dict[str, SessionState] = {}

    async def get(self, call_id: str) -> SessionState | None:
        return self._d.get(call_id)

    async def put(self, call_id: str, state: SessionState) -> None:
        self._d[call_id] = state


class RedisSessionStore:
    def __init__(self, client: Any, prefix: str = "callguard:session:") -> None:
        self._c = client
        self._p = prefix

    async def get(self, call_id: str) -> SessionState | None:
        raw = await self._c.get(self._p + call_id)
        return SessionState(**json.loads(raw)) if raw else None

    async def put(self, call_id: str, state: SessionState) -> None:
        await self._c.set(self._p + call_id, json.dumps(asdict(state)), ex=SESSION_TTL_S)


class SessionScorer:
    def __init__(
        self,
        store: SessionStore,
        scorer: Scorer | None = None,
        half_life_s: float = HALF_LIFE_S,
        threshold: float = CALL_RISK_THRESHOLD,
    ) -> None:
        self._store = store
        self._scorer = scorer or Scorer(None)
        self._half_life = half_life_s
        self._threshold = threshold

    async def update_with_crossing(self, event: CallEvent) -> tuple[CallRisk, bool]:
        """Fold ``event`` into its call's session; returns (risk, crossed_threshold_now).

        Per-call updates must be serialised (events are keyed by call_id on the bus).
        """
        state = await self._store.get(event.call_id)
        if state is not None and event.idempotency_key in state.seen:
            return self._risk(event, state), False  # replayed event: no double counting
        chunk_score, reasons, _ = self._scorer.score_chunk(event)
        new = accumulate(
            state, chunk_score, reasons, event.ts.timestamp(), self._half_life, self._threshold
        )
        new.seen = [*(state.seen if state else []), event.idempotency_key][-MAX_SEEN:]
        await self._store.put(event.call_id, new)
        crossed = new.crossings > (state.crossings if state else 0)
        log.info("call_id=%s score=%.3f crossed=%s", event.call_id, new.score, crossed)
        return self._risk(event, new), crossed

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
