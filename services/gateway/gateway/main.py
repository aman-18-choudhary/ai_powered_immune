"""Gateway app: login, RBAC-protected reverse proxy, request-id propagation.

Operational notes:
- The login failed-attempt limiter keys on the socket peer address. Behind a reverse proxy, set
  uvicorn's FORWARDED_ALLOW_IPS to the proxy address (proxy_headers is on by default) so the
  real client IP is used; otherwise all clients share one IP bucket.
- Known tradeoff: the per-username failed-login key lets an attacker lock a victim username out
  for up to 60 s per window (bounded lockout DoS) in exchange for stopping credential stuffing.
"""

import asyncio
import logging
import os
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, Protocol
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from redis.asyncio import Redis
from svckit.health import make_health_router

from .auth import InMemoryUserStore, Principal, UserStore, demo_users_from_env, issue_token
from .ratelimit import RateLimiter
from .rbac import require_role

logger = logging.getLogger(__name__)

MIN_SECRET_BYTES = 32
DEFAULT_MAX_BODY = 1024 * 1024
DEFAULT_MAX_RESPONSE = 5 * 1024 * 1024
REDIS_TIMEOUT = 0.25
LIMITER_TIMEOUT = 0.5
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# prefix -> roles allowed
ROUTE_ROLES: dict[str, tuple[str, ...]] = {
    "txn": ("analyst", "officer", "admin"),
    "graph": ("officer", "analyst", "admin"),
    "geo": ("officer", "analyst", "admin"),
    "ledger": ("officer", "admin"),
    "citizen": ("citizen", "admin"),
}

_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
    "trailers", "transfer-encoding", "upgrade",
}  # fmt: skip
_STRIP_REQUEST = _HOP_BY_HOP | {
    "authorization", "host", "content-length", "x-request-id", "cookie", "forwarded",
    "x-real-ip", "x-principal-role", "x-principal-sub",
}  # fmt: skip
_STRIP_RESPONSE = _HOP_BY_HOP | {"content-length", "content-encoding", "server"}


class LoginRequest(BaseModel):
    username: str = Field(max_length=128)
    password: str = Field(max_length=128)


class Limiter(Protocol):
    async def check(self, role: str, sub: str) -> int | None: ...
    async def login_blocked(self, ip: str, username: str) -> int | None: ...
    async def login_failed(self, ip: str, username: str) -> None: ...


class _RequestTooLarge(Exception):
    pass


class _ResponseTooLarge(Exception):
    pass


def _upstreams_from_env() -> dict[str, str]:
    return {
        p: os.environ[f"GATEWAY_UPSTREAM_{p.upper()}"]
        for p in ROUTE_ROLES
        if f"GATEWAY_UPSTREAM_{p.upper()}" in os.environ
    }


def _forward_headers(request: Request) -> dict[str, str]:
    named = {
        t.strip().lower() for t in request.headers.get("connection", "").split(",") if t.strip()
    }
    return {
        k: v
        for k, v in request.headers.items()
        if (kl := k.lower()) not in _STRIP_REQUEST
        and kl not in named
        and not kl.startswith("x-forwarded-")
    }


def _bad_path(path: str) -> bool:
    return "\x00" in path or "\\" in path or any(seg in (".", "..") for seg in path.split("/"))


