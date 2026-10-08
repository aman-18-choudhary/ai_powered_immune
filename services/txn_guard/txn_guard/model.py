"""Gradient-boosted transaction risk model (``gbm-v1``) with rules-only fallback.

What the score means
--------------------
``score`` is the output of ``HistGradientBoostingClassifier`` (monotone in the cues that must
only ever add risk) passed through an isotonic calibrator fitted on held-out simulator seeds. It
is therefore a calibrated probability *within the simulator's world*: P(transaction is part of
a labelled scam | features) at the simulator's scam prevalence (about 1 scam transaction per 100
benign in the training streams, far higher than any real base rate), with an imperfect
call-risk signal (see ``simdata.py``). It is NOT a real-world fraud probability and must not be
read as one; it is a ranking-quality risk score thresholded by ``decision.decide``.

**Policy overlays.** ``policy.py`` floors the score on every rail (young payee + large amount,
call risk + amount anomaly, future-dated timestamp). When an overlay lifts the score, the returned
score is a *policy-lifted value, not a calibrated probability*; the verdict then carries an extra
reason with the overlay's own code (``policy.OVERLAY_CODES``; see ``overlay_applied``) and the
model-derived reason weights are left untouched.

**Prior shift.** The calibration holds at the simulator's scam prevalence
(``CALIBRATED_PREVALENCE``, about 1%); on real traffic with a different base rate the score must
be recalibrated before it is read as a probability.

Reasons are occlusion attributions: for each fired cue, ``weight`` is how far the score drops
when that cue's features are reset to neutral values (floored at 0). Details never contain ids.

The artifact (joblib dict: ``model_version``, ``sklearn_version``, ``feature_names``, ``model``,
``calibrator`` + training metadata) is produced by ``python -m txn_guard.train``. It is loaded
only if its SHA-256 matches ``artifact_pin.py``, the scikit-learn version matches, the feature
names match ``features.FEATURE_NAMES`` and a smoke test passes; otherwise the Scorer runs the
transparent ``rules-fallback-v1`` scorer and says so loudly (WARNING log, ``fallback_mode``).
A failure inside ``predict_proba`` at runtime also falls back, for that call only.
"""

import hashlib
import io
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scam_contracts.models import Reason

from . import artifact_pin
from .features import FEATURE_NAMES, MODEL_FEATURES
from .policy import DAMPER_CODE, OVERLAY_CODES, damp_model_score, overlays
from .reasons import BASELINES, NO_RISK, TOP_K, triggers
from .rules import RULES_VERSION, rules_score
from .thresholds import STEP_UP_AT

log = logging.getLogger("txn_guard")

MODEL_VERSION = "gbm-v1"
ARTIFACT_PATH = Path(__file__).parent / "artifacts" / "gbm-v1.joblib"
SMOKE_SCAM_MIN = 0.5
SMOKE_BENIGN_MAX = 0.2
CALIBRATED_PREVALENCE = 0.01  # scam-transaction share of the simulator streams used to calibrate
MIN_REASON_WEIGHT = 0.01  # on allow verdicts, reasons lighter than this are dropped
SMOKE_RAIL_SPREAD_MAX = 0.05  # a rail blind spot (same txn, different rail) fails the load

_BASE: dict[str, float] = dict.fromkeys(FEATURE_NAMES, 0.0) | {
    "payee_age_days": 900.0, "history_len": 3.5, "rail": 0.0,
    "hour_ist": 14.0, "velocity_count_24h": 1.0, "velocity_amount_24h": 6.0,
}  # fmt: skip
SMOKE_BENIGN = dict(_BASE)
SMOKE_SCAM = _BASE | {
    "amount_zscore": 4.0, "amount_vs_typical_log": 4.5, "payee_age_days": 5.0,
    "payee_age_young": 1.0, "new_payee": 1.0, "active_call_risk": 0.9, "velocity_count_1h": 2.0,
    "velocity_amount_1h": 10.5,
}  # fmt: skip


@dataclass
class GbmModel:
    model: Any
    calibrator: Any
    version: str

    def proba(self, rows: np.ndarray) -> np.ndarray:
        raw = self.model.predict_proba(rows)[:, 1]
        cal = self.calibrator.predict(raw) if self.calibrator is not None else raw
        return np.clip(np.asarray(cal, dtype=float), 0.0, 1.0)


def _vector(f: dict[str, float]) -> list[float]:
    return [_clean(f.get(k, 0.0)) for k in MODEL_FEATURES]


def _clean(x: float) -> float:
    return float(x) if math.isfinite(x) else 0.0


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_model(path: Path | None = None, expected_sha256: str | None = None) -> GbmModel | None:
    """Load and verify the artifact; None (rules fallback) on any problem."""
    path = path or ARTIFACT_PATH
    pin = artifact_pin.ARTIFACT_SHA256 if expected_sha256 is None else expected_sha256
    try:
        import joblib
        import sklearn

        raw = path.read_bytes()
        if not pin or hashlib.sha256(raw).hexdigest() != pin:
            log.warning("txn model artifact failed integrity check; using %s", RULES_VERSION)
            return None
        blob = joblib.load(io.BytesIO(raw))
        if blob.get("sklearn_version") != sklearn.__version__:
            log.warning("txn model artifact sklearn version mismatch; using %s", RULES_VERSION)
            return None
        if tuple(blob.get("feature_names", ())) != MODEL_FEATURES:
            log.warning("txn model artifact feature mismatch; using %s", RULES_VERSION)
            return None
        m = GbmModel(blob["model"], blob.get("calibrator"), str(blob["model_version"]))
        rails = [_vector(SMOKE_SCAM | {"rail": r}) for r in (0.0, 1.0, 2.0)]
        p = m.proba(np.array([*rails, _vector(SMOKE_BENIGN)]))
        if not (
            p[:3].min() >= SMOKE_SCAM_MIN
            and p[:3].max() - p[:3].min() <= SMOKE_RAIL_SPREAD_MAX
            and p[3] <= SMOKE_BENIGN_MAX
        ):
            log.warning("txn model artifact failed smoke test; using %s", RULES_VERSION)
            return None
        return m
    except Exception:
        log.warning("txn model artifact unavailable at %s; using %s", path.name, RULES_VERSION)
        return None


