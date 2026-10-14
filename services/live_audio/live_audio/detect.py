"""Transcript analysis: the EXISTING call-guard scorer plus a few transparent extra rules.

Per chunk (the new transcript segments of one uploaded audio chunk):

1. ``call_guard.model.Scorer.score_text`` scores the chunk text (rules-v1 blended with the pinned
   clf-v1 classifier when it loaded; the same object the demo server uses). If the scorer is
   missing or raises, ``ScorerUnavailable`` is raised: the chunk's analysis is ``unavailable``
   and the risk is NOT updated (never reported as low).
2. Segment attribution: call-guard's own ``rules.analyse`` is run on each segment; a code found on
   a segment links that segment to the indicator. A chunk-level code that no single segment
   explains (a cue spread over two segments, or the classifier) is linked to every segment of
   the chunk.
3. Extra rules (``live_rules``, below) cover what call-guard does not: OTP/PIN/card questions
   ("what is the OTP"), plain transfer instructions without an arrest context, UPI/KYC/blocking
   fraud, remote-access apps, secrecy toward family, arrest threats, and spoken links / UPI ids
   ("dot com", "slash", "at the rate"). They run on call-guard's advisory-filtered clauses, so
   "never share your OTP" does not fire. An extra rule that overlaps a call-guard code which
   already fired on the same segment is not added again.
4. Chunk score = noisy-OR(call-guard chunk score, weights of the extra rules that fired);
   accumulated over the session with ``call_guard.session.accumulate`` (noisy-OR, half-life
   300 s of audio time, hard/soft split) by ``RiskTracker``.

Risk score = round(100 * accumulated score). It is an UNCALIBRATED heuristic
(``score_semantics: uncalibrated_heuristic``), not a probability. Levels: LOW < 25 <= MEDIUM
< 50 <= HIGH < 70 <= CRITICAL (70 = call-guard's alert threshold 0.7). Indicators are things
that were detected in speech; they are not a confirmation of fraud.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import reduce
from typing import Any

from call_guard import rules as cg
from call_guard.session import SessionState, accumulate
from scam_contracts.models import Reason

LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
STATEMENT = "Indicators detected - not a confirmation of fraud."
SCORE_SEMANTICS = "uncalibrated_heuristic"
EXCERPT_MAX = 80


def level_for(score: int) -> str:
    if score >= 70:
        return "CRITICAL"
    if score >= 50:
        return "HIGH"
    if score >= 25:
        return "MEDIUM"
    return "LOW"


class ScorerUnavailable(Exception):
    pass


# --------------------------------------------------------------------------- labels
LABELS: dict[str, str] = {
    # call-guard (rules-v1 / clf-v1)
    "DIGITAL_ARREST_PHRASE": "Says you are under 'digital arrest'",
    "SAFE_ACCOUNT_TRANSFER": "Asks to move money to a 'safe' / RBI / verification account",
    "ISOLATION_DEMAND": "Demands you stay on the call, stay silent or tell no one",
    "PAYMENT_DEMAND": "Demands money in connection with an arrest or case",
    "AUTHORITY_IMPERSONATION": "Claims to be police, CBI, customs or another authority",
    "LEGAL_THREAT": "Alleges a case, warrant, seized parcel or similar",
    "ACCOUNT_VERIFICATION_ASK": "Asks to 'verify' your account, balance or ID",
    "CREDENTIAL_ASK": "Asks for an OTP, PIN or password",
    "URGENCY": "Time pressure",
    "SCRIPT_CLASSIFIER_MATCH": "Wording matches known scam-call scripts (ML classifier)",
    # live_rules
    "OTP_PIN_REQUEST": "Asks for an OTP, PIN, CVV, password or card number",
    "MONEY_TRANSFER_INSTRUCTION": "Instructs you to send or transfer money",
    "UPI_BANK_FRAUD": "UPI / bank-account fraud pattern (KYC, blocking, PIN to receive)",
    "REMOTE_ACCESS_REQUEST": "Asks you to install a screen-sharing / remote-access app",
    "SECRECY_DEMAND": "Asks you to keep it secret from family or the bank",
    "INTIMIDATION": "Threatens arrest, police action or jail",
    "SPOKEN_LINK": "A web link was read out",
    "SPOKEN_PAYMENT_ID": "A UPI ID / payment address was read out",
}

# ------------------------------------------------------------------- extra rules
_ASK = r"(?:what is|what's|whats|tell|share|send|give|read|say|provide|enter|type|bata\w*|batay\s?ye|bhej\w*|bol\w*|dijiye|de do)"
_SECRET_ITEM = r"(?:otp|o\.?t\.?p|one time password|pin|cvv|password|passcode|verification code|card number|card details|expiry|ओटीपी|पिन|पासवर्ड|सीवीवी)"
_X: dict[str, list[re.Pattern[str]]] = {
    "OTP_PIN_REQUEST": [
        cg._rx(rf"\b{_ASK}\b.{{0,30}}{_SECRET_ITEM}"),
        cg._rx(
            rf"{_SECRET_ITEM}.{{0,30}}\b(?:bata\w*|batay\s?ye|bhej\w*|bol\w*|tell me|read (?:it )?out|share (?:it|karo|kijiye)|dijiye|de do)\b"
        ),
        cg._rx(
            r"\b(?:otp|code)\b.{0,20}\b(?:you (?:just )?(?:received|got)|aaya|aya|aayega|came)\b"
        ),
        cg._rx(r"(?:ओटीपी|OTP|पिन|पासवर्ड).{0,20}(?:बताइए|बताइये|बताओ|भेजिए|भेजो|बोलिए)"),
    ],
    "MONEY_TRANSFER_INSTRUCTION": [
        cg._rx(
            r"(?:^|[,.;:]\s*|\bplease\s+|\bjust\s+|\bnow\s+|\bimmediately\s+)(?:transfer|send|pay|deposit|move)\b.{0,40}\b(?:money|rupees|rs\.?|inr|amount|lakh|lakhs|thousand|funds|payment|upi|account|qr)\b"
        ),
        cg._rx(
            r"\byou (?:must|have to|need to|should|will have to)\s+(?:transfer|send|pay|deposit|move)\b"
        ),
        cg._rx(
            r"\b(?:paise|paisa|rupaye|rupees|amount|payment|funds)\b.{0,40}\b(?:transfer|bhej|jama|daal|dal)\w*\s*(?:kar\s?(?:do|dijiye|dijie|de|ke)|kijiye|kariye|karo|dijiye|do|dijie)\b"
        ),
        cg._rx(
            r"\b(?:transfer|bhej|jama)\w*\s*(?:kar\s?(?:do|dijiye|dijie|de)|kijiye|kariye|karo|dijiye)\b"
        ),
        cg._rx(
            r"(?:पैसे|रुपये|रकम|राशि|पेमेंट).{0,30}(?:ट्रांसफर|भेज|जमा|डाल)\S*\s*(?:कीजिए|कीजिये|करो|करें|कर दो|कर दीजिए|दीजिए|दो)"
        ),
        cg._rx(r"(?:ट्रांसफर|भेजिए|जमा)\s*(?:कीजिए|कीजिये|करो|करें|कर दो|कर दीजिए)"),
    ],
    "UPI_BANK_FRAUD": [
        cg._rx(
            r"\bkyc\b.{0,30}\b(?:expire\w*|pending|update|band|block\w*|suspend\w*|incomplete)\b"
        ),
        cg._rx(
            r"\b(?:account|card|sim|upi)\b.{0,30}\b(?:will be|has been|is being|is|get|gets)\s+(?:blocked|frozen|suspended|deactivated|closed)\b"
        ),
        cg._rx(
            r"\b(?:account|khata|card|sim)\b.{0,20}\b(?:band|block|freeze)\s*(?:ho\s*ja\w*|kar\s*di\w*|hone\s*wala)"
        ),
        cg._rx(
            r"\b(?:enter|type|daal\w*|dal\w*)\b.{0,20}\b(?:upi\s*)?pin\b.{0,30}\b(?:receive|get|refund|cashback|prize|milega|paane)\b"
        ),
        cg._rx(r"\bscan\b.{0,25}\bqr\b.{0,25}\b(?:receive|get|refund|cashback|prize)\b"),
        cg._rx(r"\b(?:approve|accept)\b.{0,20}\bcollect request\b"),
        cg._rx(r"(?:केवाईसी|KYC).{0,25}(?:अपडेट|एक्सपायर|बंद|ब्लॉक)"),
        cg._rx(r"(?:खाता|अकाउंट).{0,20}(?:बंद|ब्लॉक|फ्रीज)\s*(?:हो जाएगा|हो जायेगा|कर दिया)"),
    ],
    "REMOTE_ACCESS_REQUEST": [
        cg._rx(r"\b(?:any\s?desk|team\s?viewer|quick\s?support|rust\s?desk|airdroid|ammyy)\b"),
        cg._rx(r"\b(?:share|open|start|on kar\w*)\b.{0,15}\bscreen\b|\bscreen\s?shar\w*"),
        cg._rx(r"\bremote (?:access|control|support) (?:app|application|software)\b"),
        cg._rx(r"(?:एनीडेस्क|ऐनीडेस्क|स्क्रीन शेयर)"),
    ],
    "SECRECY_DEMAND": [
        cg._rx(r"\b(?:keep|kept)\b.{0,20}\b(?:secret|confidential|between us)\b"),
        cg._rx(
            r"\bnot even (?:to )?your (?:family|wife|husband|children|son|daughter|parents|bank)\b"
        ),
        cg._rx(
            r"\b(?:do not|don't|dont|never) (?:tell|inform|discuss with|call) (?:your )?(?:family|wife|husband|children|son|daughter|parents|bank|friends?|relatives)\b"
        ),
        cg._rx(
            r"\b(?:ghar\s?wal\w*|family|parivar|bank)\b.{0,15}\b(?:ko\s+)?(?:mat|na|nahi)\s+(?:bata\w*|bol\w*)"
        ),
        cg._rx(r"\b(?:gupt|secret)\s+(?:rakh\w*)\b"),
        cg._rx(r"(?:घर\s?वालों|परिवार|बैंक)\s*को\s*(?:मत|न|ना)\s*(?:बताइए|बताइये|बताना|बताओ|बोलना)"),
        cg._rx(r"(?:गुप्त|सीक्रेट)\s*(?:रखिए|रखना|रखें)"),
    ],
    "INTIMIDATION": [
        cg._rx(
            r"\b(?:police|cbi|officers?|we) (?:will|are going to|is going to) (?:come|arrest|pick you up|take you)\b"
        ),
        cg._rx(r"\byou will (?:be arrested|go to jail|be jailed|be taken)\b"),
        cg._rx(r"\b(?:last|final) warning\b"),
        cg._rx(r"\bgiraftar\w*\b|\bgirftar\w*\b|\bjail (?:bhej|ja)\w*"),
        cg._rx(r"गिरफ्तार|गिरफ़्तार|जेल\s*(?:भेज|जा)"),
    ],
}
# extra rule -> call-guard code that already covers the same concept on a segment
OVERLAPS = {
    "OTP_PIN_REQUEST": "CREDENTIAL_ASK",
    "SECRECY_DEMAND": "ISOLATION_DEMAND",
    "INTIMIDATION": "LEGAL_THREAT",
    "MONEY_TRANSFER_INSTRUCTION": "SAFE_ACCOUNT_TRANSFER",
}
EXTRA_WEIGHTS = {
    "OTP_PIN_REQUEST": 0.45,
    "MONEY_TRANSFER_INSTRUCTION": 0.40,
    "UPI_BANK_FRAUD": 0.40,
    "REMOTE_ACCESS_REQUEST": 0.45,
    "SECRECY_DEMAND": 0.40,
    "INTIMIDATION": 0.30,
    "SPOKEN_LINK": 0.30,
    "SPOKEN_PAYMENT_ID": 0.35,
}

# ------------------------------------------------------------- spoken links / UPI ids
_SPOKEN = [
    (re.compile(r"\s*\b(?:at the rate(?: of)?|at rate|एट द रेट|एट दा रेट)\b\s*", re.I), "@"),
    (re.compile(r"\s*\b(?:dot|डॉट|डोट)\b\s*", re.I), "."),
    (re.compile(r"\s*\b(?:slash|स्लैश)\b\s*", re.I), "/"),
    (re.compile(r"\s*\b(?:dash|hyphen|minus)\b\s*", re.I), "-"),
    (re.compile(r"\s*\bunderscore\b\s*", re.I), "_"),
    (re.compile(r"\b(?:कॉम|काम)\b"), "com"),
]
_TLD = r"(?:com|in|net|org|co|xyz|top|info|online|site|link|app|io|me|ly|live|club|shop|gov|bank)"
_LINK = re.compile(
    rf"\b(?:https?://)?(?:www\.)?[a-z0-9-]{{2,}}(?:\.[a-z0-9-]+)*\.{_TLD}\b(?:/[\w./-]*)?", re.I
)
_UPI_HANDLE = r"(?:ybl|ibl|axl|apl|upi|paytm|okaxis|oksbi|okhdfcbank|okicici|sbi|icici|hdfcbank|axisbank|airtel|jio|freecharge|ptyes|ptsbi|pthdfc|ptaxis|kotak|yesbank|idfcbank|federal)"
_UPI = re.compile(rf"\b[a-z0-9][a-z0-9._-]{{1,40}}@{_UPI_HANDLE}\b", re.I)


def normalise_spoken(text: str) -> str:
    """Rewrite spoken punctuation ("dot", "slash", "at the rate", "dash") into symbols."""
    out = cg._norm(text)
    for rx, sym in _SPOKEN:
        out = rx.sub(sym, out)
    return out


def _excerpt(m: re.Match[str]) -> str:
    return m.group(0).strip(" ,.;:")[:EXCERPT_MAX]


def _clauses(text: str) -> list[str]:
    """call-guard's sentence split + advisory-clause filtering (shared semantics)."""
    out = []
    for sent in cg._SENT_SPLIT.split(cg._norm(text)):
        if not sent.strip() or cg._BENIGN_SENT.search(sent):
            continue
        eff = cg._effective_clauses(sent)
        if eff:
            out.append(eff)
    return out


