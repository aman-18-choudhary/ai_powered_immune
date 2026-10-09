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


def test_hex_digests_are_exempt_but_digit_runs_are_not():
    digest = "0123456789abcdef" * 4
    assert contains_identifier(digest)  # the raw guard trips on digests...
    assert not string_has_identifier(digest)  # ...the digest-aware guard exempts them
    assert string_has_identifier("1" * 64)  # all-digit string is never a digest
    assert string_has_identifier(digest[:-1])  # odd length is not a digest
    assert string_has_identifier(digest.upper())


def test_json_walk_checks_keys_and_nested_values_and_ignores_numbers():
    assert json_has_identifier({"a": [{"b": "call 9876543210"}]})
    assert json_has_identifier({"9876543210": "x"})
    assert not json_has_identifier({"a": [1, 2.5, None, True, "case-77", "ab" * 32]})
    assert not json_has_identifier({"amount": 123456789012})
