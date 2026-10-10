"""Wall-clock timelines for a run: real (1 simulated second = 1 wall second at speed 1) and
cinematic (long simulated gaps are compressed so the hero scenario plays in about 70 s at 1x).

A timeline maps simulated time ``t`` to ``wall`` seconds at speed 1x (the run's compressed
timeline); the engine waits ``(wall(t2) - wall(t1)) / speed`` between two simulated instants.
Pacing changes wall timing only: event kinds, order, ids, ``t`` and scores are unaffected.

Compression of one gap ``g`` (simulated seconds between consecutive breakpoints, a breakpoint
being any scenario item time, a metric tick, 0 or the end)::

    w(g) = g                                   if g <= 0.5
    w(g) = min(2.2, 0.5 + 0.006 * (g - 0.5))   otherwise

Monotone non-decreasing, continuous, and bounded by 2.2 s; a 30 s tick gap becomes about 0.68 s.
Between breakpoints ``wall`` is linear in ``t`` (so a 5 s slice waits proportionally).
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable

PACINGS = ("real", "cinematic")
KNEE_S = 0.5
SLOPE = 0.006
MAX_GAP_WALL_S = 2.2


def compress_gap(g: float) -> float:
    if g <= KNEE_S:
        return max(0.0, g)
    return min(MAX_GAP_WALL_S, KNEE_S + SLOPE * (g - KNEE_S))


class Timeline:
    """Piecewise-linear ``t -> wall`` map; identity when ``pacing == "real"``."""

    def __init__(
        self, pacing: str, breakpoints: Iterable[float] = (), duration: float = 0.0,
        tick_s: float = 30.0,
    ) -> None:  # fmt: skip
        if pacing not in PACINGS:
            raise ValueError("unknown pacing")
        self.pacing = pacing
        self._t: list[float] = []
        self._w: list[float] = []
        if pacing == "cinematic":
            pts = {0.0, float(duration)}
            pts.update(float(b) for b in breakpoints if 0.0 <= b <= duration)
            k = 1
            while k * tick_s <= duration:
                pts.add(k * tick_s)
                k += 1
            self._t = sorted(pts)
            acc = 0.0
            self._w = [0.0]
            for a, b in zip(self._t, self._t[1:], strict=False):
                acc += compress_gap(b - a)
                self._w.append(acc)

    @property
    def end_wall(self) -> float:
        return self._w[-1] if self._w else 0.0

    def wall_of(self, t: float) -> float:
        if not self._t:
            return t
        if t <= 0.0:
            return 0.0
        if t >= self._t[-1]:
            return self._w[-1] + (t - self._t[-1])  # past the plan: real time
        i = bisect_right(self._t, t) - 1
        t0, t1 = self._t[i], self._t[i + 1]
        w0, w1 = self._w[i], self._w[i + 1]
        return w0 + (w1 - w0) * (t - t0) / (t1 - t0)

    def t_of(self, wall: float) -> float:
        """Inverse of ``wall_of`` (clamped to the run)."""
        if not self._t:
            return max(0.0, wall)
        if wall <= 0.0:
            return 0.0
        if wall >= self._w[-1]:
            return self._t[-1]
        i = bisect_right(self._w, wall) - 1
        w0, w1 = self._w[i], self._w[i + 1]
        t0, t1 = self._t[i], self._t[i + 1]
        if w1 == w0:
            return t0
        return t0 + (t1 - t0) * (wall - w0) / (w1 - w0)