def extra_hits(text: str) -> list[tuple[str, float, str]]:
    """(code, weight, excerpt) for the extra rules on one segment's text."""
    found: dict[str, tuple[str, float, str]] = {}
    for clause in _clauses(text):
        for code, pats in _X.items():
            if code in found:
                continue
            for p in pats:
                m = p.search(clause)
                if m:
                    found[code] = (code, EXTRA_WEIGHTS[code], _excerpt(m))
                    break
        spoken = normalise_spoken(clause)
        if "SPOKEN_PAYMENT_ID" not in found and (m := _UPI.search(spoken)):
            found["SPOKEN_PAYMENT_ID"] = (
                "SPOKEN_PAYMENT_ID",
                EXTRA_WEIGHTS["SPOKEN_PAYMENT_ID"],
                m.group(0)[:EXCERPT_MAX],
            )
        if "SPOKEN_LINK" not in found:
            for m in _LINK.finditer(spoken):
                if "@" in spoken[max(0, m.start() - 1) : m.start()]:
                    continue  # domain part of an e-mail / UPI id
                found["SPOKEN_LINK"] = (
                    "SPOKEN_LINK",
                    EXTRA_WEIGHTS["SPOKEN_LINK"],
                    m.group(0)[:EXCERPT_MAX],
                )
                break
    return list(found.values())


