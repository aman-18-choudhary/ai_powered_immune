"""Score -> decision, and the TxnDecision envelope."""

import logging
import math
from datetime import datetime
from typing import Literal

from scam_contracts.models import TxnDecision

from .model import Scorer

log = logging.getLogger("txn_guard")

STEP_UP_AT = 0.5
HOLD_AT = 0.8

Decision = Literal["allow", "step_up", "hold_verify"]


def decide(score: float) -> Decision:
    """>= HOLD_AT hold_verify; >= STEP_UP_AT step_up; else allow. A NaN score (should not
    happen; the Scorer sanitises) yields step_up: friction rather than a silent allow."""
    if math.isnan(score):
        return "step_up"
    if score >= HOLD_AT:
        return "hold_verify"
    if score >= STEP_UP_AT:
        return "step_up"
    return "allow"


def make_decision(
    txn_id: str, features: dict[str, float], scorer: Scorer, ts: datetime
) -> TxnDecision:
    score, reasons, version = scorer.score_with_version(features)
    decision = decide(score)
    log.info("txn_id=%s decision=%s score=%.3f", txn_id, decision, score)  # no other fields
    return TxnDecision(
        txn_id=txn_id,
        decision=decision,
        score=score,
        reasons=reasons,
        model_version=version,
        ts=ts,
    )
