from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from scam_contracts.hashing import keyed_hash
from scam_contracts.models import Transaction
from scam_contracts.topics import Topics

IST = timezone(timedelta(hours=5, minutes=30))


def make_txn(**over):
    base = dict(
        txn_id="t1",
        idempotency_key="k1",
        bank_id="bankA",
        payer_token="p",
        payee_hash="h",
        rail="UPI",
        amount_inr=Decimal("100"),
        ts=datetime(2026, 1, 1, 10, 0, tzinfo=IST),
        payee_account_age_days=3,
        device_id_token="d",
    )
    base.update(over)
    return Transaction(**base)


def test_upi_over_limit_rejected():
    with pytest.raises(ValidationError):
        make_txn(rail="UPI", amount_inr=Decimal("100001"))


def test_upi_limit_allowed():
    assert make_txn(amount_inr=Decimal("100000")).amount_inr == Decimal("100000")


def test_imps_500000_allowed():
    assert make_txn(rail="IMPS", amount_inr=Decimal("500000")).rail == "IMPS"


def test_imps_over_limit_rejected():
    with pytest.raises(ValidationError):
        make_txn(rail="IMPS", amount_inr=Decimal("500001"))


def test_naive_ts_rejected():
    with pytest.raises(ValidationError):
        make_txn(ts=datetime(2026, 1, 1, 10, 0))


@pytest.mark.parametrize("amt", ["0", "-5"])
def test_zero_and_negative_amount_rejected(amt):
    with pytest.raises(ValidationError):
        make_txn(amount_inr=Decimal(amt))


def test_models_are_frozen():
    t = make_txn()
    with pytest.raises(ValidationError):
        t.bank_id = "x"


def test_keyed_hash_deterministic_and_kind_separated():
    key = b"fed-key"
    assert keyed_hash("abc", "account", key) == keyed_hash("abc", "account", key)
    assert keyed_hash("abc", "account", key) != keyed_hash("abc", "device", key)
    assert keyed_hash("abc", "account", key) != keyed_hash("abc", "account", b"other")
    assert len(keyed_hash("abc", "account", key)) == 64


def test_topics():
    assert Topics.TXN_EVENTS == "txn.events"
    assert Topics.ANTIBODIES == "antibody.published"
    assert Topics.DLQ_SUFFIX == ".dlq"
