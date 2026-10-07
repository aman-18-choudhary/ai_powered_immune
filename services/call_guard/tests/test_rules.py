import pytest

from call_guard.hardneg import HARD_NEGATIVES
from call_guard.rules import CALL_RISK_THRESHOLD, RULES_VERSION, score_chunk, score_text

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


# ----------------------------------------------------------------- fix round 1
EVASION = [
    "We will never ask for money but you must transfer your funds to the RBI safe account now",
    "Do not tell anyone, this is a fraud case under digital arrest.",
    "Kabhi bhi kisi ko mat batana, aap digital arrest me hain",
    "Please ignore the noise, you are under digital arrest.",
    "Never disconnect this call, never tell anyone. This is the CBI officer speaking.",
]


STRONG_EVASION = [EVASION[0], EVASION[2], EVASION[4]]  # two independent cue classes


@pytest.mark.parametrize("text", EVASION)
def test_awareness_words_do_not_blank_demand_clauses(text):
    """The demand clause survives (reasons present, score high) and, for single-class texts,
    a second cue-bearing chunk in the same call crosses the threshold."""
    from call_guard.model import Scorer, load_classifier

    s = Scorer(load_classifier())
    score, reasons, _ = s.score_text(text)
    assert reasons and reasons[0].code != "NO_RISK_INDICATORS" and score >= 0.55
    if text in STRONG_EVASION:
        assert score >= 0.7
    else:  # lone accusation / lone isolation demand: below threshold alone, crosses with a 2nd cue
        assert score < 0.7
        second = "This is Inspector Rao from the CBI. Transfer your funds to the RBI safe account."
        from call_guard.session import accumulate

        st = accumulate(None, score, reasons, 0.0)
        s2, r2, _ = s.score_text(second)
        assert accumulate(st, s2, r2, 20.0).score >= 0.7


@pytest.mark.parametrize("text", STRONG_EVASION)
def test_evasion_crosses_with_rules_alone(text):
    assert score_text(text)[0] >= 0.7


@pytest.mark.parametrize(
    "text",
    [
        "If you receive a call claiming you are under digital arrest, disconnect and dial 1930.",
        "Papa, aaj TV par ek serial me digital arrest dikhaya, kitna darawana tha.",
        "We saw a web series where the hero gets arrested for money laundering.",
        "Dear customer, never share your OTP, PIN or CVV with anyone, including bank staff.",
        "Hello sir, courier here, the OTP on your phone please tell me so I can hand over the parcel.",
    ],
)
def test_advisory_and_media_mentions_stay_low(text):
    assert score_text(text)[0] < 0.2


def test_every_single_cue_class_is_below_threshold():
    from call_guard import rules

    assert max(rules._WEIGHT.values()) < CALL_RISK_THRESHOLD
    for text in (
        "Transfer your funds to the RBI safe account.",
        "Do not tell anyone.",
    ):
        s, r = score_text(text)
        assert len(r) == 1 and s < CALL_RISK_THRESHOLD


def test_authority_alone_or_allegation_alone_is_not_enough():
    assert score_text("This is Inspector Rajesh from the CBI, please confirm your name.")[0] < 0.7
    assert score_text("A warrant is registered.")[0] < 0.7
    assert (
        score_text("This is Inspector Rajesh from the CBI. A warrant is registered against you.")[0]
        >= 0.7
    )


@pytest.mark.parametrize(
    "text",
    [
        "ｙｏｕ ａｒｅ ｕｎｄｅｒ ｄｉｇｉｔａｌ ａｒｒｅｓｔ",  # full-width
        "you are under digital-arrest",
        "you are under digital_arrest",
        "you are under digital   arrest",
        "you are under digital​ arrest",
        "आप डि​जिटल अरे‍स्ट में हैं",  # zero-width chars inside Devanagari words
        "आप डिजिटल अरेस्ट में हैं",
    ],
)
def test_normalisation_variants_detected(text):
    assert "DIGITAL_ARREST_PHRASE" in codes(score_text(text)[1])


def test_dev_set_rules_only_recall_and_fp():
    from tests.data.dev_set import BENIGN, SCAM

    hits = sum(score_text(t)[0] >= 0.7 for t in SCAM)
    assert hits / len(SCAM) >= 0.70
    assert not [t for t in BENIGN if score_text(t)[0] >= 0.7]


RELAYED_OR_MEDIA = [
    "If someone tells you that you are under digital arrest, it's a scam.",
    "Someone called mom and said you are under digital arrest, what should we do?",
    "Papa, ek caller bol raha tha aap digital arrest me ho, ye kya hai? Mujhe darr lag raha hai.",
    "In the film the inspector tells the hero, you are under digital arrest, don't leave the room.",
    "Dad, I got a call. They said there is an FIR in my name and I must not tell anyone. I am calling you instead.",
    "Hi dad, did you see the news last night? An old man in Pune lost twenty lakhs to a digital arrest scam, the caller pretended to be from the CBI.",
    "Did you watch that crime show last night? The inspector put the villain under arrest after the courier with drugs was found.",
    "Yaar kal news me dekha, ek aadmi ko digital arrest scam me do lakh ka chuna laga. Mummy ko bol dena ki aise calls pe bharosa na kare, police aise video call par arrest nahi karti.",
    "Mom, a man claiming to be from CBI said I am under digital arrest and told me to stay on video. Is this real? I'm scared.",
]


@pytest.mark.parametrize("text", RELAYED_OR_MEDIA)
def test_relayed_quoted_and_media_speech_is_neutral(text):
    assert score_text(text)[0] < 0.5


def test_lone_accusation_stays_below_threshold_even_with_classifier():
    from call_guard.model import Scorer, load_classifier

    s = Scorer(load_classifier())
    for t in ("You are under digital arrest.", "आप डिजिटल अरेस्ट में हैं।"):
        assert s.score_text(t)[0] < 0.7


def test_official_payment_channel_not_flagged():
    t = "Pay the court fine to the government account listed on the challan, immediately, else warrant may be issued by the traffic court."
    assert score_text(t)[0] < 0.5


def test_digital_custody_synonym_and_camera_confinement():
    assert "DIGITAL_ARREST_PHRASE" in codes(
        score_text("We can place you in digital custody at home.")[1]
    )
    assert "ISOLATION_DEMAND" in codes(
        score_text("Keep your phone camera pointed at you at all times.")[1]
    )
    s, _ = score_text(
        "This is the Cyber Police. You cannot disconnect this call till the investigation is over."
    )
    assert s >= 0.7  # authority + stay-on-call is a strong pair
