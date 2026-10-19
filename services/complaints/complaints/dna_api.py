"""Fraud DNA route (Phase 8), staff only. See ``dna.py`` for what similarity means and does not."""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import FastAPI, Query

from .dna import FraudDna
from .store import NotFound

MAX_LIMIT = 50


def register_dna_routes(
    app: FastAPI, *, dna: FraudDna, staff: Any, throttle: Callable[[str, Any], None]
) -> None:
    """``staff`` is the app's ``Annotated[CurrentUser, Depends(role_guard(...))]`` type."""
    StaffUser = staff  # noqa: N806 - a type alias used in the signature below

    @app.get("/complaints/{complaint_id}/similar")
    async def similar(
        complaint_id: str,
        user: StaffUser,
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 10,
    ) -> dict[str, Any]:
        # one short embedding (tens of ms) and an index lookup: the read bucket. The analysis
        # bucket (10/min) was too tight: the panel polls while the model loads after a start
        throttle("read", user)
        if len(complaint_id) > 64:
            raise NotFound()
        return await dna.similar(complaint_id, limit)
