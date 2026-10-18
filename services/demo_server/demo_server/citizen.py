"""Citizen assistant: verdict for a pasted message using the real call-guard scorer.

Thresholds (documented, applied to the call-guard score in [0, 1]): ``likely_scam`` at >= 0.7
(the call-guard alert threshold), ``suspicious`` at >= 0.35, otherwise ``no_indicators_found`` (no
scam cue found by this check; NOT a finding that the message is safe; the label was ``safe``
until 2026-10-18). Only scored points with a listed reason count (``require_explained``).
The text is scored in memory and not stored or logged (only its length is). Reason and advice
texts come from the static dictionary below; the scorer's own free-text ``detail`` strings are
not used.

Handling that never errors: empty or blank text (label ``insufficient_input``, ``uncertain``, advice
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
from citizen_shield.policy import (
    BORDER,
    INSUFFICIENT,
    LIKELY_SCAM,
    MAX_CHARS,
    MIN_WORDS,
    NO_INDICATORS,
    SUSPICIOUS,
    detect_lang,
    label_for,
    refine_label,
)
from citizen_shield.texts import (
    ADVICE,
    GENERIC_REASON,
    REASON_TEXT,
    SUPPORTED,
    TOO_LONG_NOTICE,
)

__all__ = [
    "ADVICE", "BORDER", "GENERIC_REASON", "LIKELY_SCAM", "MAX_CHARS", "MIN_WORDS", "REASON_TEXT",
    "SUPPORTED", "SUSPICIOUS", "TOO_LONG_NOTICE", "Assessment", "assess", "detect_lang",
    "label_for",
]  # fmt: skip


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
        return Assessment(INSUFFICIENT, 0.0, [], _advice("no_input", chosen), chosen, True, version)
    truncated = len(text) > MAX_CHARS
    if truncated:
        text = text[:MAX_CHARS]
    # every point of the score has a listed reason (accuracy audit 2026-10-17)
    score, reasons, version = await asyncio.to_thread(
        lambda: scorer.score_text(text, require_explained=True)
    )
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
    label = refine_label(label, [r.code for r in reasons if r.weight > 0])
    if label != NO_INDICATORS:
        shown = [x for x in shown if x["code"] != "NO_RISK_INDICATORS"]
    near = any(abs(score - th) < BORDER for th in (SUSPICIOUS, LIKELY_SCAM))
    uncertain = (
        truncated or unsupported or near or len(text.split()) < MIN_WORDS or scorer.fallback_mode
    )
    return Assessment(
        label, round(score, 4), shown, _advice(label, chosen, truncated), chosen, uncertain, version
    )
