"""Classifier layer (``clf-v1``) blended with the rules.

What the score means
--------------------
``score = clip(max(rules_score, calibrated_classifier_probability), 0, 1)``.

* ``rules_score`` is a noisy-OR of hand-set cue weights (see ``call_guard.rules``); it is a
  heuristic confidence, not a fitted probability.
* The classifier probability is a TF-IDF + logistic regression output passed through an
  isotonic calibrator fitted on a held-out split (simulator transcripts + authored scam/benign
  text, prior roughly 1 scam : 4 benign chunks). ``train.py`` prints and stores a reliability
  table.
* Therefore the number is *calibrated against the simulator + authored data only*. It is a
  ranking-quality risk score to be thresholded at ``CALL_RISK_THRESHOLD`` (0.7), NOT a
  real-world probability that a given call is a scam.

The artifact (joblib dict: ``model_version``, ``sklearn_version``, ``pipeline``, ``calibrator``)
is produced by ``python -m call_guard.train``. It is loaded only if its SHA-256 matches the pin
in ``artifact_pin.py``, the sklearn version matches, and a smoke test passes; otherwise the
service runs rules-only (``rules-v1``) and says so loudly (WARNING log, /readyz, /metrics).
"""

import hashlib
import io
import logging
from pathlib import Path
from typing import Any

from scam_contracts.models import CallEvent, Reason

from . import artifact_pin
from .rules import RULES_VERSION, analyse

log = logging.getLogger(__name__)

CLF_VERSION = "clf-v1"
ARTIFACT_PATH = Path(__file__).parent / "artifacts" / "clf-v1.joblib"
MIN_CLF_WORDS = 4  # shorter utterances ("Okay.", "Yes, go on.") carry no signal
CLF_REASON_MIN = 0.5  # classifier only adds a reason when it is the confident one
CLF_CLIP = (0.0, 1.0)
# If the rules recognised an advisory clause (awareness notice, TV remark, helpline text) and
# found no strong demand cues, the classifier probability is capped: a script-like vocabulary
# alone must not alert on text that is *about* scams.
ADVISORY_RULE_FLOOR = 0.5
ADVISORY_CLF_CAP = 0.45
# The classifier is a booster, never a standalone alarm. How far it may lift a chunk depends on
# how many *independent rule cue classes* (URGENCY alone does not count) corroborate it:
#   0 classes -> at most CLF_CAP_NO_RULES (0.6, "suspicious", below the 0.7 alert threshold)
#   1 class   -> at most CLF_CAP_ONE_CLASS (0.69, still below the alert threshold)
#   2+ classes -> unrestricted
CLF_CAP_NO_RULES = 0.6
CLF_CAP_ONE_CLASS = 0.69
WEAK_CODES = frozenset({"URGENCY", "NO_RISK_INDICATORS", "SCRIPT_CLASSIFIER_MATCH"})

SMOKE_SCAM = (
    "This is the CBI. You are under digital arrest, transfer all your money to the RBI safe "
    "account now and do not tell anyone."
)
SMOKE_BENIGN = "Hello, your electricity bill for this month is ready, you can pay it in the app."


class Classifier:
    def __init__(self, pipeline: Any, calibrator: Any, version: str) -> None:
        self._pipeline = pipeline
        self._calibrator = calibrator
        self.version = version

    def predict_proba(self, text: str) -> float:
        if len(text.split()) < MIN_CLF_WORDS:
            return 0.0
        raw = float(self._pipeline.predict_proba([text])[0][1])
        p = float(self._calibrator.predict([raw])[0]) if self._calibrator is not None else raw
        return min(CLF_CLIP[1], max(CLF_CLIP[0], p))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_classifier(
    path: Path | None = None, expected_sha256: str | None = None
) -> Classifier | None:
    """Load and verify the artifact; None (rules-only fallback) on any problem."""
    path = path or ARTIFACT_PATH
    pin = artifact_pin.ARTIFACT_SHA256 if expected_sha256 is None else expected_sha256
    try:
        import joblib
        import sklearn

        raw = path.read_bytes()
        if not pin or hashlib.sha256(raw).hexdigest() != pin:
            log.warning("classifier artifact failed integrity check; using rules-only")
            return None
        blob = joblib.load(io.BytesIO(raw))
        if blob.get("sklearn_version") != sklearn.__version__:
            log.warning("classifier artifact sklearn version mismatch; using rules-only")
            return None
        clf = Classifier(blob["pipeline"], blob.get("calibrator"), str(blob["model_version"]))
        if not (clf.predict_proba(SMOKE_SCAM) > 0.7 and clf.predict_proba(SMOKE_BENIGN) < 0.2):
            log.warning("classifier artifact failed smoke test; using rules-only")
            return None
        return clf
    except Exception:
        log.warning("classifier artifact unavailable at %s; using rules-only", path.name)
        return None


class Scorer:
    """Stateless chunk/message scorer: max(rules, calibrated classifier), clipped to [0, 1]."""

    def __init__(self, classifier: Classifier | None) -> None:
        self._clf = classifier
        self.clf_errors = 0
        if classifier is None:
            log.warning(
                "call-guard running in FALLBACK mode: rules-only scoring (%s)", RULES_VERSION
            )

    @property
    def fallback_mode(self) -> bool:
        return self._clf is None

    @property
    def model_version(self) -> str:
        return self._clf.version if self._clf else RULES_VERSION

    def score_text(self, text: str) -> tuple[float, list[Reason], str]:
        rule_score, reasons, advisory = analyse(text)
        score = rule_score
        version = RULES_VERSION
        if self._clf is not None:
            try:
                p = self._clf.predict_proba(text)
            except Exception:
                self.clf_errors += 1
                log.warning("classifier inference failed; rules-only for this message")
            else:
                version = self._clf.version
                if advisory and rule_score < ADVISORY_RULE_FLOOR:
                    p = min(p, ADVISORY_CLF_CAP)  # awareness / media text: classifier cannot alert
                classes = sum(r.code not in WEAK_CODES for r in reasons)
                p = min(
                    p,
                    CLF_CAP_NO_RULES
                    if classes == 0
                    else CLF_CAP_ONE_CLASS
                    if classes == 1
                    else 1.0,
                )
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
        return score, reasons or [no_risk_reason()], version

    def score_chunk(self, event: CallEvent) -> tuple[float, list[Reason], str]:
        return self.score_text(event.transcript_chunk)


def no_risk_reason() -> Reason:
    return Reason(code="NO_RISK_INDICATORS", weight=0.0, detail="no scam cues detected")
