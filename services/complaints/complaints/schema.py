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
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.types import TypeDecorator

ROLES = ("CITIZEN", "INVESTIGATOR", "ADMIN")
ROLE_SOURCES = ("bootstrap", "allowlist", "admin")
CATEGORIES = (
    "DIGITAL_ARREST", "UPI_FRAUD", "PHISHING", "OTP_SCAM", "BANK_FRAUD",
    "INVESTMENT_SCAM", "JOB_SCAM", "SOCIAL_MEDIA_FRAUD", "OTHER",
)  # fmt: skip
PAYMENT_METHODS = ("UPI", "CARD", "NETBANKING", "WALLET", "CASH", "BANK_TRANSFER", "OTHER")
STATUSES = ("SUBMITTED", "UNDER_REVIEW", "INVESTIGATING", "RESOLVED", "REJECTED")
# "deleted": removed by the complaint owner (while SUBMITTED) or an admin; the file is gone and the
# row stays for the audit trail (migration 5 widens the CHECK on an existing database)
EVIDENCE_STATUSES = ("stored", "rejected", "deleted")
# INSUFFICIENT_INPUT: migration 7 widens the CHECK (and the column on Postgres)
ANALYSIS_STATUSES = ("COMPLETED", "PARTIAL", "UNAVAILABLE", "INSUFFICIENT_INPUT")
# CRITICAL: migration 6 widens the CHECK on an existing database (before it, CRITICAL was stored
# as HIGH with the exact level in ``analyses.detail``; the migration backfills those rows)
RISK_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
# migration 9: only definitive transcription outcomes are stored (transient ones such as a missing
# engine or a timeout are reported per analysis run and retried; see complaints.transcribe)
TRANSCRIPT_STATUSES = ("transcribed", "truncated", "decode_failed", "unsupported_format")
# migration 10 (complaint network): identifier types and investigator review decisions
ENTITY_TYPES = ("phone", "upi", "email", "url", "domain", "bank_account", "social_account")
LINK_DECISIONS = ("confirmed", "rejected")
LINK_BASES = ("observed", "inferred")


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

# Versioned migrations (``complaints.migrations``): one row per applied version. Part of
# ``metadata`` so a test ``drop_all`` also forgets the applied versions.
schema_migrations = Table(
    "schema_migrations", metadata,
    Column("version", Integer, primary_key=True, autoincrement=False),
    Column("name", String(100), nullable=False),
    Column("applied_at", UTCDateTime, nullable=False),
)  # fmt: skip

# Columns added after the first release carry a comment naming the migration that adds them to an
# existing database (``complaints.migrations``); ``create_all`` creates them on a fresh one.
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
    # how the current INVESTIGATOR/ADMIN role was granted: bootstrap | allowlist | admin
    Column("role_source", String(16)),
    # migration 4: privacy setting (stored now; similarity/network matching honours it in a later
    # phase) and whether the display name was chosen by the user (sign-in then keeps it)
    Column("allow_case_matching", Boolean, nullable=False, default=True, server_default=true()),
    Column("display_name_set", Boolean, nullable=False, default=False, server_default=false()),
    CheckConstraint(_in("role", ROLES), name="ck_users_role"),
    CheckConstraint(
        "role_source IS NULL OR " + _in("role_source", ROLE_SOURCES), name="ck_users_role_source"
    ),
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
    # migration 3: optional bank details of the suspect / beneficiary account
    Column("bank_name", String(100)),
    Column("bank_account", String(18)),  # digits only, 6-18
    Column("ifsc", String(11)),  # ^[A-Z]{4}0[A-Z0-9]{6}$
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
    Index("ix_complaints_bank_account", "bank_account"),  # migration 3
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
    Column("deleted_at", UTCDateTime),  # migration 5
    # migration 13: malware scan before storing (clean / not_scanned); NULL = no scanner then
    Column("scan_status", String(16)),
    CheckConstraint(_in("status", EVIDENCE_STATUSES), name="ck_evidence_status"),
    CheckConstraint("size_bytes >= 0", name="ck_evidence_size"),
    Index("ix_evidence_complaint", "complaint_id", "status"),
)  # fmt: skip

