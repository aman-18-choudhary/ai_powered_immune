"""Transparent policy overlays applied on every rail after the model (or rules) score.

Each overlay is a *floor*: ``final = max(model_score, floor)``. When an overlay lifts the score the
verdict carries an extra reason with the overlay's own code (``YOUNG_PAYEE_LARGE_AMOUNT_FLOOR``,
``CALL_RISK_AMOUNT_GUARD``, ``FUTURE_DATED_TIMESTAMP``); the model-derived reason weights are never
modified. A score lifted by an overlay is a *policy value*, not a calibrated probability.

Absolute-rupee thresholds are scaled per rail (``RAIL_AMOUNT_SCALE``: UPI 1, IMPS 1, NEFT 5; see
derivation below) because NEFT's benign amounts are naturally ~5x larger. The thresholds quoted
in this docstring are the UPI/IMPS values.

Why they exist: the simulator's scams are almost all UPI to young accounts, so the booster has
blind spots (call risk with an old/known payee, large IMPS/NEFT transfers). The overlays
are rail-agnostic in structure and read only the feature dict (the absolute amount comes from
``amount_log``).

* YOUNG_PAYEE_LARGE_AMOUNT_FLOOR: payee account < 30 days old AND payee not *established* for
  this payer (established = >= 3 prior payments AND first paid >= 7 days ago; so a small "test"
  payment does not disable the rule) AND a large amount (z >= 3 and >= Rs 5,000, or, for a
  payer with fewer than 3 prior transfers, >= Rs 25,000) -> at least step_up;
  if also >= Rs 50,000 or payee < 7 days old -> hold_verify.
* CALL_RISK_AMOUNT_GUARD: active call risk >= 0.7 AND an amount anomaly (z >= 3 and >= Rs 10,000,
  or the short-history rule at Rs 25,000) -> at least step_up for ANY payee; hold_verify when
  the payee is new or young to this payer, was first paid within 24 h ("recently new"), or this
  payer already sent >= Rs 10,000 to it within the last 60 min (a repeat of a large transfer).
* PAYEE_AMOUNT_ESCALATION: payee < 30 days old AND amount >= 10x the largest amount this payer
  ever sent to it AND >= Rs 25,000 -> at least step_up; hold_verify from Rs 50,000 when also
  z >= 3 and the payee is not established (the z/established gate keeps the simulator's
  heavy-tailed repeat payments to young merchants from tripling benign holds).
* NEW_PAYEE_EXTREME_AMOUNT: payee not established AND z >= 10 AND >= Rs 50,000, any payee age ->
  step_up only.
* FUTURE_DATED_TIMESTAMP: timestamp more than 5 minutes ahead of ``Context.now`` -> step_up.
"""

import math
from dataclasses import dataclass

from .features import MIN_HISTORY_FOR_Z
from .thresholds import HOLD_AT, STEP_UP_AT

# Per-rail scale for every absolute-rupee threshold below. Derivation (simulator benign traffic,
# 4 held-out seeds, benign txns only): median amount UPI Rs 452, IMPS Rs 4,288, NEFT Rs 24,157
# (p90 2.7k / 19.5k / 135k). The Rs 5k-50k thresholds were set for UPI/IMPS; NEFT's median is
# 24157 / 4288 = 5.6x IMPS's, so NEFT thresholds are scaled by 5 (rounded down so that an
# extreme Rs 3L NEFT transfer, 300k >= 50k x 5 = 250k, is still caught). z-score and payee-age
# conditions are NOT scaled, and the booster never sees the rail: only these thresholds do.
# Re-derive when real per-rail amount distributions are available.
RAIL_AMOUNT_SCALE: dict[str, float] = {"UPI": 1.0, "IMPS": 1.0, "NEFT": 5.0}
_RAIL_BY_CODE = {0.0: "UPI", 1.0: "IMPS", 2.0: "NEFT"}


def rail_scale(rail_code: float) -> float:
    return RAIL_AMOUNT_SCALE[_RAIL_BY_CODE.get(rail_code, "UPI")]


# Model damper for rails with scale > 1. The booster is rail-blind and its amount features (z,
# amount vs typical) are measured against the payer's UPI-dominated history, so every large but
# ordinary NEFT payment to a young payee looks anomalous to it (model-only hold rate on benign NEFT
# was 0.25% vs 0.003% on UPI). On such rails a model score at or above step-up is capped just
# below step-up unless the amount is anomalous for the payer even after allowing for the rail
# (z >= RAIL_MODEL_MIN_Z). The overlays below are unaffected and still floor the score, so
# anomalous young-payee transfers hold on every rail.
RAIL_MODEL_MIN_Z = 6.0
RAIL_DAMPED_CAP = 0.49
DAMPER_CODE = "RAIL_TYPICAL_AMOUNT_DAMPER"


def damp_model_score(f: dict[str, float], score: float) -> float:
    """Capped model score on rails whose typical amounts are larger; unchanged elsewhere."""
    if (
        rail_scale(f["rail"]) > 1.0
        and f["amount_zscore"] < RAIL_MODEL_MIN_Z
        and score > RAIL_DAMPED_CAP
    ):
        return RAIL_DAMPED_CAP
    return score


