"""Pending transactions per payer, kept so a late CallRisk can re-score them.

An entry stores the features computed at scoring time (so re-scoring needs no history lookup and
is not affected by later payments) together with the verdict published so far.
"""

from datetime import datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel

PENDING_TTL = timedelta(minutes=45)


class PendingEntry(BaseModel):
    txn_id: str
    payer_token: str
    ts: datetime
    decision: str
    score: float
    seq: int
    model_version: str
    features: dict[str, float]


class PendingStore(Protocol):
    async def add(self, entry: PendingEntry) -> None: ...

    async def recent(self, payer_token: str, since: datetime) -> list[PendingEntry]: ...

    async def update(self, entry: PendingEntry) -> None: ...


class InMemoryPendingStore:
    def __init__(self) -> None:
        self._by_payer: dict[str, dict[str, PendingEntry]] = {}

    async def add(self, entry: PendingEntry) -> None:
        self._by_payer.setdefault(entry.payer_token, {}).setdefault(entry.txn_id, entry)

    async def update(self, entry: PendingEntry) -> None:
        self._by_payer.setdefault(entry.payer_token, {})[entry.txn_id] = entry

    async def recent(self, payer_token: str, since: datetime) -> list[PendingEntry]:
        rows = self._by_payer.get(payer_token, {})
        for k in [k for k, e in rows.items() if e.ts < since - PENDING_TTL]:
            del rows[k]
        return sorted((e for e in rows.values() if e.ts >= since), key=lambda e: e.ts)


class RedisPendingStore:
    def __init__(self, client: Any, prefix: str = "txnguard:pending:") -> None:
        self._r, self._p = client, prefix

    async def add(self, entry: PendingEntry) -> None:
        await self._r.hsetnx(self._p + entry.payer_token, entry.txn_id, entry.model_dump_json())
        await self._r.expire(self._p + entry.payer_token, int(PENDING_TTL.total_seconds()) * 2)

    async def update(self, entry: PendingEntry) -> None:
        await self._r.hset(self._p + entry.payer_token, entry.txn_id, entry.model_dump_json())

    async def recent(self, payer_token: str, since: datetime) -> list[PendingEntry]:
        raw = await self._r.hvals(self._p + payer_token)
        rows = [PendingEntry.model_validate_json(r) for r in raw]
        return sorted((e for e in rows if e.ts >= since), key=lambda e: e.ts)
