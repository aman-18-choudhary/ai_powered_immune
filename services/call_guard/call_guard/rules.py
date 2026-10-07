"""Rule layer (``rules-v1``): multilingual cue detection for digital-arrest scam calls.

Handles English, romanised Hindi and Devanagari Hindi. Text is split into sentences; a sentence
that is *about* scams (awareness / "we will never ask for your OTP" / "ignore calls about safe
accounts") is skipped entirely, which is what keeps benign bank-care calls near zero.

Reason codes and weights (each code counts once per chunk, combined by noisy-OR so the
result is a probability-like score in [0, 1]):

====================== ====== =====================================================
code                   weight cue
====================== ====== =====================================================
DIGITAL_ARREST_PHRASE  0.85   "digital arrest" / "डिजिटल अरेस्ट" (never legitimate)
SAFE_ACCOUNT_TRANSFER  0.85   "RBI safe account", or moving funds for verification/audit
ISOLATION_DEMAND       0.50   do not disconnect / stay on video call / tell no one
CREDENTIAL_ASK         0.50   share / tell / send your OTP, PIN, CVV or password
ACCOUNT_VERIFICATION_ASK 0.45 "verify your account", "tell me your balance", show banking app
AUTHORITY_IMPERSONATION 0.40  first-person claim of being CBI/ED/customs/police/TRAI/NCB...
LEGAL_THREAT           0.35   FIR, PMLA/NDPS, warrant, arrest, money-laundering allegation
URGENCY                0.25   "only thirty minutes", "immediately", "turant", "तुरंत"
====================== ====== =====================================================

Bare mentions of "RBI" or "police" never trigger AUTHORITY_IMPERSONATION: it needs both an
authority name and a first-person / official-call framing in the same sentence.
"""

import re
import unicodedata
from functools import reduce

from scam_contracts.models import CallEvent, Reason

RULES_VERSION = "rules-v1"
CALL_RISK_THRESHOLD = 0.7

W_DIGITAL_ARREST = 0.85
W_SAFE_ACCOUNT = 0.85
W_ISOLATION = 0.50
W_CREDENTIAL = 0.50
W_VERIFY = 0.45
W_AUTHORITY = 0.40
W_LEGAL = 0.35
W_URGENCY = 0.25

_SENT_SPLIT = re.compile(r"(?<=[.?!।])\s+|\n+")


def _rx(*alts: str) -> re.Pattern[str]:
    return re.compile("|".join(alts), re.IGNORECASE)


# Sentences about scams/awareness: skipped for every cue.
_AWARENESS = _rx(
    r"\bnever\b",
    r"\bignore\b",
    r"\bbeware\b",
    r"\bbe careful\b",
    r"\bscams?\b",
    r"\bfraud",
    r"\bfake\b",
    r"\bawareness\b",
    r"\breport (?:such|it|them|these)\b",
    r"\bhang up\b",
    r"\bkabhi\b",
    r"\bpolice station\b",
    r"\bmaine\b.{0,40}\bcomplaint\b",
    r"\bsavdhan\b",
    r"\bdhokha\b",
    r"\breport kij",
    r"\bno (?:agency|bank|officer|official|one)\b",
    r"\bkoi bhi agency\b",
    r"\bpolice station\b",
    r"\bfiled a complaint\b",
    r"\blost (?:my )?phone\b",
    r"पुलिस स्टेशन",
    r"थाने",
    r"कोई भी एजेंसी",
    r"कभी",
    r"अनदेखा",
    r"सावधान",
    r"धोखा",
    r"स्कैम",
    r"फ्रॉड",
    r"रिपोर्ट",
    r"कॉल काट(?:कर|िए) ",
)