# migration 9: one transcript per stored audio evidence file (written once, reused by every later
# analysis). Same access as the evidence (owner + staff); deleted with the evidence. Segment text
# is user content: it is never logged.
evidence_transcripts = Table(
    "evidence_transcripts", metadata,
    Column("id", String(36), primary_key=True),
    Column("evidence_id", String(36), ForeignKey("complaint_evidence.id", ondelete="RESTRICT"),
           nullable=False, unique=True),
    Column("complaint_id", String(36), ForeignKey("complaints.id", ondelete="RESTRICT"),
           nullable=False),
    Column("status", String(24), nullable=False),
    Column("engine", String(32)),
    Column("model", String(64)),
    Column("language", String(8)),
    Column("duration_s", Float),
    Column("transcribed_s", Float),
    Column("truncated", Boolean, nullable=False, default=False),
    Column("segments", JSON, nullable=False),  # [{start_s, end_s, text}]
    Column("error_reason", String(64)),
    Column("created_at", UTCDateTime, nullable=False),
    CheckConstraint(_in("status", TRANSCRIPT_STATUSES), name="ck_transcript_status"),
    Index("ix_transcripts_complaint", "complaint_id"),
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
    Column("status", String(24), nullable=False),  # migration 7 widens 16 -> 24 on Postgres
    # pipeline v2 extras (stages, reasons, URL/entity detail, LLM text). Added after the first
    # release: NULL for rows written by v1; migration 2 adds the column to an existing table.
    Column("detail", JSON),
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
    # migration 8: level of a HIGH_RISK_ANALYSIS alert (one alert per complaint, kind and level)
    Column("risk_level", String(8)),
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

# migration 10: the identifiers of each complaint, taken ONLY from its structured fields
# (``complaints.network``). ``value_norm`` is the normalised value (investigators need it for
# matching); ``display`` is the masked form returned by the network API; ``value_hash`` (sha256 of
# type + value) is the indexed join key because a URL can be longer than a Postgres b-tree entry
# allows. Written with the complaint; rows of soft-deleted complaints stay but are never linked.
complaint_entities = Table(
    "complaint_entities", metadata,
    Column("id", String(36), primary_key=True),
    Column("complaint_id", String(36), ForeignKey("complaints.id", ondelete="RESTRICT"),
           nullable=False),
    Column("entity_type", String(16), nullable=False),
    Column("value_norm", String(2048), nullable=False),
    Column("value_hash", String(64), nullable=False),
    Column("display", String(200), nullable=False),
    Column("source_field", String(32), nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    UniqueConstraint("complaint_id", "entity_type", "value_hash", name="uq_entity_per_complaint"),
    CheckConstraint(_in("entity_type", ENTITY_TYPES), name="ck_entity_type"),
    Index("ix_entities_value", "entity_type", "value_hash"),
    Index("ix_entities_complaint", "complaint_id"),
)  # fmt: skip

# migration 10: an investigator's review of a pair of complaints (ordered: complaint_low <
# complaint_high). Every review is kept; the one with the highest ``seq`` for a pair is current.
# ``basis`` records the relation the pair had when it was reviewed (observed or inferred).
link_reviews = Table(
    "link_reviews", metadata,
    Column("id", String(36), primary_key=True),
    Column("complaint_low", String(36), ForeignKey("complaints.id", ondelete="RESTRICT"),
           nullable=False),
    Column("complaint_high", String(36), ForeignKey("complaints.id", ondelete="RESTRICT"),
           nullable=False),
    Column("seq", Integer, nullable=False),
    Column("decision", String(16), nullable=False),
    Column("basis", String(16), nullable=False),
    Column("reviewer_user_id", String(36), ForeignKey("users.id", ondelete="RESTRICT"),
           nullable=False),
    Column("note", String(500)),
    Column("created_at", UTCDateTime, nullable=False),
    UniqueConstraint("complaint_low", "complaint_high", "seq", name="uq_link_review_seq"),
    CheckConstraint(_in("decision", LINK_DECISIONS), name="ck_link_review_decision"),
    CheckConstraint(_in("basis", LINK_BASES), name="ck_link_review_basis"),
    CheckConstraint("complaint_low < complaint_high", name="ck_link_review_order"),
    Index("ix_link_reviews_high", "complaint_high"),
)  # fmt: skip

# migration 12 (Phase 6, OCR): text read from one image evidence file by Tesseract, stored once
# and reused by later analyses; deleted with the evidence. The text is user content (never logged).
evidence_ocr = Table(
    "evidence_ocr", metadata,
    Column("id", String(36), primary_key=True),
    Column("evidence_id", String(36), ForeignKey("complaint_evidence.id", ondelete="RESTRICT"),
           nullable=False, unique=True),
    Column("complaint_id", String(36), ForeignKey("complaints.id", ondelete="RESTRICT"),
           nullable=False),
    Column("status", String(24), nullable=False),
    Column("engine", String(64)),
    Column("languages", String(32)),
    Column("mean_confidence", Float),
    Column("text", Text, nullable=False, default=""),
    Column("created_at", UTCDateTime, nullable=False),
    Index("ix_ocr_complaint", "complaint_id"),
)  # fmt: skip

# migration 11 (Phase 12): investigators' working notes on a complaint. Staff only (never shown
# to the complainant, unlike status-history notes); append-only; the text is user content and is
# never logged or put in the audit log.
investigation_notes = Table(
    "investigation_notes", metadata,
    Column("id", String(36), primary_key=True),
    Column("complaint_id", String(36), ForeignKey("complaints.id", ondelete="RESTRICT"),
           nullable=False),
    Column("author_user_id", String(36), ForeignKey("users.id", ondelete="RESTRICT"),
           nullable=False),
    Column("body", Text, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    Index("ix_notes_complaint", "complaint_id", "created_at"),
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
