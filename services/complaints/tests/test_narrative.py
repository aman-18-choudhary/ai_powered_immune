"""Complaint-narrative cues (complaints.narrative). Synthetic TEST DATA only."""

import pytest

from complaints import narrative
from complaints.analysis import ComplaintAnalysisPipeline

COLLECT_STORY = (
    "A caller said he was from my bank and that a refund was pending. He asked me to approve a "
    "collect request on my UPI app and share the OTP. After I entered my UPI PIN, Rs 25,000 was "
    "debited from my account."
)


def codes(text):
    return [c.code for c in narrative.cues(text)]


@pytest.mark.parametrize(("text", "code"), [
    (COLLECT_STORY, "COLLECT_REQUEST_OR_QR_TO_RECEIVE"),
    (COLLECT_STORY, "CREDENTIALS_HANDED_OVER"),
    ("The buyer sent a QR code and asked me to scan it to receive the advance.",
     "COLLECT_REQUEST_OR_QR_TO_RECEIVE"),
    ("SMS aaya ki KYC update karo. Maine link khol ke password aur OTP daal diya.",
     "CREDENTIALS_HANDED_OVER"),
    ("लिंक पर क्लिक करके मैंने ओटीपी डाला, फिर खाते से पैसे कट गए।", "CREDENTIALS_HANDED_OVER"),
    ("The caller made me install AnyDesk to fix my refund.", "REMOTE_ACCESS_APP"),
    ("To receive the prize I had to pay a processing fee and GST.", "FEE_TO_RELEASE_MONEY"),
    ("The trading app showed big profits but I could not withdraw my money.",
     "FAKE_PROFIT_NO_WITHDRAWAL"),
    ("An SMS said my electricity would be cut tonight unless I called the officer.",
     "UTILITY_DISCONNECTION_THREAT"),
])  # fmt: skip
def test_victim_narratives_are_recognised(text, code):
    assert code in codes(text)


@pytest.mark.parametrize("text", [
    "The bank SMS said never share your OTP with anyone, so I did not.",
    "Kisi ko bhi OTP mat batana, bank ne SMS me bola tha.",
    "I entered my PIN at the ATM but the cash did not come out and the amount was debited.",
    "I paid my electricity bill on time but the power was cut today for six hours.",
    "The airline charged a cancellation fee and has not released my refund after two months.",
    "I gave my password to my brother to fix my laptop and now he will not return it.",
    "I scanned the QR code at the shop to pay for groceries and the payment failed.",
    "I installed the official bank app from the Play Store and it keeps crashing.",
    "My online order was delivered five days late and the box was damaged.",
])  # fmt: skip
def test_ordinary_complaints_and_advice_do_not_fire(text):
    assert codes(text) == []


def test_score_is_a_noisy_or_and_each_cue_points_to_its_sentence():
    found = narrative.cues(COLLECT_STORY)
    assert narrative.score([]) == 0
    assert narrative.score(found) == round(100 * (1 - __import__("math").prod(
        1 - c.weight for c in found)))  # fmt: skip
    for c in found:
        assert COLLECT_STORY[c.start : c.end].strip()
        assert c.end - c.start < len(COLLECT_STORY)


async def test_the_pipeline_flags_a_narrated_collect_request_scam():
    pytest.importorskip("call_guard")
    out = await ComplaintAnalysisPipeline(category_loader=lambda: None).analyse(
        {"category": "OTHER", "title": "Fake bank caller", "description": COLLECT_STORY})
    narr = [i for i in out.indicators if i["stage"] == "complaint_narrative"]
    assert {i["code"] for i in narr} >= {"COLLECT_REQUEST_OR_QR_TO_RECEIVE",
                                         "CREDENTIALS_HANDED_OVER"}  # fmt: skip
    assert all(i["source"] == "rule" and i["evidence"]["type"] == "text_span" for i in narr)
    assert out.risk_level in ("HIGH", "CRITICAL")  # rule-backed, so HIGH is allowed
    comp = out.extra["score_components"]
    assert comp["narrative_score"] >= 70
    assert out.pipeline_version.endswith("+" + narrative.NARRATIVE_VERSION)
    assert "The report describes:" in out.explanation
    assert "matched no specific scam cue" not in out.explanation
