import pytest
from call_guard.model import Scorer, load_classifier

from live_audio.detect import (
    LEVELS,
    LiveDetector,
    RiskTracker,
    ScorerUnavailable,
    extra_hits,
    level_for,
    normalise_spoken,
)


@pytest.fixture(scope="module")
def scorer() -> Scorer:
    return Scorer(load_classifier())


def codes(text: str, scorer: Scorer) -> set[str]:
    res = LiveDetector(lambda: scorer).analyse([(1, text)])
    return {h.code for h in res.hits}


# (text, expected code that must be among the indicators)
CASES = [
    # digital-arrest / impersonation (call-guard rules)
    ("This is officer Sharma from the CBI.", "AUTHORITY_IMPERSONATION"),
    ("You are under digital arrest.", "DIGITAL_ARREST_PHRASE"),
    ("Aap digital arrest mein ho.", "DIGITAL_ARREST_PHRASE"),
    ("आप डिजिटल अरेस्ट में हैं।", "DIGITAL_ARREST_PHRASE"),
    # OTP / PIN requests
    ("What is the OTP you just received?", "OTP_PIN_REQUEST"),
    ("Share the OTP with me.", "CREDENTIAL_ASK"),
    ("Abhi OTP batay ye.", "OTP_PIN_REQUEST"),
    ("Jo OTP aaya hai woh bataiye.", "CREDENTIAL_ASK"),
    ("अपना ओटीपी बताइए।", "CREDENTIAL_ASK"),
    ("Please read out your card number and CVV.", "OTP_PIN_REQUEST"),
    # transfer instructions
    ("Send five thousand rupees to this UPI ID now.", "MONEY_TRANSFER_INSTRUCTION"),
    ("Paise is account mein transfer kar dijiye.", "MONEY_TRANSFER_INSTRUCTION"),
    ("अभी पैसे ट्रांसफर कीजिए।", "MONEY_TRANSFER_INSTRUCTION"),
    # UPI / bank fraud
    ("Your KYC will expire today and your account will be blocked.", "UPI_BANK_FRAUD"),
    ("Enter your UPI PIN to receive the refund.", "UPI_BANK_FRAUD"),
    ("Aapka account band ho jayega.", "UPI_BANK_FRAUD"),
    # remote access
    ("Download AnyDesk and tell me the code.", "REMOTE_ACCESS_REQUEST"),
    # secrecy
    ("Do not tell anyone about this call.", "ISOLATION_DEMAND"),
    ("Keep this matter confidential, not even your family.", "SECRECY_DEMAND"),
    ("Kisi ko mat batana.", "ISOLATION_DEMAND"),
    ("घर वालों को मत बताइए।", "SECRECY_DEMAND"),
    # urgency / intimidation
    ("You must do it immediately.", "URGENCY"),
    ("Police will come to your house tonight.", "INTIMIDATION"),
    ("Aapko giraftar kar liya jayega.", "INTIMIDATION"),
    # spoken URLs / UPI ids
    ("Go to secure dash kyc dot in slash verify.", "SPOKEN_LINK"),
    ("Visit www dot sbi minus update dot com.", "SPOKEN_LINK"),
    ("Pay to ramesh at the rate ybl.", "SPOKEN_PAYMENT_ID"),
    ("UPI ID hai rajesh123 at the rate paytm.", "SPOKEN_PAYMENT_ID"),
]


@pytest.mark.parametrize(("text", "code"), CASES)
def test_detectors_table(text, code, scorer):
    assert code in codes(text, scorer)


BENIGN = [
    "Hello, how are you? I will be home for lunch at one.",
    "Mummy, kal subah market chalenge, sabzi leni hai.",
    "मैं कल सुबह बाजार जाऊंगा।",
    "Never share your OTP with anyone, the bank will never ask for it.",
    "The meeting moved to Thursday, please bring the slides.",
]


@pytest.mark.parametrize("text", BENIGN)
def test_benign_has_no_strong_indicators(text, scorer):
    found = codes(text, scorer) - {"URGENCY"}
    assert found == set(), found


def test_normalise_spoken():
    assert "secure-kyc.in/verify" in normalise_spoken("secure dash kyc dot in slash verify")
    assert "ramesh@ybl" in normalise_spoken("ramesh at the rate ybl")
    assert "abc.com" in normalise_spoken("A B C dot com".replace("A B C", "abc"))


def test_extra_hits_have_excerpt_of_matched_words_only():
    hits = extra_hits("Hello sir. Download AnyDesk right away please, my name is X.")
    h = next(x for x in hits if x[0] == "REMOTE_ACCESS_REQUEST")
    assert "anydesk" in h[2].lower()
    assert "my name is" not in h[2].lower()
    assert len(h[2]) <= 80


def test_segment_to_indicator_mapping(scorer):
    det = LiveDetector(lambda: scorer)
    res = det.analyse(
        [
            (10, "Hello, good morning."),
            (11, "This is officer Sharma from the CBI."),
            (12, "Download AnyDesk now."),
        ]
    )
    by = {h.code: h for h in res.hits}
    assert by["AUTHORITY_IMPERSONATION"].segment_id == 11
    assert by["REMOTE_ACCESS_REQUEST"].segment_id == 12
    assert all(h.segment_id != 10 for h in res.hits)
    assert by["AUTHORITY_IMPERSONATION"].excerpt
    assert by["AUTHORITY_IMPERSONATION"].source == "call_guard"
    assert by["REMOTE_ACCESS_REQUEST"].source == "live_rules"


def test_advisory_clause_not_flagged(scorer):
    assert "OTP_PIN_REQUEST" not in codes("We will never ask for your OTP.", scorer)


def test_scorer_failure_raises_unavailable():
    class Boom:
        model_version = "x"

        def score_text(self, text):
            raise RuntimeError("boom")

    det = LiveDetector(lambda: Boom())
    with pytest.raises(ScorerUnavailable):
        det.analyse([(1, "You are under digital arrest.")])
    with pytest.raises(ScorerUnavailable):
        LiveDetector(lambda: None).analyse([(1, "anything")])


def test_levels():
    assert LEVELS == ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    assert level_for(0) == "LOW"
    assert level_for(24) == "LOW"
    assert level_for(25) == "MEDIUM"
    assert level_for(50) == "HIGH"
    assert level_for(70) == "CRITICAL"


def test_risk_accumulates_monotonically_then_decays(scorer):
    det = LiveDetector(lambda: scorer)
    rt = RiskTracker()
    lines = [
        "This is officer Sharma from the CBI.",
        "You are under digital arrest.",
        "Do not tell anyone, share the OTP.",
    ]
    scores = []
    for i, line in enumerate(lines):
        a = det.analyse([(i, line)])
        scores.append(rt.update(a.score, a.reasons, at_s=4.0 * i))
    assert scores == sorted(scores)
    assert scores[-1] >= 70
    # 20 minutes later, a benign chunk: the score has decayed (half-life 300 s)
    a = det.analyse([(9, "Okay, thank you, bye.")])
    later = rt.update(a.score, a.reasons, at_s=8.0 + 1200)
    assert later < scores[-1] / 4


def test_benign_stays_low(scorer):
    det = LiveDetector(lambda: scorer)
    rt = RiskTracker()
    for i, line in enumerate(BENIGN):
        a = det.analyse([(i, line)])
        s = rt.update(a.score, a.reasons, at_s=4.0 * i)
    assert level_for(s) == "LOW"
