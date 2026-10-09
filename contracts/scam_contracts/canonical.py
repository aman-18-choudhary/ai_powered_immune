"""Canonical JSON and payload hashing: the ONE definition used by emitters and the ledger.

Canonical JSON (``canonical_json``) is, precisely:

* UTF-8 encoded text,
* object keys sorted by Unicode code point (``sort_keys=True``) at every depth,
* separators ``(",", ":")`` (no whitespace),
* ``ensure_ascii=False`` (non-ASCII characters are emitted as UTF-8, not ``\\uXXXX`` escapes),
* ``NaN`` / ``Infinity`` are rejected (``allow_nan=False`` -> ``ValueError``),
* numbers use Python's ``json`` rendering (``repr`` for floats). Floats are therefore only
  canonical between Python implementations; emitters that need cross-language reproducibility
  should carry decimals as strings or scaled integers.

``payload_hash(obj)`` is the lowercase hex SHA-256 of ``canonical_json(obj)``.
"""

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def payload_hash(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj)).hexdigest()
