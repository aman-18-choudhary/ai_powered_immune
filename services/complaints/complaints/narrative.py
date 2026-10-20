"""Complaint-narrative cues: deterministic rules for how VICTIMS describe a fraud afterwards.

The call-guard rules score what a scammer says ("share the OTP to get your refund") and on
purpose ignore relayed speech ("he asked me to share the OTP"), because on a live call relayed
speech is usually someone retelling a story. A complaint IS such a retelling, so this module
looks for the fraud MECHANISM in the citizen's own words: credentials handed over, a collect
request or QR code "to receive" money, a remote-access app installed at a caller's request, a
fee to release a prize or a withdrawal, a fake-profit app that blocks withdrawals, a utility
disconnection threat with a call-back number.

Each cue is a hand-written pattern (English, Hindi, Hinglish) with a hand-set weight; weights
are not fitted or calibrated. A cue needs its parts in ONE sentence (the investment cue, the
whole text), never fires on advice ("never share your OTP") and ignores sentences about an ATM.
Every cue returns the matched span so the citizen and staff can see why it fired. Developed on
the DEV split of evaluation/datasets/fraud_dna_v1.jsonl only (see evaluation/README.md).
"""

import re
from dataclasses import dataclass

NARRATIVE_VERSION = "narrative-v1"
METHOD = "complaints.narrative"
STAGE = "complaint_narrative"

_SENT = re.compile(r"(?<=[.?!।])\s+|\n+")


def _rx(*alts: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{a})" for a in alts), re.IGNORECASE)


# --------------------------------------------------------------------------- vocabularies
_SECRET = (r"\b(?:otp|o\.t\.p|upi[ -]?pin|m-?pin|atm pin|pin|cvv|password|passcode|net ?banking"
           r"(?: id| login| password| details)?|login(?: id| details)?|user ?id|card (?:number|"
           r"details)|card no)\b|ओटीपी|पिन|पासवर्ड|लॉगिन|सीवीवी")  # fmt: skip
_HANDED = _rx(
    # English: the victim handed it over / typed it in
    r"\b(?:i|we|my (?:father|mother|husband|wife|son|daughter))\b[^.]{0,30}\b(?:shared|gave|"
    r"told|sent|entered|typed|filled(?: in)?|put in|provided|disclosed|read out)\b",
    r"\b(?:after|when|on|by|while) (?:i )?(?:sharing|giving|entering|typing|filling(?: in)?)\b",
    r"\b(?:asked|told|made|forced|instructed|convinced|got) (?:me|us|him|her|my \w+) to "
    r"(?:share|give|tell|enter|type|send|read out|fill(?: in)?|provide)\b",
    r"\bi was asked (?:to|for)\b",
    # Hinglish
    r"\b(?:daal|dal|dala|daala|daale|bata|bataya|share kiya|share kar|de|diya|diye|bhej)\s*"
    r"(?:diya|diye|di|dia|kiya|kar diya)?\b",
    r"\b(?:maang|mang)(?:a|e|aa|ne)\b",
    # Hindi
    r"डाला|डाल दिया|बताया|बता दिया|शेयर किया|दे दिया|भेज दिया|मांगा|माँगा",
)
_SECRET_RX = re.compile(_SECRET, re.IGNORECASE)
_ADVICE = _rx(
    r"\b(?:never|do not|don't|dont|should not|shouldn't|must not)\s+(?:\w+\s+){0,2}"
    r"(?:share|give|tell|disclose)\b",
    r"\bkabhi\b[^.]{0,30}\bmat\b", r"\bmat (?:batana|batao|bataye|dena|do|dijiye|share)\b",
    r"कभी[^।]{0,30}(?:न|ना|मत)\b", r"मत (?:बताएं|बताना|दें|देना)",
)
_NEGATED = _rx(r"\b(?:did not|didn't|didnt|never|refused to|nahi|nahin)\b", r"नहीं")
_ATM = _rx(r"\batm\b", r"एटीएम")
_ASKED = _rx(r"\b(?:asked|told|made|forced|instructed|demanded|wanted)\b", r"\bmaang", r"मांग|माँग")

# a credential handover counts only in a report that also shows how it was obtained or used
_FRAUD_CONTEXT = _rx(
    r"\b(?:call\w*|caller|link|sms|message|text|website|site|page|portal|asked|refund|kyc|"
    r"verif\w*|customer care|debited|withdrawn|deducted|taken|transactions?|transferred|stolen|"
    r"without my (?:consent|knowledge)|fraud\w*)\b",
    r"\b(?:paise|paisa) (?:nikal|kat|chale|gaye)\w*\b|\bnikal gaye\b|\baaya\b",
    r"कॉल|लिंक|मैसेज|निकल|कट गए|ठगी|धोखा",
)
# a QR code counts only with a "to receive money" claim: scanning a QR code to PAY is normal

