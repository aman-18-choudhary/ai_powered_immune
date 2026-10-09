"""HTTP API for the evidence ledger.

Trust model: reachable only through the gateway on the internal network. The principal headers
(``X-Principal-Role`` / ``-Sub``) are trusted ONLY when ``LEDGER_GATEWAY_SECRET`` is set and the
request carries it in ``X-Gateway-Secret`` (constant-time bytes compare), or when
``TRUST_GATEWAY_HEADERS=1``. Otherwise every endpoint except the health probes answers 401.
There is deliberately no write endpoint for entries: the only writers are the Kafka consumer and
the export audit entry the service appends itself.
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
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import AfterValidator, BaseModel, Field
from scam_contracts.canonical import payload_hash
from scam_contracts.models import LedgerEntryIn
from svckit.bus import Bus
from svckit.health import make_health_router
from svckit.pii import contains_identifier, string_has_identifier

from . import package as pkg
from .chain import EntryRejected, to_dict
from .consumer import run_maintenance, run_supervised
from .keys import KeyRing, load_keyring
from .store import CaseConflict, LedgerStore
from .verify import verify_chain

log = logging.getLogger("evidence_ledger")

READERS = {"officer", "admin", "analyst"}
OFFICERS = {"officer", "admin"}
SUB_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,100}(@[a-z0-9_-]{2,32})?$")
TITLE_MAX = 120
EXPORT_SERVICE = "evidence-ledger"


def _no_identifiers(v: str) -> str:
    if contains_identifier(v):
        raise ValueError("free text must not contain account, phone, e-mail or UPI identifiers")
    return v


def _opaque(v: str) -> str:
    if string_has_identifier(v):
        raise ValueError("reference must be opaque, not an identifier")
    return v


Title = Annotated[str, Field(min_length=1, max_length=TITLE_MAX), AfterValidator(_no_identifiers)]
Ref = Annotated[str, Field(pattern=r"^[A-Za-z0-9._:-]{1,64}$"), AfterValidator(_opaque)]


class CaseBody(BaseModel):
    case_id: Ref
    title: Title
    case_refs: Annotated[list[Ref], Field(max_length=20)] = []
    seqs: Annotated[list[Annotated[int, Field(ge=1)]], Field(max_length=500)] = []


@dataclass(frozen=True)
class Principal:
    role: str
    sub: str


def _trusted(request: Request) -> bool:
    secret = os.getenv("LEDGER_GATEWAY_SECRET")
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
    if not SUB_RE.match(sub):
        raise HTTPException(403, "principal subject must be a pseudonymous token")
    return Principal(role, sub)


def need(p: Principal, roles: set[str]) -> None:
    if p.role not in roles:
        raise HTTPException(403, "role not permitted")


class BodyLimit:
    """Pure-ASGI request body cap: Content-Length is checked up front, and the received bytes
    are counted as they stream (chunked bodies have no Content-Length). Over the cap -> 413."""

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
        started = rejected = False

        async def counting_receive() -> Any:
            nonlocal seen, rejected
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.max and not rejected:
                    rejected = True  # answer 413 now; whatever the app sends next is dropped
                    await self._reject(send)
                    raise _TooLarge
            return msg

        async def tracking_send(msg: Any) -> None:
            nonlocal started
            if rejected:
                return
            started = started or msg["type"] == "http.response.start"
            await send(msg)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _TooLarge:
            pass
        except Exception:
            if not rejected:
                raise

    @staticmethod
    async def _reject(send: Any) -> None:
        body = b'{"detail":"request body too large"}'
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ]
        await send({"type": "http.response.start", "status": 413, "headers": headers})
        await send({"type": "http.response.body", "body": body})


class _TooLarge(Exception):
    pass


def create_app(
    database_url: str | None = None,
    bus: Bus | None = None,
    clock: Callable[[], datetime] | None = None,
    keyring: KeyRing | None = None,
    checkpoint_every: int | None = None,
    checkpoint_interval_s: float | None = None,
    maintenance_interval_s: float | None = None,
    consume: bool = False,
    closers: list[Callable[[], Awaitable[None]]] | None = None,
    max_package_span: int | None = None,
    max_verify_span: int | None = None,
    max_body_bytes: int | None = None,
) -> FastAPI:
    if keyring is None:
        keyring = load_keyring()  # raises with a clear message when no key is configured
    clock = clock or (lambda: datetime.now(UTC))
    cp_every = checkpoint_every or int(os.getenv("LEDGER_CHECKPOINT_EVERY", "100"))
    cp_interval = (
        checkpoint_interval_s
        if checkpoint_interval_s is not None
        else float(os.getenv("LEDGER_CHECKPOINT_INTERVAL_S", "60"))
    )
    maint = (
        maintenance_interval_s
        if maintenance_interval_s is not None
        else float(os.getenv("LEDGER_MAINTENANCE_INTERVAL_S", "10"))
    )
    pkg_span = max_package_span or int(os.getenv("LEDGER_MAX_PACKAGE_SPAN", "5000"))
    ver_span = max_verify_span or int(os.getenv("LEDGER_MAX_VERIFY_SPAN", "5000"))
    store = LedgerStore(
        database_url or os.getenv("LEDGER_DATABASE_URL", "sqlite://"),
        signer=keyring.signer, checkpoint_every=cp_every, clock=clock,
        create_schema=os.getenv("LEDGER_CREATE_SCHEMA", "1") == "1",
    )  # fmt: skip
    tasks: list[asyncio.Task[None]] = []

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if consume and bus is not None:
            tasks.append(asyncio.create_task(run_supervised(bus, store)))
        if maint > 0:
            tasks.append(asyncio.create_task(run_maintenance(store, maint, cp_interval)))
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

    app = FastAPI(title="evidence-ledger", lifespan=lifespan)
    app.add_middleware(
        BodyLimit,
        max_bytes=max_body_bytes or int(os.getenv("LEDGER_MAX_BODY_BYTES", str(64 * 1024))),
    )
    app.state.store = store
    app.state.keyring = keyring
    access_log = logging.getLogger("evidence_ledger.access")

    @app.middleware("http")
    async def access(request: Request, call_next: Callable[[Request], Awaitable[Response]]):
        """Minimal access log: method, route template, status, request id, duration. Never the
        query string, headers or body (run uvicorn with access_log=False)."""
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

    @app.get("/metrics")
    async def metrics(request: Request) -> Response:
        token = os.getenv("LEDGER_METRICS_TOKEN")
        ok = bool(token) and hmac.compare_digest(
            request.headers.get("x-metrics-token", "").encode("utf-8", "replace"), token.encode()
        )
        if not ok:
            if not _trusted(request):
                raise HTTPException(401, "metrics require LEDGER_METRICS_TOKEN or gateway trust")
            if request.headers.get("x-principal-role", "") not in READERS:
                raise HTTPException(403, "role not permitted")
        snap = store.counters.snapshot()
        head = await asyncio.to_thread(store.head)
        lines = []
        for name in ("appended", "duplicates", "rejected", "dlq", "checkpoints"):
            lines += [
                f"# TYPE evidence_ledger_{name}_total counter",
                f"evidence_ledger_{name}_total {snap[name]}",
            ]
        lines += ["# TYPE evidence_ledger_head_seq gauge", f"evidence_ledger_head_seq {head.seq}"]
        return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    # ------------------------------------------------------------------ chain reads
    @app.get("/head")
    async def head(p: Annotated[Principal, Depends(principal)]) -> dict[str, Any]:
        need(p, READERS)
        h = await asyncio.to_thread(store.head)
        return {"seq": h.seq, "entry_hash": h.entry_hash, "count": h.count}

    @app.get("/entries")
    async def entries(
        p: Annotated[Principal, Depends(principal)],
        from_seq: Annotated[int, Query(ge=1)] = 1,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> dict[str, Any]:
        need(p, READERS)
        rows = await asyncio.to_thread(store.page, from_seq, limit)
        items = [to_dict(e) for e in rows]
        nxt = items[-1]["seq"] + 1 if len(items) == limit else None
        return {"items": items, "next_from_seq": nxt}

    @app.get("/entries/{seq}")
    async def entry(seq: Annotated[int, Path(ge=1)], p: Annotated[Principal, Depends(principal)]):
        need(p, READERS)
        e = await asyncio.to_thread(store.get, seq)
        if e is None:
            raise HTTPException(404, "entry not found")
        return to_dict(e)

    @app.get("/verify")
    async def verify(
        p: Annotated[Principal, Depends(principal)],
        from_seq: Annotated[int, Query(ge=1)] = 1,
        to_seq: Annotated[int | None, Query(ge=1)] = None,
    ) -> dict[str, Any]:
        """Server-side verification of a bounded span (entries, linkage, payload hashes and the
        signed checkpoints inside the span; the whole tail when the span reaches the head)."""
        need(p, OFFICERS)
        h = await asyncio.to_thread(store.head)
        hi = min(to_seq if to_seq is not None else from_seq + ver_span - 1, h.seq)
        if hi - from_seq + 1 > ver_span:
            raise HTTPException(413, f"span exceeds {ver_span} entries; narrow from_seq/to_seq")

        def work() -> dict[str, Any]:
            seg = store.segment(from_seq, hi)
            anchor = None
            if from_seq > 1:
                prev = store.get(from_seq - 1)
                anchor = prev.entry_hash if prev else None
            cps = []
            cur = from_seq
            while True:  # bounded: at most one checkpoint per entry, span <= ver_span
                page = store.checkpoints(cur, 1000)
                cps += [c for c in page if hi >= h.seq or c["seq"] <= hi]
                if len(page) < 1000 or len(cps) > ver_span + 1000:
                    break
                cur = page[-1]["seq"] + 1
            res = verify_chain(
                seg, checkpoints=cps, pubkeys=keyring.public_keys(), anchor_prev_hash=anchor
            )
            return {
                "ok": res.ok, "first_bad_seq": res.first_bad_seq, "reason": res.reason,
                "from_seq": from_seq, "to_seq": hi, "checked": len(seg),
                "checkpoints_checked": len(cps), "head_seq": h.seq,
            }  # fmt: skip

        return await asyncio.to_thread(work)

    @app.get("/checkpoints/latest")
    async def latest_checkpoint(p: Annotated[Principal, Depends(principal)]) -> dict[str, Any]:
        need(p, READERS)
        cp = await asyncio.to_thread(store.latest_checkpoint)
        if cp is None:
            raise HTTPException(404, "no checkpoint yet")
        return cp

    @app.get("/checkpoints")
    async def list_checkpoints(
        p: Annotated[Principal, Depends(principal)],
        from_seq: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> dict[str, Any]:
        need(p, READERS)
        items = await asyncio.to_thread(store.checkpoints, from_seq, limit)
        return {
            "items": items,
            "next_from_seq": items[-1]["seq"] + 1 if len(items) == limit else None,
        }

    @app.get("/keys")
    async def keys(p: Annotated[Principal, Depends(principal)]) -> dict[str, Any]:
        need(p, READERS)
        return {"keys": keyring.describe(), "algorithm": "Ed25519"}

    # ------------------------------------------------------------------ cases and packages
    @app.post("/cases", status_code=201)
    async def create_case(
        body: CaseBody, response: Response, p: Annotated[Principal, Depends(principal)]
    ) -> dict[str, Any]:
        need(p, OFFICERS)
        if not body.case_refs and not body.seqs:
            raise HTTPException(422, "a case needs at least one case_ref or seq")
        if body.seqs:
            try:
                await asyncio.to_thread(store.select_seqs, [], body.seqs, limit=len(body.seqs))
            except LookupError as e:
                raise HTTPException(422, "unknown seq in case") from e
        try:
            rec, created = await asyncio.to_thread(
                store.put_case, body.case_id, body.title, p.sub, body.case_refs, body.seqs
            )
        except CaseConflict as e:
            raise HTTPException(
                409, "case exists with different content; cases are immutable"
            ) from e
        if not created:
            response.status_code = 200
        return rec

    @app.get("/cases/{case_id}")
    async def get_case(
        case_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9._:-]{1,64}$")],
        p: Annotated[Principal, Depends(principal)],
    ) -> dict[str, Any]:
        need(p, OFFICERS)
        rec = await asyncio.to_thread(store.get_case, case_id)
        if rec is None:
            raise HTTPException(404, "case not found")
        return rec

    @app.get("/cases/{case_id}/exports")
    async def case_exports(
        case_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9._:-]{1,64}$")],
        p: Annotated[Principal, Depends(principal)],
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> dict[str, Any]:
        """The case's own export history: `package.exported` ledger entries tagged with it."""
        need(p, OFFICERS)
        if await asyncio.to_thread(store.get_case, case_id) is None:
            raise HTTPException(404, "case not found")
        rows = await asyncio.to_thread(store.refs_page, case_id, pkg.AUDIT_EVENT, limit)
        return {"items": [to_dict(e) for e in rows]}

    @app.get("/packages/{case_id}")
    async def get_package(
        case_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9._:-]{1,64}$")],
        p: Annotated[Principal, Depends(principal)],
    ) -> Response:
        need(p, OFFICERS)
        case = await asyncio.to_thread(store.get_case, case_id)
        if case is None:
            raise HTTPException(404, "case not found")
        try:
            built = await asyncio.to_thread(
                lambda: pkg.build(
                    store,
                    keyring,
                    case_id,
                    title=case["title"],
                    seqs=case["seqs"],
                    case_refs=case["case_refs"],
                    max_span=pkg_span,
                )  # fmt: skip
            )
        except pkg.PackageTooLarge as e:
            raise HTTPException(413, str(e)) from e
        except pkg.EmptyCase as e:
            raise HTTPException(404, "no ledger entries match this case") from e
        except pkg.NotCovered as e:
            raise HTTPException(409, "no signed checkpoint covers this case yet") from e
        payload = {
            "case_id": case_id, "package_sha256": built.sha256, "from_seq": built.from_seq,
            "to_seq": built.to_seq, "exported_by": p.sub,
        }  # fmt: skip
        audit = LedgerEntryIn(
            service=EXPORT_SERVICE, actor=p.sub, event_type=pkg.AUDIT_EVENT,
            payload_hash=payload_hash(payload), payload=payload, case_refs=[case_id],
        )  # fmt: skip
        try:
            await asyncio.to_thread(store.append, audit)  # fail closed: no audit, no package
        except EntryRejected as e:
            raise HTTPException(409, f"export audit entry rejected ({e.code})") from e
        return Response(
            built.data, media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="evidence-{case_id}.zip"',
                "ETag": f'"{built.sha256}"',
            },
        )  # fmt: skip

    return app


def create_service_app() -> FastAPI:
    """Env-configured app: LEDGER_DATABASE_URL, LEDGER_SIGNING_KEY_FILE, KAFKA_BOOTSTRAP, ..."""
    from svckit.bus import KafkaBus

    kafka = os.getenv("KAFKA_BOOTSTRAP")
    bus: Bus | None = KafkaBus(kafka) if kafka else None
    closers: list[Callable[[], Awaitable[None]]] = []
    if bus is not None and hasattr(bus, "close"):
        closers.append(bus.close)  # type: ignore[attr-defined]
    return create_app(bus=bus, consume=bus is not None, closers=closers)
