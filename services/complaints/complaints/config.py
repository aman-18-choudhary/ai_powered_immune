"""Service configuration (environment only; secrets never appear in ``repr``).

The service refuses to start (``ConfigError``) when required identity settings are missing: there
is no insecure fallback mode.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

MIB = 1024 * 1024
MIN_DEMO_SECRET_BYTES = 32
_LIST_SPLIT = re.compile(r"[,;\s]+")


class ConfigError(RuntimeError):
    """Configuration is missing or unsafe; the service must not start."""


def parse_list(raw: str | None, *, fold: bool = False) -> tuple[str, ...]:
    items = [i.strip() for i in _LIST_SPLIT.split(raw or "") if i.strip()]
    return tuple(i.casefold() for i in items) if fold else tuple(items)


def _int(name: str, default: int, lo: int = 1, hi: int = 10**9) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}")
    return value


def _https_or_loopback(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return True
    return parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1")


@dataclass(frozen=True)
class Settings:
    database_url: str = ""
    auth_mode: str = "clerk"  # "clerk" (default) | "demo"
    clerk_issuer: str | None = None
    clerk_jwks_url: str | None = None
    clerk_authorized_parties: tuple[str, ...] = ()
    clerk_audience: str | None = None
    clerk_secret_key: str | None = field(default=None, repr=False)
    clerk_backend_lookup: bool = False
    demo_jwt_secret: str | None = field(default=None, repr=False)
    investigator_allowlist: frozenset[str] = frozenset()
    admin_bootstrap_emails: frozenset[str] = frozenset()
    evidence_dir: Path | None = None
    max_image_bytes: int = 10 * MIB  # images and PDFs
    max_audio_bytes: int = 25 * MIB
    max_evidence_per_complaint: int = 10
    max_json_bytes: int = 64 * 1024
    cors_origins: tuple[str, ...] = ()
    rate_write_per_min: int = 30
    rate_upload_per_min: int = 20
    rate_analysis_per_min: int = 10
    rate_read_per_min: int = 240

    @classmethod
    def from_env(cls) -> "Settings":
        evidence = os.getenv("COMPLAINTS_EVIDENCE_DIR", "").strip()
        return cls(
            database_url=os.getenv("COMPLAINTS_DATABASE_URL", "").strip(),
            auth_mode=os.getenv("COMPLAINTS_AUTH_MODE", "clerk").strip().lower() or "clerk",
            clerk_issuer=(os.getenv("CLERK_ISSUER") or "").strip().rstrip("/") or None,
            clerk_jwks_url=(os.getenv("CLERK_JWKS_URL") or "").strip() or None,
            clerk_authorized_parties=parse_list(os.getenv("CLERK_AUTHORIZED_PARTIES")),
            clerk_audience=(os.getenv("CLERK_AUDIENCE") or "").strip() or None,
            clerk_secret_key=os.getenv("CLERK_SECRET_KEY") or None,
            clerk_backend_lookup=os.getenv("CLERK_BACKEND_LOOKUP", "0") == "1",
            demo_jwt_secret=(
                os.getenv("COMPLAINTS_DEMO_JWT_SECRET") or os.getenv("GATEWAY_JWT_SECRET") or None
            ),
            investigator_allowlist=frozenset(
                parse_list(os.getenv("INVESTIGATOR_ALLOWLIST"), fold=True)
            ),
            admin_bootstrap_emails=frozenset(
                parse_list(os.getenv("ADMIN_BOOTSTRAP_EMAILS"), fold=True)
            ),
            evidence_dir=Path(evidence) if evidence else None,
            max_image_bytes=_int("COMPLAINTS_MAX_IMAGE_BYTES", 10 * MIB),
            max_audio_bytes=_int("COMPLAINTS_MAX_AUDIO_BYTES", 25 * MIB),
            max_evidence_per_complaint=_int("COMPLAINTS_MAX_EVIDENCE_PER_COMPLAINT", 10, 1, 100),
            max_json_bytes=_int("COMPLAINTS_MAX_JSON_BYTES", 64 * 1024, 1024, 4 * MIB),
            cors_origins=parse_list(os.getenv("COMPLAINTS_CORS_ORIGINS")),
            rate_write_per_min=_int("COMPLAINTS_RATE_WRITE_PER_MIN", 30),
            rate_upload_per_min=_int("COMPLAINTS_RATE_UPLOAD_PER_MIN", 20),
            rate_analysis_per_min=_int("COMPLAINTS_RATE_ANALYSIS_PER_MIN", 10),
            rate_read_per_min=_int("COMPLAINTS_RATE_READ_PER_MIN", 240),
        )

    def validate(self) -> None:
        if not self.database_url:
            raise ConfigError("COMPLAINTS_DATABASE_URL must be set")
        if self.evidence_dir is None:
            raise ConfigError("COMPLAINTS_EVIDENCE_DIR must be set")
        if self.auth_mode == "clerk":
            if not self.clerk_issuer:
                raise ConfigError("CLERK_ISSUER must be set (COMPLAINTS_AUTH_MODE=clerk)")
            if not self.clerk_jwks_url:
                raise ConfigError("CLERK_JWKS_URL must be set (COMPLAINTS_AUTH_MODE=clerk)")
            if not _https_or_loopback(self.clerk_issuer):
                raise ConfigError("CLERK_ISSUER must be an https URL")
            if not _https_or_loopback(self.clerk_jwks_url):
                raise ConfigError("CLERK_JWKS_URL must be an https URL")
            if self.clerk_backend_lookup and not self.clerk_secret_key:
                raise ConfigError("CLERK_BACKEND_LOOKUP=1 needs CLERK_SECRET_KEY")
        elif self.auth_mode == "demo":
            if not self.demo_jwt_secret:
                raise ConfigError(
                    "COMPLAINTS_DEMO_JWT_SECRET (or GATEWAY_JWT_SECRET) must be set in demo mode"
                )
            if len(self.demo_jwt_secret.encode()) < MIN_DEMO_SECRET_BYTES:
                raise ConfigError(
                    f"the demo JWT secret must be at least {MIN_DEMO_SECRET_BYTES} bytes"
                )
        else:
            raise ConfigError("COMPLAINTS_AUTH_MODE must be 'clerk' or 'demo'")
        if any(o == "*" or not o for o in self.cors_origins):
            raise ConfigError("COMPLAINTS_CORS_ORIGINS must list explicit origins, not '*'")
