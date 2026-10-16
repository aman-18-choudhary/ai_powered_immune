"""HTTP routes of the complaint network (Phase 9). Staff only (INVESTIGATOR or ADMIN); citizens
get 403 ``forbidden_role`` from the role guard before any lookup. Identifiers are searched with a
POST body (never a URL or query string) and are never logged; responses carry masked display
values only. See ``network_store`` for the link semantics, privacy rule and caps."""

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError

from .network import normalise_query, value_hash
from .network_store import NetworkStore
from .store import NotFound
from .validation import EntitySearchBody, LinkReviewBody

MAX_LIMIT = 50
MAX_OFFSET = 10_000


def _invalid(field: str, msg: str) -> RequestValidationError:
    return RequestValidationError(
        [{"loc": ("body", field), "msg": msg, "type": "value_error", "input": None}]
    )


def register_network_routes(
    app: FastAPI,
    *,
    network: NetworkStore,
    staff: Any,
    throttle: Callable[[str, Any], None],
    now: Callable[[], datetime],
) -> None:
    """``staff`` is the app's ``Annotated[CurrentUser, Depends(role_guard(...))]`` type."""
    StaffUser = staff  # noqa: N806 - a type alias used in the signatures below

    def _id(complaint_id: str) -> str:
        if len(complaint_id) > 64:
            raise NotFound()
        return complaint_id

    @app.get("/complaints/{complaint_id}/related")
    async def related(
        complaint_id: str,
        user: StaffUser,
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 20,
        offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
    ) -> dict[str, Any]:
        throttle("read", user)
        return await asyncio.to_thread(network.related, _id(complaint_id), limit, offset)

    @app.get("/complaints/{complaint_id}/graph")
    async def graph(
        complaint_id: str,
        user: StaffUser,
        depth: Annotated[int, Query(ge=1, le=2)] = 1,
    ) -> dict[str, Any]:
        throttle("read", user)
        return await asyncio.to_thread(network.graph, _id(complaint_id), depth)

    @app.get("/graph/clusters")
    async def clusters(
        user: StaffUser,
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 20,
        offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
    ) -> dict[str, Any]:
        throttle("read", user)
        return await asyncio.to_thread(network.clusters, limit, offset)

    @app.post("/entities/search")
    async def search(body: EntitySearchBody, user: StaffUser) -> dict[str, Any]:
        throttle("read", user)
        try:
            norm, display = normalise_query(body.type, body.value, platform=body.platform)
        except ValueError as exc:
            field = "platform" if "platform" in str(exc) else "value"
            raise _invalid(field, str(exc)) from None
        out = await asyncio.to_thread(
            network.search, body.type, value_hash(body.type, norm), body.limit, body.offset
        )
        return {"entity": {"type": body.type, "display": display}, **out}

    @app.post("/complaint-links/review", status_code=201)
    async def review(body: LinkReviewBody, user: StaffUser) -> dict[str, Any]:
        throttle("write", user)
        return await asyncio.to_thread(
            network.review, body.complaint_a, body.complaint_b, body.decision, body.note,
            user.id, now(),
        )  # fmt: skip
