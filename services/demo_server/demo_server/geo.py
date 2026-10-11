"""Static India geography for the console map (no external data fetch).

``CITIES`` (re-exported from ``geo_intel.table``) is a documented static table: (city, state, lat,
lon, population weight). Coordinates are approximate city centres (3 decimals, about 100 m);
weights are rough relative metro populations used only to order fallbacks and to size the
background layer. The simulator's citizens carry a ``state`` / ``district`` / ``city``;
``city_for`` maps them to a table row deterministically, so no citizen-level coordinate (the
simulator jitters those per person) is ever exposed.

Hotspots are computed from the run's own geo points (see ``hotspots``): a cell is a city; its
``count`` is the number of points in the latest window, ``prev_count`` the window before it, and
``trend`` compares the two (``up`` / ``down`` / ``flat``). All of it is simulated data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# the static table, city mapping and nearest-city lookup live in geo_intel.table (shared with the
# standalone service); re-exported here so existing imports keep working
from geo_intel.table import CITIES, FALLBACK, city_for, nearest_city

__all__ = ["CITIES", "FALLBACK", "GeoPoint", "city_for", "hotspots", "nearest_city", "point"]


@dataclass(frozen=True)
class GeoPoint:
    t: float  # sim seconds
    kind: str
    city: str
    state: str
    lat: float
    lon: float


def point(t: float, kind: str, row: tuple[str, str, float, float, float]) -> GeoPoint:
    return GeoPoint(round(t, 3), kind, row[0], row[1], row[2], row[3])


def hotspots(points: list[GeoPoint], now_t: float, window_s: float = 600.0) -> dict[str, Any]:
    """Cells (one per city) from the points seen so far.

    ``count`` = points in ``(now_t - window_s, now_t]``, ``prev_count`` = the window before,
    ``total`` = all points. Cells with nothing in either window but a non-zero total are kept
    (``trend`` ``down``); cash-out points are listed separately with their counts."""
    cells: dict[str, dict[str, object]] = {}
    cash: dict[str, dict[str, object]] = {}
    for p in points:
        if p.kind == "cash_out":
            c = cash.setdefault(
                p.city,
                {"city": p.city, "state": p.state, "lat": p.lat, "lon": p.lon, "count": 0},
            )
            c["count"] = int(c["count"]) + 1  # type: ignore[call-overload]
            continue
        c = cells.setdefault(
            p.city,
            {
                "city": p.city, "state": p.state, "lat": p.lat, "lon": p.lon,
                "count": 0, "prev_count": 0, "total": 0, "kinds": {},
            },
        )  # fmt: skip
        c["total"] = int(c["total"]) + 1  # type: ignore[call-overload]
        kinds = c["kinds"]
        assert isinstance(kinds, dict)
        kinds[p.kind] = kinds.get(p.kind, 0) + 1
        if now_t - window_s < p.t <= now_t:
            c["count"] = int(c["count"]) + 1  # type: ignore[call-overload]
        elif now_t - 2 * window_s < p.t <= now_t - window_s:
            c["prev_count"] = int(c["prev_count"]) + 1  # type: ignore[call-overload]
    out = []
    for c in sorted(cells.values(), key=lambda c: (-int(c["total"]), str(c["city"]))):  # type: ignore[call-overload]
        cur, prev = int(c["count"]), int(c["prev_count"])  # type: ignore[call-overload]
        c["trend"] = "up" if cur > prev else ("down" if cur < prev else "flat")
        c["kinds"] = dict(sorted(c["kinds"].items()))  # type: ignore[union-attr]
        out.append(c)
    return {
        "hotspots": out,
        "cash_out_points": sorted(cash.values(), key=lambda c: str(c["city"])),
        "window_s": window_s,  # type: ignore[dict-item]
    }
