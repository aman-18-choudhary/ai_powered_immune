"""Request models and field normalisers.

Optional fields that are absent or blank are stored as NULL (never an empty string). Messages
never include the submitted value; the 422 handler additionally drops pydantic's ``input``/``ctx``.
"""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    model_validator,
)

from .identity import normalise_email

Category = Literal[
    "DIGITAL_ARREST", "UPI_FRAUD", "PHISHING", "OTP_SCAM", "BANK_FRAUD",
    "INVESTMENT_SCAM", "JOB_SCAM", "SOCIAL_MEDIA_FRAUD", "OTHER",
]  # fmt: skip
PaymentMethod = Literal["UPI", "CARD", "NETBANKING", "WALLET", "CASH", "BANK_TRANSFER", "OTHER"]
Status = Literal["SUBMITTED", "UNDER_REVIEW", "INVESTIGATING", "RESOLVED", "REJECTED"]
RoleName = Literal["CITIZEN", "INVESTIGATOR", "ADMIN"]

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SINGLE_LINE_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_UPI_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,255}@[a-z][a-z0-9]{1,63}$")
_UTR_RE = re.compile(r"^[A-Z0-9]{6,30}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_PHONE_STRIP = re.compile(r"[\s\-().]")
_ACCOUNT_RE = re.compile(r"^[0-9]{6,18}$")
_IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
BANK_FIELDS = ("bank_name", "bank_account", "ifsc")
MAX_AMOUNT = Decimal("999999999999.99")


def _blank_to_none(v: Any) -> Any:
    if isinstance(v, str) and not v.strip():
        return None
    return v


def _clean(v: str | None, *, multiline: bool = False) -> str | None:
    if v is None:
        return None
    v = v.strip()
    if (_CONTROL if multiline else _SINGLE_LINE_CONTROL).search(v):
        raise ValueError("contains control characters")
    return v or None


def _line(v: str | None) -> str | None:
    return _clean(v)


def _upi(v: str | None) -> str | None:
    v = _clean(v)
    if v is None:
        return None
    v = v.casefold()
    if len(v) > 320 or not _UPI_RE.fullmatch(v):
        raise ValueError("not a valid UPI id")
    return v


def _utr(v: str | None) -> str | None:
    v = _clean(v)
    if v is None:
        return None
    v = re.sub(r"[\s-]", "", v).upper()
    if not _UTR_RE.fullmatch(v):
        raise ValueError("not a valid transaction reference")
    return v


def normalise_phone(raw: str) -> str:
    """E.164-style: ``+<country><number>``; bare Indian mobile (10 digits, 6-9 first) gets +91."""
    s = _PHONE_STRIP.sub("", raw)
    if s.startswith("00"):
        s = "+" + s[2:]
    if s.startswith("+"):
        digits = s[1:]
        if digits.isdigit() and 8 <= len(digits) <= 15 and digits[0] != "0":
            return "+" + digits
        raise ValueError("not a valid phone number")
    if not s.isdigit():
        raise ValueError("not a valid phone number")
    if len(s) == 10 and s[0] in "6789":
        return "+91" + s
    if len(s) == 12 and s.startswith("91") and s[2] in "6789":
        return "+" + s
    if len(s) in (10, 11, 12) and s.startswith("0") and s[1] != "0":
        return "+91" + s[1:]
    raise ValueError("not a valid phone number")


def _phone(v: str | None) -> str | None:
    v = _clean(v)
    return None if v is None else normalise_phone(v)


def _email(v: str | None) -> str | None:
    v = _clean(v)
    if v is None:
        return None
    out = normalise_email(v)
    if out is None:
        raise ValueError("not a valid email address")
    return out


def _url(v: str | None) -> str | None:
    v = _clean(v)
    if v is None:
        return None
    if len(v) > 2048 or re.search(r"\s", v):
        raise ValueError("not a valid URL")
    try:
        parts = urlsplit(v)
        _ = parts.port
    except ValueError as exc:
        raise ValueError("not a valid URL") from exc
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError("URL must start with http:// or https://")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise ValueError("URL must not contain credentials")
    return v


def _bank_account(v: str | None) -> str | None:
    """Suspect / beneficiary account number: 6-18 digits once spaces are removed."""
    v = _clean(v)
    if v is None:
        return None
    v = v.replace(" ", "")
    if not _ACCOUNT_RE.fullmatch(v):
        raise ValueError("account number must be 6-18 digits")
    return v


def _ifsc(v: str | None) -> str | None:
    v = _clean(v)
    if v is None:
        return None
    v = v.upper()
    if not _IFSC_RE.fullmatch(v):
        raise ValueError("not a valid IFSC (4 letters, 0, then 6 letters or digits)")
    return v


def _currency(v: str | None) -> str | None:
    v = _clean(v)
    if v is None:
        return None
    v = v.upper()
    if not _CURRENCY_RE.fullmatch(v):
        raise ValueError("currency must be a 3-letter code")
    return v


def _amount(v: Any) -> Decimal | None:
    v = _blank_to_none(v)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float, str, Decimal)):
        raise ValueError("amount must be a number")
    try:
        d = Decimal(str(v).strip())
    except InvalidOperation as exc:
        raise ValueError("amount must be a number") from exc
    if not d.is_finite() or d < 0 or d > MAX_AMOUNT:
        raise ValueError("amount out of range")
    if d != d.quantize(Decimal("0.01")):
        raise ValueError("amount allows at most two decimals")
    return d.quantize(Decimal("0.01"))


