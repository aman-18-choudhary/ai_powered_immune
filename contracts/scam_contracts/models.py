from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, model_validator

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
    score: float
    reasons: list[Reason]
    model_version: str
    ts: AwareDatetime


class TxnDecision(_Frozen):
    txn_id: str
    decision: Literal["allow", "step_up", "hold_verify"]
    score: float
    reasons: list[Reason]
    model_version: str
    ts: AwareDatetime


class Antibody(_Frozen):
    antibody_id: str
    kind: Literal["mule_account", "script", "device"]
    key_hash: str
    source_bank: str
    confirmed_by: str
    created_at: AwareDatetime
    expires_at: AwareDatetime
    revoked: bool = False


class LedgerEntry(_Frozen):
    seq: int
    ts: AwareDatetime
    service: str
    actor: str
    event_type: str
    payload_hash: str
    prev_hash: str
    entry_hash: str
    model_version: str | None = None


class LedgerEntryIn(_Frozen):
    service: str
    actor: str
    event_type: str
    payload_hash: str
    model_version: str | None = None
