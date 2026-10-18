"""Verdict policy shared by citizen-shield and the demo-server (pure functions, no I/O).

Thresholds apply to the call-guard score in [0, 1]: ``likely_scam`` at >= 0.7 (call-guard's own
alert threshold), ``suspicious`` at >= 0.35, otherwise ``no_indicators_found``: no scam cue was
found by this check, which is NOT a finding that the message is safe (the label was ``safe``
until 2026-10-18; renamed after the accuracy audit). Blank input is ``insufficient_input``.
Scores within ``BORDER`` of a threshold, very short texts and rules-only scoring are reported
as ``uncertain``.
"""

from __future__ import annotations

import unicodedata

from .texts import SUPPORTED

MAX_CHARS = 4000
LIKELY_SCAM = 0.7
SUSPICIOUS = 0.35
BORDER = 0.08  # scores this close to a threshold are reported as uncertain
MIN_WORDS = 4

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


NO_INDICATORS = "no_indicators_found"
WEAK = "weak_indicators"
INSUFFICIENT = "insufficient_input"


def label_for(score: float) -> str:
    if score >= LIKELY_SCAM:
        return "likely_scam"
    if score >= SUSPICIOUS:
        return "suspicious"
    return NO_INDICATORS


def refine_label(label: str, reason_codes: list[str], n_words: int | None = None) -> str:
    """Below the suspicious threshold, say "weak indicators" when a cue was found and "no
    indicators" only when none was: a listed cue under a "nothing found" heading would contradict
    itself. With no cue and fewer than ``MIN_WORDS`` words there is too little text to say
    anything: ``insufficient_input`` (accuracy batch, 2026-10-18)."""
    if label == NO_INDICATORS and any(c != "NO_RISK_INDICATORS" for c in reason_codes):
        return WEAK
    if label == NO_INDICATORS and n_words is not None and n_words < MIN_WORDS:
        return INSUFFICIENT
    return label


def is_blank(text: str) -> bool:
    """True when nothing but whitespace and format characters (zero-width, bidi marks) remains."""
    return not any(not ch.isspace() and unicodedata.category(ch) != "Cf" for ch in text)


def choose_lang(requested: str | None, message: str) -> tuple[str, bool]:
    """(language to answer in, requested language was unsupported)."""
    unsupported = requested is not None and requested.strip().lower() not in SUPPORTED
    chosen = (requested or "").strip().lower()
    if chosen not in SUPPORTED:
        chosen = detect_lang(message)
    return chosen, unsupported


def normalise_for_scoring(message: str) -> tuple[str, bool]:
    """(text to score, truncated). Order matters for bounded work: strip, cap the raw input at
    ``MAX_CHARS`` characters, apply NFKC (full-width letters and digits, ligatures, compatibility
    forms become their plain equivalents), strip again and cap the result at ``MAX_CHARS``."""
    text = message.strip()
    truncated = len(text) > MAX_CHARS
    text = unicodedata.normalize("NFKC", text[:MAX_CHARS]).strip()
    if len(text) > MAX_CHARS:
        text, truncated = text[:MAX_CHARS], True
    return text, truncated


COVERED_SCRIPTS = frozenset({"LATIN", "DEVANAGARI"})  # scripts call-guard's rules were written for
PARTIAL_SHARE = 0.20  # uncovered letters above this share of all letters => partial coverage


def script_coverage(text: str) -> str:
    """``"partial"`` when more than 20% of the letters are in a script call-guard has no rules for
    (Tamil, Bengali, Gujarati, Telugu, Arabic, Han, Cyrillic, ...), else ``"full"``. Text without
    letters (digits, symbols, emoji) is ``"full"``. Run on the NFKC text."""
    letters = uncovered = 0
    for ch in text:
        if not unicodedata.category(ch).startswith("L"):
            continue
        letters += 1
        script = unicodedata.name(ch, "UNKNOWN").split(" ", 1)[0]
        if script not in COVERED_SCRIPTS:
            uncovered += 1
    return "partial" if letters and uncovered / letters > PARTIAL_SHARE else "full"
