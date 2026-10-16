"""Shared grounding checks for local-LLM explanation text (pure functions, no network).

The role-inversion table is the regression for finding 1: qwen2.5:0.5b wrote "the caller is under
'digital arrest'" on 2026-10-15 and the old regex let it through because of the quotes.
"""

import pytest

from svckit.llm_grounding import (
    ground_output,
    is_local_url,
    review_output,
    role_inversion,
    strip_invented,
    tidy,
    validate_output,
)

# --------------------------------------------------------------------------- role inversion
OBSERVED_2026_10_15 = "the caller is under 'digital arrest'"

MUST_REJECT = [
    OBSERVED_2026_10_15,
    "The caller is under 'digital arrest'.",
    'The caller is under "Digital Arrest" and needs help.',
    "THE CALLER IS UNDER DIGITAL ARREST",
    "The caller is under ‘digital arrest’.",
    "The caller is now under digital arrest.",
    "The caller has been placed under digital arrest by the CBI.",
    "The caller was put under arrest.",
    "The scammer is under arrest.",
    "The sender has been arrested.",
    "The officer will be arrested if you do not pay.",
    "The caller is being investigated by the police.",
    "The caller is under investigation.",
    "The caller is the victim here.",
    "The caller is a victim of a digital arrest scam.",
    "The caller was threatened with arrest.",
    "The caller is being threatened by the police.",
    "The caller was asked to pay money to a safe account.",
    "The caller was told to transfer money.",
    "The caller has to pay a fine.",
    "The caller faces arrest.",
    "The fraudster is in digital arrest.",
    "Your money is safe. The caller is under 'digital arrest'.",  # second sentence
    "The callers are under digital arrest.",
]

MUST_PASS = [
    "The caller says you are under digital arrest.",
    "The caller claims the victim is under digital arrest.",
    "The victim was told they are under digital arrest.",
    "The caller said you were under 'digital arrest' and asked you to pay.",
    "The caller threatened you with arrest.",
    "The caller threatens digital arrest.",
    "The caller impersonates a police officer and threatens digital arrest.",
    "The caller placed you under 'digital arrest' and asked you to stay on the call.",
    "The caller asked you to pay money to a safe account.",
    "The caller asked for your OTP.",
    "You are told you are under digital arrest; this is a scam cue.",
    "The caller in this digital arrest scam asked you to transfer money.",
    "The caller is an officer who says you are under digital arrest.",
    "The message says the person is under investigation.",
    "The caller keeps you under digital arrest on a video call.",
    "No agency can place you under digital arrest.",
]


@pytest.mark.parametrize("sentence", MUST_REJECT)
def test_role_inversion_must_reject(sentence):
    assert role_inversion(sentence), sentence


@pytest.mark.parametrize("sentence", MUST_PASS)
def test_role_inversion_must_pass(sentence):
    assert not role_inversion(sentence), sentence


def test_observed_sentence_is_rejected_by_the_full_review():
    findings = ["caller says the victim is under 'digital arrest' (the message says: \"you are "
                "under digital arrest\") digital arrest phrase"]  # fmt: skip
    prompt = "Findings:\n1. " + findings[0]
    res = review_output(
        "The message mentions digital arrest; the caller is under 'digital arrest'.",
        prompt, findings,
    )  # fmt: skip
    assert res.text is None and res.reason == "role_inversion"


# --------------------------------------------------------------------------- other checks
FINDINGS = ["asks to move money to a safe or RBI account safe account transfer",
            "caller claims to be from an authority authority impersonation"]  # fmt: skip
PROMPT = "Findings:\n1. asks to move money to a safe or RBI account\n2. caller claims authority"


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("The demand for a safe account suggests the caller has evidence of a financial "
         "transaction.", "unsupported_claim"),
        ("The caller asked for your OTP and a safe account transfer.", "ungrounded_indicator"),
        ("呼叫者要求您将资金转入安全账户。这是诈骗。", "wrong_language"),
        ("Please be careful with everything you receive in general.", "not_grounded"),
        ("short.", "empty_or_too_short"),
    ],
)  # fmt: skip
def test_ground_output_reasons(text, reason):
    assert ground_output(text, FINDINGS) == reason


def test_grounded_text_passes_and_context_allows_a_cue():
    ok = "The caller claimed authority and asked to move money to a safe account."
    assert ground_output(ok, FINDINGS) is None
    # a cue named only in the context (e.g. the suggested category) is grounded, but the context
    # alone does not satisfy the "refers to a finding" check
    assert ground_output("This looks like a digital arrest pattern with a safe account.",
                         FINDINGS, context="digital arrest") is None  # fmt: skip
    assert ground_output("This looks like a digital arrest pattern.", FINDINGS) == (
        "ungrounded_indicator"
    )


def test_no_findings_means_only_cue_checks():
    assert ground_output("No scam cue was detected by the checks here.", []) is None
    assert ground_output("The caller threatened arrest.", []) == "ungrounded_indicator"


def test_validate_strip_tidy():
    assert validate_output("Visit http://evil.example now", "prompt") == "unsafe_content"
    assert validate_output("It is 95% likely fraud.", "prompt") == "likelihood_claim"
    assert validate_output("x" * 701, "prompt") == "output_too_long"
    assert validate_output("x" * 900, "prompt", max_len=1200) is None
    assert validate_output("", "p") == "bad_response"
    # a phrase copied from the prompt is not a likelihood claim
    assert validate_output("the better the chance of a freeze.",
                           "the sooner the better the chance of a freeze") is None  # fmt: skip
    assert strip_invented("call 12345678 now [payment-id] [link]", "prompt") == "call now"
    assert tidy("First one here. First one here. Two is here. Three is here. Four is here.") == (
        "First one here. Two is here. Three is here."
    )
    assert tidy("1. First item here.\n2. Second item.\nFindings:\n- x") == (
        "First item here. Second item."
    )


def test_review_output_pipeline():
    raw = ("The caller claimed authority and asked to move money to a safe account. Call "
           "9876543210 for help. The caller claimed authority and asked to move money to a safe "
           "account. And then the")  # fmt: skip
    res = review_output(raw, PROMPT, FINDINGS)
    assert res.reason is None and "9876543210" not in res.text and res.text.endswith(".")
    assert res.text.count("safe account") == 1
    assert review_output("   ", PROMPT, FINDINGS).reason == "empty_or_too_short"
    assert review_output("y" * 800, PROMPT, FINDINGS).reason == "output_too_long"
    assert review_output(None, PROMPT, FINDINGS).reason == "bad_response"


@pytest.mark.parametrize(
    ("url", "local"),
    [
        ("http://127.0.0.1:11434", True), ("http://localhost:11434", True),
        ("http://ollama:11434", True), ("http://10.0.0.5:11434", True),
        ("http://box.localhost", True), ("https://api.example.com", False),
        ("http://8.8.8.8", False), (None, False), ("", False),
    ],
)  # fmt: skip
def test_is_local_url(url, local):
    assert is_local_url(url) is local
