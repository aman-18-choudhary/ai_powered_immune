"""Message-level rules (rules-v2, accuracy batch 2, 2026-10-18).

Positive and hard-negative examples per rule. They are phrased differently from the SYNTHETIC
evaluation sets in ``evaluation/datasets`` so that the rules are not only tested on the cases they
were measured on. All texts are synthetic.
"""

import pytest

from call_guard.rules import analyse


def codes(text: str) -> set[str]:
    return {r.code for r in analyse(text)[1]}


# ----------------------------------------------------------------------- fix 5: genuine bank OTP
GENUINE_OTP = [
    "Your OTP for SBI login is 482913. Do not share this OTP with anyone. SBI never asks for your OTP or PIN.",
    "Use 7731 as your one time password for card ending 4421. Please do not share it with anyone.",
    "OTP 902211 for your Axis transaction. Never share this OTP, not even with bank staff.",
    "Your OTP is 1188. Do not disclose this OTP to anyone.",
]
OTP_THEFT = [
    "Do not share this OTP with anyone except me, I am verifying your account. Tell me the code now.",
    "Read out the OTP you just received so I can stop the fraud on your card.",
    "Sir, OTP aaya hoga, woh mujhe bata dijiye abhi.",
    "Please share the OTP only with me, the bank officer on this call.",
]


@pytest.mark.parametrize("text", GENUINE_OTP)
def test_genuine_otp_messages_raise_no_cue(text):
    assert not (codes(text) & {"CREDENTIAL_ASK", "ISOLATION_DEMAND"}), text


@pytest.mark.parametrize("text", OTP_THEFT)
def test_otp_theft_still_detected(text):
    assert "CREDENTIAL_ASK" in codes(text), text


# ----------------------------------------------------------------------- UPI PIN to receive
@pytest.mark.parametrize(
    "text",
    [
        "Congratulations! Rs 750 lottery amount is waiting. Type your UPI PIN on the next screen to receive it.",
        "To get the refund credited, accept the payment request and enter your MPIN.",
        "Aapka cashback 500 rupees hai, lene ke liye PIN dalo aur request approve karo, paisa milega.",
        "कैशबैक पाने के लिए यूपीआई पिन डालिए।",
    ],
)
def test_upi_pin_to_receive(text):
    assert "UPI_PIN_TO_RECEIVE" in codes(text), text


@pytest.mark.parametrize(
    "text",
    [
        "Remember: you never need a PIN to receive money. Enter it only to pay.",
        "Enter your UPI PIN to complete the payment of Rs 250 to the grocery store.",
        "Your refund of Rs 300 has been credited to your account. No action is required.",
        "Paise lene ke liye PIN ki zarurat nahi hoti.",
    ],
)
def test_upi_pin_hard_negatives(text):
    assert "UPI_PIN_TO_RECEIVE" not in codes(text), text


# ----------------------------------------------------------------------- advance fee
@pytest.mark.parametrize(
    "text",
    [
        "Online part-time work: review products and earn 4000 a day. A joining fee of 350 is required.",
        "You have been shortlisted for the data entry job. Pay the training fee of Rs 2,500 first.",
        "Your winnings of Rs 60,000 are on hold. Pay the GST charges of Rs 4,800 to release the amount.",
        "Kaam ghar baithe, roz ke 1500. Shuru karne se pehle 500 registration fee bhejo.",
    ],
)
def test_advance_fee(text):
    assert "ADVANCE_FEE_DEMAND" in codes(text), text


@pytest.mark.parametrize(
    "text",
    [
        "We are hiring delivery partners. There are no joining charges; bring your licence on Monday.",
        "Please pay your exam registration fee by the 30th on the university portal.",
        "Your salary for the task completed in September has been credited.",
        "The gym membership fee is Rs 1,200 per month.",
    ],
)
def test_advance_fee_hard_negatives(text):
    assert "ADVANCE_FEE_DEMAND" not in codes(text), text


# ----------------------------------------------------------------------- guaranteed returns
@pytest.mark.parametrize(
    "text",
    [
        "Assured returns of 25% every month on our forex scheme. Minimum investment Rs 5,000.",
        "Pakka munafa: 3 percent daily, guaranteed returns with zero risk.",
        "Triple your investment in 60 days with our stock tips.",
    ],
)
def test_guaranteed_returns(text):
    assert "GUARANTEED_RETURNS" in codes(text), text


@pytest.mark.parametrize(
    "text",
    [
        "Our senior citizen FD offers 8.1% interest per annum.",
        "No investment can guarantee returns; please read the scheme documents carefully.",
        "The bond gives a fixed return of 7.5% a year.",
        "My salary doubled after the promotion.",
    ],
)
def test_guaranteed_returns_hard_negatives(text):
    assert "GUARANTEED_RETURNS" not in codes(text), text


