"""Safety-net guard against raw identifiers (account numbers, phones, e-mail / UPI handles, PAN,
IFSC) in free text. Adapted from the antibody hub's ``contains_identifier``.

Known limits: spelled-out numbers, base64/hex-encoded values and fragments of 8 digits or fewer
split across fields are not detected. It is a net under, not a replacement for, schema design.
"""

import re
import unicodedata
from typing import Any

_RULES = [
    re.compile(r"\d(?:\D{0,3}\d){8,}"),  # 9+ digits, up to 3 non-digits between any two
    re.compile(r"@\s*[a-z0-9]"),  # e-mail / UPI handle (also "name @ bank")
    re.compile(r"\+\s*\d"),  # international prefix
    re.compile(r"(?<!\d)0091"),
    re.compile(r"\b[a-z]{5}\d{4}[a-z]\b"),  # PAN
    re.compile(r"\b[a-z]{4}0[a-z0-9]{6}\b"),  # IFSC
]
# Opaque identifiers minted by the platform (sha256 digests, "txn_<hex>" ids). They are exempt only
# as WHOLE strings of an exact shape: an optional lowercase "prefix_" of 2-8 letters, then exactly
# 16, 24, 32 or 64 lowercase hex chars containing at least one letter a-f, whose longest run of
# digits is at most a per-length cap. The caps are the smallest values for which the false-reject
# rate on uniformly random ids is below 0.01% (measured on 1,000,000 ids per length: README):
# 16 -> 15, 24 -> 21, 32 -> 22, 64 -> 25. Anything embedded in longer text gets the strict rule.
# Residual risk (unavoidable for a shape rule at this false-reject rate): a 15-digit number plus
# one hex letter in a 16-char string is accepted, as are phone-sized digit runs padded with hex
# letters to an id length.
_ID_RE = re.compile(r"(?:[a-z]{2,8}_)?([0-9a-f]{16}|[0-9a-f]{24}|[0-9a-f]{32}|[0-9a-f]{64})")
_ID_RUN_CAP = {16: 15, 24: 21, 32: 22, 64: 25}
_DIGITS = re.compile(r"\d+")


def _max_digit_run(s: str) -> int:
    return max((len(m) for m in _DIGITS.findall(s)), default=0)


def is_opaque_id(text: str) -> bool:
    m = _ID_RE.fullmatch(text)
    if m is None:
        return False
    body = m.group(1)
    return any(c in "abcdef" for c in body) and _max_digit_run(body) <= _ID_RUN_CAP[len(body)]


def _flatten(s: str) -> str:
    """NFKC + casefold; whitespace/separators -> one space; format/control chars removed."""
    s = unicodedata.normalize("NFKC", s).casefold()
    out = []
    for ch in s:
        cat = unicodedata.category(ch)
        if ch.isspace() or cat.startswith("Z"):
            out.append(" ")
        elif cat in ("Cf", "Cc"):
            continue
        else:
            out.append(ch)
    return re.sub(r" +", " ", "".join(out))


def contains_identifier(text: str) -> bool:
    flat = _flatten(text)
    if any(rx.search(flat) for rx in _RULES):
        return True
    alnum = "".join(ch for ch in flat if ch.isalnum())
    return re.search(r"\d{9,}", alnum) is not None  # digits split only by punctuation/space


def string_has_identifier(text: str) -> bool:
    """For STRUCTURED fields (case refs, payload keys and values): a whole-string opaque id is
    accepted, anything else gets ``contains_identifier``."""
    return False if is_opaque_id(text) else contains_identifier(text)


def json_has_identifier(obj: Any) -> bool:
    """True if any key or string value anywhere in a JSON structure looks like an identifier.
    Hex ids are exempt. Numbers with a run of 9+ digits are rejected
    (such amounts must be omitted)."""
    if isinstance(obj, str):
        return string_has_identifier(obj)
    if isinstance(obj, dict):
        return any(string_has_identifier(str(k)) or json_has_identifier(v) for k, v in obj.items())
    if isinstance(obj, list | tuple):
        return any(json_has_identifier(x) for x in obj)
    if isinstance(obj, int | float) and not isinstance(obj, bool):
        return _max_digit_run(repr(obj)) >= 9  # account-number-sized numbers
    return False
