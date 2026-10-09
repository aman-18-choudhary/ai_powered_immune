"""HTTP API for the antibody hub.

Trust model: reachable only through the gateway on the internal network. The principal headers
(``X-Principal-Role`` / ``-Sub`` / ``-Bank``) are trusted ONLY when ``HUB_GATEWAY_SECRET`` is set
and the request carries it in ``X-Gateway-Secret`` (constant-time, bytes compare), or when
``TRUST_GATEWAY_HEADERS=1``. Otherwise every endpoint except the health probes answers 401.
"""

import asyncio
import hmac
import json
import logging
import os
import re
import time
import unicodedata
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import AfterValidator, BaseModel, Field
from svckit.bus import Bus, InMemoryBus
from svckit.health import make_health_router

from .consumer import run_maintenance
from .hub import Hub
from .store import (
    ANTIBODY_TTL_DAYS,
    AntibodyStore,
    NotFound,
    Protected,
    decode_cursor,
)

log = logging.getLogger("antibody_hub")

HASH_RE = re.compile(r"^[0-9a-f]{64}$")
ANALYSTS = {"analyst", "admin"}
READERS = {"officer", "analyst", "admin"}
BANK_READERS = {"bank", "admin"}
Kind = Literal["mule_account", "script", "device"]
HashStr = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


FREE_TEXT_MAX = 200
SOURCE_BANK_RE = r"^[a-z0-9_-]{2,32}$"

_RULES = [
    re.compile(r"\d(?:\D{0,3}\d){8,}"),  # 9+ digits, up to 3 non-digits between any two
    re.compile(r"@\s*[a-z0-9]"),  # e-mail / UPI handle (also "name @ bank")
    re.compile(r"\+\s*\d"),  # international prefix
    re.compile(r"(?<!\d)0091"),
    re.compile(r"\b[a-z]{5}\d{4}[a-z]\b"),  # PAN
    re.compile(r"\b[a-z]{4}0[a-z0-9]{6}\b"),  # IFSC
]


def _flatten(s: str) -> str:
    """NFKC + casefold; whitespace/separators -> one space; format/control chars removed."""
    s = unicodedata.normalize("NFKC", s).casefold()
    out = []
    for ch in s:
        cat = unicodedata.category(ch)
        if ch.isspace() or cat.startswith("Z"):
            out.append(" ")
        elif cat in ("Cf", "Cc"):
            continue
        else:
            out.append(ch)
    return re.sub(r" +", " ", "".join(out))


def contains_identifier(text: str) -> bool:
    """Safety-net guard shared by evidence_ref, reason and note (see README for known limits)."""
    flat = _flatten(text)
    if any(rx.search(flat) for rx in _RULES):
        return True
    alnum = "".join(ch for ch in flat if ch.isalnum())
    return re.search(r"\d{9,}", alnum) is not None  # digits split only by punctuation/space


def _no_identifiers(v: str) -> str:
    if contains_identifier(v):
        raise ValueError("free text must not contain account, phone, e-mail or UPI identifiers")
    return v


FreeText = Annotated[
    str, Field(min_length=1, max_length=FREE_TEXT_MAX), AfterValidator(_no_identifiers)
]
EvidenceRef = Annotated[
    str, Field(pattern=r"^[A-Za-z0-9._:-]{1,64}$"), AfterValidator(_no_identifiers)
]


@dataclass(frozen=True)
class Principal:
    role: str
    sub: str
    bank: str | None = None


def _trusted(request: Request) -> bool:
    secret = os.getenv("HUB_GATEWAY_SECRET")
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
    bank = request.headers.get("x-principal-bank", "").strip() or None
    return Principal(role, sub, bank)


def need(p: Principal, roles: set[str]) -> None:
    if p.role not in roles:
        raise HTTPException(403, "role not permitted")


class SubmitBody(BaseModel):
    # confirmed_by is deliberately absent: it always comes from the authenticated principal
    kind: Kind
    key_hash: HashStr
    source_bank: str = Field(pattern=SOURCE_BANK_RE)
    evidence_ref: EvidenceRef | None = None
    extend: bool = False


class RevokeBody(BaseModel):
    reason: FreeText


class ProtectedBody(BaseModel):
    key_hash: HashStr
    note: FreeText | None = None


def view(rec: dict[str, Any]) -> dict[str, Any]:
    out = dict(rec)
    for k in ("created_at", "expires_at", "revoked_at"):
        out[k] = out[k].isoformat() if out.get(k) else None
    return out


def scoped_view(rec: dict[str, Any], p: Principal, now: datetime) -> dict[str, Any]:
    """Full view, except that an analyst bound to ANOTHER bank only sees the shared datum
    (key_hash is already broadcast to every bank) and the lifecycle state."""
    if p.bank is None or p.bank == rec["source_bank"] or p.role in ("officer", "admin"):
        return view(rec)
    return {
        "antibody_id": rec["antibody_id"], "kind": rec["kind"], "key_hash": rec["key_hash"],
        "active": not rec["revoked"] and rec["expires_at"] > now,
        "expires_at": rec["expires_at"].isoformat(), "revoked": rec["revoked"],
    }  # fmt: skip


