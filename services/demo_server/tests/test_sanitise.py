"""Exact amounts must not reach the stream or the snapshot (only buckets)."""

import itertools
import json
import re

import pytest
from call_guard import rules as call_rules
from conftest import login, wait_status
from txn_guard.features import FEATURE_NAMES
from txn_guard.policy import overlays
from txn_guard.reasons import triggers

from demo_server.sanitise import sanitise_detail

DIGITS4 = re.compile(r"\d{4,}")
# values that legitimately contain digit runs: ISO dates (antibody expiry) and the 8-hex
# antibody short id printed by the txn-guard policy text
ALLOWED = re.compile(r"\d{4}-\d{2}-\d{2}|antibody [0-9a-f]{8}")


def _feature_variants():
    base = dict.fromkeys(FEATURE_NAMES, 0.0)
    amounts = (9_500.0, 16_000.0, 20_944.0, 123_456.0, 1_234_567.0, 98_765_432.0)
    flags = ("payee_age_young", "new_payee", "payee_established", "payee_in_antibody",
             "payee_recently_new", "payee_repeat_large_1h", "future_dated")  # fmt: skip
    for amt, rail, call in itertools.product(amounts, (0.0, 1.0, 2.0), (0.0, 0.9)):
        for n in range(len(flags) + 1):
            for combo in itertools.combinations(flags, n):
                f = dict(base)
                f.update(
                    amount_inr=amt,
                    rail=rail,
                    active_call_risk=call,
                    amount_zscore=12.0,
                    amount_vs_typical_log=9.0,
                    payee_age_days=3.0,
                    history_len=5.0,
                    velocity_count_1h=4.0,
                    hour_ist=2.0,
                    night=1.0,
                    device_novel=1.0,
                    payee_max_prior_log=5.0,
                    amount_log=10.0,
                    antibody_id_prefix=12345678.0,
                    antibody_expires_ts=1.8e9,
                )
                f.update(dict.fromkeys(combo, 1.0))
                yield f


def _all_templates() -> list[str]:
    out: set[str] = set()
    for f in _feature_variants():
        out.update(o.detail for o in overlays(f))
        out.update(t.detail for t in triggers(f))
    out.update(call_rules._SPECS.values())
    return sorted(out)


def test_every_scorer_reason_text_is_amount_free_after_sanitising():
    texts = _all_templates()
    assert len(texts) > 20
    assert any("Rs " in t for t in texts)  # the exact-amount templates are really exercised
    for t in texts:
        for bucket in ("10k-100k", None):
            clean = sanitise_detail(t, bucket)
            stripped = ALLOWED.sub("", clean)
            assert not DIGITS4.search(stripped), (t, clean)
            assert not re.search(r"\bRs\b|₹", clean), clean


@pytest.mark.parametrize(
    "raw",
    ["Rs 16,000", "Rs. 1,23,456", "INR 20944", "₹ 5,00,000.50", "amount (Rs 20,944)", "rs 99999"],
)
def test_amount_patterns_replaced_by_bucket(raw):
    out = sanitise_detail(f"x {raw} y", "10k-100k")
    assert "10k-100k" in out and not DIGITS4.search(out)
    assert "withheld" in sanitise_detail(raw, None)


def test_bare_long_numbers_and_ratios_dropped_but_small_numbers_kept():
    out = sanitise_detail("1,23,456 and 12345678 and about 123456x typical, z-score 12.3, 3 days")
    assert not DIGITS4.search(out)
    assert "z-score 12.3" in out and "3 days" in out


def _scan(obj, path=""):
    """Yield (path, string) for every string in a JSON-like object."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _scan(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _scan(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


def test_stream_and_snapshot_carry_no_exact_amount(app_factory):
    from fastapi.testclient import TestClient

    with TestClient(app_factory()) as c:
        off = login(c, "officer")
        cit = login(c, "citizen")["Authorization"].split()[1]
        c.post("/api/demo/start", json={"seed": 1, "speed": 20, "mode": "hero"}, headers=off)
        wait_status(c, off, ("finished", "error"))
        with c.websocket_connect(f"/api/stream?token={cit}") as ws:
            snap = ws.receive_json()
        hist = c.app.state.engine.log.history
        assert any(e["kind"].startswith("hold.") for e in hist)
        blob = json.dumps([*hist, snap])
        assert not re.search(r"\bRs\b|₹", blob)
        for e in [*hist, *snap["events"]]:
            for _path, s in _scan(e.get("reasons", [])):
                assert not DIGITS4.search(ALLOWED.sub("", s)), (e["kind"], s)
        # officer REST views still show the exact amount
        holds = c.get("/api/holds?state=all", headers=off).json()["holds"]
        assert holds and all(re.fullmatch(r"\d+\.\d{2}", h["amount_inr"]) for h in holds)
        for h in holds:
            assert not re.search(r"\bRs\b", json.dumps(h["reasons"]))
