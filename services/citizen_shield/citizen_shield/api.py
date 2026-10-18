"""HTTP API for citizen-shield.

Trust model: reachable only through the gateway on the internal network. Principal headers are
trusted ONLY when ``CITIZEN_GATEWAY_SECRET`` is set and the request carries it in
``X-Gateway-Secret`` (constant-time byte comparison; non-ASCII fails it) or when
``TRUST_GATEWAY_HEADERS=1``. Otherwise every route except the probes answers 401. Roles: citizen,
admin.

Privacy: the message is scored in memory and is never logged, stored or echoed; access logs carry
method, route template, status, request id and duration only.
"""

import asyncio
import hmac
import logging
import os
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from scam_contracts.geo import CITY_CODES, STATE_CODES
from scam_contracts.hashing import keyed_hash
from scam_contracts.models import Complaint
from scam_contracts.topics import Topics
from svckit.bus import Bus
from svckit.health import make_health_router

from .assess import assess as run_assess
from .config import Settings
from .guard_client import CallGuardClient
from .ratelimit import WindowLimiter
from .store import ReportStore
from .texts import LANGUAGE_NAMES, SUPPORTED

log = logging.getLogger("citizen_shield")
access_log = logging.getLogger("citizen_shield.access")

ROLES = {"citizen", "admin"}
RID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
REPORT_KEY_DOMAIN = "citizen-report"


@dataclass(frozen=True)
class Principal:
    role: str
    sub: str


def _trusted(request: Request) -> bool:
    secret = os.getenv("CITIZEN_GATEWAY_SECRET")
    if secret:
        got = request.headers.get("x-gateway-secret", "")
        return hmac.compare_digest(got.encode("utf-8", "replace"), secret.encode("utf-8"))
    return os.getenv("TRUST_GATEWAY_HEADERS") == "1"


def principal(request: Request) -> Principal:
    if not _trusted(request):
        raise HTTPException(401, "gateway headers not trusted")
    role = request.headers.get("x-principal-role", "").strip()
    sub = request.headers.get("x-principal-sub", "").strip()
    if not role or not sub:
        raise HTTPException(401, "missing principal")
    if role not in ROLES:
        raise HTTPException(403, "role not permitted")
    return Principal(role, sub)


class AssessBody(BaseModel):
    # no max_length: an over-long message is scored on its first 4,000 characters and flagged
    # uncertain (the request body cap bounds the input); a missing or non-string value is a 422
    message: str
    lang: str | None = None


class ReportBody(BaseModel):
    category: Literal["scam_call", "held_transfer", "cash_out"]
    message_hash: str | None = None
    lang: str | None = Field(default=None, max_length=16)
    city_code: str | None = None
    state_code: str | None = None

    @field_validator("message_hash")
    @classmethod
    def _hash(cls, v: str | None) -> str | None:
        if v is not None and not HASH_RE.match(v):
            raise ValueError("message_hash must be 64 hex characters")
        return v.lower() if v else v

    @field_validator("city_code")
    @classmethod
    def _city(cls, v: str | None) -> str | None:
        if v is not None and v not in CITY_CODES:
            raise ValueError("unknown city_code")
        return v

    @field_validator("state_code")
    @classmethod
    def _state(cls, v: str | None) -> str | None:
        if v is not None and v not in STATE_CODES:
            raise ValueError("unknown state_code")
        return v


class _TooLargeBody(Exception):
    pass


