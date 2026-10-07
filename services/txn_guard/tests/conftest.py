from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from scam_contracts.models import Transaction

from txn_guard.history import Context, InMemoryHistoryStore

T0 = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def make_txn():
    n = {"i": 0}

    def _make(
        amount: str = "450",
        age: int = 900,
        payee: str = "payee_known",
        payer: str = "payer_1",
        ts: datetime = T0,
        rail: str = "UPI",
        device: str = "dev_1",
    ) -> Transaction:
        n["i"] += 1
        return Transaction(
            txn_id=f"t{n['i']}",
            idempotency_key=f"k{n['i']}",
            bank_id="bank_x",
            payer_token=payer,
            payee_hash=payee,
            rail=rail,  # type: ignore[arg-type]
            amount_inr=Decimal(amount),
            ts=ts,
            payee_account_age_days=age,
            device_id_token=device,
        )

    return _make


@pytest.fixture
def warm_store(make_txn):
    """A payer with 40 ordinary history txns (amounts ~Rs 300-700) to a known payee/device."""
    store = InMemoryHistoryStore()
    for i in range(40):
        store.record_txn(
            make_txn(
                amount=str(300 + (i * 37) % 400),
                ts=T0 - timedelta(days=10) + timedelta(hours=i * 5),
            )
        )
    return store


@pytest.fixture
def ctx_empty() -> Context:
    return Context(now=T0)
