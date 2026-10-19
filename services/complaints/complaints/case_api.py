"""Investigator case tools (Phase 12): notes, timeline, report export. See ``case_store.py``."""

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import FastAPI, Query, Response
from pydantic import BaseModel, ConfigDict, StringConstraints

from .case_store import NOTE_MAX, CaseStore, render_report

MAX_LIMIT = 100


class NoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NOTE_MAX)
    ]


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def register_case_routes(
    app: FastAPI,
    *,
    cases: CaseStore,
    user_type: Any,
    staff: Any,
    load: Callable[[Any, str], Awaitable[dict[str, Any]]],
    detail: Callable[[Any, dict[str, Any]], Awaitable[dict[str, Any]]],
    throttle: Callable[[str, Any], None],
    now: Callable[[], datetime],
) -> None:
    """``staff``/``user_type``: the app's ``Annotated[CurrentUser, Depends(...)]`` types;
    ``load`` returns the complaint for its owner or staff and 404s otherwise."""
    StaffUser = staff  # noqa: N806
    User = user_type  # noqa: N806

    @app.get("/complaints/{complaint_id}/notes")
    async def list_notes(
        complaint_id: str,
        user: StaffUser,
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
        offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    ) -> dict[str, Any]:
        throttle("read", user)
        c = await load(user, complaint_id)
        items, total = await asyncio.to_thread(cases.list_notes, c["id"], limit, offset)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.post("/complaints/{complaint_id}/notes", status_code=201)
    async def add_note(complaint_id: str, body: NoteBody, user: StaffUser) -> dict[str, Any]:
        throttle("write", user)
        c = await load(user, complaint_id)
        text = _CTRL.sub("", body.body)
        return await asyncio.to_thread(cases.add_note, c["id"], user.id, text, now())

    @app.get("/complaints/{complaint_id}/timeline")
    async def timeline(complaint_id: str, user: StaffUser) -> dict[str, Any]:
        throttle("read", user)
        c = await load(user, complaint_id)
        return {
            "complaint_id": c["id"],
            "items": await asyncio.to_thread(cases.timeline, c["id"], True),
        }

    @app.get("/complaints/{complaint_id}/report")
    async def report(complaint_id: str, user: User) -> Response:
        throttle("read", user)
        c = await load(user, complaint_id)
        staff_view = bool(user.staff)
        view = await detail(user, c)
        items = await asyncio.to_thread(cases.timeline, c["id"], staff_view)
        t = now()
        await asyncio.to_thread(cases.audit_export, c["id"], user.id, staff_view, t)
        md = render_report(view, items, staff_view, t)
        name = f"complaint-{c['id'][:8]}-report.md"
        return Response(
            md, media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}"',
                     "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )  # fmt: skip
