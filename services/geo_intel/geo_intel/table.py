"""Static India city table (data module; no external data fetch).

``CITIES`` rows are ``(city, state, lat, lon, weight)``. Coordinates are approximate city centres
(3 decimals, about 100 m) of a SIMULATED geography; weights are rough relative metro populations
used only to order fallbacks. Every row has a stable city code and its state an ISO 3166-2:IN
suffix; the codes are the closed enum ``scam_contracts.geo`` publishes for ``Complaint`` events
(a test keeps both lists identical). Nothing finer than a city is represented anywhere.

Moved here from ``demo_server.geo`` so the demo-server and the standalone service share one table.
"""

from __future__ import annotations

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

CITY_CODE: dict[str, str] = {
    "Mumbai": "MUM", "Pune": "PNQ", "Nagpur": "NAG", "Delhi": "DEL", "Gurugram": "GGN",
    "Bengaluru": "BLR", "Chennai": "MAA", "Coimbatore": "CJB", "Hyderabad": "HYD",
    "Kolkata": "CCU", "Ahmedabad": "AMD", "Surat": "STV", "Jaipur": "JAI", "Lucknow": "LKO",
    "Kanpur": "KNU", "Noida": "NOI", "Patna": "PAT", "Bhopal": "BHO", "Indore": "IDR",
    "Kochi": "COK", "Chandigarh": "IXC", "Bhubaneswar": "BBI", "Guwahati": "GAU",
    "Jamtara": "JMT", "Nuh": "NUH", "Bharatpur": "BHP", "Deoghar": "DGH",
}  # fmt: skip
STATE_CODE: dict[str, str] = {
    "Maharashtra": "MH", "Delhi": "DL", "Haryana": "HR", "Karnataka": "KA", "Tamil Nadu": "TN",
    "Telangana": "TG", "West Bengal": "WB", "Gujarat": "GJ", "Rajasthan": "RJ",
    "Uttar Pradesh": "UP", "Bihar": "BR", "Madhya Pradesh": "MP", "Kerala": "KL",
    "Chandigarh": "CH", "Odisha": "OD", "Assam": "AS", "Jharkhand": "JH",
}  # fmt: skip

Row = tuple[str, str, float, float, float]

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
_BY_CITY: dict[str, Row] = {c[0].lower(): c for c in CITIES}
BY_CODE: dict[str, Row] = {CITY_CODE[c[0]]: c for c in CITIES}
_BY_STATE: dict[str, Row] = {}
for _c in sorted(CITIES, key=lambda c: (-c[4], c[0])):  # largest city of a state is its anchor
    _BY_STATE.setdefault(_c[1].lower(), _c)
STATE_ANCHOR: dict[str, Row] = {STATE_CODE[r[1]]: r for r in _BY_STATE.values()}
FALLBACK: Row = _BY_CITY["delhi"]


def code_of(row: Row) -> str:
    return CITY_CODE[row[0]]


def city_for(state: str | None, district: str | None = None, city: str | None = None) -> Row:
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


def nearest_city(lat: float, lon: float) -> Row:
    """The table row closest to (lat, lon) (equirectangular distance; ties to the city name)."""
    return min(CITIES, key=lambda c: ((c[2] - lat) ** 2 + ((c[3] - lon) * 0.9) ** 2, c[0]))
