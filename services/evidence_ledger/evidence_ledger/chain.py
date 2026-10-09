"""Entry validation and chain construction. Hashing lives in ``verify`` (the standalone,
stdlib-only module that is also shipped inside every evidence package) so that the service and
the offline verifier can never disagree."""

import hashlib
import re
from datetime import datetime
from typing import Any

from scam_contracts.canonical import canonical_json, payload_hash
from scam_contracts.models import LedgerEntry, LedgerEntryIn
from svckit.pii import contains_identifier, json_has_identifier, string_has_identifier

from .verify import GENESIS, compute_entry_hash, entry_dict, ts_str

__all__ = [
    "GENESIS",
    "EntryRejected",
    "build_entry",
    "idempotency_key",
    "validate_entry",
    "to_model",
    "to_dict",
]

_NAME_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
_ACTOR_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,100}(@[a-z0-9_-]{2,32})?$")  # "sub@bank_a" allowed
_VERSION_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class EntryRejected(ValueError):
    """Permanent rejection of an entry; ``code`` is safe to log, the message never echoes input."""

    def __init__(self, code: str) -> None:
        super().__init__(f"entry rejected: {code}")
        self.code = code


def validate_entry(e: LedgerEntryIn) -> None:
    if not (_NAME_RE.match(e.service) and _NAME_RE.match(e.event_type)):
        raise EntryRejected("bad_field")
    if not _ACTOR_RE.match(e.actor):
        raise EntryRejected("bad_field")
    if e.model_version is not None:
        if not _VERSION_RE.match(e.model_version):
            raise EntryRejected("bad_field")
        if contains_identifier(e.model_version):  # strict: no hex-id exemption for versions
            raise EntryRejected("pii")
    if not _HASH_RE.match(e.payload_hash):
        raise EntryRejected("bad_field")
    if any(string_has_identifier(x.replace("@", "-")) for x in (e.service, e.actor, e.event_type)):
        raise EntryRejected("pii")
    if any(string_has_identifier(r) for r in e.case_refs):
        raise EntryRejected("pii")
    if e.payload is not None:
        try:
            ok = payload_hash(e.payload) == e.payload_hash
        except (TypeError, ValueError):
            raise EntryRejected("bad_payload") from None
        if not ok:
            raise EntryRejected("payload_hash_mismatch")
        if json_has_identifier(e.payload):
            raise EntryRejected("pii")


def idempotency_key(e: LedgerEntryIn) -> str:
    """Exact-repeat key: every field that ends up in the stored entry. A repeat of the identical
    entry (at-least-once delivery, replica races) maps to the same key; any differing actor,
    model_version, case_refs or payload presence is a distinct entry. A hash-only entry and the
    same entry carrying its payload are therefore two records (documented)."""
    return hashlib.sha256(
        canonical_json(
            {
                "service": e.service,
                "event_type": e.event_type,
                "payload_hash": e.payload_hash,
                "actor": e.actor,
                "model_version": e.model_version,
                "case_refs": sorted(set(e.case_refs)),
                "payload_present": e.payload is not None,
            }
        )
    ).hexdigest()


def build_entry(e: LedgerEntryIn, seq: int, ts: datetime, prev_hash: str) -> dict[str, Any]:
    d = entry_dict(
        {
            "seq": seq, "ts": ts, "service": e.service, "actor": e.actor,
            "event_type": e.event_type, "payload_hash": e.payload_hash, "prev_hash": prev_hash,
            "model_version": e.model_version, "payload": e.payload, "case_refs": e.case_refs,
        }
    )  # fmt: skip
    d["entry_hash"] = compute_entry_hash(prev_hash, d)
    return d


def to_model(d: dict[str, Any]) -> LedgerEntry:
    return LedgerEntry.model_validate(d)


def to_dict(e: LedgerEntry) -> dict[str, Any]:
    """JSON-ready canonical entry dict (ts as the fixed-width UTC string)."""
    d = e.model_dump()
    d["ts"] = ts_str(d["ts"])
    d["payload_present"] = d["payload"] is not None
    return d
