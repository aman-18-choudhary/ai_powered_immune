"""Hold-and-verify workflow around ``Scorer`` / ``make_decision``.

Guarantees (and their limits):

* One lock per payer token (bounded dict, evicts idle locks) serialises ``handle_txn`` and
  ``handle_call_risk`` of the same payer inside one process. Across processes ordering depends on
  the bus: ``txn.events`` / ``call.risk`` MUST be keyed by payer token (see
  ``scam_contracts.topics``); a re-read after the pending record closes the remaining
  txn/call-risk race between instances.
* The durable replay claim is the *pending entry*, which stores the exact first ``TxnDecision``
  JSON (kept >= 30 days, longer than the idempotency TTL). A replay, even after the idempotency
  store was lost, republishes those same bytes (identical score and class) and never re-scores;
  consumers dedupe on ``(txn_id, decision_seq)``. At the bus level this is at-least-once with
  identical payloads, not exactly-once.
* Per transaction: lock -> (replay?) -> context -> features -> ``make_decision`` -> hold ->
  pending (durable claim) -> publish -> history record -> gap re-check. Every step is idempotent,
  so a failure at any step is retried by ``consume`` (then DLQ); a hold-store outage publishes and
  records nothing.
* Late CallRisk (risk ts within +-15 minutes of the transaction, the same window rule as
  ``features.extract_features``): every unresolved pending transaction of the payer is re-scored
  with the larger risk; a strictly stronger verdict is published as a new ``TxnDecision`` with
  ``decision_seq + 1`` and the open hold is upgraded; never downgraded; resolved holds untouched.
  Upgrading an ``allow`` means the payment may already have settled: the verdict then carries
  ``LATE_CALL_RISK_POST_SETTLEMENT`` (a recall/verify request, not a prevention).
"""

import asyncio
import hashlib
import logging
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from scam_contracts.models import Antibody, CallRisk, Reason, Transaction, TxnDecision
from scam_contracts.topics import Topics
from svckit.bus import Bus
from svckit.idempotency import IdempotencyStore

from .antibody_cache import AntibodyLookup, CachedAntibody
from .decision import make_decision
from .features import extract_features
from .history import CALL_RISK_WINDOW, HistoryStore
from .holds import RANK, HoldStore
from .model import Scorer
from .pending import MAX_PENDING_SCAN, InMemoryPendingStore, PendingEntry, PendingStore

log = logging.getLogger("txn_guard")

DEFAULT_HOLD_DEADLINE_S = 120.0
LATE_CODE = "LATE_CALL_RISK_POST_SETTLEMENT"
LATE_ANTIBODY_CODE = "LATE_ANTIBODY_POST_SETTLEMENT"
_NO_ANTIBODY = {"payee_in_antibody": 0.0, "antibody_id_prefix": 0.0, "antibody_expires_ts": 0.0}


