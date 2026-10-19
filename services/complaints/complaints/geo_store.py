"""Complaint hotspots (Phase 10): counts of persisted complaints on a latitude/longitude grid.

Source: the complaint's optional ``latitude``/``longitude`` (given by the complainant, usually
with the browser's location button) and, for complaints without coordinates, the free-text
``state``/``city`` fields. Nothing is geocoded and no map or tile service is contacted.

Cells, not points: a complaint is counted in the grid cell (``cell`` degrees, 0.1 to 1.0) that
contains it and the cell's centre is returned, never the complaint's own coordinates.

Privacy (same rule as the complaint network and Fraud DNA): deleted complaints and complaints
whose owner turned off case matching are left out of every cell, place and total. Hotspots
therefore undercount by the number of opted-out complaints; that number is not shown.

Risk: the level of the complaint's latest analysis (complaints never analysed count as
``not_scored``). Comparison: the same filters over the period of equal length just before
``from``.
"""

import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select

from .network_store import _iso, _matchable
from .schema import analyses, complaints, users
from .store import ComplaintStore

CELL_SIZES = (0.1, 0.25, 0.5, 1.0)
MAX_ROWS = 50_000  # complaints read for one request
MAX_CELLS = 500
MAX_PLACES = 100
NOTE = (
    "Counts of complaints, not of offences or offenders. Cells use the coordinates complainants "
    "chose to give; complaints without them are listed by state and city. Complaints whose "
    "complainants turned off case matching, and deleted complaints, are not included."
)


def _cell_of(lat: float, lon: float, size: float) -> tuple[int, int]:
    return math.floor(lat / size), math.floor(lon / size)


def _centre(ix: int, size: float) -> float:
    return round((ix + 0.5) * size, 6)


