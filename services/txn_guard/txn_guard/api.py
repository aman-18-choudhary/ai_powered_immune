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
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
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
        got = request.headers.get("x-gateway-secret", "")
        # compare bytes: str compare_digest raises TypeError on non-ASCII input (-> 500)
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


class ResolveRequest(BaseModel):
    action: Literal["release", "confirm_block"]
    decision_seq: int | None = None  # when given, enforced inside the compare-and-set


def create_app(
    scorer: Scorer | None = None,
    holds: HoldStore | None = None,
    clock: Callable[[], datetime] | None = None,
    service: TxnGuardService | None = None,
    bus: Bus | None = None,
    idem: IdempotencyStore | None = None,
    redis: Any = None,
    closers: list[Callable[[], Awaitable[None]]] | None = None,
    sweep_interval_s: float | None = None,
) -> FastAPI:
    scorer = scorer or (service.scorer if service else Scorer())
    clock = clock or (lambda: datetime.now(UTC))
    if service is not None:
        holds = service.holds
    holds = holds or InMemoryHoldStore(audit=InMemoryAuditSink(), clock=clock)
    tasks: list[asyncio.Task[None]] = []
    if sweep_interval_s is None:
        sweep_interval_s = float(os.getenv("AUDIT_DRAIN_INTERVAL_S", "5"))

    async def sweeper() -> None:
        while True:
            try:
                await holds.sweep()  # type: ignore[union-attr]
            except Exception:
                log.warning("hold sweep failed", exc_info=True)
            await asyncio.sleep(sweep_interval_s)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if service is not None and bus is not None:
            tasks.extend(run_consumers(bus, service, idem or InMemoryIdempotencyStore()))
        if sweep_interval_s > 0:
            tasks.append(asyncio.create_task(sweeper()))
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
        return h.model_dump(mode="json", exclude={"audit_pending"}) | {"overdue": overdue}

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
    async def metrics(request: Request) -> Response:
        token = os.getenv("METRICS_TOKEN")
        ok = bool(token) and hmac.compare_digest(
            request.headers.get("x-metrics-token", "").encode("utf-8", "replace"), token.encode()
        )
        if not ok:
            if not _trusted(request):
                raise HTTPException(401, "metrics require METRICS_TOKEN or gateway trust")
            if request.headers.get("x-principal-role", "") not in STAFF_READ:
                raise HTTPException(403, "role not permitted")
        body = (
            "# TYPE txn_guard_fallback_mode gauge\n"
            f"txn_guard_fallback_mode {int(scorer.fallback_mode)}\n"
            "# TYPE txn_guard_model_errors_total counter\n"
            f"txn_guard_model_errors_total {scorer.model_errors}\n"
            "# TYPE txn_guard_holds_open gauge\n"
            f"txn_guard_holds_open {await holds.count_open()}\n"  # type: ignore[union-attr]
            "# TYPE txn_guard_holds_overdue gauge\n"
            f"txn_guard_holds_overdue {await holds.count_overdue()}\n"  # type: ignore[union-attr]
            "# TYPE txn_guard_holds_overdue_total counter\n"
            f"txn_guard_holds_overdue_total {await holds.overdue_total()}\n"  # type: ignore[union-attr]
        )
        return Response(body, media_type="text/plain; version=0.0.4")

    @app.get("/holds")
    async def list_holds(
        p: Annotated[Principal, Depends(principal)],
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ) -> list[dict[str, Any]]:
        need(p, STAFF_READ)
        return [view(h) for h in await holds.list_open(limit)]  # type: ignore[union-attr]

    @app.get("/holds/{txn_id}")
    async def get_hold(txn_id: str, p: Annotated[Principal, Depends(principal)]) -> dict[str, Any]:
        if p.role not in STAFF_READ | {"citizen"}:
            raise HTTPException(403, "role not permitted")
        h = await holds.get(txn_id)  # type: ignore[union-attr]
        # citizens get 404 for both "not mine" and "does not exist" (no existence oracle)
        if h is None or (p.role == "citizen" and h.payer_token != p.sub):
            raise HTTPException(404, "hold not found")
        return view(h)

    @app.post("/holds/{txn_id}/resolve")
    async def resolve(
        txn_id: str, body: ResolveRequest, p: Annotated[Principal, Depends(principal)]
    ) -> dict[str, Any]:
        need(p, STAFF_RESOLVE)
        h = await call(
            holds.resolve(txn_id, body.action, p.sub, expect_seq=body.decision_seq)  # type: ignore[union-attr]
        )
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
        # expectations are enforced inside the compare-and-set: if the hold was upgraded after
        # we read it, the release is refused (409) instead of releasing a hold_verify
        done = await call(
            holds.resolve(  # type: ignore[union-attr]
                txn_id, "release", p.sub, expect_decision="step_up", expect_seq=h.decision_seq
            )
        )
        return view(done)

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
    hist_client = redis_sync.Redis.from_url(url)
    idem = RedisIdempotencyStore(aredis)
    holds = RedisHoldStore(aredis, audit=BusAuditSink(bus))
    service = TxnGuardService(
        Scorer(), RedisHistoryStore(hist_client), holds, bus, idem,
        pending=RedisPendingStore(aredis),
        hold_deadline_s=float(os.getenv("HOLD_DEADLINE_S", str(DEFAULT_HOLD_DEADLINE_S))),
    )  # fmt: skip
    closers: list[Callable[[], Awaitable[None]]] = [aredis.aclose, _close_sync(hist_client)]
    if hasattr(bus, "close"):
        closers.append(bus.close)
    return create_app(
        service=service, bus=bus if kafka else None, idem=idem, redis=aredis, closers=closers
    )


def _close_sync(client: Any) -> Callable[[], Awaitable[None]]:
    async def close() -> None:
        await asyncio.to_thread(client.close)

    return close
