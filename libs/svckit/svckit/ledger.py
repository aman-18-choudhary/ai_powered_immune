"""Ledger emission helper: the ONE way services build and publish ``LedgerEntryIn``.

``build_ledger_entry`` computes ``payload_hash = sha256(canonical_json(payload))`` with the shared
canonical helper (``scam_contracts.canonical``), so the evidence ledger can verify it, and refuses
anything that could carry a raw identifier:

* keys containing a forbidden name (``phone``, ``account``, ``name``, ``email``, ... see
  ``FORBIDDEN_KEY_PARTS``; case-insensitive substring match at every depth),
* any key or string/number value the ``svckit.pii`` guard flags (9+ digit runs, e-mail / UPI
  handles, ``+91``, PAN, IFSC, ...), with whole-string opaque ids (16/24/32/64 hex, optional
  ``prefix_`` / ``prefix_ref:``) and whole-string UTC timestamps (``2026-10-09T12:00:00Z``) exempt,
* a payload larger than 4 KiB of canonical JSON, too deeply nested, or not JSON.

``LedgerPayloadError`` messages never echo the rejected key or value (they would end up in logs).
Rejection is a programming error in the emitter, not a runtime condition to recover from.

Ordering assumption: ``emit_ledger`` keys the bus message by the first ``case_ref`` (else by the
service name), which keeps one case's entries on one partition. The ledger nevertheless orders by
RECEIPT and is idempotent on the full entry key (service, event_type, payload_hash, actor,
model_version, case_refs, payload_present), so emitters must be at-least-once with deterministic
payloads (put event times in the payload, never ``now``) and need no cross-event ordering.
"""

import hashlib
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from scam_contracts.canonical import canonical_json, payload_hash
from scam_contracts.models import MAX_CASE_REFS, MAX_PAYLOAD_BYTES, LedgerEntryIn
from scam_contracts.topics import Topics

from svckit.bus import Bus
from svckit.pii import contains_identifier, is_opaque_id, json_has_identifier, string_has_identifier

FORBIDDEN_KEY_PARTS: tuple[str, ...] = (
    "phone", "mobile", "account", "account_number", "name", "email", "address", "upi", "vpa",
    "pan", "aadhaar", "otp", "password", "token_secret",
)  # fmt: skip
_VERSION_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_REF_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class LedgerPayloadError(ValueError):
    """The entry cannot be sent to the ledger. The message never contains rejected input."""


def _walk_keys(obj: Any, depth: int = 0) -> Iterable[str]:
    if depth > 32:
        raise LedgerPayloadError("payload nested too deeply")
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise LedgerPayloadError("payload keys must be strings")
            yield k
            yield from _walk_keys(v, depth + 1)
    elif isinstance(obj, list | tuple):
        for v in obj:
            yield from _walk_keys(v, depth + 1)


def _check_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise LedgerPayloadError("payload must be a mapping")
    data = dict(payload)
    for key in _walk_keys(data):
        low = key.lower()
        for part in FORBIDDEN_KEY_PARTS:
            if part in low:
                raise LedgerPayloadError(f"payload contains a forbidden key name ({part!r} class)")
    try:
        size = len(canonical_json(data))
    except (TypeError, ValueError):
        raise LedgerPayloadError("payload is not canonical-JSON serialisable") from None
    if size > MAX_PAYLOAD_BYTES:
        raise LedgerPayloadError(f"payload exceeds {MAX_PAYLOAD_BYTES} bytes of canonical JSON")
    if json_has_identifier(data):
        raise LedgerPayloadError("a payload key or value looks like a raw identifier (PII guard)")
    return data


def build_ledger_entry(
    service: str,
    actor: str,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    model_version: str | None = None,
    case_refs: Iterable[str] = (),
) -> LedgerEntryIn:
    data = _check_payload(payload)
    refs = list(dict.fromkeys(case_refs))  # de-duplicated, order kept
    if len(refs) > MAX_CASE_REFS:
        raise LedgerPayloadError(f"at most {MAX_CASE_REFS} case refs")
    for r in refs:
        if not _REF_RE.match(r):
            raise LedgerPayloadError("case ref has a forbidden shape")
        if string_has_identifier(r):
            raise LedgerPayloadError("a case ref looks like a raw identifier (PII guard)")
    if model_version is not None and (
        not _VERSION_RE.match(model_version) or contains_identifier(model_version)
    ):
        raise LedgerPayloadError("model_version has a forbidden shape")
    if any(string_has_identifier(x.replace("@", "-")) for x in (service, actor, event_type)):
        raise LedgerPayloadError("service, actor or event_type looks like a raw identifier")
    try:
        return LedgerEntryIn(
            service=service, actor=actor, event_type=event_type,
            payload_hash=payload_hash(data), model_version=model_version, payload=data,
            case_refs=refs,
        )  # fmt: skip
    except (ValidationError, TypeError, ValueError):
        raise LedgerPayloadError("entry failed contract validation") from None


async def emit_ledger(
    bus: Bus,
    service: str,
    actor: str,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    model_version: str | None = None,
    case_refs: Iterable[str] = (),
) -> LedgerEntryIn:
    """Build (and validate) the entry, then publish it to ``Topics.LEDGER`` keyed by the first
    case ref (else the service name). Raises ``LedgerPayloadError`` before anything is sent."""
    entry = build_ledger_entry(
        service, actor, event_type, payload, model_version=model_version, case_refs=case_refs
    )
    await bus.publish(Topics.LEDGER, entry.case_refs[0] if entry.case_refs else service, entry)
    return entry


# ------------------------------------------------------------------------- reference helpers
def opaque_hex16(hex_hash: str) -> str:
    """16 hex chars of a 64-hex hash: the first 16, or if that block is not an opaque-id shape for
    the PII guard (all digits, 0.05% of hashes) the next block, deterministically."""
    h = hex_hash.lower()
    if not _HEX64.match(h):
        raise LedgerPayloadError("expected a 64-char lowercase hex hash")
    for off in (0, 16, 32, 48):
        cand = h[off : off + 16]
        if is_opaque_id(cand):
            return cand
    raise LedgerPayloadError("no usable 16-hex block in hash")  # probability ~ 1e-13


def hash_ref(namespace: str, hex_hash: str) -> str:
    """``<namespace>:<16 hex>`` (namespace like ``payee_ref``) from a 64-hex hash."""
    return f"{namespace}:{opaque_hex16(hex_hash)}"


def payee_ref(payee_hash: str) -> str:
    """``payee_ref:<16 hex>`` from the keyed payee hash: identifies one (mule) account across
    banks and services without revealing it."""
    return hash_ref("payee_ref", payee_hash)


def call_ref(call_id: str) -> str:
    """``call_ref:<16 hex>`` = sha256(call_id) prefix; the call id itself is never emitted."""
    return "call_ref:" + opaque_hex16(hashlib.sha256(call_id.encode()).hexdigest())


def utc_ts(ts: datetime, *, micros: bool = False) -> str:
    """UTC ``YYYY-MM-DDTHH:MM:SSZ`` (``.ffffffZ`` with ``micros``): the timestamp shape the PII
    guard accepts as a whole string."""
    if ts.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ" if micros else "%Y-%m-%dT%H:%M:%SZ")
