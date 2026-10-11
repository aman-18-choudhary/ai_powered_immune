"""Hotspot, trend and cash-out computation over per-(city, kind) window counts.

Model (``MODEL_VERSION``): for a window ``W`` ending at ``as_of`` a cell (one city) with ``count``
non-cash-out complaints in ``(as_of - W, as_of]`` is compared with a Poisson baseline: the mean
count per window over the ``B`` windows before it (``base / B``, floored at ``min_rate`` so a
silent history does not make one report look infinite). ``z = (count - lambda) / sqrt(lambda)``;
a hotspot needs ``z >= z_threshold`` and ``count >= min_count``. ``trend`` compares ``count`` with
the previous window (``up`` / ``down`` / ``flat``). Counts are small integers, the normal
approximation is rough for tiny lambda: that is why ``min_count`` exists. Everything is a
function of the stored complaints, the table and ``as_of``: no randomness, no wall clock.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from .config import Settings
from .table import BY_CODE, CITY_CODE, STATE_CODE, Row

MODEL_VERSION = "geo-poisson-v1"
CASH_OUT = "cash_out"


def _city_fields(row: Row) -> dict[str, Any]:
    return {
        "city_code": CITY_CODE[row[0]], "city": row[0], "state": row[1],
        "state_code": STATE_CODE[row[1]], "lat": row[2], "lon": row[3],
    }  # fmt: skip


def city_view(code: str) -> dict[str, Any]:
    return _city_fields(BY_CODE[code])


def spans(as_of: datetime, window_s: float, baseline_windows: int):
    w = timedelta(seconds=window_s)
    cur_start = as_of - w
    prev_start = cur_start - w
    base_start = cur_start - baseline_windows * w
    return base_start, prev_start, cur_start


def z_score(count: int, base: int, cfg: Settings) -> tuple[float, float]:
    """(z, baseline rate per window)."""
    rate = base / cfg.baseline_windows
    lam = max(rate, cfg.min_rate)
    return (count - lam) / math.sqrt(lam), rate


def build_hotspots(
    rows: list[dict[str, Any]], as_of: datetime, window_s: float, cfg: Settings
) -> dict[str, Any]:
    cells: dict[str, dict[str, Any]] = {}
    cash: dict[str, dict[str, Any]] = {}
    for r in rows:
        code, kind = r["city_code"], r["kind"]
        if code not in BY_CODE:  # a row from a table version that no longer lists this city
            continue
        if kind == CASH_OUT:
            c = cash.setdefault(code, {**city_view(code), "count": 0, "window_count": 0})
            c["count"] += r["total"]
            c["window_count"] += r["cur"]
            continue
        c = cells.setdefault(
            code,
            {**city_view(code), "count": 0, "prev_count": 0, "base": 0, "total": 0, "kinds": {}},
        )
        c["count"] += r["cur"]
        c["prev_count"] += r["prev"]
        c["base"] += r["base"]
        c["total"] += r["total"]
        c["kinds"][kind] = c["kinds"].get(kind, 0) + r["total"]
    out = []
    for c in cells.values():
        base = c.pop("base")
        z, rate = z_score(c["count"], base, cfg)
        c["baseline_rate"] = round(rate, 3)
        c["z"] = round(z, 3)
        c["hotspot"] = c["count"] >= cfg.min_count and z >= cfg.z_threshold
        c["trend"] = (
            "up" if c["count"] > c["prev_count"]
            else ("down" if c["count"] < c["prev_count"] else "flat")
        )  # fmt: skip
        c["kinds"] = dict(sorted(c["kinds"].items()))
        out.append(c)
    out.sort(key=lambda c: (not c["hotspot"], -c["z"], -c["count"], -c["total"], c["city_code"]))
    return {
        "model_version": MODEL_VERSION,
        "as_of": as_of.isoformat(),
        "window_s": int(window_s),
        "baseline_windows": cfg.baseline_windows,
        "z_threshold": cfg.z_threshold,
        "min_count": cfg.min_count,
        "hotspots": out,
        "cash_out_points": sorted(cash.values(), key=lambda c: c["city_code"]),
        "note": "city-level aggregates of reported complaints on a simulated geography",
    }
