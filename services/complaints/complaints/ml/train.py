"""Train ``category-v1`` on the SYNTHETIC dataset and write metrics.

    cd services/complaints && python -m complaints.ml.train

Reproducible: seeded and deterministic. The split is the dataset's ``split`` column (by scenario
family; see ``data/README.md``). Hyper-parameters (C) and the abstention threshold are chosen on
the validation split only; the test split is touched once, for the final metrics. The artifact is
fitted on the TRAIN split only. All numbers are synthetic-data numbers.
"""

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support
from sklearn.pipeline import FeatureUnion, Pipeline

from . import synth
from .category_model import (
    ARTIFACT_PATH,
    CATEGORIES,
    MODEL_VERSION,
    UNDETERMINED,
    CategoryModel,
    preprocess,
)

SEED = 1729
C_GRID = (0.3, 1.0, 3.0, 10.0, 30.0)
THRESHOLD_GRID = [round(0.15 + 0.025 * i, 3) for i in range(27)]  # 0.15 .. 0.80
TARGET_ANSWERED_ACC = 0.80
MIN_COVERAGE = 0.50
C_TOLERANCE = 0.02  # prefer the largest C within this validation macro-F1 of the best
HERE = Path(__file__).parent
PIN_PATH = HERE / "artifact_pin.py"
METRICS_PATH = HERE / "artifacts" / f"{MODEL_VERSION}.metrics.json"
OPTIONAL_FIELDS = ("suspect_phone", "suspicious_url", "upi_id", "amount", "payment_method",
                   "city", "social_platform")  # fmt: skip


def make_pipeline(c: float) -> Pipeline:
    feats = FeatureUnion(
        [
            ("word", TfidfVectorizer(
                preprocessor=preprocess, token_pattern=r"[^\s.,?!।:;\"'()]+", ngram_range=(1, 2),
                min_df=2, max_features=20000, sublinear_tf=True, dtype=np.float32)),
            ("char", TfidfVectorizer(
                preprocessor=preprocess, analyzer="char_wb", ngram_range=(3, 5), min_df=3,
                max_features=30000, sublinear_tf=True, dtype=np.float32)),
        ]
    )  # fmt: skip
    clf = LogisticRegression(C=c, class_weight="balanced", max_iter=3000, random_state=SEED)
    return Pipeline([("features", feats), ("lr", clf)])


def _xy(rows):
    return [r["text"] for r in rows], [r["label"] for r in rows]


def _macro_f1(y, p) -> float:
    return float(precision_recall_fscore_support(y, p, labels=list(CATEGORIES), average="macro",
                                                 zero_division=0)[2])  # fmt: skip


def _report(y, pred, labels=CATEGORIES) -> dict:
    p, r, f, s = precision_recall_fscore_support(y, pred, labels=list(labels), zero_division=0)
    return {
        "accuracy": float(accuracy_score(y, pred)),
        "macro_f1": float(f.mean()),
        "per_class": {
            c: {"precision": float(p[i]), "recall": float(r[i]), "f1": float(f[i]),
                "support": int(s[i])}
            for i, c in enumerate(labels)
        },
        "confusion_matrix": {
            "labels": list(labels),
            "rows_true_cols_pred": confusion_matrix(y, pred, labels=list(labels)).tolist(),
        },
    }  # fmt: skip


def _abstention_stats(model: CategoryModel, texts, y, threshold: float) -> dict:
    preds = model.predict_many(texts)
    confs = np.array([p.confidence for p in preds])
    argm = [p.argmax_label for p in preds]
    ans = confs >= threshold
    n_ans = int(ans.sum())
    correct = np.array([a == t for a, t in zip(argm, y, strict=True)])
    return {
        "threshold": threshold,
        "abstention_rate": float(1 - ans.mean()),
        "coverage": float(ans.mean()),
        "accuracy_on_answered": float(correct[ans].mean()) if n_ans else None,
        "accuracy_on_abstained_if_forced": float(correct[~ans].mean()) if (~ans).any() else None,
        "macro_f1_abstention_counted_as_error": _macro_f1(
            y, [a if k else UNDETERMINED for a, k in zip(argm, ans, strict=True)]
        ),
    }


def choose_threshold(model: CategoryModel, val_texts, val_y) -> tuple[float, list[dict]]:
    """Smallest threshold whose validation accuracy on answered rows >= TARGET_ANSWERED_ACC with
    coverage >= MIN_COVERAGE; else the threshold maximising answered-accuracy x coverage."""
    table = [_abstention_stats(model, val_texts, val_y, t) for t in THRESHOLD_GRID]
    ok = [r for r in table if (r["accuracy_on_answered"] or 0) >= TARGET_ANSWERED_ACC
          and r["coverage"] >= MIN_COVERAGE]  # fmt: skip
    if ok:
        return ok[0]["threshold"], table
    best = max(table, key=lambda r: (r["accuracy_on_answered"] or 0) * r["coverage"])
    return best["threshold"], table