def _opt(max_len: int, after: Any = _line) -> Any:
    """Optional single-line string: blank -> None, bounded, then normalised by ``after``."""
    return Annotated[
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=max_len)] | None,
        BeforeValidator(_blank_to_none),
        AfterValidator(after),
    ]


class ComplaintIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category
    title: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=3, max_length=200),
        AfterValidator(lambda v: _clean(v) or ""),
    ]
    description: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=10, max_length=5000),
        AfterValidator(lambda v: _clean(v, multiline=True) or ""),
    ]
    incident_at: Annotated[datetime | None, BeforeValidator(_blank_to_none)] = None
    reported_at: Annotated[datetime | None, BeforeValidator(_blank_to_none)] = None
    amount: Annotated[Decimal | None, BeforeValidator(_amount)] = None
    currency: _opt(3, _currency) = None  # type: ignore[valid-type]
    payment_method: Annotated[PaymentMethod | None, BeforeValidator(_blank_to_none)] = None
    upi_id: _opt(320, _upi) = None  # type: ignore[valid-type]
    utr_reference: _opt(60, _utr) = None  # type: ignore[valid-type]
    suspect_phone: _opt(40, _phone) = None  # type: ignore[valid-type]
    suspect_email: _opt(254, _email) = None  # type: ignore[valid-type]
    suspicious_url: _opt(2048, _url) = None  # type: ignore[valid-type]
    location_text: _opt(200) = None  # type: ignore[valid-type]
    city: _opt(100) = None  # type: ignore[valid-type]
    state: _opt(100) = None  # type: ignore[valid-type]
    latitude: Annotated[float | None, BeforeValidator(_blank_to_none), Field(ge=-90, le=90)] = None
    longitude: Annotated[float | None, BeforeValidator(_blank_to_none), Field(ge=-180, le=180)] = (
        None
    )
    social_platform: _opt(50) = None  # type: ignore[valid-type]
    social_account: _opt(100) = None  # type: ignore[valid-type]
    bank_name: _opt(100) = None  # type: ignore[valid-type]
    bank_account: _opt(40, _bank_account) = None  # type: ignore[valid-type]
    ifsc: _opt(20, _ifsc) = None  # type: ignore[valid-type]

    @model_validator(mode="after")
    def _coords_together(self) -> "ComplaintIn":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be given together")
        return self


class PrivacyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_case_matching: StrictBool


class MeUpdate(BaseModel):
    """Self-service profile. Only these keys exist; anything else (role, email, ...) is a 422,
    so a body can never change a role or an identity attribute."""

    model_config = ConfigDict(extra="forbid")
    # null = go back to the name from sign-in
    display_name: (
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
            AfterValidator(lambda v: _clean(v) or ""),
        ]
        | None
    ) = None
    privacy: PrivacyIn = Field(default=None)  # type: ignore[assignment]  # null is refused


class LocalStartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=254)]


class LocalVerifyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=254)]
    code: Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$")]
    name: _opt(100) = None  # type: ignore[valid-type]


class EntryBody(BaseModel):
    # unknown keys (e.g. a client-sent "role") are ignored, never read
    entry: Literal["citizen", "investigator"]


class StatusBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Status
    note: _opt(500, lambda v: _clean(v, multiline=True)) = None  # type: ignore[valid-type]


class RoleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: RoleName
    expected_role: RoleName


class ActiveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_active: bool


class AnalyzeIn(BaseModel):
    """Stateless analysis input. Values are lenient on purpose (an odd phone or UPI id is
    analysed and reported, not rejected) but bounded and free of control characters."""

    model_config = ConfigDict(extra="forbid")

    text: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=10, max_length=5000),
        AfterValidator(lambda v: _clean(v, multiline=True) or ""),
    ]
    title: _opt(200) = None  # type: ignore[valid-type]
    suspicious_url: _opt(2048) = None  # type: ignore[valid-type]
    suspect_phone: _opt(40) = None  # type: ignore[valid-type]
    upi_id: _opt(320) = None  # type: ignore[valid-type]
    suspect_email: _opt(254) = None  # type: ignore[valid-type]
    amount: Annotated[Decimal | None, BeforeValidator(_amount)] = None
    payment_method: Annotated[PaymentMethod | None, BeforeValidator(_blank_to_none)] = None
    city: _opt(100) = None  # type: ignore[valid-type]
    state: _opt(100) = None  # type: ignore[valid-type]
    social_platform: _opt(50) = None  # type: ignore[valid-type]
