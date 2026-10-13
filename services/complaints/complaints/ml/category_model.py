"""Complaint category classifier (``category-v1``): TF-IDF (word 1-2 + char 3-5) + multinomial
logistic regression over the complaint TEXT ONLY, with an abstention rule.

What the output means
---------------------
* ``confidence`` is the model's maximum class probability. It is an UNCALIBRATED model score: no
  calibration was fitted or validated, so it must not be read as the chance of being right.
* Below ``min_confidence`` the result is ``UNDETERMINED``; a label is never forced.
* Structured fields (phone, URL, UPI id, amount, location...) are not features: a missing field
  cannot change the prediction. Rule-based extractors use them elsewhere in the pipeline.
* The model was trained only on SYNTHETIC template data (see ``data/README.md`` and
  ``MODEL_CARD.md``); it has not been validated on real complaints.

The artifact is loaded lazily and only if its SHA-256 matches ``artifact_pin.ARTIFACT_SHA256``, the
scikit-learn version matches the one it was trained with and a smoke test passes; otherwise the
caller gets ``None`` and a reason, and the pipeline reports ``category_classifier: unavailable``.
Nothing heavy is imported at module import time.
"""

import hashlib
import io
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("complaints.ml")

MODEL_VERSION = "category-v1"
ARTIFACT_PATH = Path(__file__).parent / "artifacts" / "category-v1.joblib"
UNDETERMINED = "UNDETERMINED"
CATEGORIES = (
    "DIGITAL_ARREST", "UPI_FRAUD", "PHISHING", "OTP_SCAM", "BANK_FRAUD", "INVESTMENT_SCAM",
    "JOB_SCAM", "SOCIAL_MEDIA_FRAUD", "OTHER",
)  # fmt: skip
MAX_CHARS = 5000
SMOKE_PHISHING = (
    "I got an SMS saying my bank KYC will expire today and to update it at "
    "https://bank-kyc-update.example.top ; the page asked for my card number and password."
)
SMOKE_BENIGN = (
    "My parcel has not arrived in 6 days and tracking says delivered, I want to complain "
    "about the delay."
)
_DIGITS = re.compile(r"\d")


def preprocess(text: str) -> str:
    """Module-level so the pickled vectorisers can reference it. Digits are folded to ``0``."""
    return _DIGITS.sub("0", text.casefold())


@dataclass(frozen=True)
class CategoryPrediction:
    label: str  # a category or UNDETERMINED
    confidence: float  # max class probability (uncalibrated)
    top: tuple[tuple[str, float], ...]  # up to 3 (category, probability), descending
    abstained: bool
    argmax_label: str


class CategoryModel:
    def __init__(self, pipeline: Any, classes: list[str], min_confidence: float, version: str):
        self._pipe = pipeline
        self.classes = list(classes)
        self.min_confidence = float(min_confidence)
        self.version = version

    def predict_proba_one(self, text: str) -> list[float]:
        return [float(p) for p in self._pipe.predict_proba([text[:MAX_CHARS]])[0]]

    def predict(self, text: str) -> CategoryPrediction:
        probs = self.predict_proba_one(text)
        order = sorted(range(len(probs)), key=lambda i: (-probs[i], self.classes[i]))
        best = order[0]
        conf = round(probs[best], 4)
        abstained = conf < self.min_confidence
        return CategoryPrediction(
            label=UNDETERMINED if abstained else self.classes[best],
            confidence=conf,
            top=tuple((self.classes[i], round(probs[i], 4)) for i in order[:3]),
            abstained=abstained,
            argmax_label=self.classes[best],
        )

    def predict_many(self, texts: list[str]) -> list[CategoryPrediction]:
        if not texts:
            return []
        P = self._pipe.predict_proba([t[:MAX_CHARS] for t in texts])
        out = []
        for row in P:
            probs = [float(p) for p in row]
            order = sorted(range(len(probs)), key=lambda i: (-probs[i], self.classes[i]))
            conf = round(probs[order[0]], 4)
            ab = conf < self.min_confidence
            out.append(
                CategoryPrediction(
                    UNDETERMINED if ab else self.classes[order[0]], conf,
                    tuple((self.classes[i], round(probs[i], 4)) for i in order[:3]), ab,
                    self.classes[order[0]],
                )  # fmt: skip
            )
        return out


@dataclass(frozen=True)
class LoadResult:
    model: CategoryModel | None
    status: str  # loaded | unavailable
    reason: str | None  # missing_artifact | pin_mismatch | sklearn_mismatch | smoke_test_failed...

    @property
    def pin_ok(self) -> bool:
        return self.status == "loaded"


def min_confidence_from_env(default: float) -> float:
    raw = os.getenv("CATEGORY_MIN_CONFIDENCE")
    if raw is None or not raw.strip():
        return default
    try:
        v = float(raw)
    except ValueError:
        log.warning("CATEGORY_MIN_CONFIDENCE is not a number; using the artifact default")
        return default
    return v if 0.0 <= v <= 1.0 else default


def load_with_status(
    path: Path | None = None, expected_sha256: str | None = None
) -> LoadResult:
    path = path or ARTIFACT_PATH
    try:
        from . import artifact_pin

        pin = artifact_pin.ARTIFACT_SHA256 if expected_sha256 is None else expected_sha256
        try:
            raw = path.read_bytes()
        except OSError:
            log.warning("category model artifact missing")
            return LoadResult(None, "unavailable", "missing_artifact")
        if not pin or hashlib.sha256(raw).hexdigest() != pin:
            log.warning("category model artifact failed integrity check")
            return LoadResult(None, "unavailable", "pin_mismatch")
        import joblib
        import sklearn

        blob = joblib.load(io.BytesIO(raw))
        if blob.get("sklearn_version") != sklearn.__version__:
            log.warning("category model sklearn version mismatch")
            return LoadResult(None, "unavailable", "sklearn_mismatch")
        model = CategoryModel(
            blob["pipeline"], list(blob["classes"]),
            min_confidence_from_env(float(blob["min_confidence"])), str(blob["model_version"]),
        )  # fmt: skip
        smoke_a = model.predict(SMOKE_PHISHING)
        smoke_b = model.predict(SMOKE_BENIGN)
        if smoke_a.argmax_label != "PHISHING" or smoke_b.label not in ("OTHER", UNDETERMINED):
            log.warning("category model failed the smoke test")
            return LoadResult(None, "unavailable", "smoke_test_failed")
        return LoadResult(model, "loaded", None)
    except Exception as exc:  # unreadable artifact, ImportError, unpickling problems...
        log.warning("category model unavailable: %s", type(exc).__name__)
        return LoadResult(None, "unavailable", "load_error")


def load_category_model(
    path: Path | None = None, expected_sha256: str | None = None
) -> CategoryModel | None:
    return load_with_status(path, expected_sha256).model