HOLD_FLOOR = 0.85
STEP_FLOOR = STEP_UP_AT
assert STEP_FLOOR < HOLD_AT < HOLD_FLOOR
Z_MIN = 3.0
YOUNG_ABS_MIN_INR = 5_000.0
CALL_ABS_MIN_INR = 10_000.0
SHORT_HISTORY_ABS_MIN_INR = 25_000.0
HOLD_ABS_INR = 50_000.0
VERY_YOUNG_DAYS = 7
ESCALATION_RATIO = 10.0
EXTREME_Z = 10.0
CALL_RISK_MIN = 0.7
OVERLAY_CODES = frozenset(
    {
        "YOUNG_PAYEE_LARGE_AMOUNT_FLOOR",
        "PAYEE_AMOUNT_ESCALATION",
        "NEW_PAYEE_EXTREME_AMOUNT",
        "CALL_RISK_AMOUNT_GUARD",
        "FUTURE_DATED_TIMESTAMP",
        DAMPER_CODE,
    }
)


@dataclass(frozen=True)
class Overlay:
    code: str
    floor: float
    detail: str


def _z_min(f: dict[str, float]) -> float:
    """z threshold of the overlays: 3 on UPI/IMPS; RAIL_MODEL_MIN_Z on rails with larger typical
    amounts, where the payer's (UPI-dominated) history inflates z for ordinary payments."""
    return RAIL_MODEL_MIN_Z if rail_scale(f["rail"]) > 1.0 else Z_MIN


def _large(f: dict[str, float], z_min_abs: float) -> bool:
    k = rail_scale(f["rail"])
    z_min_abs *= k
    amount = math.exp(min(f["amount_log"], 40.0))
    short = f["history_len"] < math.log1p(MIN_HISTORY_FOR_Z) - 1e-9
    if short:
        return amount >= SHORT_HISTORY_ABS_MIN_INR * k
    return f["amount_zscore"] >= _z_min(f) and amount >= z_min_abs


def overlays(f: dict[str, float]) -> list[Overlay]:
    out: list[Overlay] = []
    amount = math.exp(min(f["amount_log"], 40.0))
    k = rail_scale(f["rail"])
    young = f["payee_age_young"] >= 1.0
    new = f["new_payee"] >= 1.0
    if f["future_dated"] >= 1.0:
        out.append(
            Overlay(
                "FUTURE_DATED_TIMESTAMP", STEP_FLOOR,
                "Transaction timestamp is in the future; policy raises it to at least step-up",
            )
        )  # fmt: skip
    established = f["payee_established"] >= 1.0
    if young and not established and _large(f, YOUNG_ABS_MIN_INR):
        age = int(f["payee_age_days"])
        hold = amount >= HOLD_ABS_INR * k or age < VERY_YOUNG_DAYS
        out.append(
            Overlay(
                "YOUNG_PAYEE_LARGE_AMOUNT_FLOOR", HOLD_FLOOR if hold else STEP_FLOOR,
                f"Large transfer (Rs {amount:,.0f}) to a first-time payee whose account is only "
                f"{age} days old; policy floor to {'hold_verify' if hold else 'step-up'}",
            )
        )  # fmt: skip
    if young and f["payee_max_prior_log"] > 0 and amount >= SHORT_HISTORY_ABS_MIN_INR * k:
        if f["amount_log"] - f["payee_max_prior_log"] >= math.log(ESCALATION_RATIO):
            hold = (
                amount >= HOLD_ABS_INR * k and f["amount_zscore"] >= _z_min(f) and not established
            )
            out.append(
                Overlay(
                    "PAYEE_AMOUNT_ESCALATION", HOLD_FLOOR if hold else STEP_FLOOR,
                    f"Amount (Rs {amount:,.0f}) is at least {ESCALATION_RATIO:.0f}x anything this "
                    f"payer sent before to this payee, whose account is {int(f['payee_age_days'])} "
                    f"days old; policy floor to {'hold_verify' if hold else 'step-up'}",
                )
            )  # fmt: skip
    if not established and f["amount_zscore"] >= EXTREME_Z and amount >= HOLD_ABS_INR * k:
        out.append(
            Overlay(
                "NEW_PAYEE_EXTREME_AMOUNT", STEP_FLOOR,
                f"Amount (Rs {amount:,.0f}) is extreme for this payer (z-score "
                f"{f['amount_zscore']:.0f}) and the payee is not an established one; policy "
                f"floor to step-up",
            )
        )  # fmt: skip
    if f["active_call_risk"] >= CALL_RISK_MIN and _large(f, CALL_ABS_MIN_INR):
        strong = new or young or f["payee_recently_new"] >= 1.0 or f["payee_repeat_large_1h"] >= 1.0
        out.append(
            Overlay(
                "CALL_RISK_AMOUNT_GUARD", HOLD_FLOOR if strong else STEP_FLOOR,
                f"Scam-call risk {f['active_call_risk']:.2f} active and amount "
                f"(Rs {amount:,.0f}) is far above this payer's norm"
                + (" to a new/young/recently-added payee" if strong else " to a known payee")
                + f"; policy floor to {'hold_verify' if strong else 'step-up'}",
            )
        )  # fmt: skip
    return out
