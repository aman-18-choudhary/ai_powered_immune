"""The complaints service (``services/complaints``) mounted at ``/api/complaints``.

The console on :8080 reaches it same-origin. Storage defaults to a SQLite file and an evidence
directory under ``.data/complaints`` (gitignored); ``COMPLAINTS_DATABASE_URL`` /
``COMPLAINTS_EVIDENCE_DIR`` override them (a Postgres URL works too).

Identity: unless Clerk is configured (``CLERK_ISSUER`` + ``CLERK_JWKS_URL``) or
``COMPLAINTS_AUTH_MODE`` says otherwise, the mounted service runs in ``local`` mode with this
server's own JWT secret: the existing ``/api/auth/demo-login`` tokens are accepted (their ``role``
claim is ignored: every such token is a CITIZEN in the complaints service) and the development-only
email + one-time-code sign-in (``/api/complaints/local-auth/*``, code printed to this terminal) is
on. In ``clerk`` mode the local sign-in is off (404). Roles come only from the complaints ``users``
table (+ ``INVESTIGATOR_ALLOWLIST`` / ``ADMIN_BOOTSTRAP_EMAILS``, read from the environment).
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any

from complaints.api import UPLOAD_OVERHEAD
from complaints.api import create_app as create_complaints_app
from complaints.config import ConfigError as ComplaintsConfigError
from complaints.config import Settings as ComplaintsSettings
from fastapi import FastAPI

from .config import ConfigError, Settings

PREFIX = "/api/complaints"
UPLOAD_PATH = re.compile(r"^/api/complaints/complaints/[^/]{1,64}/evidence$")
AUTH_MODES = ("clerk", "demo", "local")


def complaints_settings(cfg: Settings) -> ComplaintsSettings:
    """Complaints settings for the mounted app: env for everything the service itself reads
    (allowlist, bootstrap, Clerk, limits), with storage and auth mode decided here."""
    base = ComplaintsSettings.from_env()
    if cfg.complaints_database_url:
        db_url = cfg.complaints_database_url
    else:
        db_url = f"sqlite:///{cfg.ensure_work_dir() / 'complaints' / 'complaints.db'}"
    if db_url.startswith("sqlite:///") and ":memory:" not in db_url:
        Path(db_url.removeprefix("sqlite:///")).parent.mkdir(
            mode=0o700, parents=True, exist_ok=True
        )
    evidence = cfg.complaints_evidence_dir or (cfg.ensure_work_dir() / "complaints" / "evidence")
    mode = cfg.complaints_auth_mode
    if mode is None:
        mode = "clerk" if base.clerk_issuer and base.clerk_jwks_url else "local"
    if mode not in AUTH_MODES:
        raise ConfigError(f"COMPLAINTS_AUTH_MODE must be one of {', '.join(AUTH_MODES)}")
    return dataclasses.replace(
        base,
        database_url=db_url,
        evidence_dir=evidence,
        auth_mode=mode,
        # demo mode verifies this server's own HS256 tokens (role claim ignored)
        demo_jwt_secret=cfg.jwt_secret if mode != "clerk" else base.demo_jwt_secret,
        cors_origins=(),  # the demo server's CORS middleware is in front
    )


def build(
    cfg: Settings, settings: ComplaintsSettings | None = None, *, stt_engine: Any = None
) -> FastAPI:
    """``stt_engine``: the live-audio speech-to-text engine object already created by this server;
    the complaints service uses the SAME object for audio evidence, so one Whisper model is in
    memory (it is loaded on first use by either feature)."""
    s = settings or complaints_settings(cfg)
    try:
        return create_complaints_app(s, stt_engine=stt_engine)
    except ComplaintsConfigError as exc:
        raise ConfigError(f"complaints: {exc}") from exc


def upload_cap(settings: ComplaintsSettings) -> int:
    """Body cap for evidence uploads at the demo server's edge: the complaints service's own
    largest per-file cap plus multipart overhead (the service enforces the exact caps)."""
    return max(settings.max_image_bytes, settings.max_audio_bytes) + UPLOAD_OVERHEAD