class Scorer:
    """``score(features) -> (score in [0,1], reasons)``; ``score_with_version`` also returns the
    model version that actually produced the verdict (``gbm-v1`` or ``rules-fallback-v1``)."""

    def __init__(self, path: Path | None = None, expected_sha256: str | None = None) -> None:
        self._model = load_model(path, expected_sha256)
        self.model_errors = 0
        if self._model is None:
            log.warning("txn-guard running in FALLBACK mode: %s", RULES_VERSION)

    @property
    def fallback_mode(self) -> bool:
        return self._model is None

    @property
    def model_version(self) -> str:
        return self._model.version if self._model else RULES_VERSION

    def score(self, features: dict[str, float]) -> tuple[float, list[Reason]]:
        s, reasons, _ = self.score_with_version(features)
        return s, reasons

    def score_with_version(
        self, features: dict[str, float], with_reasons: bool = True
    ) -> tuple[float, list[Reason], str]:
        """``with_reasons=False`` skips the occlusion rows (one batched predict_proba either way);
        the score and therefore the decision are identical."""
        f = {k: _clean(features.get(k, 0.0)) for k in FEATURE_NAMES}
        out: tuple[float, list[Reason]] | None = None
        version = RULES_VERSION
        if self._model is not None:
            try:
                out = self._model_score(f, with_reasons)
                version = self._model.version
            except Exception:
                self.model_errors += 1
                log.warning(
                    "txn model inference failed; using %s for this transaction", RULES_VERSION
                )
        return self._finish(f, out, version)

    def score_many(self, rows: list[dict[str, float]]) -> list[tuple[float, list[Reason], str]]:
        """Batch scoring without occlusion reasons (offline evaluation). Same scores, overlays
        and fallbacks as ``score_with_version(..., with_reasons=False)`` per row, but one
        model call for the whole batch."""
        fs = [{k: _clean(r.get(k, 0.0)) for k in FEATURE_NAMES} for r in rows]
        if self._model is not None and fs:
            try:
                p = self._model.proba(np.array([_vector(f) for f in fs]))
                version = self._model.version
                return [
                    self._finish(f, (float(x), []), version) for f, x in zip(fs, p, strict=True)
                ]
            except Exception:
                log.warning("txn batch inference failed; scoring rows individually")
        return [self.score_with_version(r, with_reasons=False) for r in rows]

    def _finish(
        self, f: dict[str, float], out: tuple[float, list[Reason]] | None, version: str
    ) -> tuple[float, list[Reason], str]:
        if out is None:
            out = rules_score(f)
        score, reasons = out
        if version != RULES_VERSION:  # the damper corrects the booster's rail-blind amount features
            damped = damp_model_score(f, score)
            if damped < score:
                reasons = [
                    *reasons,
                    Reason(
                        code=DAMPER_CODE, weight=round(score - damped, 4),
                        detail="Amount is large only relative to this payer's history; typical "
                        "for this payment rail, so the model score was capped below step-up",
                    ),
                ]  # fmt: skip
                score = damped
        lifted = [o for o in overlays(f) if o.floor > score]
        if lifted:
            new_score = max(o.floor for o in lifted)
            reasons = [
                *reasons,
                *(Reason(code=o.code, weight=round(o.floor - score, 4), detail=o.detail) for o in lifted),
            ]  # fmt: skip
            reasons = [r for r in reasons if r.code != NO_RISK]
            score = new_score
        score = min(1.0, max(0.0, _clean(score)))
        if score < STEP_UP_AT:
            kept = [r for r in reasons if r.weight >= MIN_REASON_WEIGHT]
            reasons = kept or [Reason(code=NO_RISK, weight=0.0, detail="No risk indicators fired")]
        return score, reasons, version

    def _model_score(
        self, f: dict[str, float], with_reasons: bool = True
    ) -> tuple[float, list[Reason]]:
        assert self._model is not None
        if not with_reasons:
            return float(self._model.proba(np.array([_vector(f)]))[0]), []
        trig = triggers(f)
        occl = [t for t in trig if t.code in BASELINES]
        rows = [_vector(f)] + [_vector(f | BASELINES[t.code]) for t in occl]
        p = self._model.proba(np.array(rows))
        drop = {t.code: max(0.0, float(p[0] - p[i + 1])) for i, t in enumerate(occl)}
        ranked = sorted(
            trig, key=lambda t: (drop.get(t.code, t.salience), t.salience), reverse=True
        )
        reasons = [
            Reason(code=t.code, weight=round(drop.get(t.code, t.salience), 4), detail=t.detail)
            for t in ranked[:TOP_K]
        ]
        return float(p[0]), reasons or [
            Reason(code=NO_RISK, weight=0.0, detail="No risk indicators fired")
        ]


def overlay_applied(reasons: list[Reason]) -> bool:
    """True when a policy overlay lifted the score (the score is then not a calibrated value)."""
    return any(r.code in OVERLAY_CODES for r in reasons)
