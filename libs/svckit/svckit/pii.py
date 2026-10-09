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
_DIGEST_LENGTHS = {32, 40, 56, 64, 96, 128}
_HEX_RE = re.compile(r"^[0-9a-f]+$")


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


def is_hex_digest(text: str) -> bool:
    """A lowercase hex digest (sha256 etc.) that is not all digits. Such strings are the
    platform's own pseudonymous hashes and would otherwise trip the digit rules."""
    return len(text) in _DIGEST_LENGTHS and _HEX_RE.match(text) is not None and not text.isdigit()


def string_has_identifier(text: str) -> bool:
    return False if is_hex_digest(text) else contains_identifier(text)


def json_has_identifier(obj: Any) -> bool:
    """True if any key or string value anywhere in a JSON structure looks like an identifier.
    Hex digests are exempt. Numbers are not inspected."""
    if isinstance(obj, str):
        return string_has_identifier(obj)
    if isinstance(obj, dict):
        return any(string_has_identifier(str(k)) or json_has_identifier(v) for k, v in obj.items())
    if isinstance(obj, list | tuple):
        return any(json_has_identifier(x) for x in obj)
    return False
