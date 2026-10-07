"""Per-payer history: what feature extraction needs, and an in-memory store.

``Context`` is a plain, immutable snapshot (pure input to ``extract_features``). A
``HistoryStore`` builds a ``Context`` for a transaction and ingests transactions / call risks.
``InMemoryHistoryStore`` is for tests and unit use; a Redis-backed store is Task 9's job.
Out-of-order and duplicate events are also Task 9's concern: this store assumes events are
recorded in time order and does not de-duplicate.
"""

import math
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from scam_contracts.models import CallRisk, Transaction

VELOCITY_WINDOW = timedelta(hours=24)
CALL_RISK_WINDOW = timedelta(minutes=15)


@dataclass(frozen=True)
class Context:
    """Everything feature extraction needs about the payer, payee and call state.

    ``payer_log_mean`` / ``payer_log_std`` are the mean / population std of ``ln(amount)`` over
    the payer's ``payer_n`` previous transactions. ``recent`` holds ``(ts, amount_inr)`` of the
    payer's previous transactions in the last 24 h. ``call_risks`` holds ``(ts, score)`` of the
    payer's recent CallRisk events. ``payee_recent`` holds ``(ts, amount_inr)`` of this payer's
    previous transfers to *this payee* in the last 24 h. ``payee_first_seen_ts`` is None when this
    payer has never paid
    this payee. ``now`` is the evaluation clock (used only to flag future-dated timestamps).
    """

    now: datetime
    payer_n: int = 0
    payer_log_mean: float = 0.0
    payer_log_std: float = 0.0
    recent: tuple[tuple[datetime, float], ...] = ()
    payee_first_seen_ts: datetime | None = None
    device_seen: bool = False
    payee_recent: tuple[tuple[datetime, float], ...] = ()
    call_risks: tuple[tuple[datetime, float], ...] = ()


class HistoryStore(Protocol):
    def context_for(self, txn: Transaction, now: datetime | None = None) -> Context: ...

    def record_txn(self, txn: Transaction) -> None: ...

    def record_call_risk(self, risk: CallRisk) -> None: ...


class _Welford:
    __slots__ = ("m2", "mean", "n")

    def __init__(self) -> None:
        self.n, self.mean, self.m2 = 0, 0.0, 0.0

    def add(self, x: float) -> None:
        self.n += 1
        d = x - self.mean
        self.mean += d / self.n
        self.m2 += d * (x - self.mean)

    @property
    def std(self) -> float:
        return math.sqrt(self.m2 / self.n) if self.n >= 2 else 0.0


class InMemoryHistoryStore:
    def __init__(self) -> None:
        self._stats: dict[str, _Welford] = {}
        self._recent: dict[str, deque[tuple[datetime, float]]] = {}
        self._payees: dict[str, dict[str, datetime]] = {}
        self._pair: dict[tuple[str, str], deque[tuple[datetime, float]]] = {}
        self._devices: dict[str, set[str]] = {}
        self._risks: dict[str, deque[tuple[datetime, float]]] = {}

    def record_txn(self, txn: Transaction) -> None:
        p, amt = txn.payer_token, float(txn.amount_inr)
        self._stats.setdefault(p, _Welford()).add(math.log(amt))
        q = self._recent.setdefault(p, deque())
        q.append((txn.ts, amt))
        newest = max(ts for ts, _ in q)
        while q and q[0][0] < newest - VELOCITY_WINDOW:
            q.popleft()
        pq = self._pair.setdefault((p, txn.payee_hash), deque())
        pq.append((txn.ts, amt))
        while pq and pq[0][0] < max(ts for ts, _ in pq) - VELOCITY_WINDOW:
            pq.popleft()
        self._payees.setdefault(p, {}).setdefault(txn.payee_hash, txn.ts)
        self._devices.setdefault(p, set()).add(txn.device_id_token)

    def record_call_risk(self, risk: CallRisk) -> None:
        q = self._risks.setdefault(risk.victim_token, deque())
        q.append((risk.ts, risk.score))
        newest = max(ts for ts, _ in q)
        while q and q[0][0] < newest - 2 * CALL_RISK_WINDOW:
            q.popleft()

    def context_for(self, txn: Transaction, now: datetime | None = None) -> Context:
        p = txn.payer_token
        st = self._stats.get(p)
        return Context(
            now=now or datetime.now(UTC),
            payer_n=st.n if st else 0,
            payer_log_mean=st.mean if st else 0.0,
            payer_log_std=st.std if st else 0.0,
            recent=tuple(self._recent.get(p, ())),
            payee_first_seen_ts=self._payees.get(p, {}).get(txn.payee_hash),
            device_seen=txn.device_id_token in self._devices.get(p, ()),
            payee_recent=tuple(self._pair.get((p, txn.payee_hash), ())),
            call_risks=tuple(self._risks.get(p, ())),
        )
