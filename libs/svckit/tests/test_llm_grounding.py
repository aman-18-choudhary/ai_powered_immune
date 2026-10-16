"""Shared grounding checks for local-LLM explanation text (pure functions, no network).

The role-inversion table is the regression for finding 1: qwen2.5:0.5b wrote "the caller is under
'digital arrest'" on 2026-10-15 and the old regex let it through because of the quotes.
"""

import pytest

from svckit.llm_grounding import (
    ground_output,
    is_local_url,
    repeats_scam_instruction,
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
    # the mirror inversion: the listener given the caller's authority role (qwen2.5:0.5b in the
    # Live Protection prompt, 2026-10-16: "You are from the CBI")
    "You are from the CBI.",
    "You are a police officer.",
    "You are an officer from customs.",
    # "claims to be" describes the caller himself (qwen2.5:0.5b, 2026-10-16)
    "The caller claims to be under 'digital arrest' and is not to be contacted.",
    "The officer pretends to be under investigation.",
    # passive "be" + participle: the caller is the one acted upon
    "The caller is advised to hang up the call.",
    "The caller is warned not to pay.",
    "The fraudster has been put in police custody.",
    # the safety steps attributed to the caller (qwen2.5:0.5b, Live Protection, 2026-10-16)
    "The caller advises you to hang up the call.",
    "The caller advises you not to pay, transfer money or share any code.",
    "The caller suggests calling your bank and reporting to the helpline.",
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
    "You are speaking to someone who claims to be from the CBI.",
    "You are being threatened by a fake police officer.",
    "You are told the caller is from the CBI.",
    "The caller claims to be from the CBI.",
    "The caller pretends to be a police officer.",
    # perfect tense is active: the caller is the agent
    "The caller has asked you to pay money.",
    "The caller has claimed that you are under digital arrest.",
    "The caller had threatened you with arrest.",
    # an auxiliary binds only the words right after it
    "The caller claimed to be from an authority and asked you to move money to a safe account.",
    "The caller is from the bank and asked for your PIN.",
    "The caller advises you to transfer money to a safe account.",
]

# stating the scammer's claim as a fact to the listener (qwen2.5:0.5b, 2026-10-16)
ASSERTS_ARREST = [
    "You are under 'digital arrest' and should not disclose any information.",
    "You are now under digital arrest.",
    "You will be arrested.",
    "You have been placed under arrest by the CBI.",
]
ATTRIBUTED = [
    "The caller says you are under digital arrest.",
    "You are told you are under digital arrest; this is a scam cue.",
    "You are not under digital arrest.",
    "No agency can place you under digital arrest.",
    "The caller claims you will be arrested.",
    "The message said that you were under arrest.",
]


@pytest.mark.parametrize("sentence", ASSERTS_ARREST)
def test_unattributed_arrest_claim_is_rejected(sentence):
    assert ground_output(sentence + " The caller says this.", ["you are under digital arrest "
                         "says you are under arrest arrested"]) == "unsupported_claim"  # fmt: skip


@pytest.mark.parametrize("sentence", ATTRIBUTED)
def test_attributed_or_negated_arrest_wording_passes(sentence):
    assert ground_output(sentence, ["says you are under digital arrest you are under digital "
                                    "arrest arrested"]) is None  # fmt: skip


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


SCAM_INSTRUCTIONS = [
    # qwen2.5:0.5b turned the caller's isolation demand into advice (2026-10-16)
    "Do not tell anyone about this call.",
    "Don't tell your family.",
    "Tell no one about it.",
    "Stay on the call.",
    "You should stay on the video call until it ends.",
    "Keep this secret.",
    "Do not disconnect.",
    "Do not hang up the call.",
    "Transfer the money to the safe account now.",
    "Please send the money today.",
    "Share the OTP you just received.",
    "Give your PIN to the officer.",
    "Install AnyDesk.",
]
SAFETY_ADVICE = [
    "Do not pay, transfer money or share any code.",
    "Hang up the call.",
    "The caller tells you not to tell anyone about the call.",
    "The caller demands you stay on the call.",
    "Tell your family and your bank.",
    "Do not share your OTP with anyone.",
    "Do not stay on the call.",
    "Never transfer money to a safe account.",
    "Call your bank on the number printed on your card.",
]


@pytest.mark.parametrize("sentence", SCAM_INSTRUCTIONS)
def test_repeated_scam_instruction_is_rejected(sentence):
    assert repeats_scam_instruction(sentence), sentence


@pytest.mark.parametrize("sentence", SAFETY_ADVICE)
def test_safety_advice_is_not_a_scam_instruction(sentence):
    assert not repeats_scam_instruction(sentence), sentence


def test_ground_output_reports_the_repeated_instruction():
    text = ("The caller claims you are under 'digital arrest' and is from the CBI. Do not tell "
            "anyone about this call. Hang up the call.")  # fmt: skip
    findings = ["The caller says you are under 'digital arrest' digital arrest phrase",
                "The caller claims to be police, CBI or another agency authority",
                "The caller demands you stay on the call, stay silent or tell no one isolation"]
    assert ground_output(text, findings) == "repeats_scam_instruction"


def test_prompt_echo_is_rejected():
    # the Live Protection prompt echoed back by qwen2.5:0.5b on 2026-10-16
    echo = ('1. You are under \'digital arrest\' (heard: "You are under digital arrest.")\n'
            '  2. Do not tell anyone about the call (heard: "Do not tell anyone about this call.")')
    findings = ["Says you are under 'digital arrest' You are under digital arrest.",
                "Tells you not to tell anyone Do not tell anyone about this call."]  # fmt: skip
    res = review_output(echo, "prompt " + echo, findings)
    assert res.text is None and res.reason == "prompt_echo"
    assert ground_output('The caller (the message says: "pay now") wants money now.',
                         ["wants money"]) == "prompt_echo"  # fmt: skip


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


def test_review_checks_sentences_beyond_the_shown_ones():
    ok = "The caller claimed authority and asked to move money to a safe account. "
    bad = "The caller is under digital arrest."
    res = review_output(ok * 1 + "Call for help now. Hang up the call now. " + bad, PROMPT,
                        FINDINGS)  # fmt: skip
    assert res.text is None and res.reason == "role_inversion"


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
