"""Antibody pipeline stage with an explicit, documented analyst-confirmation model.

``AnalystOracle`` (this module) stands in for a human analyst's investigation and uses the
simulator's ground-truth role labels, which a real analyst only approximates. Two gates:

* ``label`` (default): when a transaction that truly is a campaign victim transfer is first held
  (``hold_verify``), the analyst confirms its payee as a mule ``confirm_delay`` later (default 60 s,
  deterministic). A held benign payment is never confirmed. "No benign antibody matches" is
  therefore TRUE BY CONSTRUCTION here (the simulator has no benign mule payees and confirmation is
  label-gated); it is a tautology, not evidence about analyst accuracy.
* ``hold``: no labels: every first ``hold_verify`` to a payee is confirmed (a rubber-stamping
  analyst). Any false positive of the model then poisons its payee, which is what the
  benign-match count of this variant measures.

The confirmation delay is a free parameter (see the report's sensitivity table). The resulting
antibody (kind ``mule_account``, TTL 14
days) is applied to every bank's cache at once (the benchmark has one cache; the simulator has
second-bank victims only in the hero scenario, so this is optimistic about propagation to other
banks and says nothing about hub outages). Later transfers to that payee_hash are then subject to
txn-guard's ``ANTIBODY_MATCH`` overlay.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from scam_contracts.models import Antibody, Transaction, TxnDecision
from txn_guard.antibody_cache import AntibodyLookup, InMemoryAntibodyCache
from txn_guard.service import TxnGuardService

DEFAULT_CONFIRM_DELAY_S = 60.0
GATES = ("label", "hold")
TTL = timedelta(days=14)


@dataclass
class AntibodyStage:  # the AnalystOracle + antibody cache for one pipeline variant
    truth: object  # sim_engine GroundTruth (txn_role / is_scam_txn)
    confirm_delay: timedelta = timedelta(seconds=DEFAULT_CONFIRM_DELAY_S)
    gate: str = "label"
    now: datetime | None = None
    scheduled: dict[str, datetime] = field(default_factory=dict)  # payee_hash -> confirm ts
    _due: list[tuple[datetime, str]] = field(default_factory=list)
    published: int = 0
    matches: int = 0
    benign_matches: int = 0
    scam_matches: int = 0

    def __post_init__(self) -> None:
        self.cache = InMemoryAntibodyCache(clock=lambda: self.now or datetime.min)  # type: ignore[arg-type]
        self.lookup = AntibodyLookup(self.cache)

    def _publish_due(self, now: datetime) -> None:
        self._due.sort()
        while self._due and self._due[0][0] <= now:
            ts, payee = self._due.pop(0)
            aid = hashlib.sha256(f"{payee}:gen0".encode()).hexdigest()
            self.now = ts
            self.cache.apply(
                Antibody(
                    antibody_id=aid,
                    kind="mule_account",
                    key_hash=payee,
                    source_bank="analyst",
                    confirmed_by="analyst",
                    created_at=ts,
                    expires_at=ts + TTL,
                )  # fmt: skip
            )
            self.published += 1

    def patch(self, txn: Transaction) -> dict[str, float]:
        """Features to merge for ``txn``: applies antibodies confirmed up to its timestamp first."""
        self._publish_due(txn.ts)
        self.now = txn.ts
        hit = self.lookup.lookup(txn.payee_hash)
        if hit is None:
            return {}
        self.matches += 1
        if self.truth.is_scam_txn(txn.txn_id):  # type: ignore[attr-defined]
            self.scam_matches += 1
        else:
            self.benign_matches += 1
        return TxnGuardService._antibody_patch(hit)

    def needs_decision(self, txn: Transaction) -> bool:
        """Whether this transaction's decision must be known immediately (to schedule a
        confirmation): victim transfers under the label gate, every transaction under the hold
        gate."""
        return self.gate == "hold" or self.truth.txn_role(txn.txn_id) == "victim_transfer"  # type: ignore[attr-defined]

    def observe(self, txn: Transaction, decision: TxnDecision) -> None:
        """First hold (label gate: on a campaign victim transfer) schedules the confirmation."""
        if (
            decision.decision == "hold_verify"
            and self.needs_decision(txn)
            and txn.payee_hash not in self.scheduled
        ):
            at = txn.ts + self.confirm_delay
            self.scheduled[txn.payee_hash] = at
            self._due.append((at, txn.payee_hash))
