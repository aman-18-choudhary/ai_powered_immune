"""Citizen assistant: verdict for a pasted message using the real call-guard scorer.

Thresholds (documented, applied to the call-guard score in [0, 1]): ``likely_scam`` at >= 0.7
(the call-guard alert threshold), ``suspicious`` at >= 0.35, otherwise ``safe``. ``safe`` means
"no clear scam cues found", not "verified safe". The text is scored in memory and not stored or
logged (only its length is). Reason and advice texts come from the static dictionary below; the
scorer's own free-text ``detail`` strings are not used.

Handling that never errors: empty or blank text (label ``safe``, ``uncertain``, advice
``no_input``), text over 4,000 characters (the first 4,000 are scored, ``uncertain``), an
unsupported or missing language (script detection, else English; ``uncertain`` only when a
language was given and is unsupported).
"""

from __future__ import annotations

import asyncio
import unicodedata
from dataclasses import dataclass
from typing import Any

from call_guard.model import Scorer

MAX_CHARS = 4000
LIKELY_SCAM = 0.7
SUSPICIOUS = 0.35
SUPPORTED = ("en", "hi", "ta", "bn")
BORDER = 0.08  # scores this close to a threshold are reported as uncertain
MIN_WORDS = 4

REASON_TEXT: dict[str, dict[str, str]] = {
    "DIGITAL_ARREST_PHRASE": {
        "en": "The sender says you are under 'digital arrest'. No agency arrests people over a call or video call.",
        "hi": "कॉल करने वाला कहता है कि आप 'डिजिटल अरेस्ट' में हैं। कोई भी एजेंसी फोन या वीडियो कॉल पर गिरफ्तारी नहीं करती।",
        "ta": "நீங்கள் 'டிஜிட்டல் கைது'யில் இருப்பதாக அழைப்பவர் கூறுகிறார். தொலைபேசி அல்லது வீடியோ அழைப்பில் எந்த அமைப்பும் கைது செய்யாது.",
        "bn": "কলার বলছে আপনি 'ডিজিটাল অ্যারেস্ট'-এ আছেন। ফোন বা ভিডিও কলে কোনো সংস্থা গ্রেপ্তার করে না।",
    },
    "SAFE_ACCOUNT_TRANSFER": {
        "en": "You are asked to move money to a 'safe', RBI or verification account. Genuine agencies do not ask for this.",
        "hi": "आपसे पैसे 'सुरक्षित', RBI या वेरिफिकेशन खाते में भेजने को कहा जा रहा है। असली एजेंसियां ऐसा नहीं कहतीं।",
        "ta": "'பாதுகாப்பான', RBI அல்லது சரிபார்ப்பு கணக்கிற்கு பணம் அனுப்பச் சொல்கிறார்கள். உண்மையான அமைப்புகள் இவ்வாறு கேட்காது.",
        "bn": "আপনাকে 'নিরাপদ', RBI বা ভেরিফিকেশন অ্যাকাউন্টে টাকা পাঠাতে বলা হচ্ছে। আসল সংস্থা এমন বলে না।",
    },
    "ISOLATION_DEMAND": {
        "en": "You are told to stay on the call, keep it secret or stay in view of the camera.",
        "hi": "आपसे कॉल पर बने रहने, किसी को न बताने या कैमरे के सामने रहने को कहा जा रहा है।",
        "ta": "அழைப்பிலேயே இருக்கவும், யாரிடமும் சொல்லாமல் இருக்கவும் அல்லது கேமரா முன் இருக்கவும் சொல்லப்படுகிறது.",
        "bn": "আপনাকে কলে থাকতে, কাউকে না বলতে বা ক্যামেরার সামনে থাকতে বলা হচ্ছে।",
    },
    "PAYMENT_DEMAND": {
        "en": "Money is demanded in connection with a case or an arrest.",
        "hi": "किसी केस या गिरफ्तारी के नाम पर पैसे मांगे जा रहे हैं।",
        "ta": "வழக்கு அல்லது கைது தொடர்பாக பணம் கேட்கப்படுகிறது.",
        "bn": "মামলা বা গ্রেপ্তারের নামে টাকা চাওয়া হচ্ছে।",
    },
    "AUTHORITY_IMPERSONATION": {
        "en": "The sender claims to be police, CBI, a court or a regulator.",
        "hi": "भेजने वाला खुद को पुलिस, CBI, अदालत या नियामक बता रहा है।",
        "ta": "அனுப்புநர் தன்னை காவல்துறை, CBI, நீதிமன்றம் அல்லது ஒழுங்குமுறை அமைப்பு என்று கூறுகிறார்.",
        "bn": "প্রেরক নিজেকে পুলিশ, CBI, আদালত বা নিয়ন্ত্রক সংস্থা বলে দাবি করছে।",
    },
    "LEGAL_THREAT": {
        "en": "The message threatens a case, a warrant or a seized parcel.",
        "hi": "संदेश में केस, वारंट या जब्त पार्सल की धमकी दी गई है।",
        "ta": "வழக்கு, வாரண்ட் அல்லது பறிமுதல் செய்யப்பட்ட பார்சல் பற்றி மிரட்டல் உள்ளது.",
        "bn": "বার্তায় মামলা, ওয়ারেন্ট বা আটক পার্সেলের হুমকি আছে।",
    },
    "ACCOUNT_VERIFICATION_ASK": {
        "en": "You are asked to 'verify' your account, balance or ID.",
        "hi": "आपसे अपना खाता, बैलेंस या पहचान 'वेरिफाई' करने को कहा जा रहा है।",
        "ta": "உங்கள் கணக்கு, இருப்பு அல்லது அடையாளத்தை 'சரிபார்க்க' கேட்கப்படுகிறது.",
        "bn": "আপনার অ্যাকাউন্ট, ব্যালান্স বা পরিচয় 'যাচাই' করতে বলা হচ্ছে।",
    },
    "CREDENTIAL_ASK": {
        "en": "You are asked for an OTP, PIN or password.",
        "hi": "आपसे OTP, पिन या पासवर्ड मांगा जा रहा है।",
        "ta": "OTP, PIN அல்லது கடவுச்சொல் கேட்கப்படுகிறது.",
        "bn": "OTP, পিন বা পাসওয়ার্ড চাওয়া হচ্ছে।",
    },
    "URGENCY": {
        "en": "The message pressures you to act immediately.",
        "hi": "संदेश आप पर तुरंत कार्रवाई करने का दबाव डालता है।",
        "ta": "உடனடியாக செயல்படுமாறு அழுத்தம் கொடுக்கப்படுகிறது.",
        "bn": "বার্তাটি আপনাকে এখনই পদক্ষেপ নিতে চাপ দিচ্ছে।",
    },
    "SCRIPT_CLASSIFIER_MATCH": {
        "en": "The wording is similar to known digital-arrest scam scripts.",
        "hi": "भाषा जाने-पहचाने डिजिटल अरेस्ट स्कैम स्क्रिप्ट से मिलती-जुलती है।",
        "ta": "வார்த்தைகள் அறியப்பட்ட டிஜிட்டல் கைது மோசடி உரைகளை ஒத்துள்ளன.",
        "bn": "ভাষা পরিচিত ডিজিটাল অ্যারেস্ট স্ক্যাম স্ক্রিপ্টের মতো।",
    },
    "NO_RISK_INDICATORS": {
        "en": "No scam cues were found in this text.",
        "hi": "इस टेक्स्ट में कोई स्कैम संकेत नहीं मिला।",
        "ta": "இந்த உரையில் மோசடி அறிகுறிகள் எதுவும் இல்லை.",
        "bn": "এই লেখায় কোনো স্ক্যামের ইঙ্গিত পাওয়া যায়নি।",
    },
}
GENERIC_REASON = {
    "en": "The text contains wording that this checker associates with scams.",
    "hi": "टेक्स्ट में ऐसी भाषा है जिसे यह जांच स्कैम से जोड़ती है।",
    "ta": "இந்த உரையில் மோசடியுடன் தொடர்புடைய சொற்கள் உள்ளன.",
    "bn": "লেখাটিতে এমন ভাষা আছে যা এই যাচাই স্ক্যামের সঙ্গে যুক্ত করে।",
}

