import pytest

from call_guard.hardneg import HARD_NEGATIVES
from call_guard.rules import RULES_VERSION, score_chunk, score_text

ARREST_EN = (
    "This is Inspector Sharma from the CBI. You are now under digital arrest. Do not disconnect "
    "this video call and do not tell anyone, or we will arrest your family."
)
ARREST_HI_LATN = (
    "Main CBI se bol raha hoon. Aap abhi se digital arrest me hain. Video call mat kaatiye "
    "aur kisi ko mat batana."
)
ARREST_HI = (
    "मैं सीबीआई से बोल रहा हूँ। आप अभी से डिजिटल अरेस्ट में हैं। वीडियो कॉल मत काटिए और किसी को मत बताना।"
)
SAFE_ACCT = (
    "For RBI verification you must transfer your funds to the RBI safe account I am sending "
    "now. The money will be returned within 24 hours."
)


def codes(reasons):
    return {r.code for r in reasons}


def test_digital_arrest_script_scores_above_threshold(make_event):
    score, reasons = score_chunk(make_event(ARREST_EN))
    assert score >= 0.7
    assert {"AUTHORITY_IMPERSONATION", "DIGITAL_ARREST_PHRASE", "ISOLATION_DEMAND"} <= codes(
        reasons
    )


def test_normal_bank_call_scores_below_0_2(make_event):
    text = (
        "Hello, this is HDFC bank customer care. Your credit card statement was generated "
        "yesterday. For security please confirm your date of birth. We will never ask for your OTP."
    )
    score, _ = score_chunk(make_event(text))
    assert score < 0.2


@pytest.mark.parametrize("text", [ARREST_HI_LATN, ARREST_HI])
def test_hindi_script_detected(make_event, text):
    score, reasons = score_chunk(make_event(text, lang="hi"))
    assert score >= 0.7
    assert "DIGITAL_ARREST_PHRASE" in codes(reasons)
    assert "ISOLATION_DEMAND" in codes(reasons)


def test_reasons_include_codes(make_event):
    _, reasons = score_chunk(make_event(ARREST_EN + " " + SAFE_ACCT))
    c = codes(reasons)
    assert "AUTHORITY_IMPERSONATION" in c and "SAFE_ACCOUNT_TRANSFER" in c
    assert all(0 < r.weight <= 1 and r.detail for r in reasons)


def test_safe_account_alone_is_high(make_event):
    assert score_chunk(make_event(SAFE_ACCT))[0] >= 0.7


def test_urgency_and_verification_codes(make_event):
    _, r = score_text(
        "You have only thirty minutes. Tell me your account balance and verify your Aadhaar."
    )
    assert {"URGENCY", "ACCOUNT_VERIFICATION_ASK"} <= codes(r)


@pytest.mark.parametrize("text", HARD_NEGATIVES)
def test_hard_negatives_score_low(text):
    score, reasons = score_text(text)
    assert score < 0.2, (text, reasons)


def test_neutral_text_has_no_reasons_and_zero_score():
    assert score_text("Your parcel will arrive between two and four pm.") == (0.0, [])


def test_score_in_unit_interval_and_version():
    s, _ = score_text(ARREST_EN + " " + SAFE_ACCT + " " + ARREST_HI)
    assert 0 <= s <= 1
    assert RULES_VERSION == "rules-v1"
