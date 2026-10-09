"""HTTP API: stateless ``POST /score`` for the citizen shield, health probes, and (optionally)
the CallEvent consumer running in the app lifespan."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Response
from pydantic import BaseModel, Field, field_validator
from scam_contracts.models import Reason
from svckit.bus import Bus
from svckit.health import make_health_router
from svckit.idempotency import IdempotencyStore, InMemoryIdempotencyStore

from .consumer import run_consumer, run_ledger_sweeper
from .model import Scorer, load_classifier
from .session import InMemorySessionStore, RedisSessionStore, SessionScorer, SessionStore

log = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 4000


class ScoreRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    # accepted for forward compatibility; currently ignored (rules and classifier are
    # script-agnostic)
    lang: str | None = Field(default=None, max_length=16)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be blank")
        return v


class ScoreResponse(BaseModel):
    score: float = Field(ge=0, le=1)
    reasons: list[Reason]
    model_version: str


def create_app(
    scorer: Scorer | None = None,
    bus: Bus | None = None,
    redis: Any = None,
    idempotency: IdempotencyStore | None = None,
) -> FastAPI:
    scorer = scorer or Scorer(load_classifier())
    store: SessionStore = RedisSessionStore(redis) if redis is not None else InMemorySessionStore()
    session_scorer = SessionScorer(store, scorer)
    tasks: list[asyncio.Task[None]] = []

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if bus is not None:
            tasks.append(
                asyncio.create_task(
                    run_consumer(bus, session_scorer, idempotency or InMemoryIdempotencyStore())
                )
            )
            tasks.append(asyncio.create_task(run_ledger_sweeper(bus, session_scorer)))
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

    app = FastAPI(title="call-guard", lifespan=lifespan)
    app.include_router(
        make_health_router(
            ready,
            extra=lambda: {
                "model_version": scorer.model_version,
                "fallback_mode": str(scorer.fallback_mode).lower(),
            },
        )
    )

    @app.get("/metrics")
    async def metrics() -> Response:
        body = (
            "# TYPE call_guard_fallback_mode gauge\n"
            f"call_guard_fallback_mode {int(scorer.fallback_mode)}\n"
            "# TYPE call_guard_classifier_errors_total counter\n"
            f"call_guard_classifier_errors_total {scorer.clf_errors}\n"
        )
        return Response(body, media_type="text/plain; version=0.0.4")

    @app.post("/score", response_model=ScoreResponse)
    async def score(req: ScoreRequest) -> ScoreResponse:
        s, reasons, version = scorer.score_text(req.message)
        log.info("score=%.3f len=%d", s, len(req.message))
        return ScoreResponse(score=s, reasons=reasons, model_version=version)

    return app


def create_service_app() -> FastAPI:
    """Env-configured app: REDIS_URL (session state + idempotency), KAFKA_BOOTSTRAP (consumer)."""
    import os

    from redis.asyncio import Redis
    from svckit.bus import KafkaBus
    from svckit.idempotency import RedisIdempotencyStore

    redis = Redis.from_url(os.environ["REDIS_URL"]) if os.getenv("REDIS_URL") else None
    kafka = os.getenv("KAFKA_BOOTSTRAP")
    return create_app(
        bus=KafkaBus(kafka) if kafka else None,
        redis=redis,
        idempotency=RedisIdempotencyStore(redis) if redis is not None else None,
    )