ADVICE: dict[str, dict[str, list[str]]] = {
    "likely_scam": {
        "en": [
            "Do not send money or share an OTP, PIN or card details.",
            "End the call. Genuine agencies do not arrest people or take payments over a phone or video call.",
            "Call 1930 (National Cyber Crime Helpline) or report at cybercrime.gov.in.",
            "Tell a family member or a friend what happened.",
        ],
        "hi": [
            "पैसे न भेजें और OTP, पिन या कार्ड की जानकारी साझा न करें।",
            "कॉल काट दें। असली एजेंसियां फोन या वीडियो कॉल पर गिरफ्तारी या भुगतान नहीं करतीं।",
            "1930 (राष्ट्रीय साइबर अपराध हेल्पलाइन) पर कॉल करें या cybercrime.gov.in पर रिपोर्ट करें।",
            "परिवार के किसी सदस्य या दोस्त को बताएं।",
        ],
        "ta": [
            "பணம் அனுப்ப வேண்டாம்; OTP, PIN அல்லது கார்டு விவரங்களைப் பகிர வேண்டாம்.",
            "அழைப்பைத் துண்டிக்கவும். உண்மையான அமைப்புகள் தொலைபேசி அல்லது வீடியோ அழைப்பில் கைது செய்யவோ பணம் வாங்கவோ மாட்டார்கள்.",
            "1930 (தேசிய சைபர் குற்ற உதவி எண்) அழைக்கவும் அல்லது cybercrime.gov.in இல் புகார் செய்யவும்.",
            "குடும்ப உறுப்பினர் அல்லது நண்பரிடம் தெரிவிக்கவும்.",
        ],
        "bn": [
            "টাকা পাঠাবেন না এবং OTP, পিন বা কার্ডের তথ্য শেয়ার করবেন না।",
            "কল কেটে দিন। আসল সংস্থা ফোন বা ভিডিও কলে গ্রেপ্তার করে না বা টাকা নেয় না।",
            "1930 (জাতীয় সাইবার ক্রাইম হেল্পলাইন) নম্বরে কল করুন বা cybercrime.gov.in-এ রিপোর্ট করুন।",
            "পরিবারের কাউকে বা বন্ধুকে জানান।",
        ],
    },
    "suspicious": {
        "en": [
            "Pause. Do not act on the message yet.",
            "Verify using a number you look up yourself, not one given in the message.",
            "Do not share an OTP, PIN or password.",
            "If money was already sent, call 1930 immediately.",
        ],
        "hi": [
            "रुकें। अभी संदेश पर कार्रवाई न करें।",
            "संदेश में दिए नंबर पर नहीं, खुद खोजे गए नंबर से जांच करें।",
            "OTP, पिन या पासवर्ड साझा न करें।",
            "अगर पैसे भेज चुके हैं तो तुरंत 1930 पर कॉल करें।",
        ],
        "ta": [
            "நிறுத்துங்கள். இந்தச் செய்தியின்படி இன்னும் செயல்பட வேண்டாம்.",
            "செய்தியில் உள்ள எண்ணை அல்ல, நீங்களே தேடிய எண்ணில் சரிபார்க்கவும்.",
            "OTP, PIN அல்லது கடவுச்சொற்களைப் பகிர வேண்டாம்.",
            "ஏற்கனவே பணம் அனுப்பியிருந்தால் உடனே 1930 அழைக்கவும்.",
        ],
        "bn": [
            "থামুন। বার্তাটি অনুযায়ী এখনই কিছু করবেন না।",
            "বার্তায় দেওয়া নম্বরে নয়, নিজে খুঁজে পাওয়া নম্বরে যাচাই করুন।",
            "OTP, পিন বা পাসওয়ার্ড শেয়ার করবেন না।",
            "টাকা পাঠিয়ে ফেললে এখনই 1930-এ কল করুন।",
        ],
    },
    "safe": {
        "en": [
            "No clear scam cues were found, but no check is perfect.",
            "Do not share an OTP, PIN or password with anyone.",
            "If something feels wrong, call 1930 or your bank's official number.",
        ],
        "hi": [
            "कोई स्पष्ट स्कैम संकेत नहीं मिला, लेकिन कोई भी जांच पूरी तरह सटीक नहीं होती।",
            "OTP, पिन या पासवर्ड किसी को न बताएं।",
            "कुछ गलत लगे तो 1930 या अपने बैंक के आधिकारिक नंबर पर कॉल करें।",
        ],
        "ta": [
            "தெளிவான மோசடி அறிகுறிகள் இல்லை, ஆனால் எந்தச் சோதனையும் முழுமையானது அல்ல.",
            "OTP, PIN அல்லது கடவுச்சொற்களை யாரிடமும் பகிர வேண்டாம்.",
            "ஏதேனும் சந்தேகம் இருந்தால் 1930 அல்லது உங்கள் வங்கியின் அதிகாரப்பூர்வ எண்ணை அழைக்கவும்.",
        ],
        "bn": [
            "স্পষ্ট স্ক্যামের ইঙ্গিত নেই, তবে কোনো যাচাই নিখুঁত নয়।",
            "OTP, পিন বা পাসওয়ার্ড কাউকে বলবেন না।",
            "কিছু ঠিক না লাগলে 1930 বা আপনার ব্যাংকের অফিসিয়াল নম্বরে কল করুন।",
        ],
    },
    "no_input": {
        "en": [
            "Paste the message, or a short summary of the call, to check it.",
            "Do not share an OTP, PIN or password with anyone.",
        ],
        "hi": [
            "जांच के लिए संदेश या कॉल का छोटा सार चिपकाएं।",
            "OTP, पिन या पासवर्ड किसी को न बताएं।",
        ],
        "ta": [
            "சரிபார்க்க செய்தி அல்லது அழைப்பின் சுருக்கத்தை ஒட்டவும்.",
            "OTP, PIN அல்லது கடவுச்சொற்களை யாரிடமும் பகிர வேண்டாம்.",
        ],
        "bn": [
            "যাচাই করতে বার্তা বা কলের সংক্ষিপ্ত বিবরণ পেস্ট করুন।",
            "OTP, পিন বা পাসওয়ার্ড কাউকে বলবেন না।",
        ],
    },
}
TOO_LONG_NOTICE = {
    "en": "Only the first 4,000 characters were checked.",
    "hi": "केवल पहले 4,000 अक्षरों की जांच की गई।",
    "ta": "முதல் 4,000 எழுத்துகள் மட்டுமே சரிபார்க்கப்பட்டன.",
    "bn": "শুধু প্রথম ৪,০০০ অক্ষর যাচাই করা হয়েছে।",
}

