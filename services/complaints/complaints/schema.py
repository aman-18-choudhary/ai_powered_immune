"""SQLAlchemy Core schema. Timestamps are timezone-aware UTC (a TypeDecorator restores the tzinfo
SQLite drops); money is NUMERIC on Postgres and an exact decimal string on SQLite (SQLite has no
exact numeric type); ids are UUID text. There is no password or secret column anywhere.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.types import TypeDecorator

ROLES = ("CITIZEN", "INVESTIGATOR", "ADMIN")
CATEGORIES = (
    "DIGITAL_ARREST", "UPI_FRAUD", "PHISHING", "OTP_SCAM", "BANK_FRAUD",
    "INVESTMENT_SCAM", "JOB_SCAM", "SOCIAL_MEDIA_FRAUD", "OTHER",
)  # fmt: skip
PAYMENT_METHODS = ("UPI", "CARD", "NETBANKING", "WALLET", "CASH", "BANK_TRANSFER", "OTHER")
STATUSES = ("SUBMITTED", "UNDER_REVIEW", "INVESTIGATING", "RESOLVED", "REJECTED")
EVIDENCE_STATUSES = ("stored", "rejected")
ANALYSIS_STATUSES = ("COMPLETED", "PARTIAL", "UNAVAILABLE")
RISK_LEVELS = ("LOW", "MEDIUM", "HIGH")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


class UTCDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime refused")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Money(TypeDecorator):
    """NUMERIC(14, 2) on Postgres; exact decimal text on SQLite. Always Decimal in Python."""

    impl = String(32)
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Numeric(14, 2, asdecimal=True))
        return dialect.type_descriptor(String(32))

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        d = Decimal(str(value)).quantize(Decimal("0.01"))
        return d if dialect.name == "postgresql" else format(d, "f")

    def process_result_value(self, value: Any, dialect: Any) -> Decimal | None:
        return None if value is None else Decimal(str(value)).quantize(Decimal("0.01"))


metadata = MetaData()

users = Table(
    "users", metadata,
    Column("id", String(36), primary_key=True),
    Column("idp_subject", String(255), nullable=False, unique=True),
    Column("email", String(254)),
    Column("email_verified", Boolean, nullable=False, default=False),
    Column("display_name", String(100)),
    Column("role", String(16), nullable=False, default="CITIZEN"),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("created_at", UTCDateTime, nullable=False),
    Column("last_login_at", UTCDateTime),
    CheckConstraint(_in("role", ROLES), name="ck_users_role"),
    Index("ix_users_email", "email"),
    Index("ix_users_role", "role", "is_active"),
)  # fmt: skip

complaints = Table(
    "complaints", metadata,
    Column("id", String(36), primary_key=True),
    Column("owner_user_id", String(36), ForeignKey("users.id", ondelete="RESTRICT"),
           nullable=False),
    Column("category", String(24), nullable=False),
    Column("title", String(200), nullable=False),
    Column("description", Text, nullable=False),
    Column("incident_at", UTCDateTime),
    Column("reported_at", UTCDateTime, nullable=False),
    Column("amount", Money),
    Column("currency", String(3)),
    Column("payment_method", String(16)),
    Column("upi_id", String(320)),
    Column("utr_reference", String(40)),
    Column("suspect_phone", String(20)),
    Column("suspect_email", String(254)),
    Column("suspicious_url", String(2048)),
    Column("location_text", String(200)),
    Column("city", String(100)),
    Column("state", String(100)),
    Column("latitude", Numeric(9, 6, asdecimal=False)),
    Column("longitude", Numeric(9, 6, asdecimal=False)),
    Column("social_platform", String(50)),
    Column("social_account", String(100)),
    Column("status", String(16), nullable=False, default="SUBMITTED"),
    Column("created_at", UTCDateTime, nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
    Column("deleted_at", UTCDateTime),
    CheckConstraint(_in("category", CATEGORIES), name="ck_complaints_category"),
    CheckConstraint(_in("status", STATUSES), name="ck_complaints_status"),
    CheckConstraint(
        "payment_method IS NULL OR " + _in("payment_method", PAYMENT_METHODS),
        name="ck_complaints_payment_method",
    ),
    CheckConstraint(
        "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)", name="ck_complaints_lat"
    ),
    CheckConstraint(
        "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)", name="ck_complaints_lon"
    ),
    Index("ix_complaints_owner", "owner_user_id", "created_at"),
    Index("ix_complaints_status", "status"),
    Index("ix_complaints_category", "category"),
    Index("ix_complaints_created", "created_at"),
    Index("ix_complaints_city", "city"),
)  # fmt: skip

complaint_status_history = Table(
    "complaint_status_history", metadata,
    Column("id", String(36), primary_key=True),
    Column("complaint_id", String(36), ForeignKey("complaints.id", ondelete="RESTRICT"),
           nullable=False),
    Column("from_status", String(16)),
    Column("to_status", String(16), nullable=False),
    Column("actor_user_id", String(36), ForeignKey("users.id", ondelete="RESTRICT")),
    Column("note", String(500)),
    Column("created_at", UTCDateTime, nullable=False),
    CheckConstraint(_in("to_status", STATUSES), name="ck_history_to_status"),
    Index("ix_history_complaint", "complaint_id", "created_at"),
)  # fmt: skip

complaint_evidence = Table(
    "complaint_evidence", metadata,
    Column("id", String(36), primary_key=True),
    Column("complaint_id", String(36), ForeignKey("complaints.id", ondelete="RESTRICT"),
           nullable=False),
    Column("uploader_user_id", String(36), ForeignKey("users.id", ondelete="RESTRICT"),
           nullable=False),
    Column("original_filename", String(120), nullable=False),
    Column("mime_type", String(64), nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("storage_key", String(64), nullable=False, unique=True),
    Column("status", String(16), nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    CheckConstraint(_in("status", EVIDENCE_STATUSES), name="ck_evidence_status"),
    CheckConstraint("size_bytes >= 0", name="ck_evidence_size"),
    Index("ix_evidence_complaint", "complaint_id", "status"),
)  # fmt: skip

analyses = Table(
    "analyses", metadata,
    Column("id", String(36), primary_key=True),
    Column("complaint_id", String(36), ForeignKey("complaints.id", ondelete="RESTRICT"),
           nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    Column("pipeline_version", String(120), nullable=False),
    Column("capabilities", JSON, nullable=False),
    Column("risk_score", Integer),
    Column("risk_level", String(8)),
    Column("classification", String(40)),
    Column("confidence", Numeric(5, 4, asdecimal=False)),
    Column("indicators", JSON, nullable=False),
    Column("explanation", Text),
    Column("recommended_actions", JSON, nullable=False),
    Column("limitations", JSON, nullable=False),
    Column("status", String(16), nullable=False),
    UniqueConstraint("complaint_id", "version", name="uq_analysis_version"),
    CheckConstraint(_in("status", ANALYSIS_STATUSES), name="ck_analysis_status"),
    CheckConstraint(
        "risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 100)", name="ck_analysis_score"
    ),
    CheckConstraint(
        "risk_level IS NULL OR " + _in("risk_level", RISK_LEVELS), name="ck_analysis_level"
    ),
    Index("ix_analysis_level", "risk_level"),
)  # fmt: skip

alerts = Table(
    "alerts", metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
    Column("complaint_id", String(36), ForeignKey("complaints.id", ondelete="RESTRICT")),
    Column("kind", String(32), nullable=False),
    Column("message", String(300), nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    Column("read_at", UTCDateTime),
    Index("ix_alerts_user", "user_id", "read_at", "created_at"),
)  # fmt: skip

admin_audit = Table(
    "admin_audit", metadata,
    Column("id", String(36), primary_key=True),
    Column("actor_user_id", String(36), ForeignKey("users.id", ondelete="RESTRICT")),
    Column("action", String(40), nullable=False),
    Column("target_user_id", String(36), ForeignKey("users.id", ondelete="RESTRICT")),
    Column("before", JSON, nullable=False),
    Column("after", JSON, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    Index("ix_audit_created", "created_at"),
    Index("ix_audit_target", "target_user_id", "action"),
)  # fmt: skip

# POST /complaints replay protection (24 h). Only a hash of the client key is stored.
idempotency_keys = Table(
    "idempotency_keys", metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
    Column("key_hash", String(64), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("complaint_id", String(36), ForeignKey("complaints.id", ondelete="RESTRICT"),
           nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    UniqueConstraint("user_id", "key_hash", name="uq_idempotency_user_key"),
    Index("ix_idempotency_created", "created_at"),
)  # fmt: skip
