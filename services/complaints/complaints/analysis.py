"""Complaint analysis: an ``AnalysisService`` protocol and the first, deliberately minimal,
implementation.

``CallGuardTextAnalysis`` scores the complaint text (title + description) with the in-process
call-guard scorer (rules + TF-IDF/logistic classifier trained on simulator and authored data). The
0-100 ``risk_score`` is ``round(score * 100)`` of an UNCALIBRATED heuristic ordering, not a
likelihood; bands: LOW < 35, MEDIUM 35-69, HIGH >= 70. Nothing else is analysed: no category
classifier, no LLM explanation, no evidence-file analysis (all reported as ``unavailable``).
Optional complaint fields are never used as fraud evidence. If the scorer cannot be loaded or
fails the status is UNAVAILABLE with no level (never LOW).
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger("complaints.analysis")

MAX_TEXT_CHARS = 4000  # call-guard's own message cap
SCORE_SEMANTICS = "uncalibrated_heuristic"
PIPELINE = "complaints-text-v1"

CAPABILITIES_NONE: dict[str, str] = {
    "text_risk_scorer": "unavailable",
    "category_classifier": "unavailable",
    "llm_explanation": "unavailable",
    "evidence_content_analysis": "unavailable",
}


def band(risk_score: int) -> str:
    return "HIGH" if risk_score >= 70 else "MEDIUM" if risk_score >= 35 else "LOW"


@dataclass(frozen=True)
class AnalysisOutcome:
    status: str  # COMPLETED | PARTIAL | UNAVAILABLE
    pipeline_version: str
    capabilities: dict[str, str]
    risk_score: int | None
    risk_level: str | None
    indicators: list[dict[str, Any]]
    recommended_actions: list[dict[str, Any]]
    limitations: list[str]
    classification: str | None = None
    confidence: float | None = None
    explanation: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class AnalysisService(Protocol):
    async def analyse(self, complaint: dict[str, Any]) -> AnalysisOutcome: ...


_BASE_LIMITS = [
    "The risk score is an uncalibrated heuristic ordering of the complaint text, not a likelihood.",
    "The scorer was built for digital-arrest call scripts and trained on simulator and authored "
    "data; it has not been validated on real complaints. A narrative written from the victim's "
    "point of view can score lower than a transcript of what the caller said.",
    "Only the title and description text are analysed. Optional fields (phone, URL, amount, "
    "location) and uploaded evidence files are not used.",
    "Recommended actions are general static guidance, not a finding about this complaint.",
]

_COMMON = [
    "Do not send more money or share any OTP, PIN, CVV or password with anyone.",
    "If money has been lost, call the national cyber-crime helpline 1930 immediately and report "
    "at cybercrime.gov.in; the sooner it is reported the better the chance of a freeze.",
    "Tell your bank straight away so it can block the card, UPI handle or transfer.",
    "Keep screenshots, call logs, messages and transaction references; do not delete them.",
]
_BY_CATEGORY: dict[str, list[str]] = {
    "DIGITAL_ARREST": [
        "No agency can place you under 'digital arrest' or demand money over a call or video "
        "call. End the call and do not stay on video.",
    ],
    "UPI_FRAUD": [
        "Never approve a UPI collect request or scan a QR code to receive money.",
    ],
    "PHISHING": [
        "Do not click links or open attachments from the message; go to the bank or service "
        "through its official app or website.",
        "Change the password of any account whose details you entered.",
    ],
    "OTP_SCAM": [
        "A bank or any service will never ask you for an OTP; treat every request as fraud."
    ],
    "BANK_FRAUD": [
        "Ask your bank to block the card or account and to give you a written reference."
    ],
    "INVESTMENT_SCAM": [
        "Stop all transfers. Fees demanded to 'release' or 'withdraw' profits are a common sign "
        "of fraud.",
    ],
    "JOB_SCAM": ["Genuine employers do not ask candidates for fees or deposits before a job."],
    "SOCIAL_MEDIA_FRAUD": [
        "Report and block the account on the platform and do not continue the conversation.",
    ],
    "OTHER": [],
}
_HIGH_FIRST = "Treat this as urgent: act on the steps below now."


def static_guidance(category: str | None, level: str | None) -> list[dict[str, str]]:
    """Static, labelled advice keyed by category and level. Authored text: it needs legal and
    domain review before any production use (see README)."""
    texts: list[str] = []
    if level == "HIGH":
        texts.append(_HIGH_FIRST)
    texts.extend(_BY_CATEGORY.get(category or "OTHER", []))
    texts.extend(_COMMON)
    return [{"text": t, "source": "static_guidance"} for t in texts]


def _humanise(code: str) -> str:
    return code.replace("_", " ").capitalize()


def _load_call_guard() -> Any:
    from call_guard.model import Scorer, load_classifier

    return Scorer(load_classifier())


class CallGuardTextAnalysis:
    def __init__(self, scorer: Any = None, scorer_loader: Callable[[], Any] | None = None) -> None:
        self._scorer = scorer
        self._loader = scorer_loader or _load_call_guard
        self._failed: str | None = None
        self._lock = asyncio.Lock()

    @property
    def importable(self) -> bool:
        import importlib.util

        return self._scorer is not None or importlib.util.find_spec("call_guard") is not None

    async def _get_scorer(self) -> Any | None:
        if self._scorer is not None:
            return self._scorer
        if self._failed:
            return None
        async with self._lock:
            if self._scorer is None and not self._failed:
                try:
                    self._scorer = await asyncio.to_thread(self._loader)
                except Exception as exc:  # ImportError, missing artifact, sklearn mismatch...
                    self._failed = type(exc).__name__
                    log.warning("text scorer unavailable: %s", self._failed)
        return self._scorer

    def _unavailable(self, category: str | None) -> AnalysisOutcome:
        return AnalysisOutcome(
            status="UNAVAILABLE",
            pipeline_version=PIPELINE,
            capabilities=dict(CAPABILITIES_NONE),
            risk_score=None,
            risk_level=None,
            indicators=[],
            recommended_actions=static_guidance(category, None),
            limitations=["The text risk scorer is unavailable, so no risk assessment was made.",
                         *_BASE_LIMITS[2:]],
        )  # fmt: skip

    async def analyse(self, complaint: dict[str, Any]) -> AnalysisOutcome:
        category = complaint.get("category")
        scorer = await self._get_scorer()
        if scorer is None:
            return self._unavailable(category)
        text = f"{complaint.get('title', '')}\n{complaint.get('description', '')}"[:MAX_TEXT_CHARS]
        try:
            score, reasons, model_version = await asyncio.to_thread(scorer.score_text, text)
        except Exception as exc:
            log.warning("text scoring failed: %s", type(exc).__name__)
            return self._unavailable(category)
        risk = max(0, min(100, round(float(score) * 100)))
        level = band(risk)
        indicators = [
            {
                "code": r.code, "weight": r.weight, "text": r.detail or _humanise(r.code),
                "source": "call_guard",
            }
            for r in reasons
            if r.code != "NO_RISK_INDICATORS"
        ]  # fmt: skip
        limitations = list(_BASE_LIMITS)
        if risk > 0 and not indicators:
            limitations.append(
                "The score comes from the text classifier alone: no specific scam cue was matched."
            )
        caps = dict(CAPABILITIES_NONE)
        caps["text_risk_scorer"] = "available"
        return AnalysisOutcome(
            status="COMPLETED",
            pipeline_version=f"{PIPELINE}+{model_version}",
            capabilities=caps,
            risk_score=risk,
            risk_level=level,
            indicators=indicators,
            recommended_actions=static_guidance(category, level),
            limitations=limitations,
        )
