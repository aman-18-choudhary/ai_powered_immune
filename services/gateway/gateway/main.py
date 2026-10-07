"""Gateway app: login, RBAC-protected reverse proxy, request-id propagation."""

import logging
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from redis.asyncio import Redis
from svckit.health import make_health_router

from .auth import InMemoryUserStore, Principal, UserStore, demo_users_from_env, issue_token
from .ratelimit import RateLimiter
from .rbac import require_role

logger = logging.getLogger(__name__)

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
    "authorization", "host", "content-length", "x-request-id",
    "x-principal-role", "x-principal-sub",
}  # fmt: skip
_STRIP_RESPONSE = _HOP_BY_HOP | {"content-length", "content-encoding"}


class LoginRequest(BaseModel):
    username: str
    password: str


def _upstreams_from_env() -> dict[str, str]:
    return {
        p: os.environ[f"GATEWAY_UPSTREAM_{p.upper()}"]
        for p in ROUTE_ROLES
        if f"GATEWAY_UPSTREAM_{p.upper()}" in os.environ
    }


def create_app(
    secret: str | None = None,
    user_store: UserStore | None = None,
    redis: Redis | None = None,
    upstreams: dict[str, str] | None = None,
    upstream_transport: httpx.AsyncBaseTransport | None = None,
    limits: dict[str, int] | None = None,
    demo_passwords: dict[str, str] | None = None,
) -> FastAPI:
    secret = secret or os.getenv("GATEWAY_JWT_SECRET")
    if not secret:
        raise RuntimeError("GATEWAY_JWT_SECRET must be set")
    if user_store is None:
        demo = demo_passwords is not None or os.getenv("GATEWAY_DEMO_USERS") == "1"
        user_store = InMemoryUserStore(demo_users_from_env(demo_passwords) if demo else {})
    the_redis = redis or Redis.from_url(os.getenv("GATEWAY_REDIS_URL", "redis://redis:6379/0"))
    limiter = RateLimiter(the_redis, limits)
    bases = upstreams if upstreams is not None else _upstreams_from_env()
    http = httpx.AsyncClient(transport=upstream_transport, timeout=10.0)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await http.aclose()
        await the_redis.aclose()

    app = FastAPI(title="gateway", lifespan=lifespan)
    app.state.jwt_secret = secret

    @app.middleware("http")
    async def request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        return response

    async def ready() -> bool:
        return True

    app.include_router(make_health_router(ready))

    @app.post("/auth/login")
    async def login(body: LoginRequest) -> Response:
        principal = await user_store.authenticate(body.username, body.password)
        if principal is None:
            return JSONResponse({"error": "invalid_credentials"}, status_code=401)
        token, ttl = issue_token(secret, principal)
        logger.info("login ok role=%s", principal.role)
        return JSONResponse({"access_token": token, "token_type": "bearer", "expires_in": ttl})

    def make_proxy(prefix: str) -> Callable[..., Awaitable[Response]]:
        guard = Depends(require_role(*ROUTE_ROLES[prefix]))

        async def proxy(
            request: Request, principal: Annotated[Principal, guard], path: str = ""
        ) -> Response:
            retry_after = await limiter.check(principal.role, principal.sub)
            if retry_after is not None:
                return JSONResponse(
                    {"error": "rate_limited"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
            base = bases.get(prefix)
            if base is None:
                return JSONResponse({"error": "upstream_not_configured"}, status_code=502)
            headers = {k: v for k, v in request.headers.items() if k.lower() not in _STRIP_REQUEST}
            headers["X-Request-Id"] = request.state.request_id
            headers["X-Principal-Role"] = principal.role
            headers["X-Principal-Sub"] = principal.sub
            try:
                upstream = await http.request(
                    request.method,
                    f"{base.rstrip('/')}/{path}",
                    params=request.query_params,
                    headers=headers,
                    content=await request.body(),
                )
            except httpx.HTTPError as exc:
                logger.warning("upstream %s failed: %s", prefix, type(exc).__name__)
                return JSONResponse({"error": "upstream_unavailable"}, status_code=502)
            out = {k: v for k, v in upstream.headers.items() if k.lower() not in _STRIP_RESPONSE}
            return Response(upstream.content, status_code=upstream.status_code, headers=out)

        return proxy

    methods = ["GET", "POST", "PUT", "PATCH", "DELETE"]
    for prefix in ROUTE_ROLES:
        handler = make_proxy(prefix)
        app.add_api_route(f"/api/{prefix}", handler, methods=methods, include_in_schema=False)
        app.add_api_route(
            f"/api/{prefix}/{{path:path}}", handler, methods=methods, include_in_schema=False
        )
    return app
