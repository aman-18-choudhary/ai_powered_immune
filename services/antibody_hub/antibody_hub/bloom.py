"""Bloom filter over keyed hashes (own implementation: bytearray + double hashing).

Hash scheme: ``sha256(item)`` is split into two 64-bit halves ``h1``, ``h2`` (``h2`` forced odd);
bit ``i`` of the k probes is ``(h1 + i * h2) mod m`` (Kirsch-Mitzenmacher double hashing).

Sizing for capacity ``n`` and false-positive rate ``p``::

    m = ceil(-n ln p / (ln 2)^2)   (rounded up to a whole byte)
    k = round(m / n * ln 2)

A Bloom filter has NO false negatives but DOES have false positives. A bank that blocks on a
Bloom hit alone will occasionally block an innocent payee; blocking decisions should therefore
be verified against the exact active set (``GET /antibodies/exact``) where one is available and
the Bloom filter used as the fast pre-filter / hold-for-review trigger.
"""

import base64
import hashlib
import math
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

DEFAULT_FP_RATE = 1e-6
HEADROOM = 2
MIN_CAPACITY = 1024


class BloomFilter:
    def __init__(self, capacity: int, fp_rate: float, m: int, k: int, bits: bytearray, count: int):
        self.capacity = capacity
        self.fp_rate = fp_rate
        self.m = m
        self.k = k
        self.count = count
        self._bits = bits

    @staticmethod
    def sizing(capacity: int, fp_rate: float) -> tuple[int, int]:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if not 0.0 < fp_rate < 1.0:
            raise ValueError("fp_rate must be in (0, 1)")
        m = math.ceil(-capacity * math.log(fp_rate) / math.log(2) ** 2)
        m = (m + 7) // 8 * 8
        k = max(1, round(m / capacity * math.log(2)))
        return m, k

    @classmethod
    def create(cls, capacity: int, fp_rate: float = DEFAULT_FP_RATE) -> "BloomFilter":
        m, k = cls.sizing(capacity, fp_rate)
        return cls(capacity, fp_rate, m, k, bytearray(m // 8), 0)

    @classmethod
    def for_items(cls, items: Iterable[str], fp_rate: float = DEFAULT_FP_RATE) -> "BloomFilter":
        """Snapshot-grade filter: capacity = max(MIN_CAPACITY, 2x the item count)."""
        data = list(items)
        bf = cls.create(max(MIN_CAPACITY, HEADROOM * len(data)), fp_rate)
        for x in data:
            bf.add(x)
        return bf

    def _probes(self, item: str) -> Iterable[int]:
        d = hashlib.sha256(item.encode()).digest()
        h1 = int.from_bytes(d[:8], "big")
        h2 = int.from_bytes(d[8:16], "big") | 1
        return ((h1 + i * h2) % self.m for i in range(self.k))

    def add(self, item: str) -> None:
        for p in self._probes(item):
            self._bits[p >> 3] |= 1 << (p & 7)
        self.count += 1

    def contains(self, item: str) -> bool:
        return all(self._bits[p >> 3] & (1 << (p & 7)) for p in self._probes(item))

    def to_snapshot(
        self, version: str = "", generated_at: datetime | None = None
    ) -> dict[str, Any]:
        return {
            "version": version,
            "n": self.capacity,
            "m": self.m,
            "k": self.k,
            "fp_rate": self.fp_rate,
            "count": self.count,
            "bits": base64.b64encode(bytes(self._bits)).decode(),
            "generated_at": (generated_at or datetime.now(UTC)).isoformat(),
        }

    @classmethod
    def from_snapshot(cls, snap: dict[str, Any]) -> "BloomFilter":
        bits = bytearray(base64.b64decode(snap["bits"]))
        if len(bits) * 8 != snap["m"]:
            raise ValueError("bit array does not match m")
        return cls(snap["n"], snap["fp_rate"], snap["m"], snap["k"], bits, snap["count"])
