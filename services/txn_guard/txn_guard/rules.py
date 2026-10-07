"""Transparent rules scorer ``rules-fallback-v1`` used when the model artifact is unavailable.

Noisy-OR of the per-reason weights in ``reasons.triggers`` (each cue is an independent chance of
fraud), plus one explicit interaction: an active scam-call risk >= 0.7 *together with* a young
payee account, an anomalous amount or an antibody match is floored at ``CORROBORATED_FLOOR``
(hold_verify). Call risk alone, or any mix of the weaker cues, stays below HOLD_AT. These are
hand-set heuristic confidences, not fitted probabilities.
"""

from scam_contracts.models import Reason

from .reasons import NO_RISK, TOP_K, triggers

RULES_VERSION = "rules-fallback-v1"
CORROBORATED_FLOOR = 0.85
_CORROBORATORS = {"YOUNG_PAYEE_ACCOUNT", "AMOUNT_ANOMALY", "PAYEE_IN_ANTIBODY"}


def rules_score(f: dict[str, float]) -> tuple[float, list[Reason]]:
    trig = triggers(f)
    keep = 1.0
    for t in trig:
        keep *= 1.0 - t.salience
    score = 1.0 - keep
    codes = {t.code for t in trig}
    if "ACTIVE_SCAM_CALL" in codes and codes & _CORROBORATORS:
        score = max(score, CORROBORATED_FLOOR)
    score = min(1.0, max(0.0, score))
    reasons = [
        Reason(code=t.code, weight=round(t.salience, 4), detail=t.detail)
        for t in sorted(trig, key=lambda t: t.salience, reverse=True)[:TOP_K]
    ]
    return score, reasons or [Reason(code=NO_RISK, weight=0.0, detail="No risk indicators fired")]