# ----------------------------------------------------------------------- sextortion
@pytest.mark.parametrize(
    "text",
    [
        "I saved your intimate photos. Transfer 25,000 or I will leak them to your Facebook friends.",
        "We have the recording of your video call. Pay today otherwise your relatives will see it.",
        "Tumhari nangi video mere paas hai, 5000 bhejo nahi to sab ko bhej dunga.",
    ],
)
def test_sextortion(text):
    assert "SEXTORTION_THREAT" in codes(text), text


@pytest.mark.parametrize(
    "text",
    [
        "Sending the private family photos to the relatives group now.",
        "The video call recording of today's meeting is shared with the team.",
        "Please don't post my personal photos on Instagram, I would like to keep them private.",
    ],
)
def test_sextortion_hard_negatives(text):
    assert "SEXTORTION_THREAT" not in codes(text), text


# ----------------------------------------------------------------------- account block threats
@pytest.mark.parametrize(
    "text",
    [
        "Your HDFC NetBanking will be suspended tonight. Verify your PAN here: http://hdfc-netbank-kyc.top",
        "Alert: your SIM card will be blocked in 2 hours. Complete e-KYC by clicking the link below.",
        "Aapka Paytm wallet band ho jayega, abhi KYC update kariye is link se.",
    ],
)
def test_account_block_threat(text):
    assert "ACCOUNT_BLOCK_THREAT" in codes(text), text


@pytest.mark.parametrize(
    "text",
    [
        "Your card ending 1234 has been blocked at your request. Visit any branch to get a new one.",
        "Your old debit card was blocked after the new card was activated.",
        "Your KYC is due; please update it at your home branch to keep the account active.",
    ],
)
def test_account_block_hard_negatives(text):
    assert "ACCOUNT_BLOCK_THREAT" not in codes(text), text


# ----------------------------------------------------------------------- reported encounters
@pytest.mark.parametrize(
    "text",
    [
        "The man said he was from the cyber cell and that I was under digital arrest. I paid 40,000.",
        "They kept me on a video call all night and I transferred my savings to their safe account.",
        "Usne kaha parcel me drugs hain aur maine 70,000 transfer kar diye.",
    ],
)
def test_reported_scam_encounter(text):
    assert "REPORTED_SCAM_ENCOUNTER" in codes(text), text


@pytest.mark.parametrize(
    "text",
    [
        "I paid the customs duty for my parcel at the post office counter.",
        "I transferred Rs 5,000 to my landlord for the rent.",
        "I read a news article about digital arrest scams and told my parents.",
    ],
)
def test_reported_encounter_hard_negatives(text):
    assert "REPORTED_SCAM_ENCOUNTER" not in codes(text), text


# ----------------------------------------------------------------------- awareness stays quiet
@pytest.mark.parametrize(
    "text",
    [
        "Awareness: fraudsters ask you to enter your UPI PIN to receive a refund. Never do it.",
        "News report: job scams demand registration fees from students.",
        "Beware of anyone promising guaranteed 40% returns in 15 days.",
    ],
)
def test_awareness_texts_raise_no_new_class(text):
    new = {"UPI_PIN_TO_RECEIVE", "ADVANCE_FEE_DEMAND", "GUARANTEED_RETURNS", "SEXTORTION_THREAT",
           "ACCOUNT_BLOCK_THREAT", "REPORTED_SCAM_ENCOUNTER"}  # fmt: skip
    assert not (codes(text) & new), (text, codes(text))


# ----------------------------------------------------------------------- batch 2 review probes
# Found by adversarial probing during the batch 2 review (2026-10-18); each was a real mismatch.
@pytest.mark.parametrize(("text", "absent"), [
    ("PPF gives assured returns and interest is compounded daily.", "GUARANTEED_RETURNS"),
    ("I applied for a job yesterday. Also, I need to pay the security deposit for my new flat "
     "first.", "ADVANCE_FEE_DEMAND"),
    ("Job fair on Sunday. Registration fee Rs 100 per candidate at the venue.", "ADVANCE_FEE_DEMAND"),
    ("To start the course, pay the fee at the college office.", "ADVANCE_FEE_DEMAND"),
    ("The private video of the wedding is ready. Pay the photographer Rs 5,000 or he won't send "
     "it to the family.", "SEXTORTION_THREAT"),
    ("I transferred money to my savings account, which is a secure account with high interest.",
     "REPORTED_SCAM_ENCOUNTER"),
])  # fmt: skip
def test_review_hard_negatives(text, absent):
    assert absent not in codes(text), text


def test_exception_guard_sees_the_whole_sentence():
    # the clause splitter cuts at "but", so "... anyone but me" must be judged on the sentence
    assert "CREDENTIAL_ASK" in codes("Don't share the OTP with anyone but me.")
