"""Deterministic stand-ins for the Fraud DNA embedding model (no torch, no model files)."""

import hashlib
import math
import re

_WORD = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


class FakeEmbedder:
    """Bag-of-words hashed into a small vector: texts sharing words are similar, others are not.
    Mirrors the real embedder's interface (``status``, ``load``, ``dim``, ``embed``)."""

    model_id = "test/fake-bow-64"

    def __init__(self, dim: int = 64, fail: bool = False, reason: str | None = None) -> None:
        self._dim = dim
        self.fail = fail
        self.reason = reason
        self.loaded = False
        self.calls: list[list[str]] = []

    @property
    def dim(self) -> int:
        return self._dim

    def status(self) -> dict:
        base = {"model": self.model_id, "revision": "fake", "dimensions": self._dim}
        if self.reason:
            return {**base, "status": "unavailable", "reason": self.reason}
        return {**base, "status": "ready" if self.loaded else "not_loaded", "reason": None}

    def load(self) -> None:
        from complaints.dna import EmbedderUnavailable

        if self.reason:
            raise EmbedderUnavailable(self.reason)
        self.loaded = True

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise RuntimeError("embedding failed")
        self.calls.append(list(texts))
        out = []
        for t in texts:
            v = [0.0] * self._dim
            for w in _WORD.findall(t.casefold()):
                v[int(hashlib.sha256(w.encode()).hexdigest(), 16) % self._dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out