def create_app(
    secret: str | None = None,
    user_store: UserStore | None = None,
    redis: Redis | None = None,
    upstreams: dict[str, str] | None = None,
    upstream_transport: httpx.AsyncBaseTransport | None = None,
    limits: dict[str, int] | None = None,
    demo_passwords: dict[str, str] | None = None,
    limiter: Limiter | None = None,
    limiter_timeout: float = LIMITER_TIMEOUT,
    max_body_bytes: int | None = None,
    max_response_bytes: int | None = None,
) -> FastAPI:
    secret = secret or os.getenv("GATEWAY_JWT_SECRET")
    if not secret:
        raise RuntimeError("GATEWAY_JWT_SECRET must be set")
    if len(secret.encode()) < MIN_SECRET_BYTES:
        raise RuntimeError(f"GATEWAY_JWT_SECRET must be at least {MIN_SECRET_BYTES} bytes")
    max_body = max_body_bytes or int(os.getenv("GATEWAY_MAX_BODY_BYTES", DEFAULT_MAX_BODY))
    max_resp = max_response_bytes or int(
        os.getenv("GATEWAY_MAX_RESPONSE_BYTES", DEFAULT_MAX_RESPONSE)
    )
    if user_store is None:
        demo = demo_passwords is not None or os.getenv("GATEWAY_DEMO_USERS") == "1"
        user_store = InMemoryUserStore(demo_users_from_env(demo_passwords) if demo else {})
    the_redis = redis or Redis.from_url(
        os.getenv("GATEWAY_REDIS_URL", "redis://redis:6379/0"),
        socket_connect_timeout=REDIS_TIMEOUT,
        socket_timeout=REDIS_TIMEOUT,
    )
    the_limiter: Limiter = limiter or RateLimiter(the_redis, limits)
    bases = upstreams if upstreams is not None else _upstreams_from_env()
    http = httpx.AsyncClient(transport=upstream_transport, timeout=10.0)

    async def guarded(coro: Awaitable[Any], default: Any) -> Any:
        """Await a limiter call; fail open (return default) on timeout or error."""
        try:
            return await asyncio.wait_for(coro, limiter_timeout)
        except TimeoutError:
            logger.warning("rate limit check timed out; allowing request")
        except Exception:
            logger.warning("rate limit check failed; allowing request")
        return default

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await http.aclose()
        await the_redis.aclose()

    app = FastAPI(title="gateway", lifespan=lifespan)
    app.state.jwt_secret = secret
    app.state.http = http
    app.state.redis = the_redis

    @app.middleware("http")
    async def request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get("x-request-id", "")
        if not _REQUEST_ID_RE.fullmatch(rid):
            rid = str(uuid.uuid4())
        request.state.request_id = rid
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > max_body:
            response: Response = JSONResponse({"error": "payload_too_large"}, status_code=413)
        else:
            response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        return response

    async def ready() -> bool:
        return True

    app.include_router(make_health_router(ready))

    async def read_body(request: Request) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > max_body:
                raise _RequestTooLarge
            chunks.append(chunk)
        return b"".join(chunks)

    @app.post("/auth/login")
    async def login(request: Request) -> Response:
        try:
            body = LoginRequest.model_validate_json(await read_body(request))
        except _RequestTooLarge:
            return JSONResponse({"error": "payload_too_large"}, status_code=413)
        except ValidationError:
            return JSONResponse({"error": "invalid_request"}, status_code=422)
        ip = request.client.host if request.client else "unknown"
        wait = await guarded(the_limiter.login_blocked(ip, body.username), None)
        if wait is not None:
            return JSONResponse(
                {"error": "too_many_attempts"}, status_code=429, headers={"Retry-After": str(wait)}
            )
        principal = await user_store.authenticate(body.username, body.password)
        if principal is None:
            await guarded(the_limiter.login_failed(ip, body.username), None)
            return JSONResponse({"error": "invalid_credentials"}, status_code=401)
        token, ttl = issue_token(secret, principal)
        logger.info("login ok role=%s", principal.role)
        return JSONResponse({"access_token": token, "token_type": "bearer", "expires_in": ttl})

    async def fetch(method: str, url: str, request: Request, headers: dict[str, str]) -> Response:
        body = await read_body(request)
        chunks: list[bytes] = []
        size = 0
        async with http.stream(
            method, url, params=request.query_params, headers=headers, content=body
        ) as up:
            declared = up.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > max_resp:
                raise _ResponseTooLarge
            async for chunk in up.aiter_bytes():
                size += len(chunk)
                if size > max_resp:
                    raise _ResponseTooLarge
                chunks.append(chunk)
            out = Response(b"".join(chunks), status_code=up.status_code)
            for k, v in up.headers.multi_items():
                if k.lower() not in _STRIP_RESPONSE:
                    out.headers.append(k, v)
        return out

    def make_proxy(prefix: str) -> Callable[..., Awaitable[Response]]:
        guard = Depends(require_role(*ROUTE_ROLES[prefix]))

        async def proxy(
            request: Request, principal: Annotated[Principal, guard], path: str = ""
        ) -> Response:
            if _bad_path(path):
                return JSONResponse({"error": "bad_path"}, status_code=400)
            retry_after = await guarded(the_limiter.check(principal.role, principal.sub), None)
            if retry_after is not None:
                return JSONResponse(
                    {"error": "rate_limited"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
            base = bases.get(prefix)
            if base is None:
                return JSONResponse({"error": "upstream_not_configured"}, status_code=502)
            headers = _forward_headers(request)
            headers["X-Request-Id"] = request.state.request_id
            headers["X-Principal-Role"] = principal.role
            headers["X-Principal-Sub"] = principal.sub
            url = f"{base.rstrip('/')}/{quote(path, safe='/')}"
            try:
                return await fetch(request.method, url, request, headers)
            except _RequestTooLarge:
                return JSONResponse({"error": "payload_too_large"}, status_code=413)
            except _ResponseTooLarge:
                return JSONResponse({"error": "upstream_response_too_large"}, status_code=502)
            except httpx.HTTPError as exc:
                logger.warning("upstream %s failed: %s", prefix, type(exc).__name__)
                return JSONResponse({"error": "upstream_unavailable"}, status_code=502)

        return proxy

    methods = ["GET", "POST", "PUT", "PATCH", "DELETE"]
    for prefix in ROUTE_ROLES:
        handler = make_proxy(prefix)
        app.add_api_route(f"/api/{prefix}", handler, methods=methods, include_in_schema=False)
        app.add_api_route(
            f"/api/{prefix}/{{path:path}}", handler, methods=methods, include_in_schema=False
        )
    return app
