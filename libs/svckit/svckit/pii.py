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
# Opaque identifiers minted by the platform: sha256 digests, "txn_<hex>" ids, key-hash prefixes.
# A lowercase hex token of 16..64 chars, or 8..64 after a short "prefix_", containing a letter,
# is exempt ONLY if it has no long run of digits (9+; 12+ for a 64-char digest, where a run of
# 9-11 digits occurs by chance in about 1% of genuine sha256 values). All-digit tokens are never
# exempt, so "a1234567890123456" or "acct_123456789012" are still caught.
_HEX_ID = re.compile(
    r"(?<![0-9A-Za-z])(?:[a-z][a-z0-9]{0,15}[_:-]([0-9a-f]{8,64})|([0-9a-f]{16,64}))(?![0-9A-Za-z])"
)
_DIGITS = re.compile(r"\d+")


def _max_digit_run(s: str) -> int:
    return max((len(m) for m in _DIGITS.findall(s)), default=0)


def _blank_id(m: re.Match[str]) -> str:
    body = m.group(1) or m.group(2)
    if not any(c in body for c in "abcdef"):
        return m.group(0)
    limit = 12 if len(body) == 64 else 9
    return m.group(0) if _max_digit_run(body) >= limit else " "


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
    """``contains_identifier`` after blanking platform-minted hex ids (digests, txn ids), which
    would otherwise trip the digit rules. Use this for ids and payload values."""
    return contains_identifier(_HEX_ID.sub(_blank_id, text))


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
