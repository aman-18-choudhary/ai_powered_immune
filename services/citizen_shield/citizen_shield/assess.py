"""The verdict pipeline: normalise -> score (call-guard over HTTP) -> label -> localise.

The message is held in memory for the duration of the call only: it is not logged, stored,
echoed or sent anywhere except to the scorer. A scorer failure yields a degraded verdict
(``label: suspicious``, ``uncertain: true``, ``degraded: true``): a "no indicators" answer that
really means "could not check" would be dangerous, so it is never produced. Blank input is
``insufficient_input``; no cue found is ``no_indicators_found`` (never "safe").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .guard_client import CallGuardClient, GuardDown
from .policy import (
    BORDER,
    INSUFFICIENT,
    LIKELY_SCAM,
    MIN_WORDS,
    NO_INDICATORS,
    SUSPICIOUS,
    WEAK,
    choose_lang,
    is_blank,
    label_for,
    normalise_for_scoring,
    refine_label,
    script_coverage,
)
from .texts import ADVICE, GENERIC_REASON, PARTIAL_NOTICE, REASON_TEXT, TOO_LONG_NOTICE

THRESHOLDS = {"suspicious": SUSPICIOUS, "likely_scam": LIKELY_SCAM}
RULES_ONLY_PREFIX = "rules"  # call-guard's RULES_VERSION ("rules-v1"): classifier unavailable
DEGRADED_SCORE = SUSPICIOUS  # placeholder consistent with the label; not a model output
DEGRADED_VERSION = "unavailable"


@dataclass(frozen=True)
class Verdict:
    label: str
    score: float
    reasons: list[dict[str, Any]]
    advice: dict[str, Any]
    lang: str
    uncertain: bool
    model_version: str
    degraded: bool = False
    coverage: str = "full"  # "partial": mostly a script the scorer has no rules for
    score_is_placeholder: bool = False  # score is a floor/placeholder, not a model output

    def to_json(self) -> dict[str, Any]:
        return {
            "label": self.label, "score": self.score, "reasons": self.reasons,
            "advice": self.advice, "lang": self.lang, "uncertain": self.uncertain,
            "model_version": self.model_version, "degraded": self.degraded,
            "coverage": self.coverage, "score_is_placeholder": self.score_is_placeholder,
            "thresholds": THRESHOLDS,
        }  # fmt: skip


def _advice(key: str, lang: str, truncated: bool = False) -> dict[str, Any]:
    steps = list(ADVICE[key][lang])
    if truncated:
        steps.insert(0, TOO_LONG_NOTICE[lang])
    return {"key": key, "steps": steps}


async def assess(guard: CallGuardClient, message: str, requested_lang: str | None) -> Verdict:
    """Never raises for any string input."""
    lang, unsupported = choose_lang(requested_lang, message)
    # lone surrogates cannot be encoded as UTF-8 JSON: replace them before anything else
    text, truncated = normalise_for_scoring(message.encode("utf-8", "replace").decode("utf-8"))
    if is_blank(text):
        return Verdict(INSUFFICIENT, 0.0, [], _advice("no_input", lang), lang, True, "none")
    coverage = script_coverage(text)
    partial = coverage == "partial"
    try:
        got = await guard.score(text)
    except GuardDown:
        return Verdict(
            "suspicious", DEGRADED_SCORE, [], _advice("degraded", lang, truncated), lang, True,
            DEGRADED_VERSION, degraded=True, coverage=coverage, score_is_placeholder=True,
        )  # fmt: skip
    label = label_for(got.score)
    shown: list[dict[str, Any]] = []
    for code, weight in got.reasons:
        if weight <= 0 and code != "NO_RISK_INDICATORS":
            continue
        row = REASON_TEXT.get(code)
        shown.append(
            {"code": code, "text": (row or GENERIC_REASON)[lang], "weight": round(float(weight), 3)}
        )
    label = refine_label(
        label, [code for code, weight in got.reasons if weight > 0], len(text.split())
    )
    if label != NO_INDICATORS or partial:
        shown = [x for x in shown if x["code"] != "NO_RISK_INDICATORS"]
    score, placeholder = round(got.score, 4), False
    if partial:
        # no rules exist for this script: "no cues found" means nothing, so never report safe
        shown.append({"code": "COVERAGE_PARTIAL", "text": REASON_TEXT["COVERAGE_PARTIAL"][lang],
                      "weight": 0.0})  # fmt: skip
        if label in (NO_INDICATORS, WEAK):
            label, score, placeholder = "suspicious", max(score, SUSPICIOUS), True
            advice = _advice("partial_coverage", lang, truncated)
        else:
            advice = _advice(label, lang, truncated)
            advice["steps"].insert(0, PARTIAL_NOTICE[lang])
    else:
        advice = _advice(label, lang, truncated)
    near = any(abs(got.score - th) < BORDER for th in (SUSPICIOUS, LIKELY_SCAM))
    rules_only = got.model_version.startswith(RULES_ONLY_PREFIX)
    uncertain = (
        truncated or unsupported or near or len(text.split()) < MIN_WORDS or rules_only or partial
    )
    return Verdict(
        label, score, shown, advice, lang, uncertain, got.model_version,
        coverage=coverage, score_is_placeholder=placeholder,
    )  # fmt: skip