class BodyLimit:
    """Pure-ASGI request body cap (Content-Length up front, streamed bytes counted): 413."""

    def __init__(self, app: Any, max_bytes: int) -> None:
        self.app, self.max = app, max_bytes

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        try:
            declared = int(headers.get(b"content-length", b"0") or 0)
        except ValueError:
            declared = 0
        if declared > self.max:
            await self._reject(send)
            return
        seen = 0
        rejected = False

        async def counting_receive() -> Any:
            nonlocal seen, rejected
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.max and not rejected:
                    rejected = True
                    await self._reject(send)
                    raise _TooLargeBody
            return msg

        async def tracking_send(msg: Any) -> None:
            if not rejected:
                await send(msg)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _TooLargeBody:
            pass
        except Exception:
            if not rejected:
                raise

    @staticmethod
    async def _reject(send: Any) -> None:
        body = b'{"detail":"request body too large"}'
        await send(
            {
                "type": "http.response.start", "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )  # fmt: skip
        await send({"type": "http.response.body", "body": body})


def create_app(
    database_url: str | None = None,
    guard: CallGuardClient | None = None,
    bus: Bus | None = None,
    report_key: bytes | None = None,
    settings: Settings | None = None,
    clock: Callable[[], datetime] | None = None,
    background: bool = True,
    closers: list[Callable[[], Awaitable[None]]] | None = None,
    store: ReportStore | None = None,
    limiter: WindowLimiter | None = None,
) -> FastAPI:
    cfg = settings or Settings.from_env()
    clock = clock or (lambda: datetime.now(UTC))
    store = store or ReportStore(
        database_url or os.getenv("CITIZEN_DATABASE_URL", "sqlite://"),
        create_schema=os.getenv("CITIZEN_CREATE_SCHEMA", "1") == "1",
    )
    guard = guard or CallGuardClient(
        cfg.call_guard_url, cfg.call_guard_timeout_s,
        breaker_failures=cfg.breaker_failures, breaker_open_s=cfg.breaker_open_s,
    )  # fmt: skip
    limiter = limiter or WindowLimiter(cfg.limiter_max_keys)
    if report_key is not None and len(report_key) < 16:
        raise ValueError("report key must be at least 16 bytes")
    counters = {
        "assess": 0, "assess_degraded": 0, "reports": 0, "report_duplicates": 0,
        "report_emitted": 0, "report_unlocated": 0, "emit_failures": 0, "rate_limited": 0,
    }  # fmt: skip
    by_label = {"no_indicators_found": 0, "insufficient_input": 0, "suspicious": 0,
                "likely_scam": 0}  # fmt: skip
    tasks: list[asyncio.Task[None]] = []

    async def prune_loop() -> None:
        while True:
            try:
                cutoff = clock() - timedelta(days=cfg.report_retention_days)
                await asyncio.to_thread(store.prune, cutoff)
            except Exception:
                log.warning("report prune failed")
            await asyncio.sleep(cfg.prune_interval_s)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if background:
            tasks.append(asyncio.create_task(prune_loop()))
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            tasks.clear()
            for close in [guard.aclose, *(closers or [])]:
                try:
                    await close()
                except Exception:
                    log.warning("close failed")
            await asyncio.to_thread(store.close)

    async def ready() -> bool:
        return await asyncio.to_thread(store.ping)

    app = FastAPI(title="citizen-shield", lifespan=lifespan)
    app.add_middleware(BodyLimit, max_bytes=cfg.max_body_bytes)
    app.state.store = store
    app.state.guard = guard
    app.state.limiter = limiter
    app.state.counters = counters

    @app.middleware("http")
    async def access(request: Request, call_next: Callable[[Request], Awaitable[Response]]):
        """Minimal structured access log: method, route template, status, request id, duration."""
        inbound = request.headers.get("x-request-id", "")
        rid = inbound if RID_RE.match(inbound) else uuid.uuid4().hex[:12]
        t0 = time.monotonic()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-Id"] = rid
            return response
        finally:
            route = request.scope.get("route")
            access_log.info(
                "method=%s route=%s status=%d rid=%s ms=%d",
                request.method, getattr(route, "path", "unmatched"), status, rid,
                int((time.monotonic() - t0) * 1000),
            )  # fmt: skip

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # never echo the submitted value: it may be the message (FastAPI's default includes it)
        return JSONResponse(
            {
                "detail": [
                    {"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()
                ]
            },
            status_code=422,
        )

    app.include_router(make_health_router(ready, extra=lambda: {
        "call_guard": "breaker_open" if guard.breaker_open else "ok",
    }))  # fmt: skip

    def limit(request: Request, p: Principal, route: str, per_min: int) -> None:
        """Per-principal and per-peer windows; fail-open (limiter errors allow the request)."""
        peer = request.client.host if request.client else "unknown"
        for key, n in ((f"{route}:{p.role}:{p.sub}", per_min), (f"peer:{peer}", cfg.peer_per_min)):
            retry = limiter.check(key, n)
            if retry is not None:
                counters["rate_limited"] += 1
                raise HTTPException(429, "rate limited", headers={"Retry-After": str(retry)})

    @app.post("/assess")
    async def assess(
        request: Request, body: AssessBody, p: Annotated[Principal, Depends(principal)]
    ) -> JSONResponse:
        limit(request, p, "assess", cfg.assess_per_min)
        verdict = await run_assess(guard, body.message, (body.lang or "")[:16] or None)
        counters["assess"] += 1
        counters["assess_degraded"] += int(verdict.degraded)
        by_label[verdict.label] += 1
        # label and length only: never the message
        log.info(
            "assess label=%s degraded=%s len=%d", verdict.label, verdict.degraded, len(body.message)
        )
        return JSONResponse(verdict.to_json(), headers={"Cache-Control": "no-store"})

    @app.post("/report")
    async def report(
        request: Request, body: ReportBody, p: Annotated[Principal, Depends(principal)]
    ) -> JSONResponse:
        """Record a scam report (category + optional coarse location only) and emit a
        ``Complaint`` event when a location code was given. Idempotent on ``message_hash``."""
        limit(request, p, "report", cfg.report_per_min)
        if report_key is None:
            raise HTTPException(503, "reporting not configured")
        if body.message_hash:
            digest = keyed_hash(body.message_hash, REPORT_KEY_DOMAIN, report_key)
            report_id = "rpt-" + digest[:48]
        else:
            report_id = "rpt-" + uuid.uuid4().hex  # no content key: retries are not deduplicated
        located = body.city_code is not None or body.state_code is not None
        try:
            row = await asyncio.to_thread(
                store.add, report_id, body.category, body.city_code, body.state_code, clock(),
                not located,
            )  # fmt: skip
        except Exception as e:
            log.warning("report store failed (%s)", type(e).__name__)
            raise HTTPException(503, "store unavailable") from None
        counters["reports"] += 1
        emitted = False
        if row["duplicate"]:
            counters["report_duplicates"] += 1
        if not located:
            counters["report_unlocated"] += 1
        elif bus is not None and not (row["duplicate"] and row["emitted"]):
            complaint = Complaint(
                complaint_id=report_id, ts=row["created_at"], kind=row["category"],
                city_code=row["city_code"], state_code=row["state_code"],
            )  # fmt: skip
            try:
                await bus.publish(Topics.COMPLAINTS, report_id, complaint)
                await asyncio.to_thread(store.mark_emitted, report_id)
                counters["report_emitted"] += 1
                emitted = True
            except Exception as e:
                counters["emit_failures"] += 1
                log.warning("complaint publish failed (%s)", type(e).__name__)
                raise HTTPException(503, "could not record the report, please retry") from None
        elif row["duplicate"] and row["emitted"]:
            emitted = True
        return JSONResponse(
            {
                "accepted": True,
                "report_id": report_id,
                "duplicate": row["duplicate"],
                "emitted": emitted,
            }  # fmt: skip
        )

    @app.get("/languages")
    async def languages(_p: Annotated[Principal, Depends(principal)]) -> dict[str, Any]:
        return {
            "default": "en",
            "supported": [{"code": c, "name": LANGUAGE_NAMES[c]} for c in SUPPORTED],
        }

    @app.get("/metrics")
    async def metrics(request: Request) -> Response:
        token = os.getenv("CITIZEN_METRICS_TOKEN")
        ok = bool(token) and hmac.compare_digest(
            request.headers.get("x-metrics-token", "").encode("utf-8", "replace"), token.encode()
        )
        if not ok:
            if not _trusted(request):
                raise HTTPException(401, "metrics require CITIZEN_METRICS_TOKEN or gateway trust")
            if not request.headers.get("x-principal-role") or not request.headers.get(
                "x-principal-sub"
            ):
                raise HTTPException(401, "missing principal")
            if request.headers.get("x-principal-role", "").strip() != "admin":
                raise HTTPException(403, "role not permitted")
        lines: list[str] = []
        for k, v in sorted(counters.items()):
            lines += [f"# TYPE citizen_shield_{k}_total counter", f"citizen_shield_{k}_total {v}"]
        lines.append("# TYPE citizen_shield_verdicts_total counter")
        for label, v in sorted(by_label.items()):
            lines.append(f'citizen_shield_verdicts_total{{label="{label}"}} {v}')
        extra = {
            "guard_calls_total": ("counter", guard.calls),
            "guard_failures_total": ("counter", guard.failures_total),
            "guard_skipped_total": ("counter", guard.skipped_total),
            "guard_breaker_open": ("gauge", int(guard.breaker_open)),
            "limiter_errors_total": ("counter", limiter.errors),
        }
        for k, (kind, v) in extra.items():
            lines += [f"# TYPE citizen_shield_{k} {kind}", f"citizen_shield_{k} {v}"]
        return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    return app


def create_service_app() -> FastAPI:
    """Env-configured app: CALL_GUARD_URL, CITIZEN_DATABASE_URL, CITIZEN_REPORT_KEY (required),
    KAFKA_BOOTSTRAP (report events are published only when set), and the gateway trust variables."""
    from svckit.bus import KafkaBus

    raw_key = os.environ.get("CITIZEN_REPORT_KEY", "")
    if len(raw_key.encode()) < 16:
        raise RuntimeError("CITIZEN_REPORT_KEY must be set (at least 16 bytes)")
    kafka = os.getenv("KAFKA_BOOTSTRAP")
    bus: Bus | None = KafkaBus(kafka) if kafka else None
    closers: list[Callable[[], Awaitable[None]]] = []
    if bus is not None and hasattr(bus, "close"):
        closers.append(bus.close)
    return create_app(bus=bus, report_key=raw_key.encode(), closers=closers)


__all__ = ["create_app", "create_service_app"]