_SCRIPTS = (
    ("hi", 0x0900, 0x097F),
    ("bn", 0x0980, 0x09FF),
    ("ta", 0x0B80, 0x0BFF),
)


def detect_lang(text: str) -> str:
    counts = dict.fromkeys(("hi", "bn", "ta"), 0)
    for ch in text:
        o = ord(ch)
        for code, lo, hi in _SCRIPTS:
            if lo <= o <= hi:
                counts[code] += 1
    best = max(counts, key=lambda k: counts[k])
    return best if counts[best] >= 3 else "en"


def label_for(score: float) -> str:
    if score >= LIKELY_SCAM:
        return "likely_scam"
    if score >= SUSPICIOUS:
        return "suspicious"
    return "safe"


@dataclass(frozen=True)
class Assessment:
    label: str
    score: float
    reasons: list[dict[str, Any]]
    advice: dict[str, Any]
    lang: str
    uncertain: bool
    model_version: str

    def to_json(self) -> dict[str, Any]:
        return {
            "label": self.label, "score": self.score, "reasons": self.reasons,
            "advice": self.advice, "lang": self.lang, "uncertain": self.uncertain,
            "model_version": self.model_version,
            "thresholds": {"suspicious": SUSPICIOUS, "likely_scam": LIKELY_SCAM},
        }  # fmt: skip


