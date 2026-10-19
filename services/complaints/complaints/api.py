"""HTTP API.

Every endpoint except ``/healthz`` and ``/readyz`` requires a verified bearer token; the role is
read ONLY from the internal user row (never from the request or from token claims). The service
does not read gateway ``X-Principal-*`` headers.
"""

import asyncio
import contextlib
import hashlib
import hmac
import importlib.util
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from svckit.health import make_health_router

from .analysis import (
    SCORE_SEMANTICS,
    AnalysisOutcome,
    AnalysisService,
    CallGuardTextAnalysis,
    ComplaintAnalysisPipeline,
    confidence_interpretation,
    stored_level,
)
from .case_api import register_case_routes
from .case_store import CaseStore
from .config import ConfigError, Settings
from .dna import Embedder, FraudDna, SentenceTransformerEmbedder
from .dna_api import register_dna_routes
from .evidence import EvidenceStorage, UploadError, receive_upload
from .explain import OllamaClient
from .geo_api import register_geo_routes
from .geo_store import GeoStore
from .identity import (
    ClerkBackendClient,
    ClerkJwtVerifier,
    DemoVerifier,
    HttpJwksFetcher,
    Identity,
    IdentityError,
    IdentityUnavailable,
    IdentityVerifier,
    normalise_email,
)
from .local_auth import LocalAuth, LocalAuthInvalid, LocalAuthRateLimited, file_outbox
from .network_api import register_network_routes
from .network_store import NetworkStore
from .provenance import legacy_action, legacy_indicator, legacy_provenance
from .schema import CATEGORIES, ROLES, STATUSES
from .store import ComplaintStore, Conflict, NotFound, StoreError
from .transcribe import STATUS_TEXT, TRANSCRIBED, NoEngine, Transcriber, engine_from_env
from .validation import (
    ANALYZE_ALIASES,
    BANK_FIELDS,
    ActiveBody,
    AnalyzeIn,
    ComplaintIn,
    EntryBody,
    LocalStartBody,
    LocalVerifyBody,
    MeUpdate,
    RoleBody,
    StatusBody,
)

log = logging.getLogger("complaints")
access_log = logging.getLogger("complaints.access")

STAFF = frozenset({"INVESTIGATOR", "ADMIN"})
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
MAX_PAGE = 100
MAX_OFFSET = 100_000
FUTURE_SKEW = timedelta(seconds=60)
EARLIEST = datetime(2000, 1, 1, tzinfo=UTC)
UPLOAD_OVERHEAD = 64 * 1024

INVESTIGATOR_DENIED = {
    "error": "investigator_not_approved",
    "message": (
        "Investigator access is limited to approved accounts. You can continue as a citizen."
    ),
}

CAPABILITIES: dict[str, list[str]] = {
    "CITIZEN": [
        "complaints:create", "complaints:read_own", "complaints:delete_own_submitted",
        "evidence:upload_own", "evidence:read_own", "analysis:run_own", "analysis:read_own",
        "analysis:stateless", "alerts:read_own", "stats:read_own",
    ],
    "INVESTIGATOR": [
        "complaints:read_all", "complaints:update_status", "evidence:read_all",
        "analysis:run_all", "analysis:read_all", "analysis:stateless", "alerts:read_own",
        "stats:read_own", "network:read", "network:review",
    ],
    "ADMIN": [
        "complaints:read_all", "complaints:update_status", "evidence:read_all",
        "analysis:run_all", "analysis:read_all", "analysis:stateless", "alerts:read_own",
        "stats:read_own", "network:read", "network:review", "admin:users", "admin:roles",
        "admin:audit", "admin:health",
    ],
}  # fmt: skip


