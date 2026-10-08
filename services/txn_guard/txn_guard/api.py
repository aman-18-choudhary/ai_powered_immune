"""HTTP API: hold-and-verify endpoints, health probes and metrics.

Trust model: this service is reachable only through the gateway on the internal network. Role
headers (``X-Principal-Role`` / ``X-Principal-Sub``) are trusted ONLY when the environment says
so: ``GATEWAY_SHARED_SECRET`` set (the request must then carry the same value in
``X-Gateway-Secret``) or ``TRUST_GATEWAY_HEADERS=1``. Otherwise every hold endpoint answers 401.
"""

import asyncio
import hmac
import logging
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
from svckit.bus import Bus
from svckit.health import make_health_router
from svckit.idempotency import IdempotencyStore, InMemoryIdempotencyStore

from .consumer import run_consumers
from .holds import (
    ActorRequired,
    BusAuditSink,
    Hold,
    HoldConflict,
    HoldNotFound,
    HoldStore,
    InMemoryAuditSink,
    InMemoryHoldStore,
)
from .model import Scorer
from .service import DEFAULT_HOLD_DEADLINE_S, TxnGuardService

log = logging.getLogger("txn_guard")

STAFF_READ = {"analyst", "officer", "admin"}
STAFF_RESOLVE = {"analyst", "admin"}


@dataclass(frozen=True)
class Principal:
    role: str
    sub: str


def _trusted(request: Request) -> bool:
    secret = os.getenv("GATEWAY_SHARED_SECRET")
    if secret:
        return hmac.compare_digest(request.headers.get("x-gateway-secret", ""), secret)
    return os.getenv("TRUST_GATEWAY_HEADERS") == "1"


def principal(request: Request) -> Principal:
    if not _trusted(request):
        raise HTTPException(401, "gateway headers not trusted")
    role = request.headers.get("x-principal-role", "").strip()
    sub = request.headers.get("x-principal-sub", "").strip()
    if not role or not sub:
        raise HTTPException(401, "missing principal")
    return Principal(role, sub)


class ResolveRequest(BaseModel):
    action: Literal["release", "confirm_block"]