_AUTH_NAME = _rx(
    r"\bcbi\b",
    r"central bureau of investigation",
    r"enforcement directorate",
    r"\bcustoms?\b",
    r"cyber ?crime",
    r"crime branch",
    r"\bpolice\b",
    r"\binspector\b",
    r"\btrai\b",
    r"narcotics",
    r"\bncb\b",
    r"income tax (?:department|officer)",
    r"supreme court",
    r"सीबीआई",
    r"प्रवर्तन निदेशालय",
    r"कस्टम(?!र)",
    r"साइबर क्राइम",
    r"पुलिस",
    r"इंस्पेक्टर",
    r"ट्राई",
    r"नारकोटिक्स",
    r"सुप्रीम कोर्ट",
)
_AUTH_FRAME = _rx(
    r"\bthis is\b",
    r"\bi am\b",
    r"\bi'm\b",
    r"\bmy name is\b",
    r"\bcalling (?:you )?from\b",
    r"\bcall (?:is )?from\b",
    r"\bspeaking from\b",
    r"\bofficial call\b",
    r"\bbadge\b",
    r"\bmain\b.*\bbol\b",
    r"\bbol (?:raha|rahi)\b",
    r"\bmera naam\b",
    r"\bse bol\b",
    r"\bki taraf se\b",
    r"\bke taraf se\b",
    r"\bofficial\b",
    r"मैं(?!ने)",
    r"मेरा नाम",
    r"बोल (?:रहा|रही)",
    r"से बोल",
    r"की तरफ",
    r"बैज",
    r"ऑफिशियल",
)
_LEGAL = _rx(
    r"\bfir\b",
    r"\bpmla\b",
    r"\bndps\b",
    r"\bwarrant\b",
    r"\barrest",
    r"money laundering",
    r"obstruction of justice",
    r"criminal (?:offen[cs]e|case)",
    r"\bcase (?:is )?(?:number|registered)\b",
    r"\bcase (?:no|darj)\b",
    r"\bdarj\b",
    r"\bgiraft(?:a|ea)ar",
    r"\bgambhir case\b",
    r"एफआईआर",
    r"\bFIR\b",
    r"वारंट",
    r"गिरफ्त",
    r"मामला दर्ज",
    r"अपराध",
    r"न्याय में बाधा",
)
_DIGITAL_ARREST = _rx(
    r"digital(?:ly)? arrest",
    r"virtual arrest",
    r"digital giraft",
    r"डिजिटल\s*अरेस्ट",
    r"डिजिटल\s*गिरफ्त",
)
_ISOLATION = _rx(
    r"\b(?:do not|don't|dont|mat|never)\b.{0,25}\b(?:disconnect|cut|hang up|tell|speak|talk|leave|switch off|step out|inform)\b",
    r"\bstay on (?:the |this )?(?:video )?call\b",
    r"\bremain on (?:the |this )?(?:video )?call\b",
    r"\bkeep (?:the |this )?call connected\b",
    r"\bkeep (?:this )?(?:a )?secret\b",
    r"\bkeep your camera on\b",
    r"\bstrictly confidential\b",
    r"\bconfidential\b",
    r"\bkisi (?:ko|se) (?:mat|nahi)\b",
    r"\bcall (?:mat )?disconnect\b",
    r"\bmat kaat",
    r"\bvideo call par bane\b",
    r"\bcamera (?:on|band)\b",
    r"\bsecret rakh",
    r"किसी (?:को|से) (?:मत|न|नहीं)",
    r"डिस्कनेक्ट",
    r"कॉल मत काट",
    r"वीडियो कॉल पर बने",
    r"कैमरा (?:ऑन|बंद)",
    r"सीक्रेट",
    r"गोपनीय",
    r"कनेक्टेड रख",
    r"मत बताना",
)
_SAFE_ACCOUNT = _rx(
    r"\b(?:rbi |secure |safe )?safe account\b", r"\bsecure account\b", r"सेफ\s*अकाउंट", r"सुरक्षित खाते"
)
_MOVE_FUNDS = _rx(
    r"\btransfer\b.{0,30}\b(?:funds?|money|amount|balance|savings)\b",
    r"\b(?:funds?|money|amount|balance|savings)\b.{0,30}\btransfer\b",
    r"\b(?:paise|paisa|amount|rakam|balance)\b.{0,40}\btransfer\b",
    r"\btransfer\b.{0,30}\b(?:paise|paisa|rakam)\b",
    r"(?:पैसे|रकम|राशि|बैलेंस).{0,40}ट्रांसफर",
    r"ट्रांसफर.{0,30}(?:पैसे|रकम|राशि)",
)
_VERIF_CONTEXT = _rx(
    r"\brbi\b",
    r"\bverif",
    r"\baudit\b",
    r"\brefund",
    r"\breturned\b",
    r"\bwapas\b",
    r"\binvestigation\b",
    r"आरबीआई",
    r"वेरिफ",
    r"ऑडिट",
    r"रिफंड",
    r"वापस",
    r"RBI",
)
_VERIFY_ASK = _rx(
    r"\bverify\b.{0,40}\b(?:account|savings|balance|aadhaar|pan|bank)\b",
    r"\b(?:account|savings|balance)\b.{0,30}\bverif(?:y|ication)\b",
    r"\bverif(?:y|ication)\b.{0,40}\b(?:account|savings|balance)\b",
    r"\btell me your\b.{0,40}\b(?:balance|account|upi|bank)\b",
    r"\bshare your\b.{0,30}\b(?:aadhaar|pan|account|bank)\b",
    r"\bshow me the balance\b",
    r"\bopen your banking app\b",
    r"\bbalance (?:batayiye|batao|dikhaiye|dikhao)\b",
    r"\bbanking app kholkar\b",
    r"\baccount (?:aur savings )?verify\b",
    r"\bbank ka naam\b.{0,30}\bbalance\b",
    r"\baadhaar pan\b.{0,20}\bshare\b",
    r"आधार\s*पैन",
    r"(?:अकाउंट|खाता).{0,30}वेरिफ",
    r"बैलेंस (?:बताइए|दिखाइए|बताओ)",
    r"बैंकिंग ऐप खोल",
)
_CRED_ASK = _rx(
    r"\b(?:share|tell|send|give|read out|batao|bataiye|bhejiye|bata do)\b.{0,25}\b(?:otp|pin|cvv|password)\b",
    r"\b(?:otp|pin|cvv|password)\b.{0,25}\b(?:share|tell|send|bataiye|batao|bhejiye)\b",
    r"(?:बताइए|बताओ|भेजिए|शेयर).{0,20}(?:OTP|PIN|CVV|पासवर्ड)",
    r"(?:OTP|PIN|CVV).{0,20}(?:बताइए|बताओ|भेजिए|शेयर)",
)
_URGENCY = _rx(
    r"\bonly (?:\w+ )?(?:minutes?|hours?)\b",
    r"\b(?:\w+) minutes\b.{0,40}\b(?:arrest|team|warrant)\b",
    r"\bimmediately\b",
    r"\bright now\b",
    r"\bat once\b",
    r"\burgent(?:ly)?\b",
    r"\bturant\b",
    r"\btatkal\b",
    r"\babhi (?:cooperate|se)\b",
    r"\bsirf \w+ minute\b",
    r"तुरंत",
    r"तत्काल",
    r"ज़रूरी",
    r"जरूरी",
    r"सिर्फ \S+ मिनट",
    r"अभी सहयोग",
)

