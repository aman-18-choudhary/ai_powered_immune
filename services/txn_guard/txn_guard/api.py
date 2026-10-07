"""Service shell: health probes and metrics. The hold-and-verify API and the bus consumers that
build on ``Scorer`` / ``decide`` / ``make_decision`` arrive in Task 9."""

from fastapi import FastAPI, Response
from svckit.health import make_health_router

from .model import Scorer


def create_app(scorer: Scorer | None = None) -> FastAPI:
    scorer = scorer or Scorer()

    async def ready() -> bool:
        return True

    app = FastAPI(title="txn-guard")
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
            "# TYPE txn_guard_fallback_mode gauge\n"
            f"txn_guard_fallback_mode {int(scorer.fallback_mode)}\n"
            "# TYPE txn_guard_model_errors_total counter\n"
            f"txn_guard_model_errors_total {scorer.model_errors}\n"
        )
        return Response(body, media_type="text/plain; version=0.0.4")

    return app
