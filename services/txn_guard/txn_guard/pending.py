"""Scored transactions per payer: the durable record of what was decided.

It serves two purposes: (1) a late CallRisk re-scores a payer's recent entries using the features
stored at scoring time (no history lookup, unaffected by later payments); (2) it is the durable
replay claim: the first ``add`` for a ``txn_id`` wins and stores the exact ``TxnDecision`` JSON, so
a replay after the idempotency store was lost republishes those same bytes instead of re-scoring
(the transaction is already in history by then, so re-scoring would give a different answer).
Redis entries live 30+ days (longer than the 7-day idempotency TTL).
"""

from datetime import datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel

SCAN_TTL = timedelta(hours=2)  # per-payer recency index
PAYEE_INDEX_WINDOW = timedelta(minutes=30)  # 15-minute pending window + margin
MAX_PENDING_SCAN = 500
ENTRY_TTL_S = 35 * 86400


class PendingEntry(BaseModel):
    txn_id: str
    payer_token: str
    ts: datetime
    decision: str
    score: float
    seq: int
    model_version: str
    features: dict[str, float]
    first_json: str = ""  # exact bytes of the first published TxnDecision (seq 1)
    payee_hash: str = ""
    antibody_ids: list[str] = []  # antibodies already applied to this entry (one upgrade each)
    rail: str = ""  # audit context (never the amount): rail and coarse amount bucket
    amount_bucket: str = ""
    call_ref: str = ""  # "call_ref:<16 hex>" of the call risk that influenced the verdict


class PendingStore(Protocol):
    async def add(self, entry: PendingEntry) -> bool:
        """Durably claim ``entry.txn_id``; False if it was already there."""
        ...

    async def get(self, txn_id: str) -> PendingEntry | None: ...

    async def recent(self, payer_token: str, since: datetime) -> list[PendingEntry]: ...

    async def recent_by_payee(
        self, payee_hash: str, since: datetime, limit: int = MAX_PENDING_SCAN
    ) -> list[PendingEntry]:
        """Newest first, at most ``limit``."""
        ...

    async def update(self, entry: PendingEntry) -> None: ...


class InMemoryPendingStore:
    def __init__(self) -> None:
        self._by_id: dict[str, PendingEntry] = {}
        self._by_payer: dict[str, list[str]] = {}
        self._by_payee: dict[str, list[str]] = {}

    async def add(self, entry: PendingEntry) -> bool:
        if entry.txn_id in self._by_id:
            return False
        self._by_id[entry.txn_id] = entry
        self._by_payer.setdefault(entry.payer_token, []).append(entry.txn_id)
        if entry.payee_hash:
            ids = self._by_payee.setdefault(entry.payee_hash, [])
            ids.append(entry.txn_id)
            cutoff = entry.ts - PAYEE_INDEX_WINDOW  # trim: the index only serves the window
            if len(ids) > 64:
                self._by_payee[entry.payee_hash] = [i for i in ids if self._by_id[i].ts >= cutoff]
        return True

    async def get(self, txn_id: str) -> PendingEntry | None:
        return self._by_id.get(txn_id)

    async def update(self, entry: PendingEntry) -> None:
        self._by_id[entry.txn_id] = entry

    async def recent(self, payer_token: str, since: datetime) -> list[PendingEntry]:
        rows = (self._by_id[i] for i in self._by_payer.get(payer_token, []))
        return sorted((e for e in rows if e.ts >= since), key=lambda e: e.ts)

    async def recent_by_payee(
        self, payee_hash: str, since: datetime, limit: int = MAX_PENDING_SCAN
    ) -> list[PendingEntry]:
        rows = (self._by_id[i] for i in self._by_payee.get(payee_hash, []))
        return sorted((e for e in rows if e.ts >= since), key=lambda e: e.ts, reverse=True)[:limit]


class RedisPendingStore:
    def __init__(self, client: Any, prefix: str = "txnguard:pending:") -> None:
        self._r, self._p = client, prefix

    async def add(self, entry: PendingEntry) -> bool:
        ok = await self._r.set(
            self._p + "e:" + entry.txn_id, entry.model_dump_json(), nx=True, ex=ENTRY_TTL_S
        )
        if ok:
            idx = self._p + "i:" + entry.payer_token
            await self._r.zadd(idx, {entry.txn_id: entry.ts.timestamp()})
            await self._r.expire(idx, ENTRY_TTL_S)
            if entry.payee_hash:
                pidx = self._p + "p:" + entry.payee_hash
                await self._r.zadd(pidx, {entry.txn_id: entry.ts.timestamp()})
                await self._r.zremrangebyscore(
                    pidx, "-inf", (entry.ts - PAYEE_INDEX_WINDOW).timestamp()
                )  # bounded: keep only the window
                await self._r.expire(pidx, int(PAYEE_INDEX_WINDOW.total_seconds()) * 4)
        return bool(ok)

    async def get(self, txn_id: str) -> PendingEntry | None:
        raw = await self._r.get(self._p + "e:" + txn_id)
        return PendingEntry.model_validate_json(raw) if raw else None

    async def update(self, entry: PendingEntry) -> None:
        await self._r.set(self._p + "e:" + entry.txn_id, entry.model_dump_json(), ex=ENTRY_TTL_S)

    async def recent(self, payer_token: str, since: datetime) -> list[PendingEntry]:
        ids = await self._r.zrangebyscore(self._p + "i:" + payer_token, since.timestamp(), "+inf")
        if not ids:
            return []
        keys = [self._p + "e:" + (i.decode() if isinstance(i, bytes) else i) for i in ids]
        raws = await self._r.mget(keys)
        rows = (PendingEntry.model_validate_json(r) for r in raws if r)
        return sorted(rows, key=lambda e: e.ts, reverse=True)

    async def recent_by_payee(
        self, payee_hash: str, since: datetime, limit: int = MAX_PENDING_SCAN
    ) -> list[PendingEntry]:
        ids = await self._r.zrevrangebyscore(
            self._p + "p:" + payee_hash, "+inf", since.timestamp(), start=0, num=limit
        )
        if not ids:
            return []
        keys = [self._p + "e:" + (i.decode() if isinstance(i, bytes) else i) for i in ids]
        raws = await self._r.mget(keys)
        rows = (PendingEntry.model_validate_json(r) for r in raws if r)
        return sorted(rows, key=lambda e: e.ts, reverse=True)
