from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from scam_contracts.models import Antibody, Transaction, TxnDecision

from graph_twin.config import Settings
from graph_twin.store import GraphStore
from graph_twin.twin import GraphTwin

T0 = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def set(self, now: datetime) -> None:
        self.now = now


def h(n: int | str, tag: str = "a") -> str:
    """A 64-hex stand-in for a keyed hash (deterministic, letters present)."""
    import hashlib

    return hashlib.sha256(f"{tag}:{n}".encode()).hexdigest()


_seq = [0]


def txn(
    payer: str, payee: str, ts: datetime, amount: str | int = "1000", *, tid: str | None = None,
    rail: str = "UPI", bank: str = "bank_a", device: str | None = None, age: int = 5,
    ref: str | None = "auto",
) -> Transaction:  # fmt: skip
    _seq[0] += 1
    tid = tid or f"txn_{_seq[0]:016x}"
    return Transaction(
        txn_id=tid, idempotency_key=f"idem_{tid}", bank_id=bank, payer_token=f"tok_{payer}",
        payee_hash=payee, rail=rail, amount_inr=Decimal(str(amount)), ts=ts,
        payee_account_age_days=age, device_id_token=device or f"dev_{payer}",
        payer_account_ref=(payer if ref == "auto" else ref),
    )  # fmt: skip


def decision(txn_id: str, level: str, seq: int = 1, ts: datetime = T0) -> TxnDecision:
    return TxnDecision(
        txn_id=txn_id, decision=level, score=0.9, reasons=[], model_version="t", ts=ts,
        decision_seq=seq,
    )  # fmt: skip


def antibody(key_hash: str, ab_id: str = "ab1", *, revoked: bool = False, days: int = 14,
             created: datetime = T0) -> Antibody:  # fmt: skip
    return Antibody(
        antibody_id=ab_id, kind="mule_account", key_hash=key_hash, source_bank="bank_a",
        confirmed_by="analyst", created_at=created, expires_at=created + timedelta(days=days),
        revoked=revoked,
    )  # fmt: skip


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'gt.db'}"


@pytest.fixture
def store(db_url):
    s = GraphStore(db_url)
    yield s
    s.close()


@pytest.fixture
def twin(store, clock) -> GraphTwin:
    return GraphTwin(store, Settings(), clock)


def make_campaign(twin: GraphTwin, clock: Clock, *, victims: int = 6, mules: int = 3,
                  start: datetime = T0, tag: str = "c", flag: bool = True,
                  per_victim: str = "50000") -> dict:  # fmt: skip
    """Victims pay mules (flagged), mules sweep to one cash-out within minutes."""
    clock.set(max(clock.now, start + timedelta(minutes=2 * victims + 10 + mules)))
    ms = [h(f"{tag}m{i}") for i in range(mules)]
    cash = h(f"{tag}cash")
    vs = [h(f"{tag}v{i}") for i in range(victims)]
    ids = []
    for i, v in enumerate(vs):
        m = ms[i % mules]
        t = txn(v, m, start + timedelta(minutes=2 * i), per_victim, age=5)
        twin.apply_txn(t)
        ids.append(t.txn_id)
        if flag:
            twin.apply_decision(decision(t.txn_id, "hold_verify"))
    end = start + timedelta(minutes=2 * victims)
    for i, m in enumerate(ms):
        got = sum(1 for k in range(victims) if k % mules == i) * int(per_victim)
        if got:
            twin.apply_txn(
                txn(
                    m,
                    cash,
                    end + timedelta(minutes=5 + i),
                    got,
                    rail="IMPS",
                    device=f"dev_{tag}shared",
                )  # fmt: skip
            )
    return {"mules": ms, "cash": cash, "victims": vs, "txns": ids}
