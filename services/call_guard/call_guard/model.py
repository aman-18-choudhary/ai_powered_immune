"""Classifier layer (``clf-v1``) blended with the rules by max.

The artifact (joblib dict: ``model_version`` + sklearn ``pipeline``) is produced by
``python -m call_guard.train``. If it is missing or unreadable the service falls back to
rules-only scoring and reports ``rules-v1``.
"""

import logging
from pathlib import Path
from typing import Any

from scam_contracts.models import CallEvent, Reason

from .rules import RULES_VERSION, score_text

log = logging.getLogger(__name__)

CLF_VERSION = "clf-v1"
ARTIFACT_PATH = Path(__file__).parent / "artifacts" / "clf-v1.joblib"
MIN_CLF_WORDS = 4  # shorter utterances ("Okay.", "Yes, go on.") carry no signal
CLF_REASON_MIN = 0.5  # classifier only adds a reason when it is the confident one


class Classifier:
    def __init__(self, pipeline: Any, version: str) -> None:
        self._pipeline = pipeline
        self.version = version

    def predict_proba(self, text: str) -> float:
        if len(text.split()) < MIN_CLF_WORDS:
            return 0.0
        return float(self._pipeline.predict_proba([text])[0][1])


def load_classifier(path: Path | None = None) -> Classifier | None:
    import joblib

    path = path or ARTIFACT_PATH
    try:
        blob = joblib.load(path)
        return Classifier(blob["pipeline"], str(blob["model_version"]))
    except Exception:
        log.warning("classifier artifact unavailable at %s; using rules-only", path.name)
        return None


class Scorer:
    """Stateless chunk/message scorer: max(rules, classifier), clipped to [0, 1]."""

    def __init__(self, classifier: Classifier | None) -> None:
        self._clf = classifier

    @property
    def model_version(self) -> str:
        return self._clf.version if self._clf else RULES_VERSION

    def score_text(self, text: str) -> tuple[float, list[Reason], str]:
        rule_score, reasons = score_text(text)
        score = rule_score
        if self._clf is not None:
            p = self._clf.predict_proba(text)
            if p >= CLF_REASON_MIN and p > rule_score:
                reasons = [
                    *reasons,
                    Reason(
                        code="SCRIPT_CLASSIFIER_MATCH",
                        weight=round(p, 4),
                        detail="text matches known digital-arrest call scripts",
                    ),
                ]
            score = max(rule_score, p)
        score = round(min(1.0, max(0.0, score)), 4)
        return score, reasons or [no_risk_reason()], self.model_version

    def score_chunk(self, event: CallEvent) -> tuple[float, list[Reason], str]:
        return self.score_text(event.transcript_chunk)


def no_risk_reason() -> Reason:
    return Reason(code="NO_RISK_INDICATORS", weight=0.0, detail="no scam cues detected")
