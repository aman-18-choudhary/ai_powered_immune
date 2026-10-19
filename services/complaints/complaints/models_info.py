"""Facts about the trained models for ``GET /models``: read from the files shipped with each
model (metrics JSON, model card, artifact pin), never typed in by hand here, plus the runtime
status the pipeline observed. Nothing is fetched from the network.

* ``category-v1``: metrics from ``ml/artifacts/category-v1.metrics.json`` (held-out test split
  grouped by scenario family; the leaky random-split number is reported separately and flagged as
  NOT the headline), limitations from ``ml/MODEL_CARD.md``.
* call-guard ``clf-v1``: no held-out metrics file ships with that artifact, so ``metrics`` is null
  and the reason is stated; its limitations are summarised from ``call_guard/model.py``.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger("complaints.models")

ML_DIR = Path(__file__).parent / "ml"
CARD = ML_DIR / "MODEL_CARD.md"
METRICS = ML_DIR / "artifacts" / "category-v1.metrics.json"

SYNTHETIC_WARNING = (
    "Both models were trained and evaluated on 100% synthetic data (template-generated complaints; "
    "simulator transcripts and hand-authored text). Every number here is a synthetic-data number "
    "and says nothing measured about real complaints, which have not been tested."
)

CALL_GUARD_LIMITATIONS = [
    "Built for digital-arrest call scripts (what the caller says); a complaint narrative written "
    "from the victim's point of view can score lower than a transcript.",
    "Trained on simulator transcripts and hand-authored scam/benign text only; not validated on "
    "real calls or complaints.",
    "Its isotonic calibration was fitted on that same synthetic data, so its output is not a "
    "real-world likelihood; in this service it only contributes to an uncalibrated 0-100 score.",
    "A booster, never a standalone alarm: without corroborating rule cues it cannot lift text "
    "above 0.6 (one cue class: 0.69).",
    "English, romanised Hindi and Devanagari only.",
]


def card_limitations(path: Path = CARD) -> list[str]:
    """The bullet list under '## Known limitations and failure modes' in the model card."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    m = re.search(r"^## Known limitations[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        return []
    items: list[str] = []
    for line in m.group(1).splitlines():
        if line.startswith("* "):
            items.append(line[2:].strip())
        elif line.startswith("  ") and items and line.strip():
            items[-1] += " " + line.strip()
    return items


