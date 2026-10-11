"""HTTP API for geo-intel (a read model over the complaints topic).

Trust model: reachable only through the gateway on the internal network. The principal headers
(``X-Principal-Role`` / ``-Sub``) are trusted ONLY when ``GEO_GATEWAY_SECRET`` is set and the
request carries it in ``X-Gateway-Secret`` (constant-time compare of UTF-8 bytes; non-ASCII input
simply fails the compare), or when ``TRUST_GATEWAY_HEADERS=1``. Otherwise every endpoint except
the health probes answers 401. Roles allowed to read: officer, analyst, admin.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from svckit.bus import Bus, InMemoryBus
from svckit.health import make_health_router

from .compute import MODEL_VERSION, build_hotspots, city_view, spans
from .config import Settings
from .service import GeoService
from .store import GeoStore, aware
from .table import BY_CODE, CITIES, CITY_CODE

log = logging.getLogger("geo_intel")
access_log = logging.getLogger("geo_intel.access")

READERS = {"officer", "analyst", "admin"}
RID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
WINDOW_RE = re.compile(r"^([1-9][0-9]{0,6})([smhd])$")
_UNIT_S = {"s": 1, "m": 60, "h": 3600, "d": 86_400}


@dataclass(frozen=True)
class Principal:
    role: str
    sub: str


def _trusted(request: Request) -> bool:
    secret = os.getenv("GEO_GATEWAY_SECRET")
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
    if role not in READERS:
        raise HTTPException(403, "role not permitted")
    return Principal(role, sub)


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


def _etag_matches(request: Request, etag: str) -> bool:
    inm = request.headers.get("if-none-match", "")
    if inm.strip() == "*":
        return True
    bare = etag.removeprefix("W/")
    return bare in [t.strip().removeprefix("W/") for t in inm.split(",")]


def _etag(body: Any) -> str:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return f'W/"{hashlib.sha256(raw).hexdigest()[:32]}"'


def _parse_window(raw: str | None, cfg: Settings) -> float:
    if raw is None:
        return cfg.window_s
    m = WINDOW_RE.match(raw)
    if m is None:
        raise HTTPException(422, "window must look like 90s, 10m, 1h or 2d")
    secs = int(m.group(1)) * _UNIT_S[m.group(2)]
    if not cfg.min_window_s <= secs <= cfg.max_window_s:
        raise HTTPException(422, "window out of range")
    return float(secs)


def _parse_ts(raw: str | None, name: str) -> datetime | None:
    if raw is None:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, f"{name} must be an ISO-8601 timestamp") from None
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise HTTPException(422, f"{name} must carry a timezone")
    return dt.astimezone(UTC)


def create_app(
    database_url: str | None = None,
    bus: Bus | None = None,
    clock: Callable[[], datetime] | None = None,
    settings: Settings | None = None,
    consume: bool = False,
    background: bool = True,
    closers: list[Callable[[], Awaitable[None]]] | None = None,
    store: GeoStore | None = None,
) -> FastAPI:
    cfg = settings or Settings.from_env()
    store = store or GeoStore(
        database_url or os.getenv("GEO_DATABASE_URL", "sqlite://"),
        create_schema=os.getenv("GEO_CREATE_SCHEMA", "1") == "1",
    )
    service = GeoService(store, cfg, bus or InMemoryBus(), clock)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        service.start(consumers=consume, loops=background)
        try:
            yield
        finally:
            await service.stop()
            for close in closers or []:
                try:
                    await close()
                except Exception:
                    log.warning("close failed")
            await asyncio.to_thread(store.close)

    async def ready() -> bool:
        return await asyncio.to_thread(store.ping)

    app = FastAPI(title="geo-intel", lifespan=lifespan)
    app.add_middleware(BodyLimit, max_bytes=cfg.max_body_bytes)
    app.state.service = service
    app.state.store = store

    @app.middleware("http")
    async def access(request: Request, call_next: Callable[[Request], Awaitable[Response]]):
        """Minimal structured access log: method, route template, status, request id, duration.
        Never the query string, headers or body (run uvicorn with access_log=False)."""
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
        # never echo the submitted value (FastAPI's default includes "input" / "ctx")
        return JSONResponse(
            {
                "detail": [
                    {"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()
                ]
            },
            status_code=422,
        )

    app.include_router(make_health_router(ready))

    async def db(fn: Callable[..., Any], *args: Any, **kw: Any) -> Any:
        """Run a store call off the loop; any failure is a generic 503 (no internals leak)."""
        try:
            return await asyncio.to_thread(fn, *args, **kw)
        except Exception as e:
            log.warning("store call failed (%s)", type(e).__name__)
            raise HTTPException(503, "store unavailable") from None

    def respond(request: Request, body: Any) -> Response:
        etag = _etag(body)
        if _etag_matches(request, etag):
            return Response(status_code=304, headers={"ETag": etag})
        return JSONResponse(body, headers={"ETag": etag})

    async def as_of_for(at: datetime | None) -> datetime:
        if at is not None:
            return at
        mark = await db(service.watermark)
        return mark if mark is not None else aware(service.clock())

    @app.get("/hotspots")
    async def hotspots(
        request: Request,
        _p: Annotated[Principal, Depends(principal)],
        window: Annotated[str | None, Query(max_length=12)] = None,
        at: Annotated[str | None, Query(max_length=40)] = None,
    ) -> Response:
        """Hotspot cells (one per city) for a trailing window ending at ``at`` (default: the
        newest accepted complaint, so a replayed simulation reads as of its own clock)."""
        w = _parse_window(window, cfg)
        end = await as_of_for(_parse_ts(at, "at"))
        base_start, prev_start, cur_start = spans(end, w, cfg.baseline_windows)
        rows = await db(store.window_counts, base_start, prev_start, cur_start, end)
        return respond(request, build_hotspots(rows, end, w, cfg))

    @app.get("/points")
    async def points(
        request: Request,
        _p: Annotated[Principal, Depends(principal)],
        since: Annotated[str | None, Query(max_length=40)] = None,
        before: Annotated[int | None, Query(ge=1, le=2**62)] = None,
        limit: Annotated[int | None, Query(ge=1, le=10_000)] = None,
    ) -> Response:
        """Newest-first complaint points (city centres, never the complainant). ``since`` filters
        by event time; ``before`` is the ``next_before`` cursor of the previous page."""
        n = min(limit or cfg.default_limit, cfg.max_limit)
        rows = await db(store.points, since=_parse_ts(since, "since"), before=before, limit=n)
        items = []
        for r in rows:
            if r["city_code"] not in BY_CODE:
                continue
            items.append(
                {
                    "seq": r["seq"],
                    "ts": aware(r["ts"]).isoformat(),
                    "kind": r["kind"],
                    **city_view(r["city_code"]),
                    "precision": r["precision"],
                    "amount_bucket": r["amount_bucket"],
                }  # fmt: skip
            )
        nxt = rows[-1]["seq"] if len(rows) == n and rows else None
        return respond(request, {"items": items, "count": len(items), "next_before": nxt})

    @app.get("/cities")
    async def cities(request: Request, _p: Annotated[Principal, Depends(principal)]) -> Response:
        items = [
            {**city_view(CITY_CODE[c[0]]), "weight": c[4]}
            for c in sorted(CITIES, key=lambda c: CITY_CODE[c[0]])
        ]
        return respond(
            request, {"items": items, "count": len(items), "model_version": MODEL_VERSION}
        )

    @app.get("/metrics")
    async def metrics(request: Request) -> Response:
        token = os.getenv("GEO_METRICS_TOKEN")
        ok = bool(token) and hmac.compare_digest(
            request.headers.get("x-metrics-token", "").encode("utf-8", "replace"), token.encode()
        )
        if not ok:
            if not _trusted(request):
                raise HTTPException(401, "metrics require GEO_METRICS_TOKEN or gateway trust")
            if request.headers.get("x-principal-role", "") not in READERS:
                raise HTTPException(403, "role not permitted")
        try:
            rows = await asyncio.to_thread(store.count)
        except Exception:
            rows = -1
        lines = []
        for k, v in sorted(service.counters.items()):
            lines += [f"# TYPE geo_intel_{k}_total counter", f"geo_intel_{k}_total {v}"]
        lines += ["# TYPE geo_intel_rows gauge", f"geo_intel_rows {rows}"]
        return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    return app


def create_service_app() -> FastAPI:
    """Env-configured app: GEO_DATABASE_URL, KAFKA_BOOTSTRAP (consumer only when set), the GEO_*
    settings and the gateway trust variables."""
    from svckit.bus import KafkaBus

    kafka = os.getenv("KAFKA_BOOTSTRAP")
    bus: Bus = KafkaBus(kafka) if kafka else InMemoryBus()
    closers: list[Callable[[], Awaitable[None]]] = []
    if hasattr(bus, "close"):
        closers.append(bus.close)
    return create_app(bus=bus, consume=bool(kafka), closers=closers)


__all__ = ["create_app", "create_service_app"]