def create_app(
    database_url: str | None = None,
    bus: Bus | None = None,
    clock: Callable[[], datetime] | None = None,
    banks: list[str] | None = None,
    drain_interval_s: float | None = None,
    closers: list[Callable[[], Awaitable[None]]] | None = None,
) -> FastAPI:
    clock = clock or (lambda: datetime.now(UTC))
    bank_set = set(
        banks
        if banks is not None
        else [b.strip() for b in os.getenv("HUB_BANKS", "").split(",") if b.strip()]
    )
    ttl = int(os.getenv("ANTIBODY_TTL_DAYS", str(ANTIBODY_TTL_DAYS)))
    store = AntibodyStore(
        database_url or os.getenv("HUB_DATABASE_URL", "sqlite://"), ttl_days=ttl, clock=clock,
        create_schema=os.getenv("HUB_CREATE_SCHEMA", "1") == "1",
        park_attempts=int(os.getenv("HUB_PARK_ATTEMPTS", "10")),
        park_after_s=float(os.getenv("HUB_PARK_AFTER_S", "900")),
    )  # fmt: skip
    hub = Hub(
        store, bus or InMemoryBus(),
        retention_days=float(os.getenv("HUB_OUTBOX_RETENTION_DAYS", "7")),
    )  # fmt: skip
    if drain_interval_s is None:
        drain_interval_s = float(os.getenv("HUB_DRAIN_INTERVAL_S", "5"))
    tasks: list[asyncio.Task[None]] = []

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if drain_interval_s > 0:
            tasks.append(asyncio.create_task(run_maintenance(hub, drain_interval_s)))
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for close in closers or []:
                try:
                    await close()
                except Exception:
                    log.warning("close failed", exc_info=True)
            await asyncio.to_thread(store.close)

    async def ready() -> bool:
        return await asyncio.to_thread(store.ping)

    app = FastAPI(title="antibody-hub", lifespan=lifespan)
    app.state.hub = hub

    access_log = logging.getLogger("antibody_hub.access")

    @app.middleware("http")
    async def access(request: Request, call_next: Callable[[Request], Awaitable[Response]]):
        """Minimal structured access log: method, route template, status, request id, duration.
        Never the query string, headers or body (run uvicorn with access_log=False)."""
        rid = uuid.uuid4().hex[:12]
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
        # never echo the submitted value (FastAPI's default includes "input"/"ctx")
        return JSONResponse(
            {
                "detail": [
                    {"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()
                ]
            },
            status_code=422,
        )

    app.include_router(make_health_router(ready))

    def bank_check(p: Principal, bank_id: str) -> None:
        need(p, BANK_READERS)
        if bank_id not in bank_set:
            raise HTTPException(403, "unknown bank")
        if p.role == "bank" and p.bank != bank_id:
            raise HTTPException(403, "bank mismatch")

    @app.get("/metrics")
    async def metrics(request: Request) -> Response:
        token = os.getenv("HUB_METRICS_TOKEN")
        ok = bool(token) and hmac.compare_digest(
            request.headers.get("x-metrics-token", "").encode("utf-8", "replace"), token.encode()
        )
        if not ok:
            if not _trusted(request):
                raise HTTPException(401, "metrics require HUB_METRICS_TOKEN or gateway trust")
            if request.headers.get("x-principal-role", "") not in READERS:
                raise HTTPException(403, "role not permitted")
        active = await asyncio.to_thread(store.count_active)
        pending = await asyncio.to_thread(store.count_pending)
        body = (
            "# TYPE antibody_hub_active gauge\n"
            f"antibody_hub_active {active}\n"
            "# TYPE antibody_hub_outbox_pending gauge\n"
            f"antibody_hub_outbox_pending {pending}\n"
            "# TYPE antibody_hub_outbox_parked gauge\n"
            f"antibody_hub_outbox_parked {await asyncio.to_thread(store.count_parked)}\n"
            "# TYPE antibody_hub_outbox_unparked_total counter\n"
            f"antibody_hub_outbox_unparked_total {hub.unparked_total}\n"
        )
        return Response(body, media_type="text/plain; version=0.0.4")

    @app.post("/antibodies", status_code=201)
    async def submit(
        body: SubmitBody, response: Response, p: Annotated[Principal, Depends(principal)]
    ) -> dict[str, Any]:
        need(p, ANALYSTS)
        if bank_set and body.source_bank not in bank_set:
            raise HTTPException(422, "unregistered source_bank")
        if p.bank is not None and body.source_bank != p.bank:
            raise HTTPException(403, "source_bank must equal the principal's bank")
        try:
            res = await hub.submit(
                body.kind,
                body.key_hash,
                body.source_bank,
                p.sub,
                body.evidence_ref,
                body.extend,
                role=p.role,
            )
        except Protected as e:
            raise HTTPException(409, "PROTECTED") from e
        if not res.created:
            response.status_code = 200
        return scoped_view(res.record, p, clock())

    @app.api_route("/antibodies/bloom", methods=["GET", "HEAD"])
    async def bloom(
        request: Request, bank_id: str, p: Annotated[Principal, Depends(principal)]
    ) -> Response:
        bank_check(p, bank_id)
        version = await hub.bloom_version()
        etag = f'"{version}"'
        inm = request.headers.get("if-none-match", "")
        if etag in [t.strip().removeprefix("W/") for t in inm.split(",")] or inm.strip() == "*":
            return Response(status_code=304, headers={"ETag": etag})
        if request.method == "HEAD":
            return Response(media_type="application/json", headers={"ETag": etag})
        snap = await hub.bloom(version)
        return Response(json.dumps(snap), media_type="application/json", headers={"ETag": etag})

    @app.get("/antibodies/exact")
    async def exact(
        bank_id: str,
        p: Annotated[Principal, Depends(principal)],
        since: str | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    ) -> dict[str, Any]:
        bank_check(p, bank_id)
        if since:
            try:
                decode_cursor(since)
            except ValueError as e:
                raise HTTPException(422, "bad cursor") from e
        rows, nxt = await asyncio.to_thread(store.exact_page, since, limit)
        items = [
            {
                "antibody_id": r["antibody_id"], "kind": r["kind"], "key_hash": r["key_hash"],
                "expires_at": r["expires_at"].isoformat(),
            }
            for r in rows
        ]  # fmt: skip
        return {"items": items, "next_cursor": nxt}

    @app.get("/antibodies")
    async def list_antibodies(
        p: Annotated[Principal, Depends(principal)],
        state: Literal["active", "all"] = "active",
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ) -> list[dict[str, Any]]:
        need(p, READERS)
        now = clock()
        return [
            scoped_view(r, p, now) for r in await asyncio.to_thread(store.listing, state, limit)
        ]

    @app.get("/antibodies/{ab_id}")
    async def get_antibody(
        ab_id: str, p: Annotated[Principal, Depends(principal)]
    ) -> dict[str, Any]:
        need(p, READERS)
        rec = await asyncio.to_thread(store.get, ab_id)
        if rec is None:
            raise HTTPException(404, "antibody not found")
        return scoped_view(rec, p, clock())

    @app.post("/antibodies/{ab_id}/revoke")
    async def revoke(
        ab_id: str, body: RevokeBody, p: Annotated[Principal, Depends(principal)]
    ) -> dict[str, Any]:
        need(p, ANALYSTS)
        try:
            return scoped_view(
                await hub.revoke(ab_id, p.sub, body.reason, p.bank, p.role), p, clock()
            )
        except NotFound as e:
            raise HTTPException(404, "antibody not found") from e

    @app.delete("/antibodies/{ab_id}")
    async def revoke_deprecated(
        ab_id: str, p: Annotated[Principal, Depends(principal)]
    ) -> dict[str, Any]:
        """Removed: a reason in the query string would reach access logs. Use the POST form."""
        need(p, ANALYSTS)
        raise HTTPException(400, "use POST /antibodies/{id}/revoke with a JSON body {reason}")

    @app.post("/admin/outbox/unpark")
    async def unpark(p: Annotated[Principal, Depends(principal)]) -> dict[str, int]:
        need(p, {"admin"})
        n = await hub.unpark()
        return {"unparked": n}

    @app.post("/protected", status_code=201)
    async def add_protected(
        body: ProtectedBody, response: Response, p: Annotated[Principal, Depends(principal)]
    ) -> dict[str, Any]:
        need(p, {"admin"})
        created, revoked = await hub.add_protected(body.key_hash, p.sub, body.note, p.role)
        if not created:
            response.status_code = 200
        return {"key_hash": body.key_hash, "revoked_antibodies": revoked}

    @app.delete("/protected/{key_hash}")
    async def remove_protected(
        key_hash: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
        p: Annotated[Principal, Depends(principal)],
    ) -> dict[str, Any]:
        need(p, {"admin"})
        return {
            "key_hash": key_hash,
            "removed": await hub.remove_protected(key_hash, p.sub, p.role),
        }

    return app


def create_service_app() -> FastAPI:
    """Env-configured app: HUB_DATABASE_URL, KAFKA_BOOTSTRAP, HUB_BANKS, ANTIBODY_TTL_DAYS,
    HUB_DRAIN_INTERVAL_S plus the gateway trust variables."""
    from svckit.bus import KafkaBus

    kafka = os.getenv("KAFKA_BOOTSTRAP")
    bus: Bus = KafkaBus(kafka) if kafka else InMemoryBus()
    closers: list[Callable[[], Awaitable[None]]] = []
    if hasattr(bus, "close"):
        closers.append(bus.close)
    return create_app(bus=bus, closers=closers)