class GeoStore:
    def __init__(self, store: ComplaintStore) -> None:
        self.store = store

    def _rows(
        self, start: datetime | None, end: datetime | None, category: str | None
    ) -> tuple[list[Any], bool]:
        c, u = complaints, users
        q = (
            select(
                c.c.id, c.c.latitude, c.c.longitude, c.c.state, c.c.city, c.c.category,
                c.c.created_at,
            )
            .join(u, u.c.id == c.c.owner_user_id)
            .where(_matchable(c, u))
        )  # fmt: skip
        if start is not None:
            q = q.where(c.c.created_at >= start)
        if end is not None:
            q = q.where(c.c.created_at <= end)
        if category:
            q = q.where(c.c.category == category)
        with self.store.engine.connect() as conn:
            rows = conn.execute(q.order_by(c.c.created_at.desc()).limit(MAX_ROWS + 1)).all()
            truncated = len(rows) > MAX_ROWS
            rows = rows[:MAX_ROWS]
            levels = self._levels(conn, [r.id for r in rows])
        return [(r, levels.get(r.id) or "not_scored") for r in rows], truncated

    @staticmethod
    def _levels(conn: Any, ids: list[str]) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        for i in range(0, len(ids), 900):
            chunk = ids[i : i + 900]
            latest = (
                select(analyses.c.complaint_id, func.max(analyses.c.version).label("v"))
                .where(analyses.c.complaint_id.in_(chunk))
                .group_by(analyses.c.complaint_id)
                .subquery()
            )
            for r in conn.execute(
                select(analyses.c.complaint_id, analyses.c.risk_level, analyses.c.detail).join(
                    latest,
                    and_(analyses.c.complaint_id == latest.c.complaint_id,
                         analyses.c.version == latest.c.v),
                )
            ):  # fmt: skip
                d = r.detail if isinstance(r.detail, dict) else {}
                out[r.complaint_id] = d.get("risk_level") or r.risk_level
        return out

    def hotspots(
        self,
        *,
        start: datetime | None,
        end: datetime | None,
        category: str | None,
        risk_level: str | None,
        cell: float,
        bbox: tuple[float, float, float, float] | None,
        min_count: int,
        compare: bool,
        now: datetime,
    ) -> dict[str, Any]:
        rows, truncated = self._rows(start, end, category)
        if risk_level:
            rows = [(r, lv) for r, lv in rows if lv == risk_level]

        def in_box(r: Any) -> bool:
            if bbox is None:
                return True
            s, w, n, e = bbox
            return s <= r.latitude <= n and w <= r.longitude <= e

        geo = [(r, lv) for r, lv in rows if r.latitude is not None and r.longitude is not None]
        geo = [(r, lv) for r, lv in geo if in_box(r)]
        without = [(r, lv) for r, lv in rows if r.latitude is None or r.longitude is None]

        cells: dict[tuple[int, int], dict[str, Any]] = {}
        for r, lv in geo:
            key = _cell_of(float(r.latitude), float(r.longitude), cell)
            c = cells.setdefault(key, {"count": 0, "by_category": Counter(), "by_risk": Counter(),
                                       "first": None, "last": None})  # fmt: skip
            c["count"] += 1
            c["by_category"][r.category] += 1
            c["by_risk"][lv] += 1
            ts = r.created_at
            c["first"] = ts if c["first"] is None or ts < c["first"] else c["first"]
            c["last"] = ts if c["last"] is None or ts > c["last"] else c["last"]

        previous: dict[tuple[int, int], int] = defaultdict(int)
        period = None
        if compare and start is not None:  # the API requires ``from`` for a comparison
            p_end = start
            p_start = start - ((end or now) - start)
            prev_rows, _ = self._rows(p_start, p_end - timedelta(microseconds=1), category)
            if risk_level:
                prev_rows = [(r, lv) for r, lv in prev_rows if lv == risk_level]
            for r, _lv in prev_rows:
                if r.latitude is not None and r.longitude is not None and in_box(r):
                    previous[_cell_of(float(r.latitude), float(r.longitude), cell)] += 1
            period = {"from": _iso(p_start), "to": _iso(p_end)}

        shown = sorted(
            ((k, v) for k, v in cells.items() if v["count"] >= min_count),
            key=lambda kv: (-kv[1]["count"], kv[0]),
        )
        cells_truncated = len(shown) > MAX_CELLS
        out_cells = []
        for (iy, ix), v in shown[:MAX_CELLS]:
            item = {
                "id": f"{cell}:{iy}:{ix}",
                "lat": _centre(iy, cell), "lon": _centre(ix, cell),
                "bounds": {"south": round(iy * cell, 6), "west": round(ix * cell, 6),
                           "north": round((iy + 1) * cell, 6), "east": round((ix + 1) * cell, 6)},
                "count": v["count"],
                "by_category": dict(v["by_category"].most_common()),
                "by_risk": dict(v["by_risk"].most_common()),
                "first_at": _iso(v["first"]), "last_at": _iso(v["last"]),
            }  # fmt: skip
            if compare:
                prev = previous.get((iy, ix), 0)
                item["previous_count"] = prev
                item["change"] = v["count"] - prev
            out_cells.append(item)

        places: Counter[tuple[str, str]] = Counter()
        for r, _lv in without:
            st = (r.state or "").strip()
            ct = (r.city or "").strip()
            places[(st, ct)] += 1
        place_items = [
            {"state": st or None, "city": ct or None, "count": n}
            for (st, ct), n in sorted(places.items(), key=lambda kv: (-kv[1], kv[0]))
            if n >= min_count
        ]
        return {
            "cell_degrees": cell, "min_count": min_count, "note": NOTE,
            "filters": {"from": _iso(start), "to": _iso(end), "category": category,
                        "risk_level": risk_level,
                        "bbox": list(bbox) if bbox else None},
            "totals": {"complaints": len(geo) + len(without), "with_coordinates": len(geo),
                       "without_coordinates": len(without), "cells": len(shown)},
            "cells": out_cells, "cells_truncated": cells_truncated,
            "places": place_items[:MAX_PLACES], "places_truncated": len(place_items) > MAX_PLACES,
            "comparison": {"previous": period} if compare else None,
            "truncated": truncated,
        }  # fmt: skip