def create_app(
    scorer: Scorer | None = None,
    holds: HoldStore | None = None,
    clock: Callable[[], datetime] | None = None,
    service: TxnGuardService | None = None,
    bus: Bus | None = None,
    idem: IdempotencyStore | None = None,
    redis: Any = None,
) -> FastAPI:
    scorer = scorer or (service.scorer if service else Scorer())
    clock = clock or (lambda: datetime.now(UTC))
    if service is not None:
        holds = service.holds
    holds = holds or InMemoryHoldStore(audit=InMemoryAuditSink(), clock=clock)
    tasks: list[asyncio.Task[None]] = []
    overdue_seen: set[str] = set()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if service is not None and bus is not None:
            tasks.extend(run_consumers(bus, service, idem or InMemoryIdempotencyStore()))
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def ready() -> bool:
        if redis is not None:
            await redis.ping()
        return True

    app = FastAPI(title="txn-guard", lifespan=lifespan)
    app.include_router(
        make_health_router(
            ready,
            extra=lambda: {
                "model_version": scorer.model_version,
                "fallback_mode": str(scorer.fallback_mode).lower(),
            },
        )
    )

    def view(h: Hold) -> dict[str, Any]:
        overdue = holds.is_overdue(h)  # type: ignore[union-attr]
        if overdue:
            overdue_seen.add(h.txn_id)
        return h.model_dump(mode="json") | {"overdue": overdue}

    def need(p: Principal, roles: set[str]) -> None:
        if p.role not in roles:
            raise HTTPException(403, "role not permitted")

    async def call(coro: Any) -> Any:
        try:
            return await coro
        except HoldNotFound as e:
            raise HTTPException(404, "hold not found") from e
        except HoldConflict as e:
            raise HTTPException(409, str(e)) from e
        except ActorRequired as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/metrics")
    async def metrics() -> Response:
        open_ = await holds.list_open()  # type: ignore[union-attr]
        n_over = sum(1 for h in open_ if view(h)["overdue"])
        body = (
            "# TYPE txn_guard_fallback_mode gauge\n"
            f"txn_guard_fallback_mode {int(scorer.fallback_mode)}\n"
            "# TYPE txn_guard_model_errors_total counter\n"
            f"txn_guard_model_errors_total {scorer.model_errors}\n"
            "# TYPE txn_guard_holds_open gauge\n"
            f"txn_guard_holds_open {len(open_)}\n"
            "# TYPE txn_guard_holds_overdue gauge\n"
            f"txn_guard_holds_overdue {n_over}\n"
            "# TYPE txn_guard_holds_overdue_total counter\n"
            f"txn_guard_holds_overdue_total {len(overdue_seen)}\n"
        )
        return Response(body, media_type="text/plain; version=0.0.4")

    @app.get("/holds")
    async def list_holds(p: Annotated[Principal, Depends(principal)]) -> list[dict[str, Any]]:
        need(p, STAFF_READ)
        return [view(h) for h in await holds.list_open()]  # type: ignore[union-attr]

    @app.get("/holds/{txn_id}")
    async def get_hold(txn_id: str, p: Annotated[Principal, Depends(principal)]) -> dict[str, Any]:
        h = await holds.get(txn_id)  # type: ignore[union-attr]
        if h is None:
            if p.role not in STAFF_READ | {"citizen"}:
                raise HTTPException(403, "role not permitted")
            raise HTTPException(404, "hold not found")
        if p.role == "citizen":
            if h.payer_token != p.sub:
                raise HTTPException(403, "not your transfer")
        else:
            need(p, STAFF_READ)
        return view(h)

    @app.post("/holds/{txn_id}/resolve")
    async def resolve(
        txn_id: str, body: ResolveRequest, p: Annotated[Principal, Depends(principal)]
    ) -> dict[str, Any]:
        need(p, STAFF_RESOLVE)
        h = await call(holds.resolve(txn_id, body.action, p.sub))  # type: ignore[union-attr]
        return view(h)

    @app.post("/holds/{txn_id}/verify")
    async def verify(txn_id: str, p: Annotated[Principal, Depends(principal)]) -> dict[str, Any]:
        """A citizen confirms their own step-up transfer (verification result: they say yes)."""
        need(p, {"citizen"})
        h = await holds.get(txn_id)  # type: ignore[union-attr]
        if h is None:
            raise HTTPException(404, "hold not found")
        if h.payer_token != p.sub:
            raise HTTPException(403, "not your transfer")
        if h.decision != "step_up":
            raise HTTPException(403, "this hold needs an analyst")
        return view(await call(holds.resolve(txn_id, "release", p.sub)))  # type: ignore[union-attr]

    return app


def create_service_app() -> FastAPI:
    """Env-configured app: REDIS_URL (required), KAFKA_BOOTSTRAP (consumers + decision/ledger
    publishing), HOLD_DEADLINE_S, plus the gateway trust variables described above."""
    import redis as redis_sync
    from redis.asyncio import Redis
    from svckit.bus import InMemoryBus, KafkaBus
    from svckit.idempotency import RedisIdempotencyStore

    from .holds import RedisHoldStore
    from .pending import RedisPendingStore
    from .redis_history import RedisHistoryStore

    url = os.environ["REDIS_URL"]
    kafka = os.getenv("KAFKA_BOOTSTRAP")
    bus: Bus = KafkaBus(kafka) if kafka else InMemoryBus()
    aredis = Redis.from_url(url)
    idem = RedisIdempotencyStore(aredis)
    holds = RedisHoldStore(aredis, audit=BusAuditSink(bus))
    service = TxnGuardService(
        Scorer(), RedisHistoryStore(redis_sync.Redis.from_url(url)), holds, bus, idem,
        pending=RedisPendingStore(aredis),
        hold_deadline_s=float(os.getenv("HOLD_DEADLINE_S", str(DEFAULT_HOLD_DEADLINE_S))),
    )  # fmt: skip
    return create_app(service=service, bus=bus if kafka else None, idem=idem, redis=aredis)
