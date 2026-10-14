"""In-memory monitoring sessions (never persisted).

* Idle TTL (default 10 min; any request on the session refreshes it), per-user cap (2) and a
  global cap (50). Expired sessions are swept on every manager call.
* Raw audio: only the last ``tail_s`` (0.5 s) of the latest chunk is kept, to be prepended to the
  next consecutive chunk; everything else is dropped when the chunk has been transcribed.
* Transcript bounded to ``max_segments`` / ``max_transcript_chars`` (oldest dropped first,
  ``truncated`` flag set). Responses for the last 32 seq numbers are cached for idempotent
  re-sends.
"""

from __future__ import annotations

import asyncio
import math
import secrets
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .detect import RiskTracker

SESSION_ID_RE = r"^[A-Za-z0-9_-]{16,64}$"
RESULT_CACHE = 32


class CapError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Caller:
    owner: str  # token subject
    rate_key: str  # subject, or client address for roles whose tokens are free to mint


@dataclass
class IndicatorState:
    code: str
    label: str
    source: str
    weight: float
    segment_ids: list[int] = field(default_factory=list)
    excerpts: list[str] = field(default_factory=list)
    first_at_s: float = 0.0
    last_at_s: float = 0.0

    def view(self) -> dict[str, Any]:
        return {
            "code": self.code, "label": self.label, "source": self.source,
            "weight": round(self.weight, 3), "segment_ids": list(self.segment_ids),
            "evidence_excerpts": list(self.excerpts), "first_at_s": round(self.first_at_s, 2),
            "last_at_s": round(self.last_at_s, 2), "status": "detected_not_confirmed",
        }  # fmt: skip


@dataclass
class LiveSession:
    id: str
    owner: str
    rate_key: str
    language: str
    created: float
    last_seen: float
    wall_started: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    next_seq: int = 0
    processed: set[int] = field(default_factory=set)
    results: OrderedDict[int, dict[str, Any]] = field(default_factory=OrderedDict)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    transcript_chars: int = 0
    truncated: bool = False
    seg_counter: int = 0
    last_words: list[str] = field(default_factory=list)
    tail_pcm: bytes = b""
    tail_seq: int = -1
    tail_end_s: float = 0.0
    audio_end_s: float = 0.0
    indicators: dict[str, IndicatorState] = field(default_factory=dict)
    risk: RiskTracker = field(default_factory=RiskTracker)
    assessed_chunks: int = 0
    stale: bool = False
    level: str | None = None
    history: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=500))
    warnings: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=100))
    errors: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=50))
    model_version: str | None = None
    last_status: dict[str, str] = field(default_factory=dict)
    llm_status: str = "idle"
    llm_text: str | None = None
    llm_at_s: float | None = None
    llm_level: str | None = None
    llm_last: float = -math.inf
    llm_task: asyncio.Task[None] | None = None

    def cache(self, seq: int, result: dict[str, Any]) -> None:
        self.results[seq] = result
        while len(self.results) > RESULT_CACHE:
            self.results.popitem(last=False)

    def gaps(self, limit: int = 100) -> list[int]:
        missing = [s for s in range(self.next_seq) if s not in self.processed]
        return missing[-limit:]


class SessionManager:
    def __init__(
        self,
        clock: Callable[[], float],
        *,
        max_sessions: int,
        max_per_user: int,
        idle_ttl_s: float,
        on_drop: Callable[[LiveSession], None] | None = None,
    ) -> None:
        self._clock = clock
        self._sessions: dict[str, LiveSession] = {}
        self.max_sessions, self.max_per_user, self.ttl = max_sessions, max_per_user, idle_ttl_s
        self._on_drop = on_drop

    def count(self) -> int:
        self.sweep()
        return len(self._sessions)

    def sweep(self) -> None:
        now = self._clock()
        for sid in [s.id for s in self._sessions.values() if now - s.last_seen > self.ttl]:
            self._drop(sid)

    def _drop(self, sid: str) -> None:
        s = self._sessions.pop(sid, None)
        if s is not None:
            s.tail_pcm = b""
            if self._on_drop:
                self._on_drop(s)

    def create(self, caller: Caller, language: str) -> LiveSession:
        self.sweep()
        mine = sum(1 for s in self._sessions.values() if caller.rate_key in (s.rate_key, s.owner))
        if mine >= self.max_per_user:
            raise CapError("too_many_sessions")
        if len(self._sessions) >= self.max_sessions:
            raise CapError("server_busy")
        now = self._clock()
        sid = secrets.token_urlsafe(18)
        s = LiveSession(
            id=sid, owner=caller.owner, rate_key=caller.rate_key, language=language,
            created=now, last_seen=now, wall_started=datetime.now(UTC).isoformat(),
        )  # fmt: skip
        self._sessions[sid] = s
        return s

    def get(self, sid: str, caller: Caller) -> LiveSession | None:
        """The session if it exists and belongs to ``caller`` (else None: callers answer 404)."""
        self.sweep()
        s = self._sessions.get(sid)
        if s is None or not secrets.compare_digest(s.owner, caller.owner):
            return None
        s.last_seen = self._clock()
        return s

    def end(self, sid: str) -> None:
        self._drop(sid)

    def clear(self) -> None:
        for sid in list(self._sessions):
            self._drop(sid)


class RateLimiter:
    """Fixed-window counter per (bucket, key); bounded; uses the injected clock."""

    def __init__(self, clock: Callable[[], float], window_s: int = 60) -> None:
        self._clock = clock
        self.window = window_s
        self._counts: dict[tuple[str, str, int], int] = {}

    def check(self, bucket: str, key: str, limit: int) -> int | None:
        now = self._clock()
        win = int(now // self.window)
        if len(self._counts) > 10_000:
            self._counts = {k: v for k, v in self._counts.items() if k[2] >= win}
        k = (bucket, key, win)
        self._counts[k] = n = self._counts.get(k, 0) + 1
        if n > limit:
            return max(1, math.ceil((win + 1) * self.window - now))
        return None
