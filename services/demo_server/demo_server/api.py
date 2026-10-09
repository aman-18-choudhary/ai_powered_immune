"""FastAPI app: the console's ``/api`` REST + ``/api/stream`` WebSocket contract.

See the README for the table of routes and the event schema. Everything here is a thin layer over
``Engine`` (scenario + real service code), ``evidence`` (ledger + verifier) and ``citizen``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Query, Request, Response, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware
from txn_guard.holds import HoldConflict, HoldNotFound

from . import benchmark, citizen, evidence, geo
from .auth import AuthError, Principal, code_ok, decode_token, issue_token
from .config import ROLES, Settings
from .engine import SPEED_MAX, SPEED_MIN, Engine, Run
from .events import CLOSE_FULL, CLOSE_SLOW, Subscriber
from .security import RateLimiter, SecurityMiddleware

log = logging.getLogger("demo_server")

CASE_RE = r"^[A-Za-z0-9._:-]{1,64}$"
TXN_REF_RE = r"^[A-Za-z0-9._:-]{1,64}$"
NODE_RE = r"^[0-9a-f]{12}$"
SleepFn = Callable[[float], Awaitable[None]]


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status, self.code, self.detail, self.headers = status, code, detail, headers


def _err(status: int, code: str, detail: str | None = None) -> ApiError:
    return ApiError(status, code, detail)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginBody(_Body):
    role: Literal["officer", "analyst", "citizen", "admin"]
    code: str | None = Field(default=None, max_length=128)


class StartBody(_Body):
    seed: int = Field(default=1, ge=0, le=2**31 - 1)
    speed: float = Field(default=10.0, ge=SPEED_MIN, le=SPEED_MAX)
    mode: Literal["hero", "hero+noise"] = "hero"


class ControlBody(_Body):
    action: Literal["pause", "resume", "stop", "seek"]
    t: float | None = Field(default=None, ge=0, le=1e7)
    speed: float | None = Field(default=None, ge=SPEED_MIN, le=SPEED_MAX)


class ResolveBody(_Body):
    action: Literal["release", "confirm_block"]
    decision_seq: int | None = Field(default=None, ge=1, le=1000)


class CaseBody(_Body):
    case_id: str | None = Field(default=None, pattern=CASE_RE)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    case_refs: list[Annotated[str, Field(pattern=CASE_RE)]] | None = Field(
        default=None, min_length=1, max_length=10
    )


class VerifyBody(_Body):
    case_id: str = Field(pattern=CASE_RE)


class SimulateBody(_Body):
    remove: list[Annotated[str, Field(pattern=NODE_RE)]] | None = Field(default=None, max_length=50)
    k: int | None = Field(default=None, ge=1, le=5)


class AssessBody(_Body):
    message: str
    lang: str | None = Field(default=None, max_length=16)


PLACEHOLDER = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    "<title>Scam Immune System demo</title>"
    "<style>body{font:16px/1.5 system-ui,sans-serif;margin:0;padding:24px;"
    "background:#0b0f17;color:#e5e7eb}code{background:#111827;padding:2px 6px}"
    "a{color:#22d3ee}</style></head><body>"
    "<h1>Scam Immune System: demo server</h1>"
    "<p><strong>SIMULATION.</strong> All data is simulator-generated. This server composes the "
    "platform's real service code in one process.</p>"
    "<p>The web console is not bundled here. Set <code>DEMO_STATIC_DIR</code> to the built "
    "console, or use the API under <code>/api</code> "
    '(<a href="/api/health">/api/health</a>).</p></body></html>'
)


def create_app(
    settings: Settings | None = None,
    *,
    sleep: SleepFn = asyncio.sleep,
    engine: Engine | None = None,
) -> FastAPI:
    cfg = settings or Settings.from_env()
    eng = engine or Engine(cfg, sleep=sleep)
    limiter = RateLimiter(cfg.rate_limits, cfg.rate_window_s)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await eng.start()
        try:
            yield
        finally:
            await eng.aclose()

    app = FastAPI(
        title="scam-immune-system demo server", lifespan=lifespan, docs_url=None, redoc_url=None,
        openapi_url=None,
    )  # fmt: skip
    app.state.engine = eng
    app.state.settings = cfg
    app.state.limiter = limiter
    app.add_middleware(
        CORSMiddleware, allow_origins=list(cfg.cors_origins), allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
        expose_headers=["X-Request-Id", "ETag"], max_age=600,
    )  # fmt: skip
    app.add_middleware(SecurityMiddleware, max_body_bytes=cfg.max_body_bytes)

    # ------------------------------------------------------------------ errors
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        body: dict[str, Any] = {"error": exc.code}
        if exc.detail:
            body["detail"] = exc.detail
        return JSONResponse(body, status_code=exc.status, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # never echo the submitted value (FastAPI's default includes "input" / "ctx")
        items = []
        for e in exc.errors():
            loc = [str(x) for x in e["loc"]]
            if e["type"] == "extra_forbidden" and loc:
                loc[-1] = "*"  # the offending key is caller-supplied text: do not echo it
            items.append({"loc": loc, "msg": e["msg"], "type": e["type"]})
        return JSONResponse({"error": "invalid_request", "detail": items}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        names = {404: "not_found", 405: "method_not_allowed", 401: "unauthorized",
                 403: "forbidden"}  # fmt: skip
        return JSONResponse(
            {"error": names.get(exc.status_code, "http_error")}, status_code=exc.status_code
        )

    # ------------------------------------------------------------------- auth
    def _principal(request: Request) -> Principal:
        h = request.headers.get("authorization", "")
        scheme, _, token = h.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise _err(401, "unauthorized")
        try:
            return decode_token(cfg.jwt_secret, token.strip())
        except AuthError:
            raise _err(401, "unauthorized") from None

    def require(*roles: str) -> Callable[[Request], Principal]:
        allowed = set(roles) or set(ROLES)

        def dep(request: Request) -> Principal:
            p = _principal(request)
            if p.role not in allowed:
                raise _err(403, "forbidden")
            wait = limiter.check(p.role, p.sub)
            if wait is not None:
                raise ApiError(429, "rate_limited", headers={"Retry-After": str(wait)})
            return p

        return dep

    def run_or_409() -> Run:
        run = eng.run
        if run is None or run.harness is None:
            raise _err(409, "no_run", "start a scenario first")
        return run

    Any_ = Depends(require())
    OfficerAdmin = Depends(require("officer", "admin"))
    Readers = Depends(require("officer", "admin", "analyst"))
    AnalystAdmin = Depends(require("analyst", "admin"))

    # ----------------------------------------------------------------- public
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "simulated": True}

    @app.post("/api/auth/demo-login")
    async def demo_login(body: LoginBody, request: Request) -> dict[str, Any]:
        ip = request.client.host if request.client else "unknown"
        wait = limiter.check("anon", f"login:{ip}")
        if wait is not None:
            raise ApiError(429, "rate_limited", headers={"Retry-After": str(wait)})
        if not code_ok(body.code, cfg.login_code):
            raise _err(401, "unauthorized")
        token, sub, ttl = issue_token(cfg.jwt_secret, body.role)
        return {"token": token, "role": body.role, "sub": sub, "expires_in": ttl, "demo": True}

    # ------------------------------------------------------------------- demo
    @app.post("/api/demo/start")
    async def demo_start(body: StartBody, _p: Principal = OfficerAdmin) -> dict[str, Any]:
        run_id = await eng.start_run(body.seed, body.speed, body.mode)
        return {"run_id": run_id, "simulated": True}

    @app.post("/api/demo/control")
    async def demo_control(body: ControlBody, _p: Principal = OfficerAdmin) -> dict[str, Any]:
        try:
            return await eng.control(body.action, body.t, body.speed)
        except LookupError:
            raise _err(409, "no_run", "start a scenario first") from None
        except ValueError:
            raise _err(422, "invalid_request") from None

    @app.get("/api/demo/state")
    async def demo_state(_p: Principal = Any_) -> dict[str, Any]:
        return eng.state_view()

    @app.get("/api/demo/runs")
    async def demo_runs(_p: Principal = Any_) -> dict[str, Any]:
        return {"runs": list(eng.runs), "simulated": True}

    # ----------------------------------------------------------------- stream
    def _origin_ok(ws: WebSocket) -> bool:
        origin = ws.headers.get("origin")
        if origin is None:
            return True  # non-browser client
        if origin.rstrip("/") in cfg.cors_origins:
            return True
        host = ws.headers.get("host", "")
        return bool(host) and urlsplit(origin).netloc == host

    @app.websocket("/api/stream")
    async def stream(ws: WebSocket) -> None:
        token = ws.query_params.get("token", "")
        try:
            decode_token(cfg.jwt_secret, token)
        except AuthError:
            await ws.close(code=1008)  # rejected before accept (HTTP 403 handshake)
            return
        if not _origin_ok(ws):
            await ws.close(code=1008)
            return
        await ws.accept()
        sub: Subscriber | None = eng.subscribe()
        if sub is None:
            await ws.close(code=CLOSE_FULL)
            return

        async def writer() -> None:
            while True:
                if sub.dropped:
                    await ws.close(code=CLOSE_SLOW)
                    return
                try:
                    ev = await asyncio.wait_for(sub.queue.get(), 0.5)
                except TimeoutError:
                    continue
                await asyncio.wait_for(
                    ws.send_text(json.dumps(ev, separators=(",", ":"))), cfg.ws_send_timeout_s
                )

        async def reader() -> None:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                data = msg.get("text") or ""
                raw = msg.get("bytes") or b""
                if len(data.encode()) + len(raw) > cfg.ws_max_message_bytes:
                    await ws.close(code=1009)
                    return

        tasks = [asyncio.create_task(writer()), asyncio.create_task(reader())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            eng.log.unsubscribe(sub)
            for t in tasks:
                t.cancel()
            await asyncio.wait(tasks)
            try:
                await ws.close()
            except Exception:  # already closed
                pass

    # ------------------------------------------------------------------ holds
    @app.get("/api/holds")
    async def holds(
        state: Annotated[Literal["open", "all"], Query()] = "open", _p: Principal = Readers
    ) -> dict[str, Any]:
        run = run_or_409()
        return {"holds": await run.hold_views(state), "simulated": True}

    @app.post("/api/holds/{txn_ref}/resolve")
    async def resolve(
        txn_ref: Annotated[str, Field(pattern=TXN_REF_RE)], body: ResolveBody,
        p: Principal = AnalystAdmin,
    ) -> dict[str, Any]:  # fmt: skip
        if not re.fullmatch(TXN_REF_RE, txn_ref):
            raise _err(422, "invalid_request")
        run = run_or_409()
        role = "analyst" if p.role == "analyst" else "admin"
        try:
            await run.resolve_hold(txn_ref, body.action, p.sub, role, expect_seq=body.decision_seq)
        except HoldNotFound:
            raise _err(404, "hold_not_found") from None
        except HoldConflict:
            raise _err(
                409, "hold_conflict", "hold already resolved differently or changed"
            ) from None
        view = await run.hold_view(txn_ref)
        return {"hold": view, "simulated": True}

    @app.get("/api/antibodies")
    async def antibodies(
        state: Annotated[Literal["active", "all"], Query()] = "active", _p: Principal = Readers
    ) -> dict[str, Any]:
        run = run_or_409()
        return {"antibodies": await run.antibody_views(state), "simulated": True}

    # ----------------------------------------------------------------- ledger
    async def ledger_get(run: Run, path: str, p: Principal, **params: Any) -> Any:
        h = run.harness
        assert h is not None
        r = await h.ledger.get(
            path, params=params or None, headers=h.ledger_headers("officer", p.sub)
        )
        if r.status_code == 404:
            raise _err(404, "not_found")
        if r.status_code != 200:
            raise _err(502, "ledger_error")
        return r

    @app.get("/api/ledger/head")
    async def ledger_head(p: Principal = Readers) -> dict[str, Any]:
        run = run_or_409()
        r = await ledger_get(run, "/head", p)
        out = r.json()
        sg = run.harness.signer  # type: ignore[union-attr]
        out.update(public_key_hex=sg.public_raw.hex(), key_id=sg.key_id, algorithm="Ed25519")
        return out

    @app.get("/api/ledger/entries")
    async def ledger_entries(
        from_seq: Annotated[int, Query(ge=1, le=10**9)] = 1,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        p: Principal = Readers,
    ) -> dict[str, Any]:
        run = run_or_409()
        r = await ledger_get(run, "/entries", p, from_seq=from_seq, limit=limit)
        return r.json()

    @app.get("/api/ledger/checkpoints/latest")
    async def ledger_checkpoint(p: Principal = Readers) -> dict[str, Any]:
        run = run_or_409()
        h = run.harness
        assert h is not None
        r = await h.ledger.get("/checkpoints/latest", headers=h.ledger_headers("officer", p.sub))
        if r.status_code == 404:
            return {"checkpoint": None, "simulated": True}
        if r.status_code != 200:
            raise _err(502, "ledger_error")
        return {"checkpoint": r.json(), "simulated": True}

    @app.get("/api/ledger/checkpoints")
    async def ledger_checkpoints(
        limit: Annotated[int, Query(ge=1, le=200)] = 50, p: Principal = Readers
    ) -> dict[str, Any]:
        run = run_or_409()
        r = await ledger_get(run, "/checkpoints", p, from_seq=0, limit=limit)
        return r.json()

    # --------------------------------------------------------- cases + packages
    @app.post("/api/cases", status_code=201)
    async def create_case(
        body: CaseBody, response: Response, p: Principal = OfficerAdmin
    ) -> dict[str, Any]:
        run = run_or_409()
        h = run.harness
        assert h is not None
        payload = {
            "case_id": body.case_id or "hero-case",
            "title": body.title or "Hero digital-arrest campaign (simulated)",
            "case_refs": body.case_refs or run.default_case_refs(),
        }
        r = await h.ledger.post("/cases", json=payload, headers=h.ledger_headers("officer", p.sub))
        if r.status_code in (200, 201):
            response.status_code = r.status_code
            run.cases.add(payload["case_id"])
            return {**r.json(), "simulated": True}
        if r.status_code == 409:
            raise _err(409, "case_conflict", "case exists with different content")
        if r.status_code == 422:
            raise _err(422, "invalid_request")
        raise _err(502, "ledger_error")

    async def fetch_package(run: Run, case_id: str, p: Principal) -> tuple[bytes, str]:
        h = run.harness
        assert h is not None
        r = await h.ledger.get(f"/packages/{case_id}", headers=h.ledger_headers("officer", p.sub))
        if r.status_code == 404:
            raise _err(404, "not_found", "unknown case or no entries match it")
        if r.status_code == 409:
            raise _err(409, "not_covered", "no signed checkpoint covers this case yet")
        if r.status_code == 413:
            raise _err(413, "package_too_large")
        if r.status_code != 200:
            raise _err(502, "ledger_error")
        await run.sync_ledger()  # surface the package.exported audit entry on the stream
        return r.content, r.headers.get("etag", "")

    @app.get("/api/packages/{case_id}")
    async def package(
        case_id: Annotated[str, Field(pattern=CASE_RE)], p: Principal = OfficerAdmin
    ) -> Response:
        if not re.fullmatch(CASE_RE, case_id):
            raise _err(422, "invalid_request")
        run = run_or_409()
        data, etag = await fetch_package(run, case_id, p)
        return Response(
            data, media_type="application/zip",
            headers={
                "ETag": etag,
                "Content-Disposition": f'attachment; filename="evidence-{case_id}.zip"',
            },
        )  # fmt: skip

    @app.post("/api/verify")
    async def verify(body: VerifyBody, p: Principal = OfficerAdmin) -> dict[str, Any]:
        run = run_or_409()
        data, _ = await fetch_package(run, body.case_id, p)
        h = run.harness
        assert h is not None
        try:
            res = await evidence.run_verifier(
                data, h.signer.public_raw.hex(), timeout_s=cfg.verify_timeout_s,
                output_cap=cfg.verify_output_cap,
            )  # fmt: skip
        except evidence.VerifyError as e:
            raise _err(422, "unverifiable_package", str(e)) from None
        return {
            **res, "case_id": body.case_id, "pinned_key_id": h.signer.key_id,
            "public_key_hex": h.signer.public_raw.hex(), "simulated": True,
        }  # fmt: skip

    # -------------------------------------------------------------- ring + geo
    def _ring_run(case_id: str) -> tuple[Run, str | None]:
        run = run_or_409()
        if case_id in ("current", "hero") or case_id in run.cases:
            return run, None
        return run, case_id

    def _snapshot(run: Run, ring_id: str | None) -> dict[str, Any]:
        snap = run.ring.snapshot(ring_id)
        if snap is None:
            raise _err(404, "ring_not_found")
        return snap

    @app.get("/api/ring/{case_id}/graph")
    async def ring_graph(
        case_id: Annotated[str, Field(pattern=CASE_RE)], _p: Principal = Readers
    ) -> dict[str, Any]:
        run, rid = _ring_run(case_id)
        return {**_snapshot(run, rid), "simulated": True}

    @app.post("/api/ring/{case_id}/simulate")
    async def ring_simulate(
        case_id: Annotated[str, Field(pattern=CASE_RE)], body: SimulateBody,
        _p: Principal = Readers,
    ) -> dict[str, Any]:  # fmt: skip
        run, rid = _ring_run(case_id)
        _snapshot(run, rid)
        if (body.remove is None) == (body.k is None):
            raise _err(422, "invalid_request", "give exactly one of remove or k")
        try:
            if body.k is not None:
                res = await asyncio.to_thread(run.ring.best_takedown, body.k, rid)
            else:
                res = await asyncio.to_thread(run.ring.simulate_takedown, body.remove or [], rid)
        except ValueError:
            raise _err(422, "invalid_request", "unknown or non-removable node id") from None
        return {**res.to_json(), "mode": "k" if body.k is not None else "remove", "simulated": True}

    @app.get("/api/ring/{case_id}/best-takedown")
    async def ring_best(
        case_id: Annotated[str, Field(pattern=CASE_RE)],
        k: Annotated[int, Query(ge=1, le=5)] = 1, _p: Principal = Readers,
    ) -> dict[str, Any]:  # fmt: skip
        run, rid = _ring_run(case_id)
        _snapshot(run, rid)
        greedy = await asyncio.to_thread(run.ring.best_takedown, k, rid)
        out: dict[str, Any] = {"greedy": greedy.to_json(), "optimal": None, "simulated": True}
        try:
            opt = await asyncio.to_thread(run.ring.best_takedown_exhaustive, k, rid)
            out["optimal"] = opt.to_json()
            out["gap_pct"] = round(opt.reduction_pct - greedy.reduction_pct, 1)
        except ValueError:
            pass  # larger than the exhaustive bound
        return out

    @app.get("/api/geo/hotspots")
    async def hotspots(_p: Principal = Readers) -> dict[str, Any]:
        st = eng.state
        out = geo.hotspots(list(st.geo_points), st.t)
        return {**out, "t": round(st.t, 3), "simulated": True}

    # --------------------------------------------------------- benchmark + citizen
    @app.get("/api/benchmark/summary")
    async def benchmark_summary(_p: Principal = Any_) -> dict[str, Any]:
        return await asyncio.to_thread(benchmark.load_summary)

    @app.post("/api/citizen/assess")
    async def assess(body: AssessBody, _p: Principal = Any_) -> dict[str, Any]:
        scorer = eng.call_scorer
        if scorer is None:
            raise _err(503, "not_ready")
        res = await citizen.assess(scorer, body.message, body.lang)
        log.info("citizen assess label=%s len=%d", res.label, len(body.message))
        return res.to_json()

    # ------------------------------------------------------------ static + SPA
    static_root = cfg.static_dir.resolve() if cfg.static_dir else None

    @app.get("/{path:path}", include_in_schema=False)
    async def static(path: str) -> Response:
        if path.startswith("api/") or path == "api":
            raise _err(404, "not_found")
        if static_root is None or not static_root.is_dir():
            if path in ("", "index.html"):
                return HTMLResponse(PLACEHOLDER)
            raise _err(404, "not_found")
        candidate = (static_root / path).resolve() if path else static_root / "index.html"
        try:
            candidate.relative_to(static_root)
        except ValueError:
            raise _err(404, "not_found") from None
        if candidate.is_file():
            return FileResponse(candidate)
        index = static_root / "index.html"
        if index.is_file() and "." not in Path(path).name:
            return FileResponse(index)  # SPA fallback
        raise _err(404, "not_found")

    return app
