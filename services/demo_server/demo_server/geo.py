"""Static India geography for the console map (no external data fetch).

``CITIES`` is a documented static table: (city, state, lat, lon, population weight). Coordinates
are approximate city centres (3 decimals, about 100 m); weights are rough relative metro
populations used only to order fallbacks and to size the background layer. The simulator's
citizens carry a ``state`` / ``district`` / ``city``; ``city_for`` maps them to a table row
deterministically, so no citizen-level coordinate (the simulator jitters those per person) is
ever exposed.

Hotspots are computed from the run's own geo points (see ``hotspots``): a cell is a city; its
``count`` is the number of points in the latest window, ``prev_count`` the window before it, and
``trend`` compares the two (``up`` / ``down`` / ``flat``). All of it is simulated data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# (city, state, lat, lon, weight)
CITIES: tuple[tuple[str, str, float, float, float], ...] = (
    ("Mumbai", "Maharashtra", 19.076, 72.878, 12.0),
    ("Pune", "Maharashtra", 18.520, 73.857, 5.0),
    ("Nagpur", "Maharashtra", 21.146, 79.088, 2.5),
    ("Delhi", "Delhi", 28.614, 77.209, 11.0),
    ("Gurugram", "Haryana", 28.460, 77.027, 2.5),
    ("Bengaluru", "Karnataka", 12.972, 77.594, 8.0),
    ("Chennai", "Tamil Nadu", 13.083, 80.271, 5.5),
    ("Coimbatore", "Tamil Nadu", 11.017, 76.956, 2.0),
    ("Hyderabad", "Telangana", 17.385, 78.487, 7.0),
    ("Kolkata", "West Bengal", 22.573, 88.364, 6.0),
    ("Ahmedabad", "Gujarat", 23.023, 72.572, 4.5),
    ("Surat", "Gujarat", 21.170, 72.831, 3.0),
    ("Jaipur", "Rajasthan", 26.912, 75.787, 3.5),
    ("Lucknow", "Uttar Pradesh", 26.847, 80.947, 3.5),
    ("Kanpur", "Uttar Pradesh", 26.450, 80.332, 2.0),
    ("Noida", "Uttar Pradesh", 28.535, 77.391, 2.5),
    ("Patna", "Bihar", 25.594, 85.138, 2.5),
    ("Bhopal", "Madhya Pradesh", 23.259, 77.413, 2.0),
    ("Indore", "Madhya Pradesh", 22.720, 75.857, 2.5),
    ("Kochi", "Kerala", 9.931, 76.267, 2.0),
    ("Chandigarh", "Chandigarh", 30.734, 76.779, 1.5),
    ("Bhubaneswar", "Odisha", 20.296, 85.825, 1.5),
    ("Guwahati", "Assam", 26.145, 91.736, 1.5),
    ("Jamtara", "Jharkhand", 23.963, 86.803, 0.3),
    ("Nuh", "Haryana", 28.104, 77.001, 0.3),
    ("Bharatpur", "Rajasthan", 27.217, 77.490, 0.3),
    ("Deoghar", "Jharkhand", 24.485, 86.695, 0.3),
)

# simulator district -> city (the simulator names districts after the city, except these)
_DISTRICT_ALIASES: dict[str, str] = {
    "new delhi": "Delhi",
    "bengaluru urban": "Bengaluru",
    "kanpur nagar": "Kanpur",
    "gautam buddha nagar": "Noida",
    "ernakulam": "Kochi",
    "khordha": "Bhubaneswar",
    "kamrup metropolitan": "Guwahati",
}
_BY_CITY = {c[0].lower(): c for c in CITIES}
_BY_STATE: dict[str, tuple[str, str, float, float, float]] = {}
for _c in sorted(CITIES, key=lambda c: (-c[4], c[0])):  # largest city of a state is its anchor
    _BY_STATE.setdefault(_c[1].lower(), _c)
FALLBACK = _BY_CITY["delhi"]


@dataclass(frozen=True)
class GeoPoint:
    t: float  # sim seconds
    kind: str
    city: str
    state: str
    lat: float
    lon: float


def city_for(state: str | None, district: str | None = None, city: str | None = None):
    """Table row for a simulator location; deterministic, never raises. Order: city name, district
    (with aliases), the state's largest city, then the fallback (Delhi)."""
    for key in (city, district):
        if key:
            k = key.strip().lower()
            row = _BY_CITY.get(_DISTRICT_ALIASES.get(k, "").lower()) or _BY_CITY.get(k)
            if row is not None:
                return row
    if state:
        row = _BY_STATE.get(state.strip().lower())
        if row is not None:
            return row
    return FALLBACK


def nearest_city(lat: float, lon: float):
    """The table row closest to (lat, lon) (equirectangular distance; ties to the city name)."""
    return min(CITIES, key=lambda c: ((c[2] - lat) ** 2 + ((c[3] - lon) * 0.9) ** 2, c[0]))


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
