"""Held-out metrics for the shipped ``clf-v1`` artifact (Phase 5). Nothing is retrained.

    python -m call_guard.evaluate            # writes artifacts/clf-v1.metrics.json

Needs the optional ``train`` extra (sim-engine) to rebuild the report split, exactly as
``train.py`` built it (seed ``REPORT_SEED``, the held-out 20% of the authored benign lines and
hard negatives).

Three views, from the most optimistic to the most independent:

* ``report_split_same_generators``: the split ``train.py`` held out. Different random seeds, but
  the SAME simulator and authored templates as the training data, so phrasing overlaps heavily.
  This is not a template-grouped split; ``is_headline`` is false.
* ``held_out_hand_written_benign``: the 20% of hand-written benign lines and hard negatives that
  training never saw (benign only: false-positive rate).
* ``independent_accuracy_sets``: the frozen SYNTHETIC accuracy sets v1/v2 (hand-written chat and
  SMS messages, not call chunks: a domain shift), scam vs legitimate cases only.

The classifier alone is measured here. In the service it is a booster: without two independent
rule cue classes its contribution is capped below the alert threshold (``model.py``).
All data is synthetic; none of this is real-world performance.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from .model import ARTIFACT_PATH, CLF_VERSION, MIN_CLF_WORDS, load_classifier
from .train import REPORT_SEED, build_split, reliability, split_benign

ROOT = Path(__file__).resolve().parents[3]
OUT = ARTIFACT_PATH.with_name(f"{CLF_VERSION}.metrics.json")
THRESHOLDS = (0.5, 0.7)
ACCURACY_SETS = ("accuracy_probes_v1.jsonl", "accuracy_probes_v2.jsonl")


def binary(probs: np.ndarray, y: np.ndarray, t: float) -> dict:
    pred = probs >= t
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())

    def r(a: int, b: int) -> float | None:
        return round(a / b, 4) if b else None

    p, rc = r(tp, tp + fp), r(tp, tp + fn)
    f1 = round(2 * p * rc / (p + rc), 4) if p and rc else None
    return {"threshold": t, "tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": p,
            "recall": rc, "f1": f1, "false_positive_rate": r(fp, fp + tn)}  # fmt: skip


def auc(probs: np.ndarray, y: np.ndarray) -> float | None:
    from sklearn.metrics import roc_auc_score

    return round(float(roc_auc_score(y, probs)), 4) if 0 < y.sum() < len(y) else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    clf = load_classifier()
    if clf is None:
        raise SystemExit("the pinned clf-v1 artifact did not load (integrity or sklearn version)")
    score = np.vectorize(clf.predict_proba, otypes=[float])

    _, _, b_te = split_benign()
    rx, ry, _ = build_split(REPORT_SEED, b_te, (250, 450), 800)
    rx_kept = [t for t in rx if len(t.split()) >= MIN_CLF_WORDS]
    ry_kept = np.array([y for t, y in zip(rx, ry, strict=True) if len(t.split()) >= MIN_CLF_WORDS])
    rp = score(rx_kept)
    table, ece = reliability(rp, ry_kept)

    held = [t for t in b_te if len(t.split()) >= MIN_CLF_WORDS]
    hp = score(held)

    indep: dict[str, dict] = {}
    for name in ACCURACY_SETS:
        path = ROOT / "evaluation" / "datasets" / name
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        rows = [r for r in rows if r.get("expected_label") in ("scam", "legit")]
        texts = [r["text"] for r in rows]
        y = np.array([1 if r["expected_label"] == "scam" else 0 for r in rows])
        p = score(texts)
        indep[name] = {
            "dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "n": len(rows), "scam": int(y.sum()), "legit": int(len(y) - y.sum()),
            "roc_auc": auc(p, y), "at": [binary(p, y, t) for t in THRESHOLDS],
        }  # fmt: skip

    out = {
        "model_version": CLF_VERSION,
        "DATA_IS_100_PERCENT_SYNTHETIC": True,
        "disclaimer": "Synthetic data only. The report split shares generators and templates with "
        "the training data, so it is optimistic; the independent sets are small and hand-written "
        "by the rule author. None of these numbers is real-world performance.",
        "artifact": {"file": ARTIFACT_PATH.name,
                     "sha256": hashlib.sha256(ARTIFACT_PATH.read_bytes()).hexdigest()},
        "measured": "the calibrated classifier probability alone (chunks under "
        f"{MIN_CLF_WORDS} words score 0); in the service it is capped unless rules corroborate it",
        "report_split_same_generators": {
            "is_headline": False, "seed": REPORT_SEED, "n": len(ry_kept),
            "scam": int(ry_kept.sum()), "benign": int(len(ry_kept) - ry_kept.sum()),
            "roc_auc": auc(rp, ry_kept), "ece": round(ece, 4), "reliability": table,
            "at": [binary(rp, ry_kept, t) for t in THRESHOLDS],
        },
        "held_out_hand_written_benign": {
            "n": len(held),
            "flagged_at_0.5": int((hp >= 0.5).sum()), "flagged_at_0.7": int((hp >= 0.7).sum()),
            "false_positive_rate_at_0.5": round(float((hp >= 0.5).mean()), 4) if held else None,
        },
        "independent_accuracy_sets": indep,
    }  # fmt: skip
    a.out.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "report_split_same_generators"}
                     | {"report": {k: out["report_split_same_generators"][k]
                                   for k in ("n", "roc_auc", "ece", "at")}}, indent=1))  # fmt: skip


if __name__ == "__main__":
    main()
