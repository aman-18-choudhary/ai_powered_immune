"""Phase 9: identifiers taken ONLY from the structured complaint fields, their normalisation and
their masked display values. Pure functions; no database. All values below are made-up test data.
"""

import pytest

from complaints.network import (
    ENTITY_TYPES,
    extract_entities,
    mask_account,
    mask_email,
    mask_phone,
    mask_upi,
    normalise_query,
    normalise_social,
    normalise_url,
    registrable_domain,
    value_hash,
)


def _by_type(ents):
    return {(e.entity_type, e.value_norm): e for e in ents}


# ---------------------------------------------------------------------------- phone
@pytest.mark.parametrize(
    "raw",
    ["9876543210", "+91 98765 43210", "+91-98765-43210", "0091 9876543210", "09876543210",
     "919876543210", "(98765) 43210"],
)  # fmt: skip
def test_phone_formats_normalise_to_one_value(raw):
    (e,) = extract_entities({"suspect_phone": raw})
    assert (e.entity_type, e.value_norm, e.source_field) == (
        "phone",
        "+919876543210",
        "suspect_phone",
    )
    assert e.display == "••••••3210"


def test_phone_mask_never_shows_more_than_the_last_four_digits():
    assert mask_phone("+14155550123") == "••••••0123"
    assert "4155" not in mask_phone("+14155550123")


# ---------------------------------------------------------------------------- UPI
def test_upi_is_case_insensitive_and_masked():
    a = extract_entities({"upi_id": "AbCdef.Pay@OKAXIS"})
    b = extract_entities({"upi_id": "abcdef.pay@okaxis"})
    assert a == b
    (e,) = a
    assert (e.entity_type, e.value_norm) == ("upi", "abcdef.pay@okaxis")
    assert e.display == "ab•••@okaxis"
    assert mask_upi("ab@ybl") == "a•••@ybl"


# ---------------------------------------------------------------------------- email
def test_email_is_case_insensitive_and_masked():
    (e,) = extract_entities({"suspect_email": "  Refund.Desk@Example.ORG "})
    assert (e.entity_type, e.value_norm, e.source_field) == (
        "email", "refund.desk@example.org", "suspect_email",
    )  # fmt: skip
    assert e.display == "r•••@example.org"
    assert mask_email("x@example.org") == "x•••@example.org"


# ---------------------------------------------------------------------------- URL + domain
def test_url_gives_the_full_url_and_its_registrable_domain():
    ents = _by_type(
        extract_entities(
            {
                "suspicious_url": "HTTPS://Secure.KYC-Update.example.co.in:443/verify/account?id=7#top"
            }
        )
    )
    url = ents[("url", "https://secure.kyc-update.example.co.in/verify/account?id=7")]
    dom = ents[("domain", "example.co.in")]
    assert url.source_field == dom.source_field == "suspicious_url"
    # host and a truncated path, never the query
    assert url.display.startswith("secure.kyc-update.example.co.in/")
    assert "id=7" not in url.display
    assert dom.display == "example.co.in"


@pytest.mark.parametrize(
    ("host", "want"),
    [("a.b.example.com", "example.com"), ("example.com", "example.com"),
     ("www.sbi.co.in", "sbi.co.in"), ("portal.incometax.gov.in", "incometax.gov.in"),
     ("iitb.ac.in", "iitb.ac.in"), ("x.y.ac.in", "y.ac.in"), ("shop.example.co.uk", "example.co.uk"),
     ("example.org.in", "example.org.in"), ("Example.COM.", "example.com"),
     ("localhost", None), ("192.168.1.10", None), ("[::1]", None), ("co.in", None)],
)  # fmt: skip
def test_registrable_domain_rule_set(host, want):
    assert registrable_domain(host) == want


def test_url_normalisation_details():
    assert normalise_url("http://Example.com") == ("http://example.com/", "example.com")
    assert normalise_url("http://example.com:8080/a/B") == (
        "http://example.com:8080/a/B",
        "example.com",
    )
    assert normalise_url("https://example.com/path/") == (
        "https://example.com/path/",
        "example.com",
    )
    assert normalise_url("ftp://example.com/") is None
    assert normalise_url("not a url") is None


def test_ip_literal_url_has_no_domain_entity():
    ents = extract_entities({"suspicious_url": "http://192.168.1.10/login"})
    assert [e.entity_type for e in ents] == ["url"]


