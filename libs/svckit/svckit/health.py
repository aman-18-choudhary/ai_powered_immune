"""Health endpoints."""

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Response


def make_health_router(
    ready_check: Callable[[], Awaitable[bool]],
    extra: Callable[[], dict[str, str]] | None = None,
) -> APIRouter:
    """`extra` (optional) adds fields to the /readyz body, e.g. the running model version."""
    router = APIRouter()

    @router.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/readyz")
    async def readyz(response: Response) -> dict[str, str]:
        try:
            ok = await ready_check()
        except Exception:
            ok = False
        if not ok:
            response.status_code = 503
            return {"status": "not_ready"}
        return {"status": "ready", **(extra() if extra else {})}

    return router