def _advice(key: str, lang: str, truncated: bool = False) -> dict[str, Any]:
    steps = list(ADVICE[key][lang])
    if truncated:
        steps.insert(0, TOO_LONG_NOTICE[lang])
    return {"key": key, "steps": steps}


async def assess(scorer: Scorer, message: str, lang: str | None = None) -> Assessment:
    """Never raises for any string input."""
    unsupported = lang is not None and lang.strip().lower() not in SUPPORTED
    chosen = (lang or "").strip().lower()
    if chosen not in SUPPORTED:
        chosen = detect_lang(message)
    version = scorer.model_version
    text = message.strip()
    if not any(not ch.isspace() and unicodedata.category(ch) != "Cf" for ch in text):
        return Assessment("safe", 0.0, [], _advice("no_input", chosen), chosen, True, version)
    truncated = len(text) > MAX_CHARS
    if truncated:
        text = text[:MAX_CHARS]
    score, reasons, version = await asyncio.to_thread(scorer.score_text, text)
    label = label_for(score)
    shown = []
    for r in reasons:
        if r.weight <= 0 and r.code != "NO_RISK_INDICATORS":
            continue
        row = REASON_TEXT.get(r.code)
        shown.append(
            {
                "code": r.code,
                "text": (row or GENERIC_REASON)[chosen],
                "weight": round(float(r.weight), 3),
            }
        )
    if label != "safe":
        shown = [x for x in shown if x["code"] != "NO_RISK_INDICATORS"]
    near = any(abs(score - th) < BORDER for th in (SUSPICIOUS, LIKELY_SCAM))
    uncertain = (
        truncated or unsupported or near or len(text.split()) < MIN_WORDS or scorer.fallback_mode
    )
    return Assessment(
        label, round(score, 4), shown, _advice(label, chosen, truncated), chosen, uncertain, version
    )