# ---------------------------------------------------------------------------- bank account
def test_account_number_alone_is_an_identifier():
    (e,) = extract_entities({"bank_account": "0012 3456 7890"})
    assert (e.entity_type, e.value_norm, e.source_field) == (
        "bank_account",
        "001234567890",
        "bank_account",
    )
    assert e.display == "••••••7890"


def test_account_with_ifsc_is_the_same_account_identifier():
    with_ifsc = extract_entities({"bank_account": "001234567890", "ifsc": "HDFC0001234"})
    without = extract_entities({"bank_account": "001234567890"})
    assert [(e.entity_type, e.value_norm) for e in with_ifsc] == [
        (e.entity_type, e.value_norm) for e in without
    ]
    assert mask_account("123456") == "••••••3456"


def test_ifsc_alone_is_not_an_identifier():
    assert extract_entities({"ifsc": "HDFC0001234"}) == []


# ---------------------------------------------------------------------------- social
def test_social_needs_platform_and_account():
    assert extract_entities({"social_account": "@scam.desk"}) == []
    assert extract_entities({"social_platform": "Instagram"}) == []
    (e,) = extract_entities({"social_platform": " Insta ", "social_account": "@Scam.Desk"})
    assert (e.entity_type, e.value_norm, e.source_field) == (
        "social_account", "instagram:scam.desk", "social_account",
    )  # fmt: skip
    assert e.display == "instagram: sc•••"
    assert normalise_social("WhatsApp", "+91 98765 43210") == "whatsapp:+919876543210"
    assert normalise_social("Telegram", "Scam_Desk") == "telegram:scam_desk"


# ---------------------------------------------------------------------------- no free text
def test_identifiers_come_only_from_structured_fields():
    c = {
        "title": "Call from 9876543210 about upi abc@okaxis",
        "description": "Visit https://evil.example.com and mail x@example.org, account 123456789",
        "location_text": "near 9876543210",
        "utr_reference": "UTR123456789",
    }
    assert extract_entities(c) == []


def test_invalid_legacy_values_are_skipped_not_raised():
    # rows stored before validation existed may hold odd values: they produce no entity
    assert extract_entities({"suspect_phone": "12", "upi_id": "no-at-sign", "suspect_email": "x",
                             "suspicious_url": "javascript:alert(1)", "bank_account": "12ab"}) == []  # fmt: skip


def test_every_type_is_known_and_hash_is_stable():
    c = {"suspect_phone": "9876543210", "upi_id": "a1@ybl", "suspect_email": "a@b.org",
         "suspicious_url": "https://x.example.com/p", "bank_account": "123456789",
         "social_platform": "Facebook", "social_account": "someone"}  # fmt: skip
    ents = extract_entities(c)
    assert {e.entity_type for e in ents} == set(ENTITY_TYPES)
    assert value_hash("upi", "a1@ybl") == value_hash("upi", "a1@ybl")
    assert value_hash("upi", "a1@ybl") != value_hash("email", "a1@ybl")
    assert len(value_hash("upi", "a1@ybl")) == 64


# ---------------------------------------------------------------------------- search input
def test_normalise_query_per_type():
    assert normalise_query("phone", "098765 43210") == ("+919876543210", "••••••3210")
    assert normalise_query("upi", "AB12@YBL")[0] == "ab12@ybl"
    assert normalise_query("domain", "https://login.example.co.in/x")[0] == "example.co.in"
    assert normalise_query("domain", "Login.Example.co.in")[0] == "example.co.in"
    assert normalise_query("url", "https://Example.com")[0] == "https://example.com/"
    assert normalise_query("bank_account", "1234 5678 90")[0] == "1234567890"
    assert normalise_query("social_account", "@Who", platform="FB")[0] == "facebook:who"


@pytest.mark.parametrize(
    ("t", "v", "platform"),
    [("phone", "12", None), ("upi", "nope", None), ("email", "x@", None), ("url", "example", None),
     ("domain", "localhost", None), ("bank_account", "12ab", None),
     ("social_account", "@who", None), ("social_account", "", "Instagram")],
)  # fmt: skip
def test_normalise_query_rejects_invalid_values_without_echoing_them(t, v, platform):
    with pytest.raises(ValueError) as exc:
        normalise_query(t, v, platform=platform)
    if v:
        assert v not in str(exc.value)