def run(out: Path = ARTIFACT_PATH, csv_path: Path = synth.DEFAULT_CSV, write: bool = True) -> dict:
    rows = synth.read_csv(csv_path)
    by = {s: [r for r in rows if r["split"] == s] for s in synth.SPLITS}
    tr_x, tr_y = _xy(by["train"])
    va_x, va_y = _xy(by["validation"])
    te_x, te_y = _xy(by["test"])

    # hyper-parameter search on VALIDATION only
    search = []
    for c in C_GRID:
        pipe = make_pipeline(c).fit(tr_x, tr_y)
        search.append({"C": c, "validation_macro_f1": _macro_f1(va_y, pipe.predict(va_x))})
    top = max(r["validation_macro_f1"] for r in search)
    best_c = max(r["C"] for r in search if r["validation_macro_f1"] >= top - C_TOLERANCE)
    pipe = make_pipeline(best_c).fit(tr_x, tr_y)
    classes = [str(c) for c in pipe.classes_]
    model = CategoryModel(pipe, classes, 0.0, MODEL_VERSION)
    threshold, val_table = choose_threshold(model, va_x, va_y)
    model = CategoryModel(pipe, classes, threshold, MODEL_VERSION)

    # ---- final metrics: test split, touched once
    te_pred = [p.argmax_label for p in model.predict_many(te_x)]
    headline = _report(te_y, te_pred)
    ab = _abstention_stats(model, te_x, te_y, threshold)
    clean_y = [r["true_category"] for r in by["test"]]

    # (a) random (leaky) split, same hyper-parameters; NOT the headline
    rng = random.Random(SEED)
    idx = list(range(len(rows)))
    rng.shuffle(idx)
    n_tr, n_va = int(0.6 * len(idx)), int(0.2 * len(idx))
    lk_tr, lk_te = idx[:n_tr], idx[n_tr + n_va :]
    lk_pipe = make_pipeline(best_c).fit(
        [rows[i]["text"] for i in lk_tr], [rows[i]["label"] for i in lk_tr]
    )
    lk_y = [rows[i]["label"] for i in lk_te]
    lk_pred = lk_pipe.predict([rows[i]["text"] for i in lk_te])
    tr_groups = {rows[i]["group_id"] for i in lk_tr}
    leaky = {
        "NOT_THE_HEADLINE": "random row split; the same template families appear in train and "
                            "test, so this overstates generalisation to new scenarios",
        "macro_f1": _macro_f1(lk_y, lk_pred),
        "accuracy": float(accuracy_score(lk_y, lk_pred)),
        "test_rows_whose_family_is_in_train": float(
            np.mean([rows[i]["group_id"] in tr_groups for i in lk_te])
        ),
    }  # fmt: skip

    # (b) structured fields: the model never sees them. Same predictions by construction;
    # accuracy on rows with all optional fields missing vs several present is reported.
    nfields = [sum(bool(r[f]) for f in OPTIONAL_FIELDS) for r in by["test"]]
    pred_arr, y_arr = np.array(te_pred), np.array(te_y)
    nf = np.array(nfields)

    def sl(mask):
        m = np.asarray(mask)
        return {"n": int(m.sum()), "accuracy": float((pred_arr[m] == y_arr[m]).mean())} if m.any() \
            else {"n": 0, "accuracy": None}  # fmt: skip

    fields = {
        "structured_fields_used_as_features": False,
        "note": "text-only model: dropping optional fields cannot change a prediction",
        "no_optional_field_present": sl(nf == 0),
        "three_or_more_fields_present": sl(nf >= 3),
    }

    # (c) noise and language slices
    pr = random.Random(SEED + 1)
    noisy_x = [synth.perturb(t, pr, strength=0.15) for t in te_x]
    noisy_pred = [p.argmax_label for p in model.predict_many(noisy_x)]
    noise = {
        "perturbation": "synth.perturb strength=0.15 on the untouched test texts",
        "macro_f1": _macro_f1(te_y, noisy_pred),
        "accuracy": float(accuracy_score(te_y, noisy_pred)),
        "clean_macro_f1": headline["macro_f1"],
    }
    aug_mask = np.array([r["augmented"] == "true" for r in by["test"]])
    languages = {}
    for lang in ("en", "hi", "dv"):
        m = np.array([r["language"] == lang for r in by["test"]])
        languages[lang] = {
            "n": int(m.sum()), "accuracy": float((pred_arr[m] == y_arr[m]).mean()),
            "macro_f1": _macro_f1(list(y_arr[m]), list(pred_arr[m])),
        }  # fmt: skip
    hn = np.array([r["hard_negative"] == "true" for r in by["test"]])
    hn_preds = model.predict_many([t for t, k in zip(te_x, hn, strict=True) if k])
    fraud_flagged = [p for p in hn_preds if not p.abstained and p.label != "OTHER"]
    hard = {
        "n": int(hn.sum()),
        "argmax_fraud_rate": float(np.mean([p.argmax_label != "OTHER" for p in hn_preds])),
        "answered_as_fraud_category_rate": float(len(fraud_flagged) / max(1, len(hn_preds))),
        "answered_as_other_rate": float(
            np.mean([(not p.abstained) and p.label == "OTHER" for p in hn_preds])
        ),
        "abstained_rate": float(np.mean([p.abstained for p in hn_preds])),
    }

    sha_csv = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    metrics = {
        "model_version": MODEL_VERSION,
        "DATA_IS_100_PERCENT_SYNTHETIC": True,
        "disclaimer": "Synthetic-data metrics. Not a statement about real-world performance; "
        "the model has not been validated on real complaints.",
        "dataset": {
            "file": csv_path.name,
            "sha256": sha_csv,
            "rows": len(rows),
            "rows_per_split": {s: len(v) for s, v in by.items()},
            "families_per_split": {s: len({r["group_id"] for r in v}) for s, v in by.items()},
            "generator_seed": synth.SEED,
            "label_noise": synth.LABEL_NOISE,
        },
        "training": {
            "seed": SEED,
            "algorithm": "tfidf(word1-2+char_wb3-5)+multinomial_logreg",
            "class_weight": "balanced",
            "fitted_on": "train split only",
            "C_search_on_validation": search,
            "chosen_C": best_c,
            "C_rule": f"largest C with validation macro-F1 within {C_TOLERANCE} of the best",
            "abstention_threshold_min_confidence": threshold,
            "threshold_rule": f"smallest threshold with validation accuracy on answered >= "
            f"{TARGET_ANSWERED_ACC} and coverage >= {MIN_COVERAGE}",
            "validation_threshold_table": val_table,
            "sklearn_version": sklearn.__version__,
            "numpy_version": np.__version__,
        },
        "headline_test_group_split": {
            **headline,
            "macro_f1": headline["macro_f1"],
            "n": len(te_y),
            "metric_basis": "argmax label vs observed (noisy) label, abstention ignored",
            "accuracy_vs_clean_labels": float(accuracy_score(clean_y, te_pred)),
            "abstention": ab,
        },
        "leaky_random_split": leaky,
        "optional_fields": fields,
        "noise_robustness": noise,
        "language_slices": languages,
        "augmented_rows_in_test": {
            "n": int(aug_mask.sum()),
            "accuracy": float((pred_arr[aug_mask] == y_arr[aug_mask]).mean())
            if aug_mask.any()
            else None,
        },
        "hard_negatives_test": hard,
    }
    if write:
        out.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {"model_version": MODEL_VERSION, "sklearn_version": sklearn.__version__,
             "pipeline": pipe, "classes": classes, "min_confidence": threshold,
             "trained_on": "synthetic", "dataset_sha256": sha_csv},
            out, compress=3,
        )  # fmt: skip
        sha = hashlib.sha256(out.read_bytes()).hexdigest()
        PIN_PATH.write_text(
            '"""Pinned SHA-256 of the category artifact (``python -m complaints.ml.train``)."""\n\n'
            f'ARTIFACT_SHA256 = "{sha}"\n'
        )
        metrics["artifact"] = {"file": out.name, "bytes": out.stat().st_size, "sha256": sha}
        METRICS_PATH.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")
    return metrics


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ARTIFACT_PATH)
    a = ap.parse_args(argv)
    m = run(a.out)
    h = m["headline_test_group_split"]
    print(
        f"{MODEL_VERSION} (SYNTHETIC data) C={m['training']['chosen_C']} "
        f"threshold={m['training']['abstention_threshold_min_confidence']}"
    )
    print(
        f"test macro-F1={h['macro_f1']:.4f} accuracy={h['accuracy']:.4f} "
        f"abstention={h['abstention']['abstention_rate']:.3f} "
        f"acc_answered={h['abstention']['accuracy_on_answered']:.4f}"
    )
    print(f"leaky random-split macro-F1={m['leaky_random_split']['macro_f1']:.4f} (NOT headline)")
    print(f"artifact {m['artifact']['bytes']} bytes sha256={m['artifact']['sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
