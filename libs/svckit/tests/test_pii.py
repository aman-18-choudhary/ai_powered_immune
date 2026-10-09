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


def test_opaque_ids_are_exempt_only_as_whole_strings():
    digest = "0123456789abcdef" * 4
    assert contains_identifier(digest)  # the raw guard trips on digests...
    assert not string_has_identifier(digest)  # ...the shape-aware guard accepts them whole
    assert not string_has_identifier("txn_3a9f0c12d45b7e68")
    assert string_has_identifier("1" * 64)  # all-digit string is never an id
    assert string_has_identifier("acct 123456789012")
    assert string_has_identifier(digest.upper())
    # embedded in other text the strict rule applies (documented)
    assert string_has_identifier("3a9f0c12d45b7e68 9876543210")
    assert string_has_identifier("antibody 3a9f0c12d45b7e6801ab34cd56ef7812 confirmed")


def test_json_walk_checks_keys_and_nested_values():
    assert json_has_identifier({"a": [{"b": "call 9876543210"}]})
    assert json_has_identifier({"9876543210": "x"})
    assert not json_has_identifier({"a": [1, 2.5, None, True, "case-77", "ab" * 32]})
    assert json_has_identifier({"amount": 123456789012})  # 9+ digit numbers are rejected by design


# ---------------------------------------------------------------- fix round 1 (review item 3)
HEX_BYPASS = [
    "a1234567890123456",  # 17 chars: not an id shape
    "1234567890123456a",
    "12345678a12345678a",  # 18 chars
    "acct_1234567890123456a",
    "txn_1234567890123456a",
    "f" + "0123456789" + "abcdef",
    "ab" * 8 + "123456789012",
    "1" * 16 + "a" * 8 + "1" * 3,  # not a whole-string id shape
    "9" * 24,  # all digits
    "a" + "9" * 22 + "b",  # 24 chars, run 22 > cap
]
LEGIT_IDS = [
    "txn_3a9f0c12d45b7e68",
    "ab" * 32,
    "0123456789abcdef" * 4,
    "3a9f0c12d45b7e68",
    "cmp_3a9f0c12d45b7e6801ab34cd",
    "3a9f0c12d45b7e6801ab34cd56ef7812",
    "ab12cd34",  # short hex: not an id shape, but the strict rule has nothing to object to
    "kh_ab12cd34",
]
# digits-heavy but real-looking random ids that the OLD 9-digit rule falsely rejected (5-14%)
DIGIT_HEAVY_IDS = [
    "txn_1234567890abcdef",  # run of 10
    "txn_123456789012345a",  # run 15 at the cap for 16-hex
    "0" * 20 + "abcd",  # run 20 in a 24-hex id
    "f" + "1" * 22 + "2",  # 24-hex, run 22 > cap 21 -> must be rejected below
]


@pytest.mark.parametrize("text", HEX_BYPASS)
def test_id_shape_cannot_hide_long_digit_runs(text):
    assert string_has_identifier(text), text


@pytest.mark.parametrize("text", LEGIT_IDS)
def test_legit_ids_still_accepted(text):
    assert not string_has_identifier(text), text


def test_digit_heavy_ids_within_the_caps_are_accepted():
    assert not string_has_identifier(DIGIT_HEAVY_IDS[0])
    assert not string_has_identifier(DIGIT_HEAVY_IDS[1])
    assert not string_has_identifier(DIGIT_HEAVY_IDS[2])
    assert string_has_identifier(DIGIT_HEAVY_IDS[3])


def test_documented_residual_risk_of_the_shape_rule():
    """A 15-digit number plus ONE hex letter in a 16-char string is indistinguishable from the
    0.5% of random 16-hex ids with a 15-digit run, so it is accepted by design (see README)."""
    assert not string_has_identifier("123456789012345a")


def test_false_reject_rate_on_random_ids_is_negligible():
    import random

    rnd = random.Random(20261009)
    n = 200_000
    for length in (16, 24, 32, 64):
        bad = 0
        for _ in range(n):
            body = format(rnd.getrandbits(4 * length), f"0{length}x")
            bad += string_has_identifier(body) or string_has_identifier("txn_" + body)
        # 16-hex ids: 0.054% are all digits ((10/16)^16), indistinguishable from a card number
        floor = 6e-4 if length == 16 else 1e-4
        assert bad / n < floor, (length, bad / n)


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


# ---- Task 13: ref namespaces, UTC timestamps
def test_ref_namespace_prefix_accepts_whole_id_only():
    assert not string_has_identifier("payee_ref:3a9f0c12d45b7e68")
    assert not string_has_identifier("call_ref:0123456789abcdef")
    assert string_has_identifier("payee_ref:1234567890123456")  # all digits: never an id
    assert string_has_identifier("payee_ref:3a9f0c12d45b7e68 9876543210")
    assert string_has_identifier("acct_ref:3a9f0c12d45b7e68123")  # wrong length


def test_ref_namespace_false_reject_rate_is_the_all_digit_floor():
    import random

    rnd = random.Random(7)
    n = 100_000
    refs = ("payee_ref:" + format(rnd.getrandbits(64), "016x") for _ in range(n))
    assert sum(string_has_identifier(r) for r in refs) / n < 1e-3


@pytest.mark.parametrize("ts", ["2026-10-09T12:00:00Z", "1999-01-31T23:59:59Z"])
def test_utc_timestamps_are_exempt_whole_string(ts):
    assert not string_has_identifier(ts)
    assert contains_identifier(ts)  # the raw rule trips on the 14 digits


@pytest.mark.parametrize(
    "text",
    ["2026-10-09T12:00:00Z 9876543210", "x2026-10-09T12:00:00Z", "2026-13-09T12:00:00Z",
     "9876543210123", "2026-10-09T12:00:00+05:30", "2026-10-09 12:00:00", "20261009120000"],
)  # fmt: skip
def test_timestamp_exemption_is_exact(text):
    assert string_has_identifier(text), text


# ---------------------------------------------------------------- fix round 1: prefix allowlist
@pytest.mark.parametrize(
    "ok", ["txn_3a9f0c12d45b7e68", "cmp_3a9f0c12d45b7e68", "kh_3a9f0c12d45b7e68",
           "ab_3a9f0c12d45b7e68", "redacted_3a9f0c12d45b7e68", "payee_ref:3a9f0c12d45b7e68",
           "call_ref:3a9f0c12d45b7e68", "device_ref:3a9f0c12d45b7e68"],
)  # fmt: skip
def test_allowlisted_prefixes(ok):
    assert not string_has_identifier(ok)


@pytest.mark.parametrize(
    "bad", ["acct_3a9f0c12d45b7e68", "phone_3a9f0c12d45b7e68", "acct_ref:3a9f0c12d45b7e68",
            "upi_ref:3a9f0c12d45b7e68", "pan:3a9f0c12d45b7e68"],
)  # fmt: skip
def test_free_form_prefixes_are_not_exempt(bad):
    assert string_has_identifier(bad)