_SPECS: tuple[tuple[str, float, str], ...] = (
    ("DIGITAL_ARREST_PHRASE", W_DIGITAL_ARREST, "caller announces a 'digital arrest'"),
    (
        "SAFE_ACCOUNT_TRANSFER",
        W_SAFE_ACCOUNT,
        "caller demands moving funds to a 'safe'/verification account",
    ),
    ("ISOLATION_DEMAND", W_ISOLATION, "caller demands the victim stay on the call or tell no one"),
    ("CREDENTIAL_ASK", W_CREDENTIAL, "caller asks for an OTP, PIN or password"),
    (
        "ACCOUNT_VERIFICATION_ASK",
        W_VERIFY,
        "caller asks to 'verify' the victim's account or balance",
    ),
    (
        "AUTHORITY_IMPERSONATION",
        W_AUTHORITY,
        "caller claims to be a law-enforcement or regulatory body",
    ),
    ("LEGAL_THREAT", W_LEGAL, "caller alleges a criminal case, FIR, warrant or arrest"),
    ("URGENCY", W_URGENCY, "caller applies time pressure"),
)


def _normalise(text: str) -> str:
    return unicodedata.normalize("NFC", text).replace("‍", "").replace("‌", "")


def _sentence_cues(s: str) -> set[str]:
    cues: set[str] = set()
    if _DIGITAL_ARREST.search(s):
        cues.add("DIGITAL_ARREST_PHRASE")
    if _SAFE_ACCOUNT.search(s) or (_MOVE_FUNDS.search(s) and _VERIF_CONTEXT.search(s)):
        cues.add("SAFE_ACCOUNT_TRANSFER")
    if _ISOLATION.search(s):
        cues.add("ISOLATION_DEMAND")
    if _CRED_ASK.search(s):
        cues.add("CREDENTIAL_ASK")
    if _VERIFY_ASK.search(s):
        cues.add("ACCOUNT_VERIFICATION_ASK")
    if _AUTH_NAME.search(s) and _AUTH_FRAME.search(s):
        cues.add("AUTHORITY_IMPERSONATION")
    if _LEGAL.search(s):
        cues.add("LEGAL_THREAT")
    if _URGENCY.search(s):
        cues.add("URGENCY")
    return cues


def noisy_or(weights: list[float]) -> float:
    return 1.0 - reduce(lambda acc, w: acc * (1.0 - w), weights, 1.0)


def score_text(text: str) -> tuple[float, list[Reason]]:
    """Rules-only score and reasons (no reasons for text with no cues)."""
    found: set[str] = set()
    for sent in _SENT_SPLIT.split(_normalise(text)):
        if not sent.strip() or _AWARENESS.search(sent):
            continue
        found |= _sentence_cues(sent)
    reasons = [Reason(code=c, weight=w, detail=d) for c, w, d in _SPECS if c in found]
    return round(min(1.0, noisy_or([r.weight for r in reasons])), 4), reasons


def score_chunk(event: CallEvent) -> tuple[float, list[Reason]]:
    """Rules-only score for one transcript chunk (``rules-v1``)."""
    return score_text(event.transcript_chunk)
