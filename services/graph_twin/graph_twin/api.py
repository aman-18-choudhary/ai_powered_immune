"""HTTP API for graph-twin.

Trust model: reachable only through the gateway on the internal network. The principal headers
(``X-Principal-Role`` / ``-Sub``) are trusted ONLY when ``GRAPH_GATEWAY_SECRET`` is set and the
request carries it in ``X-Gateway-Secret`` (constant-time compare of UTF-8 bytes; non-ASCII input
simply fails the compare), or when ``TRUST_GATEWAY_HEADERS=1``. Otherwise every endpoint except
the health probes answers 401. Roles: officer, analyst, admin read; officer and admin simulate.
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
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from svckit.bus import Bus, InMemoryBus
from svckit.health import make_health_router

from . import audit
from .config import Settings
from .graph import api_id, paise_str, to_us
from .model import MODEL_VERSION, Ring
from .service import TwinService
from .store import GraphStore
from .takedown import (
    TooLarge,
    best_flow,
    extract_flow_input,
    node_flow_ids,
    simulate_flow,
)
from .twin import GraphTwin
from .views import graph_view, list_etag, ring_etag, ring_summary

log = logging.getLogger("graph_twin")
access_log = logging.getLogger("graph_twin.access")

READERS = {"officer", "analyst", "admin"}
SIMULATORS = {"officer", "admin"}
RING_ID_RE = r"^[0-9a-f]{16}$"
NODE_ID_RE = r"^[0-9a-f]{12}$"
RID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
RingIdPath = Annotated[str, Path(pattern=RING_ID_RE)]
NodeId = Annotated[str, Field(pattern=NODE_ID_RE)]


@dataclass(frozen=True)
class Principal:
    role: str
    sub: str


def _trusted(request: Request) -> bool:
    secret = os.getenv("GRAPH_GATEWAY_SECRET")
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
    return Principal(role, sub)


def need(p: Principal, roles: set[str]) -> None:
    if p.role not in roles:
        raise HTTPException(403, "role not permitted")


class SimBody(BaseModel):
    remove: Annotated[list[NodeId], Field(max_length=50)] | None = None
    k: Annotated[int, Field(ge=1, le=5)] | None = None

    @model_validator(mode="after")
    def _one_of(self) -> "SimBody":
        if (self.remove is None) == (self.k is None):
            raise ValueError("send exactly one of remove or k")
        return self


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


def _listed(r: Ring, status: str | None) -> bool:
    """Retired rings (tombstones) appear only when asked for with ``status=retired``."""
    return r.status != "retired" if status is None else r.status == status


def _etag_matches(request: Request, etag: str) -> bool:
    inm = request.headers.get("if-none-match", "")
    if inm.strip() == "*":
        return True
    bare = etag.removeprefix("W/")
    return bare in [t.strip().removeprefix("W/") for t in inm.split(",")]


def create_app(
    database_url: str | None = None,
    bus: Bus | None = None,
    clock: Callable[[], datetime] | None = None,
    settings: Settings | None = None,
    consume: bool = False,
    background: bool = True,
    closers: list[Callable[[], Awaitable[None]]] | None = None,
) -> FastAPI:
    cfg = settings or Settings.from_env()
    store = GraphStore(
        database_url or os.getenv("GRAPH_DATABASE_URL", "sqlite://"),
        create_schema=os.getenv("GRAPH_CREATE_SCHEMA", "1") == "1",
    )
    twin = GraphTwin(store, cfg, clock)
    service = TwinService(twin, bus or InMemoryBus())

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

    app = FastAPI(title="graph-twin", lifespan=lifespan)
    app.add_middleware(BodyLimit, max_bytes=cfg.max_body_bytes)
    app.state.twin = twin
    app.state.service = service

    @app.middleware("http")
    async def access(request: Request, call_next: Callable[[Request], Awaitable[Response]]):
        """Minimal structured access log: method, route template, status, request id, duration.
        Never the query string, headers or body (run uvicorn with access_log=False). An incoming
        ``X-Request-Id`` is kept only if it is 8-64 chars of [A-Za-z0-9-]."""
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

    def get_ring(ring_id: str) -> Ring:
        r = twin.resolve_published(ring_id)
        if r is None:
            raise HTTPException(404, "ring not found")
        return r

    def no_takedown_when_retired(r: Ring) -> None:
        if r.status == "retired":
            raise HTTPException(409, "ring retired: its evidence no longer passes the gates")

    def not_modified(request: Request, etag: str) -> Response | None:
        if _etag_matches(request, etag):
            return Response(status_code=304, headers={"ETag": etag})
        return None

    def json_resp(body: Any, etag: str | None = None) -> JSONResponse:
        return JSONResponse(body, headers={"ETag": etag} if etag else None)

    @app.get("/metrics")
    async def metrics(request: Request) -> Response:
        token = os.getenv("GRAPH_METRICS_TOKEN")
        ok = bool(token) and hmac.compare_digest(
            request.headers.get("x-metrics-token", "").encode("utf-8", "replace"), token.encode()
        )
        if not ok:
            if not _trusted(request):
                raise HTTPException(401, "metrics require GRAPH_METRICS_TOKEN or gateway trust")
            if request.headers.get("x-principal-role", "") not in READERS:
                raise HTTPException(403, "role not permitted")
        st = await asyncio.to_thread(twin.stats)
        pending_audit = await asyncio.to_thread(store.count_audit_pending)
        lines = []
        for k, v in sorted(st.items()):
            kind = (
                "gauge"
                if k in ("nodes", "edges", "rings", "rings_retired", "pending_decisions", "dirty")
                else "counter"
            )
            lines += [f"# TYPE graph_twin_{k} {kind}", f"graph_twin_{k} {v}"]
        extra = {
            "audit_pending": ("gauge", pending_audit),
            "audit_sent_total": ("counter", service.audit_sent),
            "audit_dropped_total": ("counter", service.audit_dropped),
            "heavy_timeouts_total": ("counter", service.heavy_timeouts),
            "detect_errors_total": ("counter", service.detect_errors),
            "background_tasks": ("gauge", len(service._bg)),
            "drain_tasks": ("gauge", service.drainer.live_tasks),
            "drain_breaker_open": ("gauge", int(service.drainer.breaker_open)),
        }
        for k, (kind, v) in extra.items():
            lines += [f"# TYPE graph_twin_{k} {kind}", f"graph_twin_{k} {v}"]
        return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    @app.get("/stats")
    async def stats(p: Annotated[Principal, Depends(principal)]) -> dict[str, Any]:
        need(p, READERS)
        st = await asyncio.to_thread(twin.stats)
        return {
            "model_version": MODEL_VERSION, **st, "retention_days": cfg.retention_days,
            "max_nodes": cfg.max_nodes,
        }  # fmt: skip

    @app.get("/rings")
    async def list_rings(
        request: Request,
        p: Annotated[Principal, Depends(principal)],
        status: Literal["new", "growing", "stable", "retired"] | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> Response:
        need(p, READERS)
        rings = sorted(
            (r for r in twin.published.values() if _listed(r, status)),
            key=lambda r: (r.first_detected_us, r.ring_id),
        )[:limit]
        etag = list_etag(rings, status, limit)
        if (nm := not_modified(request, etag)) is not None:
            return nm
        return json_resp({"items": [ring_summary(r) for r in rings], "count": len(rings)}, etag)

    @app.get("/rings/{ring_id}")
    async def one_ring(
        request: Request, ring_id: RingIdPath, p: Annotated[Principal, Depends(principal)]
    ) -> Response:
        need(p, READERS)
        r = get_ring(ring_id)
        etag = ring_etag(r)
        if (nm := not_modified(request, etag)) is not None:
            return nm
        return json_resp(ring_summary(r), etag)

    @app.get("/rings/{ring_id}/graph")
    async def ring_graph(
        request: Request, ring_id: RingIdPath, p: Annotated[Principal, Depends(principal)]
    ) -> Response:
        need(p, READERS)
        r = get_ring(ring_id)
        etag = ring_etag(r, f"-g{cfg.max_response_nodes}")
        if (nm := not_modified(request, etag)) is not None:
            return nm

        def build() -> dict[str, Any]:
            with twin.lock:
                return graph_view(twin, r, cfg.max_response_nodes)

        return json_resp(await asyncio.to_thread(build), etag)

    async def flow_input(r: Ring):  # type: ignore[no-untyped-def]
        def build():  # type: ignore[no-untyped-def]
            with twin.lock:
                return extract_flow_input(twin.tg.g, r, cfg)

        try:
            return await asyncio.to_thread(build)
        except TooLarge as e:
            raise HTTPException(422, "ring too large to simulate") from e

    async def run_flow(fn):  # type: ignore[no-untyped-def]
        try:
            return await service.run_heavy(fn)
        except TimeoutError as e:
            raise HTTPException(503, "simulation timed out") from e

    def result_body(r: Ring, res, mode: str, selected: list[str]):  # type: ignore[no-untyped-def]
        return {
            "ring_id": r.ring_id, "ring_version": r.version, "model_version": MODEL_VERSION,
            "mode": mode, "horizon_days": cfg.harm_horizon_days,
            "harm_before_inr": paise_str(int(res.harm_before_inr * 100)),
            "harm_after_inr": paise_str(int(res.harm_after_inr * 100)),
            "reduction_pct": res.reduction_pct,
            "removed": sorted(api_id(k) for k in res.removed),
            "selected": sorted(api_id(k) for k in selected) if mode == "k" else [],
            "edges_cut": sorted(
                [{"src": api_id(a), "dst": api_id(b)} for a, b in res.edges_cut],
                key=lambda e: (e["src"], e["dst"]),
            ),
            "model_note": "projection of observed rates, not a prediction",
        }  # fmt: skip

    @app.post("/rings/{ring_id}/simulate")
    async def simulate(
        ring_id: RingIdPath, body: SimBody, p: Annotated[Principal, Depends(principal)]
    ) -> JSONResponse:
        need(p, SIMULATORS)
        r = get_ring(ring_id)
        no_takedown_when_retired(r)
        fi = await flow_input(r)
        by_id: dict[str, list[str]] = {}
        for k in node_flow_ids(fi):
            by_id.setdefault(api_id(k), []).append(k)
        selected: list[str] = []
        if body.k is not None:
            mode = "k"
            selected = await run_flow(lambda: best_flow(fi, body.k, cfg))  # type: ignore[arg-type]
            remove = selected
        else:
            mode = "list"
            remove = []
            for nid in body.remove or []:
                keys = by_id.get(nid)
                if not keys or len(keys) != 1:
                    raise HTTPException(422, "unknown node id for this ring")
                remove.append(keys[0])
        res = await run_flow(lambda: simulate_flow(fi, remove, cfg))
        out = result_body(r, res, mode, selected)
        service.audit_takedown(
            audit.takedown_row(
                p.sub,
                r,
                mode,
                len(set(remove)),
                res.reduction_pct,
                res.harm_before_inr,
                to_us(twin.clock()),
                sorted(api_id(k) for k in remove),
            )
        )
        return JSONResponse(out)

    @app.get("/rings/{ring_id}/best-takedown")
    async def best_takedown_route(
        ring_id: RingIdPath, p: Annotated[Principal, Depends(principal)],
        k: Annotated[int, Query(ge=1, le=5)] = 1,
    ) -> JSONResponse:  # fmt: skip
        need(p, READERS)
        r = get_ring(ring_id)
        no_takedown_when_retired(r)
        fi = await flow_input(r)
        chosen = await run_flow(lambda: best_flow(fi, k, cfg))
        res = await run_flow(lambda: simulate_flow(fi, chosen, cfg))
        out = result_body(r, res, "k", chosen)
        out["k"] = k
        return JSONResponse(out)

    return app


def create_service_app() -> FastAPI:
    """Env-configured app: GRAPH_DATABASE_URL, KAFKA_BOOTSTRAP, the GRAPH_* settings and the
    gateway trust variables. Consumers run only when a Kafka bootstrap is configured."""
    from svckit.bus import KafkaBus

    kafka = os.getenv("KAFKA_BOOTSTRAP")
    bus: Bus = KafkaBus(kafka) if kafka else InMemoryBus()
    closers: list[Callable[[], Awaitable[None]]] = []
    if hasattr(bus, "close"):
        closers.append(bus.close)
    return create_app(bus=bus, consume=bool(kafka), closers=closers)


__all__ = ["create_app", "create_service_app"]
