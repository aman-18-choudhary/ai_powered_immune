"""Environment-driven settings (read once at app creation)."""

from __future__ import annotations

import logging
import os
import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("demo_server")

MIN_SECRET_BYTES = 32
DEFAULT_CORS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
)
ROLES = ("officer", "analyst", "citizen", "admin")
DEFAULT_RATE_LIMITS = {"citizen": 30, "analyst": 300, "officer": 300, "admin": 600, "anon": 60}


class ConfigError(RuntimeError):
    pass


def _int(env: dict[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except ValueError as e:
        raise ConfigError(f"{name} must be an integer") from e
    if not lo <= v <= hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}")
    return v


@dataclass
class Settings:
    jwt_secret: str
    cors_origins: tuple[str, ...] = DEFAULT_CORS
    static_dir: Path | None = None
    login_code: str | None = None
    max_body_bytes: int = 64 * 1024
    history_size: int = 5000
    client_queue_size: int = 256
    max_ws_clients: int = 50
    ws_max_message_bytes: int = 1024
    ws_send_timeout_s: float = 5.0
    snapshot_events: int = 200
    runs_kept: int = 20
    rate_limits: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_RATE_LIMITS))
    rate_window_s: int = 60
    verify_timeout_s: float = 20.0
    verify_output_cap: int = 64 * 1024
    work_dir: Path | None = None
    secret_generated: bool = False
    trust_proxy: bool = False

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        e = dict(os.environ if env is None else env)
        secret = e.get("DEMO_JWT_SECRET", "")
        generated = False
        if secret:
            if len(secret.encode()) < MIN_SECRET_BYTES:
                raise ConfigError(f"DEMO_JWT_SECRET must be at least {MIN_SECRET_BYTES} bytes")
        else:
            secret = secrets.token_urlsafe(48)
            generated = True
            log.warning(
                "DEMO_JWT_SECRET not set: using a random per-process secret "
                "(tokens stop working on restart; demo only)"
            )
        origins = tuple(
            o.strip().rstrip("/") for o in e.get("DEMO_CORS_ORIGINS", "").split(",") if o.strip()
        )
        if "*" in origins:
            raise ConfigError("DEMO_CORS_ORIGINS must list explicit origins, not '*'")
        static = e.get("DEMO_STATIC_DIR")
        work = e.get("DEMO_WORK_DIR")
        return cls(
            jwt_secret=secret,
            cors_origins=origins or DEFAULT_CORS,
            static_dir=Path(static) if static else None,
            login_code=e.get("DEMO_LOGIN_CODE") or None,
            history_size=_int(e, "DEMO_HISTORY_SIZE", 5000, 100, 100_000),
            client_queue_size=_int(e, "DEMO_CLIENT_QUEUE", 256, 8, 10_000),
            max_ws_clients=_int(e, "DEMO_MAX_WS_CLIENTS", 50, 1, 1000),
            work_dir=Path(work) if work else None,
            secret_generated=generated,
            trust_proxy=e.get("DEMO_TRUST_PROXY") == "1",
        )

    def ensure_work_dir(self) -> Path:
        if self.work_dir is None:
            self.work_dir = Path(tempfile.mkdtemp(prefix="demo-server-"))
        self.work_dir.mkdir(parents=True, exist_ok=True)
        return self.work_dir