class ApiError(Exception):
    def __init__(
        self, status: int, code: str, message: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(code)
        self.status, self.code, self.message, self.headers = status, code, message, headers or {}


class BodyTooLarge(HTTPException):
    """Raised from the ASGI receive wrapper. An HTTPException subclass so FastAPI's body parsing
    lets it through (it turns any other error into a 400)."""

    def __init__(self) -> None:
        super().__init__(status_code=413)


def _err(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> Response:
    return JSONResponse({"error": code, "message": message}, status_code=status, headers=headers)


@dataclass(frozen=True)
class CurrentUser:
    id: str
    role: str
    email: str | None
    email_verified: bool
    display_name: str | None
    is_active: bool
    uid_hash: str
    notice: dict[str, str] | None = None  # e.g. {"investigator_access": "lapsed", "message": ...}

    @property
    def staff(self) -> bool:
        return self.role in STAFF


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat() if dt else None


class RateLimiter:
    """Per-process fixed-window limiter keyed by (kind, user). Fails open on any error."""

    def __init__(self, limits: dict[str, int], clock: Callable[[], float] = time.monotonic) -> None:
        self._limits, self._clock = limits, clock
        self._hits: dict[tuple[str, str], tuple[int, int]] = {}

    def hit(self, kind: str, user_id: str) -> int | None:
        """None when allowed, else seconds to wait."""
        try:
            window = 60
            now = self._clock()
            slot = int(now // window)
            if len(self._hits) > 20_000:
                self._hits = {k: v for k, v in self._hits.items() if v[0] == slot}
            cur_slot, count = self._hits.get((kind, user_id), (slot, 0))
            if cur_slot != slot:
                count = 0
            count += 1
            self._hits[(kind, user_id)] = (slot, count)
            if count > self._limits[kind]:
                return max(1, int((slot + 1) * window - now))
        except Exception:  # fail open
            log.warning("rate limiter error; allowing request")
        return None


class HttpGuard:
    """Pure ASGI middleware: request id, body-size cap, security headers, minimal access log."""

    def __init__(self, app: ASGIApp, *, max_json: int, max_upload: int) -> None:
        self.app, self.max_json, self.max_upload = app, max_json, max_upload

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        rid = raw.get("x-request-id", "")
        if not REQUEST_ID_RE.fullmatch(rid):
            rid = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = rid
        is_upload = scope["method"] == "POST" and scope["path"].endswith("/evidence")
        limit = self.max_upload if is_upload else self.max_json
        t0 = time.monotonic()
        status = {"v": 500}

        async def send_w(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["v"] = message["status"]
                headers = list(message.get("headers", []))
                skip = {b"x-request-id", b"cache-control", b"x-content-type-options"}
                headers = [(k, v) for k, v in headers if k.lower() not in skip]
                headers += [
                    (b"x-request-id", rid.encode()),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
                ]
                message = {**message, "headers": headers}
            await send(message)

        declared = raw.get("content-length", "")
        count = 0

        async def receive_w() -> Message:
            nonlocal count
            msg = await receive()
            if msg["type"] == "http.request":
                count += len(msg.get("body", b""))
                if count > limit:
                    raise BodyTooLarge
            return msg

        try:
            if declared.isdigit() and int(declared) > limit:
                resp = _err(413, "payload_too_large", "The request body is too large.")
                await resp(scope, receive, send_w)
            else:
                await self.app(scope, receive_w, send_w)
        finally:
            route = scope.get("route")
            uid = scope.get("state", {}).get("uid_hash", "-")
            access_log.info(
                "method=%s route=%s status=%d rid=%s uid=%s ms=%d",
                scope["method"], getattr(route, "path", "unmatched"), status["v"], rid, uid,
                int((time.monotonic() - t0) * 1000),
            )  # fmt: skip


def complaint_view(
    c: dict[str, Any], *, staff: bool, detail: bool = False, **extra: Any
) -> dict[str, Any]:
    out = {
        "id": c["id"], "category": c["category"], "title": c["title"],
        "description": c["description"], "status": c["status"],
        "incident_at": _iso(c["incident_at"]), "reported_at": _iso(c["reported_at"]),
        "amount": format(c["amount"], "f") if c["amount"] is not None else None,
        "currency": c["currency"], "payment_method": c["payment_method"], "upi_id": c["upi_id"],
        "utr_reference": c["utr_reference"], "suspect_phone": c["suspect_phone"],
        "suspect_email": c["suspect_email"], "suspicious_url": c["suspicious_url"],
        "location_text": c["location_text"], "city": c["city"], "state": c["state"],
        "latitude": c["latitude"], "longitude": c["longitude"],
        "social_platform": c["social_platform"], "social_account": c["social_account"],
        # bank details: only ever returned to the owner and to staff (like every other field
        # here; no complaint field is masked in list views)
        "bank_name": c.get("bank_name"), "bank_account": c.get("bank_account"),
        "ifsc": c.get("ifsc"),
        "created_at": _iso(c["created_at"]), "updated_at": _iso(c["updated_at"]),
    }  # fmt: skip
    if staff:
        out["owner_user_id"] = c["owner_user_id"]
    out.update(extra)
    return out


def _history_view(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "from_status": r["from_status"], "to_status": r["to_status"],
            "actor_user_id": r["actor_user_id"], "note": r["note"],
            "created_at": _iso(r["created_at"]),
        }
        for r in rows
    ]  # fmt: skip


def _effective_level(a: dict[str, Any]) -> str | None:
    """The exact level. Rows from before migration 6 stored CRITICAL as HIGH and kept the exact
    level in ``detail`` (the migration backfills them; ``detail`` still wins for safety)."""
    d = a.get("detail") or {}
    return d.get("risk_level") or a["risk_level"]


def _assessment(d: dict[str, Any] | None, row: dict[str, Any] | None) -> tuple[str | None, bool]:
    """(assessment, derived). Analyses stored before 2026-10-18 have none: it is derived from the
    stored status, level and indicators with the same rules as new analyses, so an old level that
    rested on the removed category prior is not shown as "strong scam indicators"."""
    stored = (d or {}).get("assessment")
    if stored or row is None or "status" not in row:
        return stored, False
    level = _effective_level(row)
    if row.get("status") == "INSUFFICIENT_INPUT":
        return "insufficient_evidence", True
    if row.get("status") == "UNAVAILABLE" or level is None:
        return "not_assessed", True
    srcs = {legacy_indicator(i).get("source") for i in row.get("indicators") or []
            if isinstance(i, dict)}  # fmt: skip
    if not srcs & {"rule", "ml"}:
        return "no_indicators_found", True
    if "rule" not in srcs:
        return "model_prediction_only", True
    return "indicators_found", True


def _analysis_summary(a: dict[str, Any] | None) -> dict[str, Any] | None:
    if a is None:
        return None
    return {
        "version": a["version"], "created_at": _iso(a["created_at"]), "status": a["status"],
        "risk_score": a["risk_score"], "risk_level": _effective_level(a),
        "score_semantics": SCORE_SEMANTICS,
        # indicators_found | model_prediction_only | no_indicators_found | insufficient_evidence |
        # not_assessed; None for analyses stored before 2026-10-18
        "assessment": _assessment(a.get("detail"), a)[0],
        "assessment_derived": _assessment(a.get("detail"), a)[1],
    }  # fmt: skip


def _version_view(a: dict[str, Any]) -> dict[str, Any]:
    """One entry of the analysis version list."""
    prov = (a.get("detail") or {}).get("provenance") or legacy_provenance(a)
    return {
        **(_analysis_summary(a) or {}), "id": a["id"], "pipeline_version": a["pipeline_version"],
        "classification": a["classification"], "confidence": a["confidence"],
        "model_versions": prov.get("model_versions"),
        "provenance_derived_from_legacy": prov.get("derived_from_legacy", False),
    }  # fmt: skip


# Rows written before audio evidence was transcribed (Phase 6) stored the inaccurate reason
# "no_transcription_engine_installed" (faster-whisper was installed, just not wired to evidence).
# They are shown with an accurate reason; the stored row itself is not changed.
_LEGACY_AUDIO_REASON = "no_transcription_engine_installed"
_LEGACY_AUDIO_TEXT = "audio transcription of evidence was not implemented when this analysis ran"


def _legacy_audio(d: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    reasons = dict(d.get("capability_reasons") or {})
    files = [dict(a) for a in d.get("unanalysed_attachments") or []]
    if reasons.get("audio_transcription") == _LEGACY_AUDIO_REASON:
        reasons["audio_transcription"] = "not_implemented_at_analysis_time"
        for a in files:
            if a.get("type") == "audio" and "status" not in a:
                a.update(reason=_LEGACY_AUDIO_TEXT, status="not_implemented_at_analysis_time")
    return reasons, files


def _detail_fields(d: dict[str, Any] | None, row: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pipeline v2 fields; defaults for rows written by v1 (additive API fields only). Rows
    stored before provenance existed get a derived provenance (``derived_from_legacy``)."""
    d = d or {}
    cap_reasons, unanalysed = _legacy_audio(d)
    prov = d.get("provenance")
    if prov is None and row is not None:
        prov = legacy_provenance(row)
    conf_int = d.get("confidence_interpretation")
    if conf_int is None and row is not None and row.get("confidence") is not None:
        conf_int = confidence_interpretation(row["confidence"], d.get("category_model"))
    parts = d.get("explanation_parts")
    if parts is None and row is not None and row.get("explanation"):
        parts = [{"text": row["explanation"], "source": "rule", "method": "explanation_template",
                  "derived_from_legacy": True}]  # fmt: skip
    return {
        # derived from stored status, level and indicators for analyses stored before 2026-10-18
        "assessment": _assessment(d, row)[0],
        "assessment_derived": _assessment(d, row)[1],
        "explanation_source": d.get("explanation_source"),
        "explanation_parts": parts or [],
        "llm_explanation": d.get("llm_explanation"),
        "llm_model": d.get("llm_model"),
        "confidence_semantics": d.get("confidence_semantics"),
        "confidence_interpretation": conf_int,
        "capability_reasons": cap_reasons,
        "unavailable_capabilities": d.get("unavailable_capabilities") or [],
        "unanalysed_attachments": unanalysed,
        "audio_transcription": d.get("audio_transcription") or [],
        "category_model": d.get("category_model"),
        "url_analysis": d.get("url_analysis"),
        "links_in_text": d.get("links_in_text") or [],
        "entity_checks": d.get("entity_checks"),
        "context": d.get("context"),
        "score_components": d.get("score_components"),
        "provenance": prov,
    }


def _analysis_view(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": a["id"], "complaint_id": a["complaint_id"], "version": a["version"],
        "created_at": _iso(a["created_at"]), "pipeline_version": a["pipeline_version"],
        "status": a["status"], "capabilities": a["capabilities"], "risk_score": a["risk_score"],
        "risk_level": _effective_level(a), "score_semantics": SCORE_SEMANTICS,
        "classification": a["classification"], "confidence": a["confidence"],
        "indicators": [legacy_indicator(i) for i in a["indicators"] or []],
        "explanation": a["explanation"],
        "recommended_actions": [legacy_action(x) for x in a["recommended_actions"] or []],
        "limitations": a["limitations"],
        **_detail_fields(a.get("detail"), a),
    }  # fmt: skip


def _outcome_view(out: AnalysisOutcome, created_at: datetime) -> dict[str, Any]:
    """The same structure for an analysis that is not stored (``POST /analyze``)."""
    return {
        "pipeline_version": out.pipeline_version, "status": out.status,
        "capabilities": out.capabilities, "risk_score": out.risk_score,
        "risk_level": out.risk_level, "score_semantics": SCORE_SEMANTICS,
        "classification": out.classification, "confidence": out.confidence,
        "indicators": [legacy_indicator(i) for i in out.indicators],
        "explanation": out.explanation,
        "recommended_actions": [legacy_action(x) for x in out.recommended_actions],
        "limitations": out.limitations,
        **_detail_fields(
            out.extra or None,
            {"pipeline_version": out.pipeline_version, "capabilities": out.capabilities,
             "confidence": out.confidence, "explanation": out.explanation},
        ),
        "created_at": _iso(created_at), "persisted": False,
    }  # fmt: skip


_AUDIO_HERE = (
    "Audio recordings are analysed only when they are attached to a complaint: they are "
    "transcribed locally when the complaint analysis runs. This check takes text and the "
    "optional fields only."
)
_SCREENSHOT_HERE = (
    "Screenshots are not analysed: no OCR engine is installed on this server. This check takes "
    "text and the optional fields only."
)


def _input_capabilities(c: dict[str, Any], out: AnalysisOutcome) -> dict[str, Any]:
    """What was done with each input of ``POST /analyze``."""
    caps = out.capabilities

    def given(k: str) -> bool:
        return c.get(k) not in (None, "")

    txt = (caps.get("text_risk_scorer"), caps.get("category_classifier"))
    if "skipped_insufficient_input" in txt:
        text = {"status": "insufficient_input", "reason": "too few words to analyse"}
    elif "available" in txt:
        stages = ((out.extra or {}).get("provenance") or {}).get("stages") or []
        text = {
            "status": "analysed",
            "methods": [
                x["method"] for x in stages
                if x["status"] == "ran"
                and x["stage"] in ("text_rules", "script_classifier", "category_classifier")
            ],
        }  # fmt: skip
    else:
        text = {"status": "unavailable", "reason": "the text models are not available"}
    url_state = (out.extra or {}).get("url_analysis") or {}
    out_caps: dict[str, Any] = {
        "text": text,
        "url": {"status": "not_provided"}
        if not given("suspicious_url")
        else {
            "status": "analysed" if url_state.get("status") == "analysed" else "unparseable",
            "method": "url_heuristics",
            "note": "offline lexical checks; the page is not opened or looked up",
        },  # fmt: skip
    }
    for name, key in (("upi_id", "upi_id"), ("phone", "suspect_phone"),
                      ("email", "suspect_email")):  # fmt: skip
        out_caps[name] = {"status": "not_provided"} if not given(key) else {
            "status": "format_checked", "method": "entity_checks",
            "note": "format check only; carries no risk weight; no reputation lookup",
        }  # fmt: skip
    for name in ("amount", "payment_method"):
        out_caps[name] = {"status": "not_provided"} if not given(name) else {
            "status": "context_only", "note": "recorded as context; does not change the score",
        }  # fmt: skip
    out_caps["audio"] = {"status": "not_accepted_here", "reason": _AUDIO_HERE,
                         "where": "complaint_evidence"}  # fmt: skip
    out_caps["screenshot"] = {"status": "unavailable", "reason": _SCREENSHOT_HERE,
                              "cause": "no_ocr_engine_installed"}  # fmt: skip
    return out_caps


def _kind(mime: str) -> str:
    return (
        "audio" if mime.startswith("audio/") else "image" if mime.startswith("image/")
        else "document" if mime == "application/pdf" else "other"
    )  # fmt: skip


_NOT_YET = "not transcribed yet: audio evidence is transcribed when the complaint analysis runs"


def _transcription_view(
    e: dict[str, Any], row: dict[str, Any] | None, last: dict[str, Any] | None,
    last_at: datetime | None,
) -> dict[str, Any] | None:  # fmt: skip
    """Transcription state of one audio file: the stored transcript, else the outcome of the
    latest analysis run for this file (a transient reason such as a missing engine), else not
    yet attempted. ``None`` for files that are not audio."""
    if _kind(e["mime_type"]) != "audio":
        return None
    if row is not None:
        st = row["status"]
        return {
            "status": st, "reason": row.get("error_reason") or st,
            "message": STATUS_TEXT.get(st, st), "source": "stored",
            "transcript_available": st in TRANSCRIBED, "engine": row.get("engine"),
            "model": row.get("model"), "language": row.get("language"),
            "duration_s": row.get("duration_s"), "transcribed_s": row.get("transcribed_s"),
            "truncated": bool(row.get("truncated")), "segment_count": len(row["segments"]),
            "checked_at": _iso(row.get("created_at")),
        }  # fmt: skip
    if last is not None:
        st = last.get("status") or "not_attempted"
        return {
            "status": st, "reason": last.get("reason") or st,
            "message": last.get("message") or STATUS_TEXT.get(st, st),
            "source": "last_analysis", "transcript_available": False,
            "engine": last.get("engine"), "model": last.get("model"), "language": None,
            "duration_s": last.get("duration_s"), "transcribed_s": None, "truncated": False,
            "segment_count": 0, "checked_at": _iso(last_at),
        }  # fmt: skip
    return {
        "status": "not_attempted", "reason": "not_attempted", "message": _NOT_YET,
        "source": "none", "transcript_available": False, "engine": None, "model": None,
        "language": None, "duration_s": None, "transcribed_s": None, "truncated": False,
        "segment_count": 0, "checked_at": None,
    }  # fmt: skip


def _evidence_view(
    e: dict[str, Any],
    transcripts: dict[str, dict[str, Any]] | None = None,
    latest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    last_runs = {
        a.get("evidence_id"): a
        for a in ((latest or {}).get("detail") or {}).get("audio_transcription") or []
    }
    return {
        "id": e["id"], "complaint_id": e["complaint_id"],
        "original_filename": e["original_filename"], "mime_type": e["mime_type"],
        "size_bytes": e["size_bytes"], "sha256": e["sha256"], "status": e["status"],
        "created_at": _iso(e["created_at"]), "kind": _kind(e["mime_type"]),
        "transcription": _transcription_view(
            e, (transcripts or {}).get(e["id"]), last_runs.get(e["id"]),
            (latest or {}).get("created_at"),
        ),
    }  # fmt: skip


def _user_view(u: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": u["id"], "email": u["email"], "email_verified": u["email_verified"],
        "role": u["role"], "is_active": u["is_active"],
    }  # fmt: skip


def create_app(
    settings: Settings | None = None,
    *,
    jwks: dict[str, Any] | None = None,
    jwks_fetcher: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    verifier: IdentityVerifier | None = None,
    analysis: AnalysisService | None = None,
    ollama: OllamaClient | None = None,
    clock: Callable[[], datetime] | None = None,
    backend_client: ClerkBackendClient | None = None,
    local_auth: LocalAuth | None = None,
    stt_engine: Any = None,
    transcriber: Transcriber | None = None,
    dna_embedder: Embedder | None = None,
    dna_background: bool = True,
) -> FastAPI:
    """``stt_engine``: an already-built speech-to-text engine to share (the demo server passes
    its live-audio engine so one model is in memory); default: ``LIVE_STT_ENGINE`` /
    ``LIVE_STT_MODEL`` through live_audio, or none when the optional 'stt' extra is missing."""
    settings = settings or Settings.from_env()
    settings.validate()
    now = clock or (lambda: datetime.now(UTC))

    closers: list[Callable[[], Awaitable[None]]] = []
    jwks_status = "not_configured"
    if verifier is None:
        if settings.auth_mode in ("demo", "local"):
            assert settings.demo_jwt_secret
            verifier = DemoVerifier(settings.demo_jwt_secret)
            jwks_status = "demo_mode"
            log.warning(
                "COMPLAINTS_AUTH_MODE=%s: demo tokens and local sign-in codes accepted. "
                "Never use in production.", settings.auth_mode,
            )  # fmt: skip
        else:
            assert settings.clerk_issuer
            fetcher = jwks_fetcher
            if jwks is None and fetcher is None:
                assert settings.clerk_jwks_url
                http_fetcher = HttpJwksFetcher(settings.clerk_jwks_url)
                fetcher = http_fetcher
                closers.append(http_fetcher.aclose)
            clerk = ClerkJwtVerifier(
                settings.clerk_issuer,
                jwks=jwks,
                jwks_fetcher=fetcher if jwks is None else None,
                authorized_parties=settings.clerk_authorized_parties,
                audience=settings.clerk_audience,
            )
            verifier = clerk
            jwks_status = clerk.configured
    if backend_client is None and settings.clerk_backend_lookup and settings.clerk_secret_key:
        backend_client = ClerkBackendClient(settings.clerk_secret_key)
    if backend_client is not None:
        closers.append(backend_client.aclose)
    ollama_client = ollama or OllamaClient(
        settings.ollama_url, settings.ollama_model, settings.ollama_timeout_s
    )
    analysis_service: AnalysisService = analysis or ComplaintAnalysisPipeline(
        explainer=ollama_client
    )
    if transcriber is None:
        engine = None
        if settings.stt_enabled:
            engine = stt_engine if stt_engine is not None else engine_from_env()
        transcriber = Transcriber(
            engine or NoEngine("disabled_by_config"),
            max_seconds=settings.stt_max_seconds_per_file,
            max_files=settings.stt_max_files_per_run, budget_s=settings.stt_run_budget_s,
            enabled=settings.stt_enabled,
        )  # fmt: skip
    stt = transcriber

    local_codes: LocalAuth | None = None
    if settings.local_auth_enabled:
        assert settings.demo_jwt_secret
        if local_auth is not None:
            local_codes = local_auth
        elif settings.local_auth_outbox is not None:
            log.warning("COMPLAINTS_LOCAL_AUTH_OUTBOX is set: sign-in codes are also written to a "
                        "file. Test-only option.")  # fmt: skip
            local_codes = LocalAuth(
                settings.demo_jwt_secret, deliver=file_outbox(settings.local_auth_outbox)
            )
        else:
            local_codes = LocalAuth(settings.demo_jwt_secret)

    storage = EvidenceStorage(settings.evidence_dir)  # type: ignore[arg-type]
    try:
        storage.prepare()
        storage.sweep_tmp()
    except OSError as exc:
        raise ConfigError("COMPLAINTS_EVIDENCE_DIR is not usable") from exc

    store = ComplaintStore(settings.database_url)
    # Fraud DNA: the cached sentence-transformers model unless an embedder is injected (tests use
    # a deterministic fake and no background task)
    fraud_dna = FraudDna(
        store, dna_embedder or SentenceTransformerEmbedder(settings.dna_model), settings.dna_dir,
        enabled=settings.dna_enabled, background=dna_background,
    )  # fmt: skip
    closers.append(fraud_dna.close)
    limiter = RateLimiter(
        {
            "write": settings.rate_write_per_min,
            "upload": settings.rate_upload_per_min,
            "analysis": settings.rate_analysis_per_min,
            "analyze": settings.rate_analyze_per_min,
            "read": settings.rate_read_per_min,
        }
    )
    lookup_cache: dict[str, tuple[float, tuple[str, bool] | None]] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        fraud_dna.start()  # loads the model and builds the index in the background
        try:
            yield
        finally:
            for close in closers:
                with contextlib.suppress(Exception):
                    await close()
            await asyncio.to_thread(store.close)

    async def ready() -> bool:
        await asyncio.to_thread(store.ping)
        return await asyncio.to_thread(storage.writable)

    app = FastAPI(title="complaints", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)  # fmt: skip
    app.state.store = store
    app.state.settings = settings
    app.state.local_auth = local_codes
    app.state.stt = stt
    app.state.dna = fraud_dna
    app.include_router(make_health_router(ready))

    # ------------------------------------------------------------------ errors
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> Response:
        return _err(exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(BodyTooLarge)
    async def _too_large(_: Request, __: BodyTooLarge) -> Response:
        return _err(413, "payload_too_large", "The request body is too large.")

    @app.exception_handler(UploadError)
    async def _upload_error(_: Request, exc: UploadError) -> Response:
        return _err(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> Response:
        # never echo submitted values (FastAPI's default includes "input" and "ctx")
        detail = [
            {
                # an unknown key name is client input: do not echo it either
                "loc": [
                    str(p) for p in (e["loc"][:-1] if e["type"] == "extra_forbidden" else e["loc"])
                ],
                "msg": e["msg"],
                "type": e["type"],
            }
            for e in exc.errors()
        ]
        return JSONResponse({"error": "validation_error", "detail": detail}, status_code=422)

    @app.exception_handler(StoreError)
    async def _store_error(_: Request, exc: StoreError) -> Response:
        if isinstance(exc, NotFound):
            return _err(404, "not_found", "Not found.")
        if isinstance(exc, Conflict):
            return _err(409, exc.code, _CONFLICT_TEXT.get(exc.code, "The request conflicts."))
        return JSONResponse({"error": "service_unavailable"}, status_code=503)

    @app.exception_handler(SQLAlchemyError)
    async def _db_error(_: Request, exc: SQLAlchemyError) -> Response:
        log.error("database error: %s", type(exc).__name__)  # no statement, no parameters
        return JSONResponse({"error": "service_unavailable"}, status_code=503)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> Response:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _err(exc.status_code, code, "Request not accepted.")

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> Response:
        log.error("unhandled error: %s", type(exc).__name__)
        return JSONResponse({"error": "internal_error"}, status_code=500)

    # ------------------------------------------------------------------ auth
    def in_list(email: str | None, entries: frozenset[str]) -> bool:
        if not email:
            return False
        hit = False
        for entry in entries:  # no early exit: constant-time per entry
            hit |= hmac.compare_digest(email.encode(), entry.encode())
        return hit

    def allowlisted(email: str | None) -> bool:
        return in_list(email, settings.investigator_allowlist)

    async def _effective_role(
        row: dict[str, Any], ident: Identity
    ) -> tuple[str, dict[str, str] | None]:
        """The role for THIS request. INVESTIGATOR granted via the allowlist and ADMIN granted by
        env bootstrap stay effective only while the current VERIFIED token email is still in the
        corresponding list; the stored role is never rewritten. Admin-granted roles are not
        affected. Nothing is cached beyond the request."""
        role, source = row["role"], row["role_source"]
        if role == "INVESTIGATOR" and source == "allowlist":
            valid = ident.email_verified and allowlisted(ident.email)
            await asyncio.to_thread(
                store.record_access_transition, row["id"], "investigator", valid, False, now()
            )
            if valid:
                return role, None
            return "CITIZEN", {
                "investigator_access": "lapsed",
                "message": "Your investigator access is no longer approved for this account. "
                "You can continue as a citizen.",
            }
        if role == "ADMIN" and source == "bootstrap":
            valid = ident.email_verified and in_list(ident.email, settings.admin_bootstrap_emails)
            retained = False
            if not valid:
                retained = not await asyncio.to_thread(
                    store.has_other_admin_source_admin, row["id"]
                )
            await asyncio.to_thread(
                store.record_access_transition, row["id"], "admin", valid, retained, now()
            )
            if valid:
                return role, None
            if retained:
                log.error(
                    "last_admin_bootstrap_lapsed: the only admin is no longer in "
                    "ADMIN_BOOTSTRAP_EMAILS; access kept so the system is not locked out"
                )
                return role, {
                    "admin_access": "lapsed_retained",
                    "message": "This administrator is no longer in the bootstrap list but is the "
                    "only administrator, so access is kept. Appoint another administrator.",
                }
            return "CITIZEN", {
                "admin_access": "lapsed",
                "message": "Your administrator access is no longer approved for this account. "
                "You can continue as a citizen.",
            }
        return role, None

    async def current_user(request: Request) -> CurrentUser:
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            raise ApiError(401, "authentication_required", "Sign in to continue.",
                           {"WWW-Authenticate": "Bearer"})  # fmt: skip
        try:
            ident = await verifier.verify(token)  # type: ignore[union-attr]
        except IdentityError:
            raise ApiError(401, "invalid_token", "The access token is not valid.",
                           {"WWW-Authenticate": "Bearer"}) from None  # fmt: skip
        except IdentityUnavailable:
            raise ApiError(503, "identity_provider_unavailable",
                           "Sign-in is temporarily unavailable.") from None  # fmt: skip
        ident = await _maybe_backend_lookup(ident)
        request.state.ident = ident
        row = await asyncio.to_thread(
            store.upsert_user, ident, now(), settings.admin_bootstrap_emails
        )
        uid_hash = hashlib.sha256(row["id"].encode()).hexdigest()[:12]
        request.state.uid_hash = uid_hash
        if not row["is_active"]:
            raise ApiError(403, "account_inactive", "This account has been deactivated.")
        role, notice = await _effective_role(row, ident)
        return CurrentUser(
            row["id"], role, row["email"], row["email_verified"], row["display_name"],
            row["is_active"], uid_hash, notice,
        )  # fmt: skip

    async def _maybe_backend_lookup(ident: Identity) -> Identity:
        """Optional (OFF by default): fill a missing email from Clerk's Backend API."""
        if backend_client is None or ident.email is not None or ident.subject.startswith("demo:"):
            return ident
        cached = lookup_cache.get(ident.subject)
        if cached and time.monotonic() - cached[0] < 300:
            found = cached[1]
        else:
            found = await backend_client.lookup_primary_email(ident.subject)
            if len(lookup_cache) > 5000:
                lookup_cache.clear()
            lookup_cache[ident.subject] = (time.monotonic(), found)
        if not found:
            return ident
        return Identity(ident.subject, found[0], found[1], ident.display_name)

    User = Annotated[CurrentUser, Depends(current_user)]

    def role_guard(*roles: str) -> Callable[..., Awaitable[CurrentUser]]:
        async def dep(user: User) -> CurrentUser:
            if user.role not in roles:
                raise ApiError(403, "forbidden_role", "Your role cannot do this.")
            return user

        return dep

    Citizen = Annotated[CurrentUser, Depends(role_guard("CITIZEN"))]
    StaffUser = Annotated[CurrentUser, Depends(role_guard("INVESTIGATOR", "ADMIN"))]
    AdminUser = Annotated[CurrentUser, Depends(role_guard("ADMIN"))]

    def throttle(kind: str, user: CurrentUser) -> None:
        wait = limiter.hit(kind, user.id)
        if wait is not None:
            raise ApiError(429, "rate_limited", "Too many requests. Try again shortly.",
                           {"Retry-After": str(wait)})  # fmt: skip

    # complaint network (Phase 9): staff-only routes in network_api.py
    register_network_routes(
        app, network=NetworkStore(store), staff=StaffUser, throttle=throttle, now=now
    )
    # Fraud DNA (Phase 8): staff-only similar-complaints route in dna_api.py
    register_dna_routes(app, dna=fraud_dna, staff=StaffUser, throttle=throttle)
    # complaint hotspots (Phase 10): staff-only route in geo_api.py
    register_geo_routes(app, geo=GeoStore(store), staff=StaffUser, throttle=throttle, now=now)

    def session_payload(row: CurrentUser) -> dict[str, Any]:
        return {
            "user": {
                "id": row.id, "email": row.email, "email_verified": row.email_verified,
                "role": row.role, "is_active": row.is_active,
            },
            "capabilities": CAPABILITIES[row.role],
            **(row.notice or {}),
        }  # fmt: skip

    # ------------------------------------------------------------------ sign-in methods
    @app.get("/auth/config")
    async def auth_config() -> dict[str, Any]:
        """Public: which sign-in methods this deployment offers (no secrets; the Clerk key is the
        publishable browser key and is validated to be one)."""
        clerk_on = settings.auth_mode == "clerk"
        return {
            "auth_mode": settings.auth_mode,
            "clerk": {
                "enabled": clerk_on,
                "publishable_key": settings.clerk_publishable_key if clerk_on else None,
            },
            "local": {
                "enabled": local_codes is not None,
                "dev_only": True,
                "delivery": "server_terminal",
                "code_ttl_s": local_codes.code_ttl_s if local_codes else None,
            },
        }

    def _client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    if local_codes is not None:
        codes = local_codes

        @app.post("/local-auth/start", status_code=202)
        async def local_start(body: LocalStartBody, request: Request) -> dict[str, Any]:
            """DEV ONLY. The code goes to the server terminal; the response is the same whether
            or not an account exists for the address and never contains the code."""
            email = normalise_email(body.email)
            if email is None:
                raise _field_error("email", "not a valid email address")
            try:
                codes.start(email, _client_ip(request))
            except LocalAuthRateLimited as exc:
                raise ApiError(429, "rate_limited", "Too many requests. Try again shortly.",
                               {"Retry-After": str(exc.retry_after)}) from None  # fmt: skip
            return {
                "status": "code_sent",
                "delivery": "server_terminal",
                "expires_in": codes.code_ttl_s,
                "message": "A sign-in code was issued. Local development mode: the code is "
                "printed in the server terminal instead of being emailed.",
            }

        @app.post("/local-auth/verify")
        async def local_verify(body: LocalVerifyBody, request: Request) -> dict[str, Any]:
            email = normalise_email(body.email)
            if email is None:
                raise _field_error("email", "not a valid email address")
            try:
                token = codes.verify(email, body.code, _client_ip(request), body.name)
            except LocalAuthRateLimited as exc:
                raise ApiError(429, "rate_limited", "Too many requests. Try again shortly.",
                               {"Retry-After": str(exc.retry_after)}) from None  # fmt: skip
            except LocalAuthInvalid:
                raise ApiError(
                    400, "invalid_code", "The code is wrong or has expired. Request a new code."
                ) from None
            return {"token": token, "token_type": "Bearer", "expires_in": codes.token_ttl_s}

    @app.get("/auth/session")
    async def auth_session(user: User) -> dict[str, Any]:
        return session_payload(user)

    @app.post("/auth/entry")
    async def auth_entry(body: EntryBody, request: Request, user: User) -> Response:
        throttle("write", user)
        if body.entry == "citizen":
            return JSONResponse({**session_payload(user), "entry": "citizen"})
        ident: Identity = request.state.ident
        eligible = user.staff or (ident.email_verified and allowlisted(ident.email))
        if not eligible:
            return JSONResponse(INVESTIGATOR_DENIED, status_code=403)
        if user.role == "CITIZEN":
            row, _ = await asyncio.to_thread(store.grant_investigator, user.id, now())
            user = CurrentUser(
                row["id"], row["role"], row["email"], row["email_verified"], row["display_name"],
                row["is_active"], user.uid_hash,
            )  # fmt: skip
        return JSONResponse({**session_payload(user), "entry": "investigator"})

    # ------------------------------------------------------------------ complaints
    def _own_or_404(user: CurrentUser, c: dict[str, Any] | None) -> dict[str, Any]:
        if c is None or (c["owner_user_id"] != user.id and not user.staff):
            raise ApiError(404, "not_found", "Not found.")
        return c

    async def _load(user: CurrentUser, complaint_id: str) -> dict[str, Any]:
        if len(complaint_id) > 64:
            raise ApiError(404, "not_found", "Not found.")
        c = await asyncio.to_thread(store.get_complaint, complaint_id)
        return _own_or_404(user, c)

    async def _evidence_views(
        complaint_id: str, evidence: list[dict[str, Any]], latest: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        transcripts: dict[str, dict[str, Any]] = {}
        if any(_kind(e["mime_type"]) == "audio" for e in evidence):
            transcripts = await asyncio.to_thread(store.list_transcripts, complaint_id)
        return [_evidence_view(e, transcripts, latest) for e in evidence]

    async def _detail(user: CurrentUser, c: dict[str, Any]) -> dict[str, Any]:
        history = await asyncio.to_thread(store.complaint_history, c["id"])
        latest = await asyncio.to_thread(store.latest_analysis, c["id"])
        evidence = await asyncio.to_thread(store.list_evidence, c["id"])
        return complaint_view(
            c, staff=user.staff, status_history=_history_view(history),
            latest_analysis=_analysis_summary(latest),
            evidence=await _evidence_views(c["id"], evidence, latest),
        )  # fmt: skip

    # investigator case tools (Phase 12): notes, timeline, report (case_api.py)
    register_case_routes(
        app, cases=CaseStore(store), user_type=User, staff=StaffUser, load=_load, detail=_detail,
        throttle=throttle, now=now,
    )  # fmt: skip

    def _time_checks(body: ComplaintIn) -> tuple[datetime | None, datetime | None]:
        def norm(v: datetime | None, field: str) -> datetime | None:
            if v is None:
                return None
            v = v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)
            if v > now() + FUTURE_SKEW:
                raise _field_error(field, "must not be in the future")
            if v < EARLIEST:
                raise _field_error(field, "is too far in the past")
            return v

        return norm(body.incident_at, "incident_at"), norm(body.reported_at, "reported_at")

    @app.post("/complaints")
    async def create_complaint(
        body: ComplaintIn,
        user: Citizen,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> Response:
        throttle("write", user)
        if idempotency_key is not None and not IDEMPOTENCY_RE.fullmatch(idempotency_key):
            raise _field_error("Idempotency-Key", "must be 1-64 characters of A-Z a-z 0-9 . _ : -")
        incident_at, reported_at = _time_checks(body)
        fields = body.model_dump(exclude={"incident_at", "reported_at"})
        fields["incident_at"] = incident_at
        fields["reported_at"] = reported_at or now()
        if fields["amount"] is not None and fields["currency"] is None:
            fields["currency"] = "INR"
        # optional keys added after the first release are left out of the hash while empty, so
        # an Idempotency-Key replay of a request without them hashes as it did before
        hashed = body.model_dump(mode="json")
        for k in BANK_FIELDS:
            if hashed.get(k) is None:
                hashed.pop(k, None)
        request_hash = hashlib.sha256(json.dumps(hashed, sort_keys=True).encode()).hexdigest()
        c, created = await asyncio.to_thread(
            store.create_complaint, user.id, fields, now(), idempotency_key, request_hash
        )
        if created:
            fraud_dna.notify()  # index it in the background (never blocks or fails the request)
        view = await _detail(user, c)
        return JSONResponse(
            view,
            status_code=201 if created else 200,
            headers={} if created else {"Idempotent-Replay": "true"},
        )

    @app.get("/complaints")
    async def list_complaints(
        user: User,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 20,
        offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
        status: Literal["SUBMITTED", "UNDER_REVIEW", "INVESTIGATING", "RESOLVED", "REJECTED"]
        | None = None,
        category: Literal[
            "DIGITAL_ARREST",
            "UPI_FRAUD",
            "PHISHING",
            "OTP_SCAM",
            "BANK_FRAUD",
            "INVESTMENT_SCAM",
            "JOB_SCAM",
            "SOCIAL_MEDIA_FRAUD",
            "OTHER",
        ]
        | None = None,  # fmt: skip
        city: Annotated[str | None, Query(max_length=100)] = None,
        state: Annotated[str | None, Query(max_length=100)] = None,
        incident_from: datetime | None = None,
        incident_to: datetime | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] | None = None,
        has_evidence: bool | None = None,
        q: Annotated[str | None, Query(max_length=100)] = None,
        sort: Literal[
            "created_at", "-created_at", "incident_at", "-incident_at", "amount", "-amount"
        ] = "-created_at",
    ) -> dict[str, Any]:
        throttle("read", user)
        filters: dict[str, Any] = {
            "status": status, "category": category, "incident_from": incident_from,
            "incident_to": incident_to, "created_from": created_from, "created_to": created_to,
            "city": city, "state": state, "q": q.strip() if q else None,
            "has_evidence": has_evidence, "risk_level": risk_level,
        }  # fmt: skip
        for k in ("incident_from", "incident_to", "created_from", "created_to"):
            v = filters[k]
            if v is not None and v.tzinfo is None:
                filters[k] = v.replace(tzinfo=UTC)
        # the scope is applied by the store (owner_id for citizens); filters can only narrow it
        items, total = await asyncio.to_thread(
            store.list_complaints,
            owner_id=None if user.staff else user.id,
            filters=filters, sort=sort, limit=limit, offset=offset,
        )  # fmt: skip
        out = []
        for c in items:
            out.append(
                complaint_view(
                    c,
                    staff=user.staff,
                    evidence_count=c["evidence_count"],
                    latest_analysis=_analysis_summary(c["latest_analysis"]),
                )
            )
        return {"items": out, "total": total, "limit": limit, "offset": offset}

    @app.get("/complaints/{complaint_id}")
    async def get_complaint(complaint_id: str, user: User) -> dict[str, Any]:
        throttle("read", user)
        c = await _load(user, complaint_id)
        return await _detail(user, c)

    @app.patch("/complaints/{complaint_id}/status")
    async def patch_status(complaint_id: str, body: StatusBody, user: StaffUser) -> dict[str, Any]:
        throttle("write", user)
        await _load(user, complaint_id)
        c = await asyncio.to_thread(
            store.change_status, complaint_id, body.status, user.id, body.note, now()
        )
        return await _detail(user, c)

    @app.delete("/complaints/{complaint_id}", status_code=204)
    async def delete_complaint(complaint_id: str, user: Citizen) -> Response:
        throttle("write", user)
        if len(complaint_id) > 64:
            raise ApiError(404, "not_found", "Not found.")
        await asyncio.to_thread(store.soft_delete, complaint_id, user.id, now())
        fraud_dna.notify()  # removed from the index; queries re-check deletion regardless
        return Response(status_code=204)

    # ------------------------------------------------------------------ evidence
    @app.post("/complaints/{complaint_id}/evidence", status_code=201)
    async def upload_evidence(complaint_id: str, request: Request, user: Citizen) -> Response:
        throttle("upload", user)
        c = await _load(user, complaint_id)
        if c["status"] in ("RESOLVED", "REJECTED"):
            raise ApiError(409, "complaint_closed", _CONFLICT_TEXT["complaint_closed"])
        existing = await asyncio.to_thread(store.list_evidence, complaint_id)
        if len(existing) >= settings.max_evidence_per_complaint:
            raise ApiError(409, "evidence_limit_reached", _CONFLICT_TEXT["evidence_limit_reached"])
        try:
            stored = await receive_upload(
                request.stream(), request.headers.get("content-type", ""), storage,
                settings.max_image_bytes, settings.max_audio_bytes,
            )  # fmt: skip
        except OSError:
            log.error("evidence write failed")
            raise ApiError(503, "evidence_storage_failed",
                           "The file could not be stored.") from None  # fmt: skip
        try:
            rec, created = await asyncio.to_thread(
                store.add_evidence, complaint_id, user.id,
                {
                    "original_filename": stored.original_filename, "mime_type": stored.mime_type,
                    "size_bytes": stored.size_bytes, "sha256": stored.sha256,
                    "storage_key": stored.storage_key,
                },
                settings.max_evidence_per_complaint, now(),
            )  # fmt: skip
        except BaseException:
            await asyncio.to_thread(storage.remove, stored.storage_key)  # no orphan on failure
            raise
        if not created:  # the same bytes were already stored for this complaint: a retry
            await asyncio.to_thread(storage.remove, stored.storage_key)
        view = (await _evidence_views(complaint_id, [rec], None))[0]
        return JSONResponse(view, status_code=201 if created else 200)

    @app.get("/complaints/{complaint_id}/evidence")
    async def list_evidence(complaint_id: str, user: User) -> dict[str, Any]:
        throttle("read", user)
        await _load(user, complaint_id)
        rows = await asyncio.to_thread(store.list_evidence, complaint_id)
        latest = await asyncio.to_thread(store.latest_analysis, complaint_id)
        return {"items": await _evidence_views(complaint_id, rows, latest)}

    @app.get("/complaints/{complaint_id}/evidence/{evidence_id}/transcript")
    async def get_transcript(complaint_id: str, evidence_id: str, user: User) -> Response:
        """The stored transcript of one audio evidence file (owner and staff; others 404)."""
        throttle("read", user)
        await _load(user, complaint_id)
        ev = await asyncio.to_thread(store.get_evidence, complaint_id, evidence_id[:64])
        if ev is None:
            raise ApiError(404, "not_found", "Not found.")
        if _kind(ev["mime_type"]) != "audio":
            raise ApiError(404, "not_audio_evidence", "This evidence file is not audio.")
        row = await asyncio.to_thread(store.get_transcript, complaint_id, ev["id"])
        if row is None or row["status"] not in TRANSCRIBED:
            latest = await asyncio.to_thread(store.latest_analysis, complaint_id)
            view = _evidence_view(ev, {ev["id"]: row} if row else {}, latest)["transcription"]
            return JSONResponse(
                {"error": "transcript_not_available",
                 "message": "No transcript is available for this file: " + view["message"] + ".",
                 "transcription": view}, status_code=404,
            )  # fmt: skip
        return JSONResponse({
            "evidence_id": ev["id"], "complaint_id": complaint_id, "status": row["status"],
            "engine": row["engine"], "model": row["model"], "language": row["language"],
            "duration_s": row["duration_s"], "transcribed_s": row["transcribed_s"],
            "truncated": row["truncated"], "segments": row["segments"],
            "created_at": _iso(row["created_at"]),
            "note": "Machine transcription made locally; words can be misheard and time offsets "
            "are approximate.",
        })  # fmt: skip

    @app.delete("/complaints/{complaint_id}/evidence/{evidence_id}")
    async def delete_evidence(complaint_id: str, evidence_id: str, user: User) -> Response:
        """Owner (while SUBMITTED) or ADMIN. Foreign or unknown ids are 404 (never 403)."""
        if user.role == "INVESTIGATOR":
            raise ApiError(403, "forbidden_role", "Your role cannot do this.")
        throttle("write", user)
        if len(complaint_id) > 64 or len(evidence_id) > 64:
            raise ApiError(404, "not_found", "Not found.")
        ev = await asyncio.to_thread(
            store.delete_evidence, complaint_id, evidence_id, user.id,
            as_admin=user.role == "ADMIN", now=now(),
        )  # fmt: skip
        removed = await asyncio.to_thread(storage.remove_checked, ev["storage_key"])
        if not removed:
            log.error("evidence file could not be removed after delete")  # no name, no key
        return JSONResponse({"id": ev["id"], "status": "deleted", "file_removed": removed})

    @app.get("/complaints/{complaint_id}/evidence/{evidence_id}/download")
    async def download_evidence(complaint_id: str, evidence_id: str, user: User) -> Response:
        throttle("read", user)
        c = await _load(user, complaint_id)
        ev = await asyncio.to_thread(store.get_evidence, complaint_id, evidence_id[:64])
        if ev is None:
            raise ApiError(404, "not_found", "Not found.")
        try:
            path = storage.path_for(ev["storage_key"])
        except ValueError:
            raise ApiError(404, "not_found", "Not found.") from None
        if not await asyncio.to_thread(path.is_file):
            log.error("evidence file missing on disk")
            raise ApiError(404, "evidence_file_missing", "The stored file is not available.")
        # every download is recorded (finding 4): who, which complaint and file; never the name
        await asyncio.to_thread(
            store.record_audit, user.id, "evidence_download",
            {"complaint_id": complaint_id, "evidence_id": ev["id"],
             "owner": c["owner_user_id"] == user.id}, now(),
        )  # fmt: skip
        safe_name = ev["original_filename"]
        return FileResponse(
            path,
            media_type=ev["mime_type"],
            headers={
                "Content-Disposition": f'attachment; filename="{safe_name}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    # ------------------------------------------------------------------ analysis
    @app.post("/complaints/{complaint_id}/analysis", status_code=201)
    async def run_analysis(complaint_id: str, user: User) -> dict[str, Any]:
        throttle("analysis", user)
        c = await _load(user, complaint_id)
        files = await asyncio.to_thread(store.list_evidence, complaint_id)

        def path_for(f: dict[str, Any]) -> Any:
            try:
                return storage.path_for(f["storage_key"])
            except ValueError:
                return storage.root / "missing"

        async def load_existing() -> dict[str, dict[str, Any]]:
            return await asyncio.to_thread(store.list_transcripts, complaint_id)

        async def save(rec: dict[str, Any]) -> dict[str, Any] | None:
            return await asyncio.to_thread(store.save_transcript, complaint_id, rec, now())

        # audio evidence: transcribed once (synchronously, within the run's budget), reused after
        transcripts = await stt.prepare(complaint_id, files, path_for, load_existing, save)
        out = await analysis_service.analyse(
            {
                **c,
                "evidence": [
                    {"evidence_id": e["id"], "mime_type": e["mime_type"],
                     "size_bytes": e["size_bytes"], "transcription": transcripts.get(e["id"])}
                    for e in files
                ],
            }  # fmt: skip
        )
        rec = await asyncio.to_thread(
            store.add_analysis,
            complaint_id,
            {
                "pipeline_version": out.pipeline_version, "capabilities": out.capabilities,
                "risk_score": out.risk_score, "risk_level": stored_level(out.risk_level),
                "detail": ({**out.extra, "risk_level": out.risk_level} if out.extra else None),
                "classification": out.classification, "confidence": out.confidence,
                "indicators": out.indicators, "explanation": out.explanation,
                "recommended_actions": out.recommended_actions, "limitations": out.limitations,
                "status": out.status,
            },
            now(),
        )  # fmt: skip
        return _analysis_view(rec)

    @app.post("/analyze")
    async def analyze_text(body: AnalyzeIn, user: User) -> dict[str, Any]:
        """Stateless: analyse submitted text and optional fields. Nothing is stored and the
        content is not logged."""
        throttle("analyze", user)
        for short in body.conflicts():
            raise _field_error(short, f"give either {short} or {ANALYZE_ALIASES[short]}, not both")
        short = {k: getattr(body, k) is not None for k in ANALYZE_ALIASES}
        names = {old: k if short[k] else old for k, old in ANALYZE_ALIASES.items()}
        complaint = {
            "title": body.title or "", "description": body.text, "category": None,
            "suspicious_url": body.value("url"), "suspect_phone": body.value("phone"),
            "upi_id": body.upi_id, "suspect_email": body.value("email"), "amount": body.amount,
            "payment_method": body.payment_method, "city": body.city, "state": body.state,
            "social_platform": body.social_platform, "evidence": [],
            # field names used in evidence pointers: the ones the client sent
            "_text_field": "text", "_url_field": names["suspicious_url"],
            "_field_names": names,
        }  # fmt: skip
        out = await analysis_service.analyse(complaint)
        view = _outcome_view(out, now())
        view["input_capabilities"] = _input_capabilities(complaint, out)
        return view

    @app.get("/complaints/{complaint_id}/analysis")
    async def get_analysis(complaint_id: str, user: User) -> Response:
        throttle("read", user)
        await _load(user, complaint_id)
        latest = await asyncio.to_thread(store.latest_analysis, complaint_id)
        if latest is None:
            return _err(404, "analysis_not_found", "No analysis has been run for this complaint.")
        history = await asyncio.to_thread(store.analysis_history, complaint_id)
        return JSONResponse(
            {**_analysis_view(latest), "history": [_analysis_summary(h) for h in history]}
        )

    @app.get("/complaints/{complaint_id}/analyses")
    async def list_analyses(
        complaint_id: str,
        user: User,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 20,
        offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
    ) -> dict[str, Any]:
        """Every analysis version of a complaint, newest first (owner and staff only)."""
        throttle("read", user)
        await _load(user, complaint_id)
        rows, total = await asyncio.to_thread(store.list_analyses, complaint_id, limit, offset)
        return {
            "items": [_version_view(r) for r in rows],
            "total": total, "limit": limit, "offset": offset,
        }  # fmt: skip

    @app.get("/models")
    async def models(user: User) -> dict[str, Any]:
        """The trained models behind the analysis, from their shipped metrics and model card,
        with the runtime status seen by this process. Any signed-in role."""
        throttle("read", user)
        from .models_info import SYNTHETIC_WARNING, call_guard_entry, category_entry

        if isinstance(analysis_service, ComplaintAnalysisPipeline):
            cg_rt = await analysis_service.text_scorer_runtime()
            cat_rt = await analysis_service.category_runtime()
        else:
            cg_rt = cat_rt = {"status": "not_used", "reason": "analysis_service_without_models"}
        cat, cg = await asyncio.gather(
            asyncio.to_thread(category_entry, cat_rt), asyncio.to_thread(call_guard_entry, cg_rt)
        )
        llm = await ollama_client.status()
        return {
            "synthetic_data_warning": SYNTHETIC_WARNING,
            "score_semantics": SCORE_SEMANTICS,
            "models": [cat, cg],
            "llm": {
                "provider": "ollama", **llm,
                "used_for": "explanation wording only; never the score, level or category",
            },
            "dna": {
                **fraud_dna.status(),
                "used_for": "finding complaints described in similar words (staff only); never "
                "the score, level or category, and never proof of a link",
            },
            "stt": {
                **await stt.health(),
                "used_for": "transcribing uploaded audio evidence when a complaint analysis runs; "
                "the transcript is scored by the same rules and classifier as the description",
            },
        }  # fmt: skip

    # ------------------------------------------------------------------ alerts / stats
    @app.get("/alerts")
    async def list_alerts(
        user: User,
        unread: bool = False,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 20,
        offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
    ) -> dict[str, Any]:
        throttle("read", user)
        items, total, unread_n = await asyncio.to_thread(
            store.list_alerts, user.id, unread, limit, offset
        )
        return {
            "items": [_alert_view(a) for a in items],
            "total": total, "unread": unread_n, "limit": limit, "offset": offset,
        }  # fmt: skip

    @app.post("/alerts/{alert_id}/read")
    async def read_alert(alert_id: str, user: User) -> dict[str, Any]:
        throttle("write", user)
        if len(alert_id) > 64:
            raise ApiError(404, "not_found", "Not found.")
        a = await asyncio.to_thread(store.mark_alert_read, user.id, alert_id, now())
        return _alert_view(a)

    def _me_view(u: dict[str, Any], user: CurrentUser) -> dict[str, Any]:
        return {
            "id": u["id"], "email": u["email"], "email_verified": u["email_verified"],
            "display_name": u["display_name"], "role": user.role,  # effective role
            "created_at": _iso(u["created_at"]),
            "privacy": {"allow_case_matching": u["allow_case_matching"]},
            **(user.notice or {}),
        }  # fmt: skip

    @app.get("/me")
    async def get_me(user: User) -> dict[str, Any]:
        throttle("read", user)
        u = await asyncio.to_thread(store.get_user, user.id)
        if u is None:  # pragma: no cover - the user row was just upserted
            raise ApiError(404, "not_found", "Not found.")
        return _me_view(u, user)

    @app.patch("/me")
    async def patch_me(body: MeUpdate, request: Request, user: User) -> dict[str, Any]:
        """display_name and privacy settings only; the role is never read from the body."""
        throttle("write", user)
        changes: dict[str, Any] = {}
        if "display_name" in body.model_fields_set:
            changes["display_name"] = body.display_name
        if "privacy" in body.model_fields_set:
            changes["allow_case_matching"] = body.privacy.allow_case_matching
        ident: Identity = request.state.ident
        u = await asyncio.to_thread(
            store.update_profile, user.id, changes, now(), ident.display_name
        )
        if "allow_case_matching" in changes:
            fraud_dna.notify()  # index or remove this user's complaints; queries re-check anyway
        return _me_view(u, user)

    @app.get("/me/stats")
    async def my_stats(user: User) -> dict[str, Any]:
        throttle("read", user)
        s = await asyncio.to_thread(store.stats, user.id)
        by_status = {k: s["by_status"].get(k, 0) for k in STATUSES}
        by_category = {k: s["by_category"].get(k, 0) for k in CATEGORIES}
        return {
            "total": sum(by_status.values()), "by_status": by_status, "by_category": by_category,
            "last_complaint_at": _iso(s["last_complaint_at"]), "unread_alerts": s["unread_alerts"],
            "by_risk_level": s["by_risk_level"],
        }  # fmt: skip

    # ------------------------------------------------------------------ admin
    @app.get("/admin/users")
    async def admin_users(
        user: AdminUser,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 20,
        offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
        role: Literal["CITIZEN", "INVESTIGATOR", "ADMIN"] | None = None,
        is_active: bool | None = None,
    ) -> dict[str, Any]:
        rows, total = await asyncio.to_thread(store.list_users, limit, offset, role, is_active)
        return {
            "items": [_admin_user_view(u) for u in rows],
            "total": total, "limit": limit, "offset": offset,
        }  # fmt: skip

    @app.get("/admin/audit")
    async def admin_audit_log(
        user: AdminUser,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 20,
        offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
    ) -> dict[str, Any]:
        rows, total = await asyncio.to_thread(store.list_audit, limit, offset)
        items = [
            {
                "id": r["id"], "actor_user_id": r["actor_user_id"], "action": r["action"],
                "target_user_id": r["target_user_id"], "before": r["before"], "after": r["after"],
                "created_at": _iso(r["created_at"]),
            }
            for r in rows
        ]  # fmt: skip
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.put("/admin/users/{user_id}/role")
    async def admin_set_role(user_id: str, body: RoleBody, user: AdminUser) -> dict[str, Any]:
        throttle("write", user)
        if body.role not in ROLES:  # pragma: no cover - the model already enforces the enum
            raise _field_error("role", "unknown role")
        row = await asyncio.to_thread(
            store.set_role, user.id, user_id[:64], body.role, body.expected_role, now()
        )
        return _admin_user_view(row)

    @app.put("/admin/users/{user_id}/active")
    async def admin_set_active(user_id: str, body: ActiveBody, user: AdminUser) -> dict[str, Any]:
        throttle("write", user)
        row = await asyncio.to_thread(
            store.set_active, user.id, user_id[:64], body.is_active, now()
        )
        return _admin_user_view(row)

    @app.get("/admin/health")
    async def admin_health(user: AdminUser) -> dict[str, Any]:
        async def db_ok() -> dict[str, str]:
            try:
                await asyncio.to_thread(store.ping)
                return {"status": "ok"}
            except Exception:
                return {"status": "error"}

        ev_ok = await asyncio.to_thread(storage.writable)
        guard_ok = (
            isinstance(analysis_service, CallGuardTextAnalysis)
            and (analysis_service.importable)
            or importlib.util.find_spec("call_guard") is not None
        )
        out: dict[str, Any] = {
            "db": await db_ok(),
            "evidence_dir": {"status": "ok" if ev_ok else "error"},
            "jwks": {"status": jwks_status},
            "call_guard": {"status": "importable" if guard_ok else "unavailable"},
        }
        for name in ("neo4j", "postgis", "redis"):
            out[name] = {"status": "not_configured"}
        # complaint network queries run as SQL on the complaints database (Phase 9)
        out["graph"] = {
            "status": "sql", "backend": "sql", "neo4j": "not_configured",
            "message": "sql (neo4j not configured)",
        }  # fmt: skip
        if isinstance(analysis_service, ComplaintAnalysisPipeline):
            out["category_model"] = await analysis_service.category_health()
        else:
            out["category_model"] = {"status": "not_configured"}
        out["ollama"] = {
            "status": await ollama_client.probe(),
            "model": ollama_client.model if ollama_client.configured else None,
        }
        out["stt"] = await stt.health()
        out["dna"] = fraud_dna.status()
        out["qdrant"] = {
            "status": "local" if fraud_dna.index.ready else "not_started",
            "mode": "embedded" if settings.dna_dir else "memory",
            "message": "embedded (local mode), used by Fraud DNA",
        }  # fmt: skip
        return out

    # ------------------------------------------------------------------ middleware stack
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-Id"],
            allow_credentials=False,
            max_age=600,
        )
    max_file = max(settings.max_image_bytes, settings.max_audio_bytes)
    app.add_middleware(
        HttpGuard, max_json=settings.max_json_bytes, max_upload=max_file + UPLOAD_OVERHEAD
    )
    return app


_CONFLICT_TEXT = {
    "invalid_transition": "That status change is not allowed from the current status.",
    "status_conflict": "The complaint was changed by someone else. Reload and try again.",
    "not_deletable": "Only a complaint that has not been picked up for review can be deleted.",
    "complaint_closed": "This complaint is closed and can no longer be changed.",
    "evidence_limit_reached": "The complaint already has the maximum number of evidence files.",
    "evidence_not_deletable": "Evidence can only be removed before the complaint is picked up "
    "for review.",
    "idempotency_key_reuse": "This Idempotency-Key was already used with a different request.",
    "idempotent_target_deleted": "The complaint created with this key has been deleted.",
    "role_conflict": "The user's role changed. Reload and try again.",
    "last_admin": "At least one active administrator must remain.",
    "target_email_not_verified": "The user has no verified email address.",
    "link_no_relation": "These complaints share no identifier, so there is no link to review.",
    "link_complaint_deleted": "One of these complaints has been deleted.",
    "link_matching_disabled": "Linking is off for one of these complaints at the complainant's "
    "request.",
}


def _field_error(field: str, msg: str) -> RequestValidationError:
    return RequestValidationError([{"loc": ("body", field), "msg": msg, "type": "value_error",
                                    "input": None}])  # fmt: skip


def _alert_view(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": a["id"], "complaint_id": a["complaint_id"], "kind": a["kind"],
        "message": a["message"], "created_at": _iso(a["created_at"]), "read_at": _iso(a["read_at"]),
        "risk_level": a.get("risk_level"),
    }  # fmt: skip


def _admin_user_view(u: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": u["id"], "email": u["email"], "email_verified": u["email_verified"],
        "display_name": u["display_name"], "role": u["role"],
        "role_source": u.get("role_source"), "is_active": u["is_active"],
        "created_at": _iso(u["created_at"]), "last_login_at": _iso(u["last_login_at"]),
    }  # fmt: skip


def create_service_app() -> FastAPI:
    """Environment-configured app. Raises ``ConfigError`` (the process exits) when identity or
    storage settings are missing: there is no insecure fallback."""
    settings = Settings.from_env()
    settings.validate()
    return create_app(settings)


# Re-exported for tests and tooling.
__all__ = ["ApiError", "CAPABILITIES", "create_app", "create_service_app", "httpx"]