class TxnGuardService:
    MAX_LOCKS = 10_000

    def __init__(
        self,
        scorer: Scorer,
        history: HistoryStore,
        holds: HoldStore,
        bus: Bus,
        idem: IdempotencyStore,
        pending: PendingStore | None = None,
        hold_deadline_s: float = DEFAULT_HOLD_DEADLINE_S,
        clock: Callable[[], datetime] | None = None,
        antibodies: AntibodyLookup | None = None,
        max_pending_scan: int = MAX_PENDING_SCAN,
    ) -> None:
        self.scorer, self.history, self.holds = scorer, history, holds
        self.bus, self.idem = bus, idem
        self.pending: PendingStore = pending or InMemoryPendingStore()
        self.antibodies = antibodies
        self.max_pending_scan = max_pending_scan
        self.antibody_scan_truncated = 0
        self.hold_deadline = timedelta(seconds=hold_deadline_s)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self.handled = 0  # handler invocations completed (observability / tests)

    def _lock(self, payer: str) -> asyncio.Lock:
        lock = self._locks.get(payer)
        if lock is None:
            lock = self._locks[payer] = asyncio.Lock()
            if len(self._locks) > self.MAX_LOCKS:
                for k in [k for k, v in self._locks.items() if not v.locked()]:
                    if len(self._locks) <= self.MAX_LOCKS:
                        break
                    if k != payer:
                        del self._locks[k]
        else:
            self._locks.move_to_end(payer)
        return lock

    async def _h(self, fn: Any, *args: Any) -> Any:
        if getattr(self.history, "blocking", False):
            return await asyncio.to_thread(fn, *args)
        return fn(*args)

    async def _score(self, txn_id: str, feats: dict[str, float], ts: datetime) -> TxnDecision:
        return await asyncio.to_thread(make_decision, txn_id, feats, self.scorer, ts)

    async def _publish(self, d: TxnDecision) -> None:
        key = f"txnguard:dec:{d.txn_id}:{d.decision_seq}"
        if await self.idem.seen(key) or not await self.idem.claim(key):
            return
        try:
            await self.bus.publish(Topics.TXN_DECISIONS, d.txn_id, d)
        except BaseException:
            await self.idem.release(key)
            raise
        await self.idem.mark(key)

    async def _ensure_hold(self, d: TxnDecision, payer: str) -> None:
        if d.decision != "allow":
            await self.holds.create(
                d.txn_id, d.decision, d.reasons, self._clock() + self.hold_deadline,  # type: ignore[arg-type]
                payer_token=payer, score=d.score, model_version=d.model_version,
                decision_seq=d.decision_seq,
            )  # fmt: skip

    # ------------------------------------------------------------------------ transactions
    async def handle_txn(self, txn: Transaction) -> TxnDecision:
        async with self._lock(txn.payer_token):
            existing = await self.pending.get(txn.txn_id)
            if existing is not None:
                return await self._replay(txn, existing)
            ctx = await self._h(self.history.context_for, txn, txn.ts)
            feats = extract_features(txn, ctx)
            hit = await self._lookup(txn.payee_hash)
            if hit is not None:
                feats = feats | self._antibody_patch(hit)
            d = await self._score(txn.txn_id, feats, txn.ts)
            await self._ensure_hold(d, txn.payer_token)
            entry = PendingEntry(
                txn_id=txn.txn_id, payer_token=txn.payer_token, ts=txn.ts, decision=d.decision,
                score=d.score, seq=d.decision_seq, model_version=d.model_version, features=feats,
                first_json=d.model_dump_json(), payee_hash=txn.payee_hash.lower(),
                antibody_ids=[hit.antibody_id] if hit is not None else [],
            )  # fmt: skip
            if not await self.pending.add(entry):  # lost a cross-instance race: replay the winner
                winner = await self.pending.get(txn.txn_id)
                assert winner is not None
                return await self._replay(txn, winner)
            await self._publish(d)
            await self._h(self.history.record_txn, txn)
            await self._gap_check(txn, entry)
            self.handled += 1
            return d

    async def _lookup(self, payee_hash: str) -> CachedAntibody | None:
        """Antibody lookup off the event loop (the cache may be a blocking Redis client)."""
        if self.antibodies is None:
            return None
        return await asyncio.to_thread(self.antibodies.lookup, payee_hash)

    async def _replay(self, txn: Transaction, entry: PendingEntry) -> TxnDecision:
        """Same bytes, same hold, same history: never a fresh score."""
        d = TxnDecision.model_validate_json(entry.first_json)
        await self._ensure_hold(d, txn.payer_token)
        await self._publish(d)
        await self._h(self.history.record_txn, txn)  # idempotent per txn_id
        self.handled += 1
        return d

    async def _gap_check(self, txn: Transaction, entry: PendingEntry) -> None:
        """A risk recorded after our context read but before ``pending.add`` was invisible to both
        the context and the risk's pending scan; re-read once now that the entry exists."""
        ctx = await self._h(self.history.context_for, txn, txn.ts)
        risk = extract_features(txn, ctx)["active_call_risk"]
        cur = await self.pending.get(txn.txn_id) or entry
        if risk > cur.features.get("active_call_risk", 0.0):
            await self._upgrade(cur, {"active_call_risk": risk})
            cur = await self.pending.get(txn.txn_id) or cur
        # same gap for antibodies: one applied after our lookup but before ``pending.add`` was
        # missed by handle_antibody's pending scan; the upgrade is recorded here exactly once
        hit = await self._lookup(txn.payee_hash)
        if hit is not None and hit.antibody_id not in cur.antibody_ids:
            await self._upgrade(
                cur, self._antibody_patch(hit), late_code=LATE_ANTIBODY_CODE,
                antibody_id=hit.antibody_id,
            )  # fmt: skip

    # --------------------------------------------------------------------------- call risk
    async def handle_call_risk(self, risk: CallRisk) -> list[TxnDecision]:
        async with self._lock(risk.victim_token):
            await self._h(self.history.record_call_risk, risk)
            upgrades: list[TxnDecision] = []
            for e in await self.pending.recent(risk.victim_token, risk.ts - CALL_RISK_WINDOW):
                if abs(e.ts - risk.ts) > CALL_RISK_WINDOW:
                    continue  # same window rule as features.extract_features
                if risk.score <= e.features.get("active_call_risk", 0.0):
                    continue  # adds nothing the stored verdict has not seen
                d = await self._upgrade(e, {"active_call_risk": risk.score})
                if d is not None:
                    upgrades.append(d)
            self.handled += 1
            return upgrades

    @staticmethod
    def _antibody_patch(hit: CachedAntibody) -> dict[str, float]:
        try:
            prefix = int(hit.antibody_id[:8], 16)
        except ValueError:  # non-hub ids: a stable stand-in so the reason can still show a prefix
            prefix = int(hashlib.sha256(hit.antibody_id.encode()).hexdigest()[:8], 16)
        return {
            "payee_in_antibody": 1.0, "antibody_id_prefix": float(prefix),
            "antibody_expires_ts": hit.expires_at.timestamp(),
        }  # fmt: skip

    # -------------------------------------------------------------------------- antibodies
    async def handle_antibody(self, ab: Antibody) -> list[TxnDecision]:
        """Apply an antibody event to the cache; a newly active mule antibody upgrades the
        payer-unresolved transactions to that payee inside the 15-minute window (one upgrade per
        transaction per antibody, never a downgrade). A tombstone only stops future matches:
        existing holds stay open for a human to resolve."""
        if self.antibodies is None:
            return []
        await self._h_cache(self.antibodies.cache.apply, ab)
        ups: list[TxnDecision] = []
        hit = (
            None if ab.revoked
            else await self._h_cache(self.antibodies.cache.contains, ab.key_hash, ab.kind)
        )  # fmt: skip
        if hit is not None and ab.kind == "mule_account":
            patch = self._antibody_patch(hit)
            since = self._clock() - CALL_RISK_WINDOW
            rows = await self.pending.recent_by_payee(
                ab.key_hash.lower(), since, self.max_pending_scan + 1
            )
            if len(rows) > self.max_pending_scan:  # one hot payee must not stall the consumer
                self.antibody_scan_truncated += 1
                log.warning(
                    "antibody late-scan truncated at %d pending transactions (newest first)",
                    self.max_pending_scan,
                )
                rows = rows[: self.max_pending_scan]
            for i, e in enumerate(rows):
                if i and i % 50 == 0:
                    await asyncio.sleep(0)  # yield between batches
                async with self._lock(e.payer_token):
                    cur = await self.pending.get(e.txn_id) or e
                    if hit.antibody_id in cur.antibody_ids:
                        continue
                    d = await self._upgrade(
                        cur, patch, late_code=LATE_ANTIBODY_CODE, antibody_id=hit.antibody_id,
                    )  # fmt: skip
                    if d is not None:
                        ups.append(d)
        self.handled += 1
        return ups

    async def _h_cache(self, fn: Any, *args: Any) -> Any:
        return await asyncio.to_thread(fn, *args)

    async def _upgrade(
        self, e: PendingEntry, patch: dict[str, float], late_code: str = LATE_CODE,
        antibody_id: str | None = None,
    ) -> TxnDecision | None:  # fmt: skip
        hold = await self.holds.get(e.txn_id)
        if hold is not None and hold.state != "open":
            return None  # a human already resolved it
        if antibody_id is None and self.antibodies is not None:
            # not an antibody-driven upgrade: refresh the antibody features from the cache so a
            # tombstoned antibody is not kept alive in the stored features
            cur_hit = await self._lookup(e.payee_hash) if e.payee_hash else None
            patch = {**patch, **(self._antibody_patch(cur_hit) if cur_hit else _NO_ANTIBODY)}
        feats = e.features | patch
        applied = [*e.antibody_ids, antibody_id] if antibody_id else e.antibody_ids
        d = await self._score(e.txn_id, feats, e.ts)
        if RANK[d.decision] <= RANK[e.decision]:
            await self.pending.update(
                e.model_copy(update={"features": feats, "antibody_ids": applied})
            )
            return None  # never downgrade; nothing stronger to say
        seq = e.seq + 1
        reasons = list(d.reasons)
        if e.decision == "allow":  # an allowed payment may already have settled
            reasons.append(
                Reason(
                    code=late_code, weight=0.0,
                    detail=(
                        "Antibody for this payee was published"
                        if late_code == LATE_ANTIBODY_CODE
                        else "Call risk arrived"
                    )
                    + " after this payment was allowed; the payment may already have "
                    "completed, so this hold is a recall/verify request",
                )
            )  # fmt: skip
        d = d.model_copy(
            update={"decision_seq": seq, "reasons": reasons, "ts": max(e.ts, self._clock())}
        )
        if hold is None:
            await self._ensure_hold(d, e.payer_token)
        else:
            await self.holds.upgrade(
                e.txn_id, d.decision, reasons, d.score, d.model_version, seq  # type: ignore[arg-type]
            )  # fmt: skip
        await self._publish(d)
        await self.pending.update(
            e.model_copy(
                update={
                    "features": feats,
                    "decision": d.decision,
                    "seq": seq,
                    "score": d.score,
                    "model_version": d.model_version,
                    "antibody_ids": applied,
                }
            )  # fmt: skip
        )
        log.info("txn_id=%s upgraded to %s seq=%d", e.txn_id, d.decision, seq)
        return d