_COLLECT = _rx(r"\bcollect (?:request|req)\b", r"\bpayment request\b[^.]{0,40}\b(?:approve|accept)",
               r"\b(?:approve|accept)\w*\b[^.]{0,40}\b(?:payment|money) request\b",
               r"कलेक्ट रिक्वेस्ट")  # fmt: skip
_QR = _rx(r"\bqr\b", r"\bq\.r\.", r"क्यूआर")
_TO_RECEIVE = _rx(
    r"\b(?:to|would|will|so as to|in order to)\s+(?:receive|get|credit|collect|accept)\b",
    r"\breceive (?:the )?(?:money|payment|amount|advance|refund|cashback)\b",
    r"\b(?:credit|send) (?:the |me the )?(?:advance|payment|money)\b",
    r"\bpaise? (?:aa jayenge|aayenge|milenge|mil jayenge|bhejne)\b", r"पैसे (?:भेजने|आ जाएंगे|मिलेंगे)",
)
_PIN_TO_RECEIVE = _rx(r"\b(?:pin|upi pin)\b[^.]{0,60}\b(?:to receive|to get (?:the )?(?:money|"
                      r"refund|cashback)|for (?:the )?(?:refund|cashback))\b")  # fmt: skip

_INSTALL = _rx(r"\b(?:install|download)\w*\b", r"\bdownload karwa", r"इंस्टॉल|डाउनलोड")
_APP = _rx(r"\bapp\b", r"\bapk\b", r"\bapplication\b", r"ऐप")
_REMOTE = _rx(r"\b(?:anydesk|any desk|teamviewer|team viewer|quick ?support|rustdesk|airdroid|"
              r"ammyy|screen ?shar\w*|remote (?:app|access|control|desktop))\b",
              r"\b(?:got|gained|had|took) access to my (?:phone|mobile|device|screen)\b",
              r"\.apk\b")  # fmt: skip
_SUPPORT_CONTEXT = _rx(
    r"\b(?:caller|the man|the person|he|she|they|officer|customer care|executive|agent)\b",
    r"\b(?:for|to complete|to do) (?:the )?(?:verification|kyc|update|bill update|refund)\b",
    r"\b(?:support|verification|update) app\b", r"\bbola\b|\bkaha\b|\bkarwaya\b",
    r"करवाया|को कहा|कहा",
)

_FEE = _rx(r"\b(?:fee|fees|charges?|tax|taxes|gst|deposit|commission|clearance|duty)\b",
           r"\bfees?\b|\bshulk\b", r"फीस|शुल्क|टैक्स|जीएसटी")  # fmt: skip
_FEE_REASON = _rx(
    r"\b(?:prize|lottery|lucky draw|jackpot|won|winning|reward|gift)\b",
    r"\b(?:to|before|for) (?:release|unlock|claim|withdraw)\w*\b", r"\bwithdraw\w*\b",
    r"\btake (?:the|my) money out\b",
    r"\b(?:inaam|jeeta|jeete|jeet)\b|\bnikal\w*\b", r"इनाम|जीत|लॉटरी|निकाल",
)

_INVEST = _rx(r"\b(?:trading|stock|share market|shares?|ipo|crypto|bitcoin|forex|invest\w*|"
              r"returns?|mentor|tips)\b", r"शेयर|ट्रेडिंग|निवेश|मुनाफ")  # fmt: skip
_PROFIT_SHOWN = _rx(r"\b(?:profit|profits|doubl\w*|returns?|gains?|munafa)\b", r"मुनाफा|दोगुना")
_NO_WITHDRAWAL = _rx(
    r"\b(?:could not|couldn't|cannot|can't|unable to|never) (?:\w+ ){0,3}(?:withdraw|take (?:the|"
    r"my) money out|get (?:my|the) money (?:back|out))\b",
    r"\bwithdraw\w*\b[^.]{0,60}\b(?:rejected|blocked|refused|fee|tax|charges?|denied|"
    r"not allowed)\b",
    r"\bwithdraw karne\b", r"निकालने पर|पैसे निकाल",
)

_UTILITY = _rx(r"\b(?:electricity|power|bijli|light bill|gas connection|electric)\b", r"बिजली")
_CUT = _rx(r"\b(?:cut|disconnect\w*|kat|kaat|band)\b", r"काट|कट")
_SOON = _rx(r"\b(?:tonight|today|aaj|in \d+ (?:hours?|minutes?)|\d{1,2}[:.]\d{2}\s*(?:pm|am))\b",
            r"आज|रात")  # fmt: skip
_MESSAGE = _rx(r"\b(?:sms|message|text|whatsapp|call (?:this|the) number|officer)\b",
               r"मैसेज|एसएमएस")  # fmt: skip

_CLAIMED = _rx(
    r"\b(?:claiming|claimed|pretending|posing|pretended|said (?:he|she|they) (?:was|were|is|are))"
    r"\b[^.]{0,40}\b(?:army|military|crpf|bsf|police|cbi|ed|customs|income tax|rbi|trai|bank|"
    r"officer|customer care|courier|fedex|dhl|official)\b",
    r"\b(?:khud ko|bola main|bola ki main)\b[^.]{0,30}\b(?:army|police|cbi|bank|officer|fauji)\b",
    r"खुद को[^।]{0,30}(?:आर्मी|पुलिस|सीबीआई|बैंक|अधिकारी|अफसर|फौजी)",
)


