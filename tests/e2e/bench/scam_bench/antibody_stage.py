"""Antibody pipeline stage with an explicit, documented analyst-confirmation model.

Assumption (NOT measured, it is a modelling choice): when a victim transfer of a campaign is first
held (``hold_verify``), a fraud analyst reviews the case and, ``confirm_delay_s`` later
(default 60 s, deterministic), confirms the payee as a mule. The confirmation is an oracle on the
simulator's ground truth: only holds whose transaction truly is a campaign victim transfer are ever
confirmed (a held benign payment is not). The resulting antibody (kind ``mule_account``, TTL 14
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
TTL = timedelta(days=14)


@dataclass
class AntibodyStage:
    truth: object  # sim_engine GroundTruth (txn_role / is_scam_txn)
    confirm_delay: timedelta = timedelta(seconds=DEFAULT_CONFIRM_DELAY_S)
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

    def observe(self, txn: Transaction, decision: TxnDecision) -> None:
        """First hold on a campaign victim transfer schedules the analyst confirmation."""
        if (
            decision.decision == "hold_verify"
            and self.truth.txn_role(txn.txn_id) == "victim_transfer"  # type: ignore[attr-defined]
            and txn.payee_hash not in self.scheduled
        ):
            at = txn.ts + self.confirm_delay
            self.scheduled[txn.payee_hash] = at
            self._due.append((at, txn.payee_hash))
