"""Complaint hotspots route (Phase 10), staff only. See ``geo_store.py``."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError

from .geo_store import CELL_SIZES, GeoStore
from .schema import CATEGORIES, RISK_LEVELS


def _invalid(field: str, msg: str) -> RequestValidationError:
    return RequestValidationError(
        [{"loc": ("query", field), "msg": msg, "type": "value_error", "input": None}]
    )


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def register_geo_routes(
    app: FastAPI,
    *,
    geo: GeoStore,
    staff: Any,
    throttle: Callable[[str, Any], None],
    now: Callable[[], datetime],
) -> None:
    """``staff`` is the app's ``Annotated[CurrentUser, Depends(role_guard(...))]`` type."""
    StaffUser = staff  # noqa: N806 - a type alias used in the signature below

    @app.get("/geo/complaints/hotspots")
    async def hotspots(
        user: StaffUser,
        from_: Annotated[datetime | None, Query(alias="from")] = None,
        to: datetime | None = None,
        category: str | None = None,
        risk_level: str | None = None,
        cell: float = 0.25,
        bbox: str | None = None,
        min_count: Annotated[int, Query(ge=1, le=20)] = 1,
        compare: bool = False,
    ) -> dict[str, Any]:
        throttle("read", user)
        start, end = _aware(from_), _aware(to)
        if start and end and start > end:
            raise _invalid("from", "'from' must be on or before 'to'")
        if category and category not in CATEGORIES:
            raise _invalid("category", "unknown category")
        if risk_level and risk_level not in (*RISK_LEVELS, "not_scored"):
            raise _invalid("risk_level", "unknown risk level")
        if cell not in CELL_SIZES:
            raise _invalid("cell", f"cell must be one of {', '.join(map(str, CELL_SIZES))}")
        if compare and start is None:
            raise _invalid("compare", "a comparison needs 'from'")
        box = None
        if bbox:
            try:
                s, w, n, e = (float(x) for x in bbox.split(","))
            except ValueError:
                raise _invalid("bbox", "bbox is south,west,north,east") from None
            if not (-90 <= s <= n <= 90 and -180 <= w <= e <= 180):
                raise _invalid("bbox", "bbox is south,west,north,east within valid ranges")
            box = (s, w, n, e)
        return await asyncio.to_thread(
            geo.hotspots, start=start, end=end, category=category, risk_level=risk_level,
            cell=cell, bbox=box, min_count=min_count, compare=compare, now=now(),
        )  # fmt: skip
