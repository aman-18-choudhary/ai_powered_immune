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


# --- additional model tests ---
from scam_contracts.models import Antibody, CallRisk, LedgerEntry, TxnDecision  # noqa: E402

NOW = datetime(2026, 1, 1, 10, 0, tzinfo=IST)


def make_risk(**o):
    b = dict(call_id="c", victim_token="v", score=0.5, reasons=[], model_version="m1", ts=NOW)
    b.update(o)
    return CallRisk(**b)


def make_decision(**o):
    b = dict(txn_id="t", decision="allow", score=0.5, reasons=[], model_version="m1", ts=NOW)
    b.update(o)
    return TxnDecision(**b)


def make_antibody(**o):
    b = dict(
        antibody_id="a", kind="device", key_hash="h", source_bank="b", confirmed_by="x",
        created_at=NOW, expires_at=NOW + timedelta(days=1),
    )
    b.update(o)
    return Antibody(**b)


def make_entry(**o):
    b = dict(
        seq=1, ts=NOW, service="s", actor="a", event_type="e", payload_hash="p",
        prev_hash="0", entry_hash="1",
    )
    b.update(o)
    return LedgerEntry(**b)


@pytest.mark.parametrize("make", [make_risk, make_decision])
def test_score_bounds(make):
    assert make(score=0).score == 0
    assert make(score=1).score == 1
    for bad in (1.5, -0.1):
        with pytest.raises(ValidationError):
            make(score=bad)


@pytest.mark.parametrize("make", [make_risk, make_decision, make_antibody, make_entry])
def test_naive_ts_rejected_other_models(make):
    naive = datetime(2026, 1, 1)
    field = "created_at" if make is make_antibody else "ts"
    with pytest.raises(ValidationError):
        make(**{field: naive})


@pytest.mark.parametrize("make", [make_risk, make_decision, make_antibody, make_entry])
def test_other_models_frozen(make):
    m = make()
    with pytest.raises(ValidationError):
        setattr(m, next(iter(type(m).model_fields)), "zzz")


def test_antibody_defaults_and_entry_optional():
    assert make_antibody().revoked is False
    assert make_entry().model_version is None


def test_neft_has_no_cap():
    assert make_txn(rail="NEFT", amount_inr=Decimal("99999999")).rail == "NEFT"


def test_keyed_hash_no_colon_collision():
    k = b"key"
    assert keyed_hash("c", "a:b", k) != keyed_hash("b:c", "a", k)


def test_keyed_hash_empty_key_rejected():
    with pytest.raises(ValueError):
        keyed_hash("v", "k", b"")