# call-guard code -> the call-guard patterns used only to quote the matched words
_CG_PATTERNS: dict[str, tuple[str, ...]] = {
    "DIGITAL_ARREST_PHRASE": ("_DIGITAL_ARREST",),
    "SAFE_ACCOUNT_TRANSFER": ("_SAFE_ACCOUNT", "_MOVE_FUNDS"),
    "ISOLATION_DEMAND": ("_ISO_SECRET", "_ISO_STAY", "_ISO_CONFINE"),
    "PAYMENT_DEMAND": ("_PAY_VERB",),
    "AUTHORITY_IMPERSONATION": ("_AUTH_NAME",),
    "LEGAL_THREAT": ("_LEGAL_STRONG", "_LEGAL_WEAK"),
    "ACCOUNT_VERIFICATION_ASK": ("_VERIFY_ASK",),
    "CREDENTIAL_ASK": ("_CRED_ASK",),
    "URGENCY": ("_URGENCY",),
}


def cg_excerpt(code: str, text: str) -> str | None:
    norm = cg._norm(text)
    for name in _CG_PATTERNS.get(code, ()):
        p = getattr(cg, name, None)
        if p is not None and (m := p.search(norm)):
            return _excerpt(m)
    return None


# ----------------------------------------------------------------------------- analysis
@dataclass(frozen=True)
class Hit:
    code: str
    label: str
    source: str  # call_guard | live_rules
    weight: float
    segment_id: int
    excerpt: str | None


