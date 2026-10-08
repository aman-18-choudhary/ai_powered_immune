"""Pure, deterministic feature extraction: ``extract_features(txn, ctx) -> dict[str, float]``.

Same inputs always give the same features; nothing here reads a clock or global state (the
evaluation time comes from ``ctx.now``). Every value is finite: NaN / inf anywhere in the
context is sanitised to 0 and extreme values are clipped. Cold start (no history) yields
neutral amount features, never a division by zero. All timestamps are interpreted in IST for
hour-of-day. Amount validity (>0, rail limits) is enforced by the ``Transaction`` model.
"""

import math
from datetime import timedelta
from zoneinfo import ZoneInfo

from scam_contracts.models import Transaction

from .history import CALL_RISK_WINDOW, Context

IST = ZoneInfo("Asia/Kolkata")
CALL_RISK_THRESHOLD = 0.7  # mirrors call-guard's alert threshold
FUTURE_TOLERANCE = timedelta(minutes=5)
MIN_HISTORY_FOR_Z = 3
MIN_STD = 0.35  # floor on the payer's log-amount std so near-constant payers don't blow up z
Z_CLIP = 10.0
RATIO_CLIP = 10.0  # |ln(amount / typical)| clip
AGE_CAP_DAYS = 3650.0
YOUNG_PAYEE_DAYS = 30
RECENTLY_NEW_PAYEE = timedelta(hours=24)
REPEAT_LARGE_WINDOW = timedelta(hours=1)
REPEAT_LARGE_INR = 10_000.0  # UPI/IMPS value; multiplied by the rail's RAIL_AMOUNT_SCALE
# Per-rail scale applied to every absolute-rupee threshold (policy.py and REPEAT_LARGE_INR);
# derivation in policy.py / README.
RAIL_AMOUNT_SCALE: dict[str, float] = {"UPI": 1.0, "IMPS": 1.0, "NEFT": 5.0}
ESTABLISHED_MIN_PAYMENTS = 3
ESTABLISHED_MIN_AGE = timedelta(days=7)
RAIL_CODE = {"UPI": 0.0, "IMPS": 1.0, "NEFT": 2.0}

FEATURE_NAMES: tuple[str, ...] = (
    "amount_log",
    "amount_inr",
    "amount_zscore",
    "amount_vs_typical_log",
    "history_len",
    "payee_age_days",
    "payee_age_young",
    "new_payee",
    "velocity_count_1h",
    "velocity_amount_1h",
    "velocity_count_24h",
    "velocity_amount_24h",
    "rail",
    "hour_ist",
    "night",
    "active_call_risk",
    "device_novel",
    "payee_in_antibody",
    "future_dated",
    "payee_recently_new",
    "payee_repeat_large_1h",
    "payee_established",
    "payee_max_prior_log",
)
# Used by the policy overlays / reason text only, never by the booster: ``rail`` (simulated scams
# are almost all UPI, so the booster would learn "IMPS/NEFT == benign"), the absolute amount, and
# the two payee-relationship features that policy rules read.
POLICY_ONLY_FEATURES = frozenset(
    {
        "rail",
        "amount_inr",
        "amount_log",
        "payee_recently_new",
        "payee_repeat_large_1h",
        "payee_established",
        "payee_max_prior_log",
    }
)
MODEL_FEATURES: tuple[str, ...] = tuple(n for n in FEATURE_NAMES if n not in POLICY_ONLY_FEATURES)


def _finite(x: float, default: float = 0.0, lo: float = -1e9, hi: float = 1e9) -> float:
    if not math.isfinite(x):
        return default
    return min(hi, max(lo, x))


def extract_features(txn: Transaction, ctx: Context) -> dict[str, float]:
    amount = _finite(float(txn.amount_inr), 1.0, 1e-6)
    la = math.log(amount)
    n = max(0, ctx.payer_n)
    mean = _finite(ctx.payer_log_mean)
    std = max(_finite(ctx.payer_log_std), MIN_STD)
    z = _finite((la - mean) / std, 0.0, -Z_CLIP, Z_CLIP) if n >= MIN_HISTORY_FOR_Z else 0.0
    vs_typical = _finite(la - mean, 0.0, -RATIO_CLIP, RATIO_CLIP) if n >= 1 else 0.0

    ts = txn.ts
    c1 = c24 = 0
    a1 = a24 = 0.0
    for rts, ramt in ctx.recent:
        age = ts - rts
        if timedelta(0) <= age < timedelta(hours=24):
            c24 += 1
            a24 += _finite(ramt, 0.0, 0.0)
            if age < timedelta(hours=1):
                c1 += 1
                a1 += _finite(ramt, 0.0, 0.0)

    risk = 0.0
    for rts, score in ctx.call_risks:
        if timedelta(0) <= ts - rts <= CALL_RISK_WINDOW:
            risk = max(risk, _finite(score, 0.0, 0.0, 1.0))

    first = ctx.payee_first_seen_ts
    recently_new = first is not None and timedelta(0) <= ts - first < RECENTLY_NEW_PAYEE
    repeat_large = any(
        timedelta(0) <= ts - rts < REPEAT_LARGE_WINDOW
        and _finite(ramt) >= REPEAT_LARGE_INR * RAIL_AMOUNT_SCALE[txn.rail]
        for rts, ramt in ctx.payee_recent
    )

    established = (
        first is not None
        and max(0, ctx.payee_n) >= ESTABLISHED_MIN_PAYMENTS
        and ts - first >= ESTABLISHED_MIN_AGE
    )
    max_prior = _finite(ctx.payee_max_amount, 0.0, 0.0)

    hour = ts.astimezone(IST).hour
    feats = {
        "amount_log": la,
        "amount_inr": amount,
        "amount_zscore": z,
        "amount_vs_typical_log": vs_typical,
        "history_len": math.log1p(n),
        "payee_age_days": min(AGE_CAP_DAYS, float(max(0, txn.payee_account_age_days))),
        "payee_age_young": 1.0 if txn.payee_account_age_days < YOUNG_PAYEE_DAYS else 0.0,
        "new_payee": 1.0 if ctx.payee_first_seen_ts is None else 0.0,
        "velocity_count_1h": float(c1),
        "velocity_amount_1h": math.log1p(a1),
        "velocity_count_24h": float(c24),
        "velocity_amount_24h": math.log1p(a24),
        "rail": RAIL_CODE[txn.rail],
        "hour_ist": float(hour),
        "night": 1.0 if hour < 5 else 0.0,
        "active_call_risk": risk,
        "device_novel": 1.0 if (n > 0 and not ctx.device_seen) else 0.0,
        "payee_in_antibody": 0.0,  # Task 11 fills this from the antibody cache
        "future_dated": 1.0 if ts - ctx.now > FUTURE_TOLERANCE else 0.0,
        "payee_recently_new": 1.0 if recently_new else 0.0,
        "payee_repeat_large_1h": 1.0 if repeat_large else 0.0,
        "payee_established": 1.0 if established else 0.0,
        "payee_max_prior_log": math.log(max_prior) if max_prior > 0 else 0.0,
    }
    return {k: _finite(feats[k]) for k in FEATURE_NAMES}
