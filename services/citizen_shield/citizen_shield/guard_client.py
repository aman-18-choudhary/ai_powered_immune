"""HTTP client for call-guard's ``POST /score`` with a short timeout and a small circuit breaker.

``timeout_s`` is the TOTAL deadline of one call; the response is capped at 64 KiB. Any failure
(connect, timeout, non-200, oversize, malformed body, out-of-range score, any other error) raises
``GuardDown``;
the caller turns that into a degraded verdict, never into a ``safe`` one. After
``breaker_failures`` consecutive failures calls are skipped (also ``GuardDown``) for
``breaker_open_s`` seconds, so a dead scorer does not add its timeout to every request.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field

MAX_RESPONSE_BYTES = 65_536


class GuardDown(Exception):
    """The scorer could not produce a trustworthy score."""


class _Reason(BaseModel):
    code: str = Field(max_length=64)
    weight: float


class _ScoreResponse(BaseModel):
    score: float = Field(ge=0, le=1)
    reasons: list[_Reason] = Field(max_length=64)
    model_version: str = Field(max_length=64)


@dataclass(frozen=True)
class GuardScore:
    score: float
    reasons: list[tuple[str, float]]  # (code, weight); free-text detail is dropped
    model_version: str


class CallGuardClient:
    def __init__(
        self, base_url: str, timeout_s: float = 1.5, *, breaker_failures: int = 5,
        breaker_open_s: float = 10.0, transport: httpx.AsyncBaseTransport | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:  # fmt: skip
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport
        )
        self._total_s = timeout_s
        self._fail_limit, self._open_s, self._now = breaker_failures, breaker_open_s, monotonic
        self._failures = 0
        self._open_until = 0.0
        self.calls = self.failures_total = self.skipped_total = 0

    @property
    def breaker_open(self) -> bool:
        return self._now() < self._open_until

    async def _post(self, text: str) -> bytes:
        async with self._http.stream("POST", "/score", json={"message": text}) as r:
            if r.status_code != 200:
                raise GuardDown(f"status {r.status_code}")
            chunks: list[bytes] = []
            size = 0
            async for chunk in r.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise GuardDown("response too large")
                chunks.append(chunk)
            return b"".join(chunks)

    async def score(self, text: str) -> GuardScore:
        if self.breaker_open:
            self.skipped_total += 1
            raise GuardDown("breaker open")
        self.calls += 1
        try:
            # one TOTAL deadline: httpx's timeouts are per phase, so a server that trickles bytes
            # could otherwise hold a request for many multiples of the configured timeout
            async with asyncio.timeout(self._total_s):
                raw = await self._post(text)
            body = _ScoreResponse.model_validate_json(raw)
        except Exception as e:  # incl. TimeoutError; CancelledError is a BaseException
            self._failures += 1
            self.failures_total += 1
            if self._failures >= self._fail_limit:
                self._open_until = self._now() + self._open_s
            raise GuardDown(type(e).__name__) from None
        self._failures = 0
        return GuardScore(
            body.score, [(x.code, x.weight) for x in body.reasons], body.model_version
        )

    async def ping(self) -> bool:
        r = await self._http.get("/readyz")
        return r.status_code == 200

    async def aclose(self) -> None:
        await self._http.aclose()
