"""Verdict policy shared by citizen-shield and the demo-server (pure functions, no I/O).

Thresholds apply to the call-guard score in [0, 1]: ``likely_scam`` at >= 0.7 (call-guard's own
alert threshold), ``suspicious`` at >= 0.35, otherwise ``safe``. ``safe`` means "no clear scam
cues found", not "verified safe". Scores within ``BORDER`` of a threshold, very short texts and
rules-only scoring are reported as ``uncertain``.
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


def label_for(score: float) -> str:
    if score >= LIKELY_SCAM:
        return "likely_scam"
    if score >= SUSPICIOUS:
        return "suspicious"
    return "safe"


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
