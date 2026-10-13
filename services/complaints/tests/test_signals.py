"""URL heuristics (table-driven, offline), entity format checks, context fields."""

import pytest

from complaints.signals import analyse_url, check_entities, context_fields


def codes(url: str) -> set[str]:
    return {i["code"] for i in analyse_url(url)["indicators"]}


@pytest.mark.parametrize(
    ("url", "expected", "absent"),
    [
        ("https://www.sbi.co.in/web/personal-banking", set(), {"BRAND_LOOKALIKE", "NOT_HTTPS"}),
        ("http://example.org/page", {"NOT_HTTPS"}, {"IP_LITERAL_HOST"}),
        ("http://192.168.10.5/login", {"IP_LITERAL_HOST", "NOT_HTTPS"}, {"BRAND_LOOKALIKE"}),
        ("http://3232235777/x", {"IP_LITERAL_HOST"}, set()),
        ("https://[2001:db8::1]/a", {"IP_LITERAL_HOST"}, set()),
        ("https://xn--sbi-kyc-9ua.com/", {"PUNYCODE_HOST"}, set()),
        ("https://a.b.c.d.e.example.com/", {"EXCESSIVE_SUBDOMAINS"}, set()),
        ("https://prize-claim.xyz/win", {"SUSPICIOUS_TLD"}, set()),
        ("https://bit.ly/3abcde", {"URL_SHORTENER"}, set()),
        ("https://hdfc-kyc-update.top/verify", {"BRAND_LOOKALIKE", "SUSPICIOUS_TLD"}, set()),
        ("https://sbi.secure-login.example.com/", {"BRAND_LOOKALIKE"}, set()),
        ("https://netbanking.hdfcbank.com/login", set(), {"BRAND_LOOKALIKE"}),
        ("https://paytm.com/", set(), {"BRAND_LOOKALIKE"}),
        ("https://user:pw@evil.example.net/", {"CREDENTIALS_IN_URL"}, set()),
        ("https://x.example.com/?" + "a=1&" * 80, {"LONG_QUERY"}, set()),
        ("https://x.example.com:8443/", {"NONSTANDARD_PORT"}, set()),
        ("https://s0lo.example.com/", set(), {"BRAND_LOOKALIKE"}),
    ],
)
def test_url_indicator_table(url, expected, absent):
    got = codes(url)
    assert expected <= got, (url, got)
    assert not (absent & got), (url, got)


def test_clean_plain_url_has_no_indicators_and_states_limits():
    r = analyse_url("https://www.example.org/about")
    assert r["status"] == "analysed" and r["indicators"] == []
    assert r["fetched"] is False and r["reputation"] == "unavailable"
    assert any("not fetched" in x for x in r["limits"])
    assert any("cannot be known" in x for x in r["limits"])


def test_indicator_shape_and_weights_in_range():
    r = analyse_url("http://sbi-kyc.example.top/login?" + "x=1&" * 70)
    assert r["indicators"]
    for i in r["indicators"]:
        assert set(i) >= {"code", "weight", "text", "source"} and 0 < i["weight"] <= 1
        assert i["source"] == "url_heuristics"


@pytest.mark.parametrize("bad", ["", "   ", "http://", "x" * 3000, "http://a b.com/", "http://[::1"])
def test_unparseable_urls_give_no_indicators(bad):
    r = analyse_url(bad)
    assert r["status"] == "unparseable" and r["indicators"] == []


def test_bare_host_gets_a_note_not_a_crash():
    r = analyse_url("hdfc-secure.example.xyz/login")
    assert r["status"] == "analysed" and r["notes"]
    assert "BRAND_LOOKALIKE" in {i["code"] for i in r["indicators"]}


def test_entity_checks_are_context_not_evidence():
    out = check_entities({"suspect_phone": "+917042918356", "upi_id": "ravi@okaxis",
                          "suspect_email": "a@example.org"})  # fmt: skip
    f = {c["field"]: c for c in out["checks"]}
    assert f["phone"]["plausible"] and f["upi_id"]["handle_known"] and f["email"]["plausible"]
    assert out["reputation"] == "unavailable"
    assert not any("weight" in c for c in out["checks"])


def test_placeholder_and_malformed_values_are_flagged_as_data_quality():
    f = {c["field"]: c for c in check_entities({"suspect_phone": "+910000000000",
                                                "upi_id": "bad id@x"})["checks"]}  # fmt: skip
    assert f["phone"]["plausible"] is False and f["phone"]["notes"]
    assert f["upi_id"]["format"] == "invalid"
    g = {c["field"]: c for c in check_entities({"suspect_phone": "12ab", "upi_id": "x1@weirdbank"})["checks"]}
    assert g["phone"]["format"] == "invalid"
    assert g["upi_id"]["format"] == "valid" and g["upi_id"]["handle_known"] is False
    assert "not evidence of fraud" in " ".join(g["upi_id"]["notes"])


def test_absent_fields_produce_no_checks():
    assert check_entities({})["checks"] == []
    ctx = context_fields({"city": None, "amount": None})
    assert ctx["geographic"]["present"] is False and ctx["used_for_scoring"] is False


def test_context_fields_report_presence_only():
    ctx = context_fields({"amount": "100.00", "city": "Pune", "latitude": 1.0, "longitude": 2.0})
    assert ctx["geographic"]["present"] and "coordinates" in ctx["geographic"]["fields"]
    assert ctx["amount_present"] and ctx["geographic"]["used_for_scoring"] is False