@dataclass(frozen=True)
class ChunkAnalysis:
    score: float  # 0..1 chunk score (before accumulation)
    reasons: list[Reason]
    hits: list[Hit]
    model_version: str


def noisy_or(ws: list[float]) -> float:
    return 1.0 - reduce(lambda a, w: a * (1.0 - w), ws, 1.0)


class LiveDetector:
    def __init__(self, scorer_provider: Callable[[], Any]) -> None:
        self._scorer = scorer_provider

    def analyse(self, segments: list[tuple[int, str]]) -> ChunkAnalysis:
        scorer = self._scorer()
        if scorer is None:
            raise ScorerUnavailable("scorer_not_loaded")
        text = " ".join(t for _, t in segments if t)
        try:
            cg_score, cg_reasons, version = scorer.score_text(text)
        except Exception as e:
            raise ScorerUnavailable("scorer_failed") from e
        cg_codes = {r.code for r in cg_reasons if r.weight > 0 and r.code != "NO_RISK_INDICATORS"}
        hits: list[Hit] = []
        explained: set[str] = set()
        extra_codes: dict[str, float] = {}
        for sid, seg_text in segments:
            _, seg_reasons, _ = cg.analyse(seg_text)
            seg_codes = {r.code for r in seg_reasons if r.weight > 0}
            for code in sorted(seg_codes & cg_codes):
                explained.add(code)
                w = next(r.weight for r in cg_reasons if r.code == code)
                hits.append(
                    Hit(
                        code,
                        LABELS.get(code, code),
                        "call_guard",
                        w,
                        sid,
                        cg_excerpt(code, seg_text),
                    )
                )
            for code, w, ex in extra_hits(seg_text):
                if OVERLAPS.get(code) in seg_codes:
                    continue
                extra_codes[code] = max(extra_codes.get(code, 0.0), w)
                hits.append(Hit(code, LABELS[code], "live_rules", w, sid, ex))
        for r in cg_reasons:  # chunk-level codes no single segment explains
            if r.code in cg_codes and r.code not in explained:
                for sid, _ in segments:
                    hits.append(
                        Hit(r.code, LABELS.get(r.code, r.code), "call_guard", r.weight, sid, None)
                    )
        extra_reasons = [
            Reason(code=c, weight=w, detail=LABELS[c]) for c, w in sorted(extra_codes.items())
        ]
        score = noisy_or([cg_score, *extra_codes.values()])
        reasons = [r for r in cg_reasons if r.code in cg_codes] + extra_reasons
        return ChunkAnalysis(round(min(1.0, score), 4), reasons, hits, version)


class RiskTracker:
    """Session risk: call-guard's ``accumulate`` over chunk scores, in audio time (seconds)."""

    def __init__(self, half_life_s: float = 300.0) -> None:
        self.state: SessionState | None = None
        self.half_life_s = half_life_s

    def update(self, chunk_score: float, reasons: list[Reason], at_s: float) -> int:
        self.state = accumulate(self.state, chunk_score, reasons, at_s, self.half_life_s)
        return self.score

    @property
    def score(self) -> int:
        return round(100 * (self.state.score if self.state else 0.0))
