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


def _float(name: str, default: float, lo: float, hi: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc
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
    auth_mode: str = "clerk"  # "clerk" (default) | "demo" | "local" (demo + local sign-in codes)
    clerk_issuer: str | None = None
    clerk_jwks_url: str | None = None
    clerk_authorized_parties: tuple[str, ...] = ()
    clerk_audience: str | None = None
    clerk_secret_key: str | None = field(default=None, repr=False)
    clerk_backend_lookup: bool = False
    # publishable (browser) key, only echoed by GET /auth/config so the console can load Clerk
    clerk_publishable_key: str | None = None
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
    rate_analyze_per_min: int = 10
    ollama_url: str | None = None  # local Ollama server; None = LLM explanation not configured
    ollama_model: str = "qwen2.5:0.5b"  # qwen2.5:3b preferred when the hardware allows
    ollama_timeout_s: float = 8.0
    # TEST ONLY: also append local sign-in codes to this file (end-to-end tests read it). Refused
    # in clerk mode; never set it outside an automated test run.
    local_auth_outbox: Path | None = None

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
            clerk_publishable_key=(os.getenv("CLERK_PUBLISHABLE_KEY") or "").strip() or None,
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
            rate_analyze_per_min=_int("COMPLAINTS_RATE_ANALYZE_PER_MIN", 10),
            # COMPLAINTS_OLLAMA_URL (set by `make demo` when a local Ollama answers) wins over the
            # shared OLLAMA_URL, so the complaints explainer can be enabled on its own
            ollama_url=(os.getenv("COMPLAINTS_OLLAMA_URL") or os.getenv("OLLAMA_URL") or "")
            .strip()
            .rstrip("/")
            or None,
            ollama_model=(os.getenv("OLLAMA_MODEL") or "").strip() or "qwen2.5:0.5b",
            ollama_timeout_s=_float("OLLAMA_TIMEOUT_S", 8.0, 1.0, 120.0),
            local_auth_outbox=(
                Path(os.environ["COMPLAINTS_LOCAL_AUTH_OUTBOX"].strip())
                if (os.getenv("COMPLAINTS_LOCAL_AUTH_OUTBOX") or "").strip()
                else None
            ),
        )

    @property
    def local_auth_enabled(self) -> bool:
        """Email + one-time-code sign-in (development only): demo/local modes, never clerk."""
        return self.auth_mode in ("demo", "local")

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
        elif self.auth_mode in ("demo", "local"):
            if not self.demo_jwt_secret:
                raise ConfigError(
                    "COMPLAINTS_DEMO_JWT_SECRET (or GATEWAY_JWT_SECRET) must be set in demo mode"
                )
            if len(self.demo_jwt_secret.encode()) < MIN_DEMO_SECRET_BYTES:
                raise ConfigError(
                    f"the demo JWT secret must be at least {MIN_DEMO_SECRET_BYTES} bytes"
                )
        else:
            raise ConfigError("COMPLAINTS_AUTH_MODE must be 'clerk', 'demo' or 'local'")
        if self.local_auth_outbox is not None and not self.local_auth_enabled:
            raise ConfigError(
                "COMPLAINTS_LOCAL_AUTH_OUTBOX is a test-only option for local/demo mode"
            )
        if self.clerk_publishable_key is not None and not re.fullmatch(
            r"pk_(test|live)_[A-Za-z0-9+/=_-]{1,200}", self.clerk_publishable_key
        ):
            # never echo a secret key (sk_...) to browsers by mistake
            raise ConfigError("CLERK_PUBLISHABLE_KEY must be a Clerk publishable key (pk_...)")
        if self.ollama_url is not None:
            parts = urlsplit(self.ollama_url)
            if parts.scheme not in ("http", "https") or not parts.hostname or parts.username:
                raise ConfigError("OLLAMA_URL must be an http(s) URL without credentials")
        if any(o == "*" or not o for o in self.cors_origins):
            raise ConfigError("COMPLAINTS_CORS_ORIGINS must list explicit origins, not '*'")
