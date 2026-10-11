"""Small in-process fixed-window rate limiter with bounded memory.

Per process, not shared across replicas (the gateway applies the platform-wide per-role limit).
Used fail-open: ``allow`` never raises to the caller's detriment; any internal error counts as
"allowed" and is recorded in ``errors``.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable


class WindowLimiter:
    def __init__(
        self, max_keys: int = 50_000, window_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:  # fmt: skip
        self._max, self._w, self._clock = max_keys, window_s, clock
        self._hits: OrderedDict[str, tuple[float, int]] = OrderedDict()
        self.errors = 0
        self.limited = 0

    def check(self, key: str, limit: int) -> int | None:
        """None when allowed (and counted), else the seconds until the window resets."""
        try:
            now = self._clock()
            start, n = self._hits.get(key, (now, 0))
            if now - start >= self._w:
                start, n = now, 0
            if n >= limit:
                self.limited += 1
                return max(1, int(self._w - (now - start)) + 1)
            self._hits[key] = (start, n + 1)
            self._hits.move_to_end(key)
            while len(self._hits) > self._max:
                self._hits.popitem(last=False)  # forgetting the oldest key favours availability
            return None
        except Exception:
            self.errors += 1
            return None  # fail open