@dataclass(frozen=True)
class Cue:
    code: str
    weight: float
    text: str
    start: int
    end: int


TEXT = {
    "CREDENTIALS_HANDED_OVER": "the report says an OTP, PIN, password or card details were "
                               "shared or entered at someone's request",
    "COLLECT_REQUEST_OR_QR_TO_RECEIVE": "a collect request, a QR code or a PIN was used "
                                        "'to receive' money; receiving money never needs them",
    "REMOTE_ACCESS_APP": "an app was installed at a caller's request (remote access or a "
                         "'support' app)",
    "FEE_TO_RELEASE_MONEY": "a fee, tax or charge was demanded before a prize, refund or "
                            "withdrawal could be released",
    "FAKE_PROFIT_NO_WITHDRAWAL": "an investment app or group showed profits but withdrawals "
                                 "were blocked or needed a payment",
    "UTILITY_DISCONNECTION_THREAT": "a message threatened an electricity or gas disconnection "
                                    "soon and pushed a call or update",
    "CLAIMED_OFFICIAL_IDENTITY": "the other person claimed to be an officer, soldier, bank or "
                                 "courier employee (context; not proof on its own)",
}  # fmt: skip
WEIGHTS = {
    "CREDENTIALS_HANDED_OVER": 0.55, "COLLECT_REQUEST_OR_QR_TO_RECEIVE": 0.6,
    "REMOTE_ACCESS_APP": 0.55, "FEE_TO_RELEASE_MONEY": 0.5, "FAKE_PROFIT_NO_WITHDRAWAL": 0.5,
    "UTILITY_DISCONNECTION_THREAT": 0.45, "CLAIMED_OFFICIAL_IDENTITY": 0.2,
}  # fmt: skip


def _sentences(text: str) -> list[tuple[int, int]]:
    out, start = [], 0
    for m in _SENT.finditer(text):
        if m.start() > start:
            out.append((start, m.start()))
        start = m.end()
    if start < len(text):
        out.append((start, len(text)))
    return out


def _cue(code: str, a: int, b: int) -> Cue:
    return Cue(code, WEIGHTS[code], TEXT[code], a, b)


def _sentence_cues(s: str, fraud_context: bool) -> list[str]:
    found: list[str] = []
    if _ATM.search(s):
        return found
    advice = bool(_ADVICE.search(s))
    if _SECRET_RX.search(s) and _HANDED.search(s) and not advice and fraud_context:
        if not _NEGATED.search(s) or _ASKED.search(s):
            found.append("CREDENTIALS_HANDED_OVER")
    if (_COLLECT.search(s) or (_QR.search(s) and _TO_RECEIVE.search(s))
            or _PIN_TO_RECEIVE.search(s)) and not advice:  # fmt: skip
        found.append("COLLECT_REQUEST_OR_QR_TO_RECEIVE")
    if _REMOTE.search(s) or (_INSTALL.search(s) and _APP.search(s) and _SUPPORT_CONTEXT.search(s)
                             and not _INVEST.search(s)):  # fmt: skip
        found.append("REMOTE_ACCESS_APP")
    if _FEE.search(s) and _FEE_REASON.search(s):
        found.append("FEE_TO_RELEASE_MONEY")
    if _UTILITY.search(s) and _CUT.search(s) and _SOON.search(s) and _MESSAGE.search(s):
        found.append("UTILITY_DISCONNECTION_THREAT")
    if _CLAIMED.search(s):
        found.append("CLAIMED_OFFICIAL_IDENTITY")
    return found


def cues(text: str) -> list[Cue]:
    """At most one cue per code (the first sentence that shows it)."""
    out: dict[str, Cue] = {}
    fraud_context = bool(_FRAUD_CONTEXT.search(text))
    for a, b in _sentences(text):
        for code in _sentence_cues(text[a:b], fraud_context):
            out.setdefault(code, _cue(code, a, b))
    if "FAKE_PROFIT_NO_WITHDRAWAL" not in out and _INVEST.search(text) and \
            _PROFIT_SHOWN.search(text):  # fmt: skip
        m = _NO_WITHDRAWAL.search(text)
        if m:
            sent = next(((a, b) for a, b in _sentences(text) if a <= m.start() < b),
                        (m.start(), m.end()))  # fmt: skip
            out["FAKE_PROFIT_NO_WITHDRAWAL"] = _cue("FAKE_PROFIT_NO_WITHDRAWAL", *sent)
    return sorted(out.values(), key=lambda c: c.start)


def score(found: list[Cue]) -> int:
    """round(100 * noisy-OR of the weights); 0 without cues."""
    miss = 1.0
    for c in found:
        miss *= 1.0 - c.weight
    return round(100 * (1.0 - miss))
