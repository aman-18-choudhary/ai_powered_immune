"""Reason codes derived from the feature vector. Details are human-readable, built from numbers
only (never ids)."""

import math
from dataclasses import dataclass

from .features import CALL_RISK_THRESHOLD

TOP_K = 4
NO_RISK = "NO_RISK_INDICATORS"

# Neutral feature values used to ask "how much did this factor contribute?" (occlusion).
BASELINES: dict[str, dict[str, float]] = {
    "NEW_PAYEE": {"new_payee": 0.0},
    "YOUNG_PAYEE_ACCOUNT": {"payee_age_young": 0.0, "payee_age_days": 365.0},
    "ACTIVE_SCAM_CALL": {"active_call_risk": 0.0},
    "AMOUNT_ANOMALY": {"amount_zscore": 0.0, "amount_vs_typical_log": 0.0},
    "VELOCITY_SPIKE": {
        "velocity_count_1h": 0.0,
        "velocity_amount_1h": 0.0,
        "velocity_count_24h": 0.0,
        "velocity_amount_24h": 0.0,
    },  # fmt: skip
    "NIGHT_TRANSFER": {"night": 0.0, "hour_ist": 12.0},
    "NEW_DEVICE": {"device_novel": 0.0},
    "PAYEE_IN_ANTIBODY": {"payee_in_antibody": 0.0},
}


@dataclass(frozen=True)
class Trigger:
    code: str
    salience: float  # 0..1 tie-breaker / rules weight
    detail: str


def triggers(f: dict[str, float]) -> list[Trigger]:
    out: list[Trigger] = []
    if f["payee_in_antibody"] >= 1.0:
        out.append(Trigger("PAYEE_IN_ANTIBODY", 0.9, "Payee matches a confirmed mule antibody"))
    if f["active_call_risk"] >= CALL_RISK_THRESHOLD:
        out.append(
            Trigger(
                "ACTIVE_SCAM_CALL", 0.5 * f["active_call_risk"],
                f"Scam-call risk {f['active_call_risk']:.2f} for this payer in the last 15 minutes",
            )
        )  # fmt: skip
    if f["payee_age_young"] >= 1.0:
        d = int(f["payee_age_days"])
        out.append(
            Trigger(
                "YOUNG_PAYEE_ACCOUNT", 0.4 if d < 7 else 0.3,
                f"Payee account is only {d} day{'s' if d != 1 else ''} old",
            )
        )  # fmt: skip
    if f["amount_zscore"] >= 3.0 or f["amount_vs_typical_log"] >= math.log(5):
        ratio = math.exp(min(f["amount_vs_typical_log"], 20.0))
        out.append(
            Trigger(
                "AMOUNT_ANOMALY", 0.3,
                f"Amount is about {ratio:.0f}x this payer's typical transfer "
                f"(z-score {f['amount_zscore']:.1f})",
            )
        )  # fmt: skip
    if f["velocity_count_1h"] >= 3:
        out.append(
            Trigger(
                "VELOCITY_SPIKE", 0.25,
                f"{int(f['velocity_count_1h'])} earlier transfers in the last hour",
            )
        )  # fmt: skip
    if f["new_payee"] >= 1.0 and f["history_len"] > 0:
        out.append(Trigger("NEW_PAYEE", 0.12, "First payment from this payer to this payee"))
    if f["device_novel"] >= 1.0:
        out.append(Trigger("NEW_DEVICE", 0.15, "Payer used a device not seen before"))
    if f["night"] >= 1.0:
        out.append(Trigger("NIGHT_TRANSFER", 0.05, f"Transfer at {int(f['hour_ist'])}:00 IST"))
    return out
