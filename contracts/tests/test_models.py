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
        antibody_id="a",
        kind="device",
        key_hash="h",
        source_bank="b",
        confirmed_by="x",
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    b.update(o)
    return Antibody(**b)


def make_entry(**o):
    b = dict(
        seq=1,
        ts=NOW,
        service="s",
        actor="a",
        event_type="e",
        payload_hash="p",
        prev_hash="0",
        entry_hash="1",
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


def test_txn_decision_seq_defaults_to_1_for_old_payloads():
    from scam_contracts.models import TxnDecision

    old = (
        '{"txn_id":"t","decision":"allow","score":0.1,"reasons":[],"model_version":"m",'
        '"ts":"2026-03-10T12:00:00+00:00"}'
    )
    d = TxnDecision.model_validate_json(old)
    assert d.decision_seq == 1  # consumers dedupe on (txn_id, decision_seq), take the highest
    assert TxnDecision.model_validate_json(d.model_dump_json()).decision_seq == 1


# ---------------------------------------------------------------- ledger additions (Task 12)
def test_ledger_entry_in_old_payload_still_valid():
    from scam_contracts.models import LedgerEntry, LedgerEntryIn

    e = LedgerEntryIn.model_validate_json(
        '{"service":"s","actor":"a","event_type":"e","payload_hash":"h","model_version":null}'
    )
    assert e.payload is None and e.case_refs == []
    old = (
        '{"seq":1,"ts":"2026-01-01T00:00:00Z","service":"s","actor":"a","event_type":"e",'
        '"payload_hash":"h","prev_hash":"p","entry_hash":"x"}'
    )
    le = LedgerEntry.model_validate_json(old)
    assert le.payload is None and le.case_refs == [] and le.model_version is None


def test_ledger_entry_in_payload_limits_and_refs():
    from scam_contracts.models import LedgerEntryIn

    base = dict(service="s", actor="a", event_type="e", payload_hash="h")
    LedgerEntryIn(**base, payload={"k": [1, "x", {"y": None}]}, case_refs=["txn:1", "a.b-c_d"[:7]])
    with pytest.raises(ValidationError):
        LedgerEntryIn(**base, payload={"k": "x" * 5000})
    with pytest.raises(ValidationError):
        LedgerEntryIn(**base, payload={"k": float("nan")})
    with pytest.raises(ValidationError):
        LedgerEntryIn(**base, payload={"k": {"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}}})
    with pytest.raises(ValidationError):
        LedgerEntryIn(**base, case_refs=["ok"] * 11)
    for bad in ("", "has space", "x" * 65, "a/b", "a@b"):
        with pytest.raises(ValidationError):
            LedgerEntryIn(**base, case_refs=[bad])


def test_canonical_json_golden_vectors():
    from scam_contracts.canonical import canonical_json, payload_hash

    assert canonical_json({"b": 1, "a": [True, None, "x"]}) == b'{"a":[true,null,"x"],"b":1}'
    assert canonical_json({"k": "é₹"}) == '{"k":"é₹"}'.encode()  # not \u-escaped
    assert canonical_json({}) == b"{}"
    assert canonical_json({"z": {"b": 2, "a": 1}}) == b'{"z":{"a":1,"b":2}}'
    assert payload_hash({}) == "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    assert payload_hash({"a": 1}) == (
        "015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862"
    )
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})
