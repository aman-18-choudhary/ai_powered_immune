import hashlib
import json
from datetime import UTC, datetime

import pytest
from scam_contracts.canonical import payload_hash
from scam_contracts.models import LedgerEntryIn
from scam_contracts.topics import Topics

from svckit.bus import InMemoryBus
from svckit.ledger import (
    FORBIDDEN_KEY_PARTS,
    LedgerPayloadError,
    build_ledger_entry,
    call_ref,
    emit_ledger,
    payee_ref,
    utc_ts,
)

# Shared with services/evidence_ledger/tests/test_chain.py::test_emitter_golden_payload_hash.
GOLDEN_PAYLOAD = {
    "txn_id": "txn_3a9f0c12d45b7e68",
    "decision": "hold_verify",
    "decision_seq": 2,
    "score": 0.9731,
    "reason_codes": ["ANTIBODY_MATCH", "CALL_RISK_ACTIVE"],
    "rail": "UPI",
    "amount_bucket": "10k-100k",
    "deadline_ts": "2026-10-09T12:02:00Z",
}
GOLDEN_HASH = "d3527646ec642ef02e3e0ee474e911b46dd8eb0a4d4352615b8a930367552a92"


def test_golden_payload_hash_vector():
    e = build_ledger_entry("txn-guard", "system:txn-guard", "hold.created", GOLDEN_PAYLOAD)
    assert e.payload_hash == GOLDEN_HASH
    raw = json.dumps(GOLDEN_PAYLOAD, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert hashlib.sha256(raw.encode()).hexdigest() == GOLDEN_HASH
    assert payload_hash(GOLDEN_PAYLOAD) == GOLDEN_HASH


def test_build_is_deterministic_and_key_order_independent():
    a = build_ledger_entry("s", "system:s", "e.x", {"a": 1, "b": [1, 2]}, model_version="m1")
    b = build_ledger_entry("s", "system:s", "e.x", {"b": [1, 2], "a": 1}, model_version="m1")
    assert a == b and a.payload == {"a": 1, "b": [1, 2]} and a.model_version == "m1"
    assert isinstance(a, LedgerEntryIn)


@pytest.mark.parametrize("part", FORBIDDEN_KEY_PARTS)
def test_forbidden_key_names_rejected_case_insensitive_and_nested(part):
    for key in (part, part.upper(), f"payer_{part}_x"):
        with pytest.raises(LedgerPayloadError):
            build_ledger_entry("s", "a", "e", {key: "x"})
    with pytest.raises(LedgerPayloadError):
        build_ledger_entry("s", "a", "e", {"ok": [{"deep": {part.title(): 1}}]})


def test_required_forbidden_key_list():
    for k in (
        "phone",
        "mobile",
        "account",
        "account_number",
        "name",
        "email",
        "address",
        "upi",
        "vpa",
        "pan",
        "aadhaar",
        "otp",
        "password",
        "token_secret",
    ):
        assert k in FORBIDDEN_KEY_PARTS


@pytest.mark.parametrize(
    "value",
    ["9876543210", "+91 98765 43210", "user@okaxis", "ABCDE1234F", "HDFC0001234",
     "acct 123456789012", 123456789012, [{"x": "call 9876543210"}]],
)  # fmt: skip
def test_pii_looking_values_rejected_without_echo(value):
    with pytest.raises(LedgerPayloadError) as ei:
        build_ledger_entry("s", "a", "e", {"note": value})
    msg = str(ei.value)
    assert "9876543210" not in msg and "okaxis" not in msg and "123456789012" not in msg
    assert "ABCDE1234F" not in msg and "HDFC0001234" not in msg and "note" not in msg
    assert "9876543210" not in repr(ei.value)


def test_pii_in_nested_key_not_echoed():
    with pytest.raises(LedgerPayloadError) as ei:
        build_ledger_entry("s", "a", "e", {"9876543210": 1})
    assert "9876543210" not in str(ei.value)


def test_opaque_ids_refs_and_timestamps_pass():
    e = build_ledger_entry(
        "s", "a", "e",
        {"txn_id": "txn_3a9f0c12d45b7e68", "ts": "2026-10-09T12:00:00Z", "d": "ab" * 32},
        case_refs=["txn_3a9f0c12d45b7e68", "payee_ref:3a9f0c12d45b7e68"],
    )  # fmt: skip
    assert e.case_refs[1].startswith("payee_ref:")


def _sized(n: int) -> dict:
    # canonical size of {"k":"<x * m>"} is 8 + m bytes
    return {"k": "x" * (n - 8)}


def test_four_kib_boundary():
    ok = _sized(4096)
    assert len(json.dumps(ok, separators=(",", ":"))) == 4096
    assert build_ledger_entry("s", "a", "e", ok).payload == ok
    with pytest.raises(LedgerPayloadError):
        build_ledger_entry("s", "a", "e", _sized(4097))


def test_bad_shapes_rejected_cleanly():
    for bad in (
        {"a": float("nan")},
        {"a": object()},
        {1: "x"},
        {"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}},
    ):
        with pytest.raises(LedgerPayloadError):
            build_ledger_entry("s", "a", "e", bad)  # type: ignore[arg-type]
    with pytest.raises(LedgerPayloadError):
        build_ledger_entry("s", "a", "e", {"a": 1}, case_refs=[f"r{i}" for i in range(11)])
    with pytest.raises(LedgerPayloadError):
        build_ledger_entry("s", "a", "e", {"a": 1}, case_refs=["has space"])
    with pytest.raises(LedgerPayloadError):
        build_ledger_entry("s", "a", "e", {"a": 1}, model_version="v9876543210")
    with pytest.raises(LedgerPayloadError):
        build_ledger_entry("s", "a", "e", {"a": 1}, case_refs=["9876543210"])


def test_ref_helpers_are_opaque_and_stable():
    h = "3a9f0c12d45b7e68" + "0" * 48
    assert payee_ref(h) == "payee_ref:3a9f0c12d45b7e68"
    assert payee_ref(h.upper()) == payee_ref(h)
    c = call_ref("call-abc")
    assert c == "call_ref:" + hashlib.sha256(b"call-abc").hexdigest()[:16]
    assert "call-abc" not in c
    # an all-digit first block is not an id shape: the next 16 hex chars are used (deterministic)
    h2 = "1234567890123456" + "abcdef0123456789" + "0" * 32
    assert payee_ref(h2) == "payee_ref:abcdef0123456789"
    with pytest.raises(LedgerPayloadError):
        payee_ref("not-a-hash")


def test_utc_ts_format():
    assert utc_ts(datetime(2026, 10, 9, 12, 0, 0, 999, tzinfo=UTC)) == "2026-10-09T12:00:00Z"
    assert (
        utc_ts(datetime(2026, 10, 9, 12, 0, 0, 999, tzinfo=UTC), micros=True)
        == "2026-10-09T12:00:00.000999Z"
    )
    build_ledger_entry(
        "s", "a", "e", {"at": utc_ts(datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=UTC), micros=True)}
    )
    with pytest.raises(ValueError):
        utc_ts(datetime(2026, 10, 9, 12, 0, 0))


async def test_emit_publishes_on_ledger_topic_keyed_by_first_ref_or_service():
    bus = InMemoryBus()
    e1 = await emit_ledger(bus, "txn-guard", "system:txn-guard", "hold.created", {"a": 1},
                           case_refs=["txn_3a9f0c12d45b7e68"])  # fmt: skip
    e2 = await emit_ledger(bus, "call-guard", "system:call-guard", "callrisk.alert", {"a": 2})
    msgs = bus.messages(Topics.LEDGER)
    assert [k for k, _ in msgs] == ["txn_3a9f0c12d45b7e68", "call-guard"]
    assert LedgerEntryIn.model_validate_json(msgs[0][1]) == e1
    assert LedgerEntryIn.model_validate_json(msgs[1][1]) == e2


async def test_emit_rejects_pii_keys_and_publishes_nothing():
    bus = InMemoryBus()
    for payload in ({"phone": "x"}, {"account_number": "y"}, {"name": "z"}):
        with pytest.raises(LedgerPayloadError):
            await emit_ledger(bus, "s", "a", "e", payload)
    assert bus.messages(Topics.LEDGER) == []


# ---------------------------------------------------------------- fix round 1
from svckit.ledger import build_or_placeholder, redacted_ref, safe_actor, txn_ref  # noqa: E402
from svckit.pii import string_has_identifier  # noqa: E402


def test_txn_ref_passes_safe_ids_and_hashes_the_rest():
    assert txn_ref("txn_3a9f0c12d45b7e68") == "txn_3a9f0c12d45b7e68"
    assert txn_ref("t1") == "t1"
    for raw in ("402312345678", "UPI/402312345678/x", "a" * 80, "has space"):
        r = txn_ref(raw)
        assert r.startswith("txn_") and len(r) == 20 and raw not in r
        assert not string_has_identifier(r) and r == txn_ref(raw)


def test_redacted_ref_and_safe_actor():
    assert safe_actor("analyst-7") == "analyst-7" and safe_actor("a1@bank_a") == "a1@bank_a"
    for bad in ("919876543210", "user@okaxis.com", "x" * 120):
        r = safe_actor(bad)
        assert r.startswith("redacted_") and bad not in r and not string_has_identifier(r)
    assert redacted_ref("abc") == redacted_ref("abc") != redacted_ref("abd")


def test_build_or_placeholder_never_raises_and_never_echoes():
    e, refused = build_or_placeholder(
        "svc", "analyst-1", "ev.x", {"ok": 1}, case_refs=["r1"],
        placeholder={"event": "ev.x", "audit": "payload_refused"}, placeholder_refs=["r1"],
    )  # fmt: skip
    assert not refused and e.payload == {"ok": 1}
    e, refused = build_or_placeholder(
        "svc", "919876543210", "ev.x", {"note": "9876543210"}, model_version="v9876543210",
        case_refs=["9876543210"],
        placeholder={"event": "ev.x"}, placeholder_refs=["9876543210", "r1"],
    )  # fmt: skip
    assert refused and e.payload == {"event": "ev.x", "audit": "payload_refused"}
    assert e.actor.startswith("redacted_") and e.case_refs == ["r1"] and e.model_version is None
    assert "9876543210" not in e.model_dump_json()
    e, refused = build_or_placeholder(
        "svc", "a", "ev.x", {"phone": 1},
        placeholder={"phone": "still bad"}, placeholder_refs=[],
    )  # fmt: skip
    assert refused and e.payload == {"audit": "payload_refused"}
