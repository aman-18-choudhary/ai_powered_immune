import pytest

from svckit.pii import contains_identifier, json_has_identifier, string_has_identifier

ZW = "​"
BAD = [
    "(022) 2345 6789",
    "(98765) 43210",
    "(98765)43210",
    "9876  543210",
    "9876. 543210",
    "1234 - 5678 - 9012",
    "9876 543210",
    "9876 543210",
    "9876\t543210",
    "9876\n543210",
    "9876/543210",
    "9876,543210",
    "9876_543210",
    "9876\x00543210",
    f"9876{ZW}543210",
    "①②③④⑤⑥⑦⑧⑨①",  # circled digits
    "¹²³⁴⁵⁶⁷⁸⁹⁰",  # superscripts
    "＋919876543210",
    "bob＠okaxis",
    "name @ okaxis",
    "name  @  okaxis",
    "ABCDE1234F",
    "abcde1234f",
    "HDFC0001234",
    "call 0091 98765 43210",
    "+91 98765 43210",
    "123456789",
    "ref 1234 5678 9",
    "9.8.7.6.5.4.3.2.1",
]
GOOD = [
    "case-77 reviewed twice on 12 Oct",
    "ticket 4521",
    "3 banks reported",
    "false positive per case 42",
    "merchant verified by phone call",
    "reviewed on 2026-03-10",
]


@pytest.mark.parametrize("text", BAD)
def test_guard_rejects(text):
    assert contains_identifier(text), repr(text)


@pytest.mark.parametrize("text", GOOD)
def test_guard_accepts(text):
    assert not contains_identifier(text), text


def test_hex_ids_are_exempt_but_digit_runs_are_not():
    digest = "0123456789abcdef" * 4
    assert contains_identifier(digest)  # the raw guard trips on digests...
    assert not string_has_identifier(digest)  # ...the id-aware guard exempts them
    assert not string_has_identifier("txn_3a9f0c12d45b7e68")
    assert not string_has_identifier("antibody 3a9f0c12d45b7e680 confirmed")
    assert string_has_identifier("1" * 64)  # all-digit string is never an id
    assert string_has_identifier("acct 123456789012")
    assert string_has_identifier(digest.upper())
    assert string_has_identifier("3a9f0c12d45b7e68 9876543210")  # id next to a phone number


def test_json_walk_checks_keys_and_nested_values_and_ignores_numbers():
    assert json_has_identifier({"a": [{"b": "call 9876543210"}]})
    assert json_has_identifier({"9876543210": "x"})
    assert not json_has_identifier({"a": [1, 2.5, None, True, "case-77", "ab" * 32]})
    assert json_has_identifier({"amount": 123456789012})  # 9+ digit numbers are rejected by design


# ---------------------------------------------------------------- fix round 1 (review item 3)
HEX_BYPASS = [
    "a1234567890123456",
    "1234567890123456a",
    "acct_123456789012345a",
    "txn_1234567890123456a",
    "x" + "1" * 12 + "a" * 3 + "1" * 4,
    "f" + "0123456789" + "abcdef",
    "ab" * 8 + "123456789012",  # 64-hex digest-length token with a 12-digit run
]
LEGIT_IDS = [
    "txn_3a9f0c12d45b7e68",
    "ab" * 32,
    "0123456789abcdef" * 4,  # 64-hex digest: a run of 10 digits is fine (<12)
    "key_3a9f0c12",
    "3a9f0c12d45b7e68",
    "kh_ab12cd34",
    "antibody 3a9f0c12d45b7e6801ab34cd56ef7812 confirmed",
]


@pytest.mark.parametrize("text", HEX_BYPASS)
def test_hex_exemption_cannot_hide_long_digit_runs(text):
    assert string_has_identifier(text), text


@pytest.mark.parametrize("text", LEGIT_IDS)
def test_legit_ids_still_accepted(text):
    assert not string_has_identifier(text), text


@pytest.mark.parametrize("text", BAD)
def test_hub_table_rejected_by_id_aware_guard_too(text):
    assert string_has_identifier(text), repr(text)


@pytest.mark.parametrize(
    "obj",
    [{"acct": 123456789012}, {"a": [1, {"b": 9876543210}]}, {"x": 1234567890.5},
     {"123456789012": 1}, {"n": -987654321}],
)  # fmt: skip
def test_numbers_and_numeric_keys_are_inspected(obj):
    assert json_has_identifier(obj), obj


def test_legit_numbers_accepted():
    ok = {"decision_seq": 2, "score": 0.91, "amount_inr": 49999, "amount_paise": 99999999,
          "flag": True, "none": None, "k": "ab12cd34", "txn": "txn_3a9f0c12d45b7e68"}  # fmt: skip
    assert not json_has_identifier(ok)
