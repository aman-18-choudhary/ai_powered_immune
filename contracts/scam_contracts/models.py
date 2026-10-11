from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from .canonical import canonical_json
from .geo import CITY_CODES, STATE_CODES

UPI_LIMIT_INR = Decimal("100000")
IMPS_LIMIT_INR = Decimal("500000")


def _require_aware(v: datetime) -> datetime:
    if v.tzinfo is None or v.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return v


AwareDatetime = Annotated[datetime, AfterValidator(_require_aware)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Reason(_Frozen):
    code: str
    weight: float
    detail: str


class Transaction(_Frozen):
    txn_id: str
    idempotency_key: str
    bank_id: str
    payer_token: str
    payee_hash: str
    rail: Literal["UPI", "IMPS", "NEFT"]
    amount_inr: Decimal
    ts: AwareDatetime
    payee_account_age_days: int
    device_id_token: str
    # Account identity assumption (additive, optional): the keyed hash of the PAYING account, with
    # the same function and key as ``payee_hash`` (``keyed_hash(account, "payee", key)``), so one
    # account carries one hash whether it is paid or paying. The paying bank can compute it from
    # its own ledger. It links a mule's incoming payments to its outgoing sweep; absent for events
    # from producers that do not supply it (consumers then fall back to ``payer_token``).
    payer_account_ref: str | None = None

    @model_validator(mode="after")
    def _check_amount(self) -> "Transaction":
        if self.amount_inr <= 0:
            raise ValueError("amount_inr must be > 0")
        if self.rail == "UPI" and self.amount_inr > UPI_LIMIT_INR:
            raise ValueError("UPI amount exceeds 1,00,000 limit")
        if self.rail == "IMPS" and self.amount_inr > IMPS_LIMIT_INR:
            raise ValueError("IMPS amount exceeds 5,00,000 limit")
        return self


class CallEvent(_Frozen):
    call_id: str
    idempotency_key: str
    victim_token: str
    caller_number_hash: str
    ts: AwareDatetime
    transcript_chunk: str
    channel: Literal["pstn", "voip", "video"]
    lang: str


class CallRisk(_Frozen):
    call_id: str
    victim_token: str
    score: Annotated[float, Field(ge=0, le=1)]
    reasons: list[Reason]
    model_version: str
    ts: AwareDatetime


class TxnDecision(_Frozen):
    txn_id: str
    decision: Literal["allow", "step_up", "hold_verify"]
    score: Annotated[float, Field(ge=0, le=1)]
    reasons: list[Reason]
    model_version: str
    ts: AwareDatetime
    decision_seq: int = 1  # 1 = first verdict; >1 = an upgrade of the same transaction


class Antibody(_Frozen):
    antibody_id: str
    kind: Literal["mule_account", "script", "device"]
    key_hash: str
    source_bank: str
    confirmed_by: str
    created_at: AwareDatetime
    expires_at: AwareDatetime
    revoked: bool = False


MAX_PAYLOAD_BYTES = 4096
MAX_CASE_REFS = 10
MAX_PAYLOAD_DEPTH = 6
CASE_REF_RE = r"^[A-Za-z0-9._:-]{1,64}$"
CaseRef = Annotated[str, Field(pattern=CASE_REF_RE)]


def _check_json(v: object, depth: int = 0) -> None:
    if depth > MAX_PAYLOAD_DEPTH:
        raise ValueError("payload nested too deeply")
    if isinstance(v, dict):
        for k, x in v.items():
            if not isinstance(k, str):
                raise ValueError("payload keys must be strings")
            _check_json(x, depth + 1)
    elif isinstance(v, list):
        for x in v:
            _check_json(x, depth + 1)
    elif isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("payload must not contain NaN or Infinity")
    elif not (v is None or isinstance(v, str | int | bool)):
        raise ValueError("payload must be JSON-serialisable")


def _check_payload(v: dict[str, Any] | None) -> dict[str, Any] | None:
    """Canonical-JSON-serialisable (see ``scam_contracts.canonical``) and at most 4 KiB."""
    if v is None:
        return v
    _check_json(v)
    if len(canonical_json(v)) > MAX_PAYLOAD_BYTES:
        raise ValueError("payload exceeds 4 KiB when serialised")
    return v


Payload = Annotated[dict[str, Any], AfterValidator(lambda v: _check_payload(v))]


class LedgerEntry(_Frozen):
    """One link of the evidence ledger's hash chain (see ``evidence_ledger``).

    ``ts`` is the ledger's RECEIPT time (UTC), so chain order is receipt order, not event order.
    ``payload`` is optional PII-free evidence; when present ``payload_hash`` equals
    ``scam_contracts.canonical.payload_hash(payload)``.

    Chain format 2: ``entry_hash = sha256(prev_hash || canonical_json({seq, ts, service, actor,
    event_type, payload_hash, payload_present, model_version, case_refs}))`` (hex; prev_hash is
    the ASCII hex of the previous entry_hash, 64 zeros at genesis). The payload BODY is not part
    of the hash, so an exported entry may withhold it (redaction) and still verify.
    """

    seq: int
    ts: AwareDatetime
    service: str
    actor: str
    event_type: str
    payload_hash: str
    prev_hash: str
    entry_hash: str
    model_version: str | None = None
    payload: dict[str, Any] | None = None
    case_refs: list[str] = []


class LedgerEntryIn(_Frozen):
    """An audit event as emitted by a service.

    ``payload`` (optional, <= 4 KiB canonical JSON, PII-free) lets the ledger verify
    ``payload_hash == sha256(canonical_json(payload))`` and render human-readable evidence.
    ``case_refs`` (<= 10 opaque references such as txn ids, antibody ids, campaign ids) group
    entries into cases. Both fields are additive: old messages without them stay valid.
    """

    service: str
    actor: str
    event_type: str
    payload_hash: str
    model_version: str | None = None
    payload: Payload | None = None
    case_refs: Annotated[list[CaseRef], Field(max_length=MAX_CASE_REFS)] = []


COMPLAINT_ID_RE = r"^[A-Za-z0-9._:-]{8,64}$"
AMOUNT_BUCKETS = ("<1k", "1k-10k", "10k-100k", "100k-1m", ">=1m")


def _city_code(v: str) -> str:
    if v not in CITY_CODES:
        raise ValueError("unknown city_code")
    return v


def _state_code(v: str) -> str:
    if v not in STATE_CODES:
        raise ValueError("unknown state_code")
    return v


class Complaint(_Frozen):
    """A citizen- or officer-reported incident, location coarsened to a fixed enum (topic
    ``complaints``; additive contract, keyed by ``complaint_id``).

    PII-free by construction: ``complaint_id`` is an opaque producer-chosen id (also the
    idempotency key: a duplicate id is the same complaint); the location is a ``city_code`` or a
    ``state_code`` from ``scam_contracts.geo`` (at least one; a city code wins when both are
    present), never free text, a phone number or a coordinate; ``amount_bucket`` is a coarse class,
    never an amount; ``case_ref`` is an opaque case reference as used by the ledger. ``ts`` is the
    event time of the incident (timezone-aware).
    """

    complaint_id: Annotated[str, Field(pattern=COMPLAINT_ID_RE)]
    ts: AwareDatetime
    kind: Literal["scam_call", "held_transfer", "cash_out"]
    city_code: Annotated[str, AfterValidator(_city_code)] | None = None
    state_code: Annotated[str, AfterValidator(_state_code)] | None = None
    amount_bucket: Literal["<1k", "1k-10k", "10k-100k", "100k-1m", ">=1m"] | None = None
    case_ref: CaseRef | None = None

    @model_validator(mode="after")
    def _has_location(self) -> "Complaint":
        if self.city_code is None and self.state_code is None:
            raise ValueError("a city_code or a state_code is required")
        return self
