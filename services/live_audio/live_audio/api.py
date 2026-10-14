"""HTTP API: ``build_router`` (mounted by the demo server under ``/api/live-audio`` with its own
authentication) and ``create_app`` (standalone, HS256 bearer tokens with ``LIVE_AUDIO_JWT_SECRET``).

Errors are ``{"error": code, "detail"?: code}``; nothing submitted is ever echoed.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

import jwt
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import LiveAudioConfig
from .explain import OllamaExplainer
from .service import LiveAudioService
from .session import SESSION_ID_RE, Caller, CapError
from .stt import SttEngine, engine_from_env
from .wav import WavError, parse_wav

log = logging.getLogger("live_audio")

PREFIX = "/api/live-audio"
AUDIO_TYPES = (
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/vnd.wave",
    "application/octet-stream",
)
_SEQ = re.compile(r"^\d{1,7}$")
_START = re.compile(r"^\d{1,8}$")
_SID = re.compile(SESSION_ID_RE)
MAX_START_MS = 4 * 3600 * 1000


class SessionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: Literal["auto", "hi", "en"] = "auto"


def error(
    status: int, code: str, detail: str | None = None, retry: int | None = None
) -> JSONResponse:
    body: dict[str, Any] = {"error": code}
    if detail:
        body["detail"] = detail
    headers = {"Retry-After": str(retry)} if retry else None
    return JSONResponse(body, status_code=status, headers=headers)


def build_router(service: LiveAudioService, caller_dep: Callable[..., Caller]) -> APIRouter:
    cfg = service.cfg
    r = APIRouter(prefix=PREFIX)
    CallerDep = Depends(caller_dep)

    def limited(bucket: str, caller: Caller, limit: int) -> JSONResponse | None:
        wait = service.limiter.check(bucket, caller.rate_key, limit)
        return error(429, "rate_limited", retry=wait) if wait is not None else None

    def lookup(sid: str, caller: Caller):
        if not _SID.fullmatch(sid):
            return None
        return service.sessions.get(sid, caller)

    @r.get("/capabilities")
    async def capabilities(caller: Caller = CallerDep) -> Any:
        if resp := limited("read", caller, cfg.reads_per_minute):
            return resp
        return await service.capabilities()

    @r.post("/sessions", status_code=201)
    async def create_session(body: SessionBody, caller: Caller = CallerDep) -> Any:
        if resp := limited("session", caller, cfg.sessions_per_minute):
            return resp
        try:
            s = service.sessions.create(caller, body.language)
        except CapError as e:
            return error(409 if e.code == "too_many_sessions" else 503, e.code)
        service.ensure_preload()
        log.info(
            "live session created language=%s active=%d", body.language, service.sessions.count()
        )
        return JSONResponse(
            {
                "session_id": s.id,
                "language": s.language,
                "chunk_ms": cfg.chunk_ms,
                "max_chunk_ms": cfg.max_chunk_ms,
                "max_chunk_bytes": cfg.max_chunk_bytes,
                "sample_rate": 16000,
                "idle_ttl_s": cfg.session_idle_ttl_s,
                "mode": "incremental_chunked",
            },  # fmt: skip
            status_code=201,
        )

    @r.post("/sessions/{sid}/chunks")
    async def upload_chunk(sid: str, request: Request, caller: Caller = CallerDep) -> Any:
        if resp := limited("chunk", caller, cfg.chunks_per_minute):
            return resp
        s = lookup(sid, caller)
        if s is None:
            return error(404, "not_found")
        q = request.query_params
        seq_raw, start_raw = q.get("seq", ""), q.get("start_ms")
        if not _SEQ.fullmatch(seq_raw) or int(seq_raw) > cfg.max_seq:
            return error(422, "invalid_request", "seq")
        if start_raw is not None and (
            not _START.fullmatch(start_raw) or int(start_raw) > MAX_START_MS
        ):
            return error(422, "invalid_request", "start_ms")
        seq = int(seq_raw)
        start_ms = int(start_raw) if start_raw is not None else None
        if seq >= s.next_seq + cfg.max_seq_ahead:
            return error(422, "seq_out_of_window")
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype not in AUDIO_TYPES:
            return error(415, "unsupported_media_type")
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > cfg.max_chunk_bytes:
            return error(413, "payload_too_large")
        body = bytearray()
        try:
            async for part in request.stream():
                body += part
                if len(body) > cfg.max_chunk_bytes:
                    return error(413, "payload_too_large")
        except Exception:  # client went away mid-upload
            return error(400, "upload_interrupted")
        try:
            pcm = parse_wav(
                bytes(body), max_seconds=cfg.max_chunk_ms / 1000, max_bytes=cfg.max_chunk_bytes
            )
        except WavError as e:
            if e.code == "too_large":
                return error(413, "payload_too_large")
            return error(422, "invalid_audio", e.code)
        finally:
            body.clear()
        # processing continues even if the client disconnects: the result is cached under
        # `seq`, so a re-send after a reconnect returns it without transcribing again
        task = asyncio.ensure_future(service.process_chunk(s, seq, pcm, start_ms))
        del pcm
        return await asyncio.shield(task)

    @r.get("/sessions/{sid}")
    async def get_session(sid: str, caller: Caller = CallerDep) -> Any:
        if resp := limited("read", caller, cfg.reads_per_minute):
            return resp
        s = lookup(sid, caller)
        if s is None:
            return error(404, "not_found")
        return service.state_view(s)

    @r.post("/sessions/{sid}/end")
    async def end_session(sid: str, caller: Caller = CallerDep) -> Any:
        s = lookup(sid, caller)
        if s is None:
            return error(404, "not_found")
        async with s.lock:  # let an in-flight chunk finish first
            summary = service.summary_view(s)
            service.sessions.end(sid)
        log.info("live session ended chunks=%d", summary["chunks_processed"])
        return summary

    return r


# ------------------------------------------------------------------ standalone app
def bearer_caller(secret: str) -> Callable[[Request], Caller]:
    def dep(request: Request) -> Caller:
        scheme, _, tok = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not tok:
            raise StarletteHTTPException(401)
        try:
            claims = jwt.decode(
                tok.strip(), secret, algorithms=["HS256"], options={"require": ["exp", "sub"]}
            )
        except jwt.PyJWTError:
            raise StarletteHTTPException(401) from None
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub or len(sub) > 100:
            raise StarletteHTTPException(401)
        return Caller(owner=sub, rate_key=sub)

    return dep


def _load_scorer() -> Any:
    from call_guard.model import Scorer, load_classifier

    return Scorer(load_classifier())


def create_app(
    config: LiveAudioConfig | None = None,
    *,
    engine: SttEngine | None = None,
    scorer: Any = None,
    explainer: OllamaExplainer | None = None,
    clock: Callable[[], float] = time.monotonic,
    caller_dep: Callable[..., Caller] | None = None,
) -> FastAPI:
    cfg = config or LiveAudioConfig.from_env()
    if caller_dep is None:
        if not cfg.jwt_secret or len(cfg.jwt_secret.encode()) < 32:
            raise ValueError(
                "LIVE_AUDIO_JWT_SECRET (>= 32 bytes) is required for the standalone app"
            )
        caller_dep = bearer_caller(cfg.jwt_secret)
    eng = engine or engine_from_env(cfg.env_for_engine())
    holder: dict[str, Any] = {"scorer": scorer}
    service = LiveAudioService(cfg, eng, lambda: holder["scorer"], explainer=explainer, clock=clock)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if holder["scorer"] is None:
            holder["scorer"] = await asyncio.to_thread(_load_scorer)
        try:
            yield
        finally:
            await service.aclose()

    app = FastAPI(
        title="live-audio", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.live_audio = service

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        items = [{"loc": [str(x) for x in e["loc"]][:-1] + ["*"] if e["type"] == "extra_forbidden"
                  else [str(x) for x in e["loc"]], "msg": e["msg"], "type": e["type"]}
                 for e in exc.errors()]  # fmt: skip
        return JSONResponse({"error": "invalid_request", "detail": items}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        names = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}
        return JSONResponse(
            {"error": names.get(exc.status_code, "http_error")}, status_code=exc.status_code
        )

    app.include_router(build_router(service, caller_dep))
    return app
