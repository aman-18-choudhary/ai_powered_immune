"""Hold-and-verify workflow around ``Scorer`` / ``make_decision``.

Per transaction (in this order, so a failure at any step is safe to retry):
  1. claim ``txn_id`` (a replay, even with a different idempotency key, is a no-op);
  2. context -> features -> ``make_decision`` (ts = the transaction's own ts, so a replay would
     produce the same bytes);
  3. create the hold for step_up / hold_verify (idempotent: one hold per txn_id);
  4. remember the scored features as *pending* (for late call risks);
  5. publish the ``TxnDecision`` to ``txn.decisions`` keyed by txn_id (once per txn_id and
     decision_seq: guarded by the idempotency store);
  6. only then record the transaction in the history store (once per txn_id).
If the hold store is down step 3 raises, nothing is published or recorded, and ``consume``
retries and finally dead-letters the message.

Late CallRisk: the risk is recorded in history, then every pending (unresolved) transaction of the
payer whose timestamp is within 15 minutes of the risk is re-scored with the larger call risk. A
strictly stronger verdict is published as a new ``TxnDecision`` with ``decision_seq + 1`` and the
open hold is upgraded; verdicts are never downgraded automatically and resolved holds are left
alone. A pending transaction is any scored transaction in the window, including ``allow``
(settlement can still be intercepted within the window).
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from scam_contracts.models import CallRisk, Transaction, TxnDecision
from scam_contracts.topics import Topics
from svckit.bus import Bus
from svckit.idempotency import IdempotencyStore

from .decision import make_decision
from .features import extract_features
from .history import CALL_RISK_WINDOW, HistoryStore
from .holds import RANK, HoldStore
from .model import Scorer
from .pending import InMemoryPendingStore, PendingEntry, PendingStore

log = logging.getLogger("txn_guard")

DEFAULT_HOLD_DEADLINE_S = 120.0


class TxnGuardService:
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
    ) -> None:
        self.scorer, self.history, self.holds = scorer, history, holds
        self.bus, self.idem = bus, idem
        self.pending: PendingStore = pending or InMemoryPendingStore()
        self.hold_deadline = timedelta(seconds=hold_deadline_s)
        self._clock = clock or (lambda: datetime.now(UTC))
        self.handled = 0  # handler invocations completed (observability / tests)

    async def _h(self, fn: Any, *args: Any) -> Any:
        if getattr(self.history, "blocking", False):
            return await asyncio.to_thread(fn, *args)
        return fn(*args)

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

    async def handle_txn(self, txn: Transaction) -> TxnDecision | None:
        key = f"txnguard:txn:{txn.txn_id}"
        if await self.idem.seen(key) or not await self.idem.claim(key):
            self.handled += 1
            return None  # replay / duplicate txn_id
        try:
            ctx = await self._h(self.history.context_for, txn, txn.ts)
            feats = extract_features(txn, ctx)
            d = make_decision(txn.txn_id, feats, self.scorer, txn.ts)
            if d.decision != "allow":
                await self.holds.create(
                    txn.txn_id, d.decision, d.reasons, self._clock() + self.hold_deadline,  # type: ignore[arg-type]
                    payer_token=txn.payer_token, score=d.score, model_version=d.model_version,
                    decision_seq=d.decision_seq,
                )  # fmt: skip
            await self.pending.add(
                PendingEntry(
                    txn_id=txn.txn_id,
                    payer_token=txn.payer_token,
                    ts=txn.ts,
                    decision=d.decision,
                    score=d.score,
                    seq=d.decision_seq,
                    model_version=d.model_version,
                    features=feats,
                )  # fmt: skip
            )
            await self._publish(d)
            await self._h(self.history.record_txn, txn)
        except BaseException:
            await self.idem.release(key)
            raise
        await self.idem.mark(key)
        self.handled += 1
        return d

    async def handle_call_risk(self, risk: CallRisk) -> list[TxnDecision]:
        await self._h(self.history.record_call_risk, risk)
        upgrades: list[TxnDecision] = []
        for e in await self.pending.recent(risk.victim_token, risk.ts - CALL_RISK_WINDOW):
            if abs(e.ts - risk.ts) > CALL_RISK_WINDOW:
                continue
            if risk.score <= e.features.get("active_call_risk", 0.0):
                continue  # this risk adds nothing the stored verdict has not seen
            hold = await self.holds.get(e.txn_id)
            if hold is not None and hold.state != "open":
                continue  # a human already resolved it
            feats = e.features | {"active_call_risk": risk.score}
            d = make_decision(e.txn_id, feats, self.scorer, e.ts)
            if RANK[d.decision] <= RANK[e.decision]:
                await self.pending.update(e.model_copy(update={"features": feats}))
                continue  # never downgrade; nothing stronger to say
            seq = e.seq + 1
            d = d.model_copy(update={"decision_seq": seq, "ts": max(e.ts, self._clock())})
            if hold is None:
                await self.holds.create(
                    e.txn_id, d.decision, d.reasons, self._clock() + self.hold_deadline,  # type: ignore[arg-type]
                    payer_token=e.payer_token, score=d.score, model_version=d.model_version,
                    decision_seq=seq,
                )  # fmt: skip
            else:
                await self.holds.upgrade(
                    e.txn_id, d.decision, d.reasons, d.score, d.model_version, seq  # type: ignore[arg-type]
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
                    }
                )  # fmt: skip
            )
            upgrades.append(d)
            log.info("txn_id=%s upgraded to %s seq=%d", e.txn_id, d.decision, seq)
        self.handled += 1
        return upgrades
