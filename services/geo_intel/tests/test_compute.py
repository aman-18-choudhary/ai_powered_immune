from datetime import timedelta

from geo_intel.compute import build_hotspots, spans, z_score
from geo_intel.config import Settings

from .conftest import T0

CFG = Settings(baseline_windows=6, z_threshold=3.0, min_count=3, min_rate=0.5)


def row(code, kind="scam_call", cur=0, prev=0, base=0, total=None):
    return {"city_code": code, "kind": kind, "cur": cur, "prev": prev, "base": base,
            "total": total if total is not None else max(cur, prev, base)}  # fmt: skip


def test_spans():
    b, p, c = spans(T0, 600, 6)
    assert c == T0 - timedelta(seconds=600) and p == T0 - timedelta(seconds=1200)
    assert b == T0 - timedelta(seconds=4200)


def test_z_score_poisson_with_floor():
    z, rate = z_score(9, 12, CFG)  # baseline 2 per window
    assert rate == 2.0 and round(z, 3) == round(7 / 2**0.5, 3)
    z0, rate0 = z_score(3, 0, CFG)  # silent history: floored at min_rate 0.5
    assert rate0 == 0.0 and round(z0, 3) == round((3 - 0.5) / 0.5**0.5, 3)


def test_hotspot_needs_z_and_min_count():
    out = build_hotspots(
        [row("MUM", cur=9, base=12), row("DEL", cur=2, base=0), row("BLR", cur=6, base=36)],
        T0, 600, CFG,
    )  # fmt: skip
    by = {c["city_code"]: c for c in out["hotspots"]}
    assert by["MUM"]["hotspot"] is True
    assert by["DEL"]["hotspot"] is False  # z high-ish but below min_count 3
    assert by["BLR"]["hotspot"] is False  # at its usual rate: 6 vs baseline 6 per window
    assert out["hotspots"][0]["city_code"] == "MUM"  # hotspots first


def test_trend_compares_last_two_windows_and_kinds_merge():
    out = build_hotspots(
        [row("MUM", "scam_call", cur=4, prev=2), row("MUM", "held_transfer", cur=1, prev=3),
         row("PNQ", cur=1, prev=5), row("DEL", cur=2, prev=2)], T0, 600, CFG,
    )  # fmt: skip
    by = {c["city_code"]: c for c in out["hotspots"]}
    assert by["MUM"]["count"] == 5 and by["MUM"]["prev_count"] == 5 and by["MUM"]["trend"] == "flat"
    assert by["PNQ"]["trend"] == "down" and by["DEL"]["trend"] == "flat"
    assert by["MUM"]["kinds"] == {"held_transfer": 3, "scam_call": 4}


def test_cash_out_points_are_separate_and_not_cells():
    out = build_hotspots(
        [row("MUM", "cash_out", cur=2, total=7), row("JMT", "cash_out", cur=0, total=1)],
        T0, 600, CFG,
    )  # fmt: skip
    assert out["hotspots"] == []
    assert [(c["city_code"], c["count"], c["window_count"]) for c in out["cash_out_points"]] == [
        ("JMT", 1, 0), ("MUM", 7, 2),
    ]  # fmt: skip


def test_deterministic_and_unknown_codes_skipped():
    rows = [row("MUM", cur=5, base=2), row("ZZZ", cur=9), row("DEL", cur=5, base=2)]
    a = build_hotspots(rows, T0, 600, CFG)
    b = build_hotspots(list(reversed(rows)), T0, 600, CFG)
    assert a == b and [c["city_code"] for c in a["hotspots"]] == ["DEL", "MUM"]  # tie: code order