def _metrics() -> dict[str, Any] | None:
    try:
        return json.loads(METRICS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("category metrics file unreadable")
        return None


def category_entry(runtime: dict[str, Any]) -> dict[str, Any]:
    from .ml import artifact_pin
    from .ml.category_model import MODEL_VERSION

    m = _metrics() or {}
    head = m.get("headline_test_group_split") or {}
    ab = head.get("abstention") or {}
    leaky = m.get("leaky_random_split") or {}
    ds = m.get("dataset") or {}
    tr = m.get("training") or {}
    art = m.get("artifact") or {}
    return {
        "id": MODEL_VERSION, "kind": "complaint_category_classifier", "source": "ml",
        "version": m.get("model_version", MODEL_VERSION),
        "algorithm": tr.get("algorithm"),
        "artifact": {
            "file": art.get("file"), "bytes": art.get("bytes"),
            "sha256": artifact_pin.ARTIFACT_SHA256,
            "sha256_recorded_at_training": art.get("sha256"),
            "verified_at_load": "SHA-256 pin, scikit-learn version and a smoke test",
        },
        "training_data": {
            "synthetic": bool(m.get("DATA_IS_100_PERCENT_SYNTHETIC", True)),
            "description": "100% synthetic: template-generated complaints in English, romanised "
            "Hindi and Devanagari (no real complaint was used)",
            "rows": ds.get("rows"), "file": ds.get("file"),
            "split": "by scenario family: no template family is shared between train and test",
            "rows_per_split": ds.get("rows_per_split"),
        },
        "metrics": None if not head else {
            "basis": "held-out test split grouped by scenario family (synthetic)",
            "n": head.get("n"), "macro_f1": head.get("macro_f1"),
            "accuracy": head.get("accuracy"),
            "abstention_rate": ab.get("abstention_rate"),
            "accuracy_on_answered": ab.get("accuracy_on_answered"),
            "abstention_threshold": ab.get("threshold",
                                           tr.get("abstention_threshold_min_confidence")),
            "metric_basis": head.get("metric_basis"),
            "per_class": head.get("per_class"),
            "language_slices": m.get("language_slices"),
            "hard_negatives": m.get("hard_negatives_test"),
        },
        "leaky_random_split": None if not leaky else {
            "is_headline": False, "macro_f1": leaky.get("macro_f1"),
            "accuracy": leaky.get("accuracy"),
            "note": "Random row split: NOT the headline. " + str(leaky.get("NOT_THE_HEADLINE", "")),
        },
        "disclaimer": m.get("disclaimer"),
        "limitations": card_limitations(),
        "confidence_meaning": "top-class score of the model, uncalibrated; below the abstention "
        "threshold the category is UNDETERMINED",
        "runtime": runtime,
    }  # fmt: skip


def _call_guard_metrics() -> dict[str, Any]:
    """Headline = the independent hand-written set v2 (call_guard.evaluate, Phase 5); the split
    from the training generators is shown only as 'not the headline'."""
    try:
        from call_guard.model import ARTIFACT_PATH

        m = json.loads(ARTIFACT_PATH.with_name("clf-v1.metrics.json").read_text("utf-8"))
        v2 = m["independent_accuracy_sets"]["accuracy_probes_v2.jsonl"]
        at = next(x for x in v2["at"] if x["threshold"] == 0.5)
        rep = m["report_split_same_generators"]
        rep_at = next(x for x in rep["at"] if x["threshold"] == 0.5)
        held = m["held_out_hand_written_benign"]
    except Exception:
        return {
            "metrics": None,
            "metrics_unavailable_reason": "the clf-v1 metrics file is missing or unreadable",
            "leaky_random_split": None,
        }
    n = v2["n"]
    return {
        "metrics": {
            "basis": "Independent hand-written SYNTHETIC set accuracy_probes_v2 (scam vs "
            "legitimate messages, not call chunks; written by the rule author); the classifier "
            "alone at probability >= 0.5",
            "n": n, "macro_f1": None, "accuracy": round((at["tp"] + at["tn"]) / n, 4),
            "abstention_rate": None, "abstention_threshold": None,
            "binary": {"threshold": 0.5, "precision": at["precision"], "recall": at["recall"],
                       "f1": at["f1"], "roc_auc": v2["roc_auc"],
                       "false_positive_rate": at["false_positive_rate"]},
            "held_out_benign": f"{held['flagged_at_0.5']} of {held['n']} held-out hand-written "
            "benign lines and hard negatives flagged",
        },
        "leaky_random_split": {
            "is_headline": False, "metric_label": "F1", "macro_f1": rep_at["f1"],
            "accuracy": round((rep_at["tp"] + rep_at["tn"]) / rep["n"], 4),
            "note": "Held-out seeds from the SAME simulator and authored templates as training "
            f"(ROC-AUC {rep['roc_auc']}): optimistic, it measures the templates, not scams in "
            "general.",
        },
    }  # fmt: skip


def call_guard_entry(runtime: dict[str, Any]) -> dict[str, Any]:
    try:
        from call_guard import artifact_pin as cg_pin
        from call_guard.model import ARTIFACT_PATH, CLF_VERSION

        sha, version = cg_pin.ARTIFACT_SHA256, CLF_VERSION
        size = ARTIFACT_PATH.stat().st_size if ARTIFACT_PATH.exists() else None
        name = ARTIFACT_PATH.name
    except Exception:
        sha, version, size, name = None, "clf-v1", None, None
    return {
        "id": f"call_guard.{version}", "kind": "scam_script_classifier", "source": "ml",
        "version": version,
        "algorithm": "TF-IDF + logistic regression with an isotonic calibrator, blended with the "
        "call-guard cue rules (rules-v2) as max(rules, capped classifier)",
        "artifact": {"file": name, "bytes": size, "sha256": sha,
                     "verified_at_load": "SHA-256 pin, scikit-learn version and a smoke test"},
        "training_data": {
            "synthetic": True,
            "description": "100% synthetic: simulator call transcripts and hand-authored scam and "
            "benign text (no real call or complaint was used)",
        },
        **_call_guard_metrics(),
        "limitations": CALL_GUARD_LIMITATIONS,
        "runtime": runtime,
    }  # fmt: skip
