"""Coarse location codes for ``Complaint`` events (a fixed enum, never free text).

City codes name rows of the static city table owned by ``geo_intel.table``; state codes are the
ISO 3166-2:IN subdivision suffixes of the states those cities are in. ``geo_intel`` has a test
that its table and these enums list exactly the same codes, so a city can be added in one place
only together with the other. Location is city-level or coarser by construction.
"""

CITY_CODES: tuple[str, ...] = (
    "MUM", "PNQ", "NAG", "DEL", "GGN", "BLR", "MAA", "CJB", "HYD", "CCU", "AMD", "STV", "JAI",
    "LKO", "KNU", "NOI", "PAT", "BHO", "IDR", "COK", "IXC", "BBI", "GAU", "JMT", "NUH", "BHP",
    "DGH",
)  # fmt: skip
STATE_CODES: tuple[str, ...] = (
    "MH", "DL", "HR", "KA", "TN", "TG", "WB", "GJ", "RJ", "UP", "BR", "MP", "KL", "CH", "OD",
    "AS", "JH",
)  # fmt: skip
