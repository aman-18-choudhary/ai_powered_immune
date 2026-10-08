"""Train the ``gbm-v1`` transaction model (HistGradientBoosting + isotonic calibration).

Needs the optional ``train`` extra (sim-engine); the service itself never imports it.

    pip install -e "services/txn_guard[train]" && python -m txn_guard.train

Seeds are disjoint by role: ``TRAIN_SEEDS`` fit the booster, ``CALIB_SEEDS`` fit the isotonic
calibrator and measure permutation importance, ``EVAL_SEEDS`` are never used for fitting and
produce the reported precision / recall / FPR / held-benign figures. Retraining rewrites
``artifact_pin.py`` (commit it together with the joblib).
"""

import argparse
import hashlib
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score

from .decision import HOLD_AT, STEP_UP_AT
from .features import MODEL_FEATURES
from .model import ARTIFACT_PATH, CALIBRATED_PREVALENCE, MODEL_VERSION, GbmModel
from .policy import damp_model_score, overlays
from .simdata import LabelledTxn, build_stream, to_matrix

TRAIN_SEEDS = (101, 102, 103, 104, 105, 106)
CALIB_SEEDS = (201, 202, 203)
EVAL_SEEDS = (9001, 9002, 9003, 9004)
PIN_PATH = Path(__file__).parent / "artifact_pin.py"
STREAM_KW = {"n_citizens": 2500}

# +1: more of this can only add risk. 0: unconstrained.
MONOTONE_UP = {
    "amount_zscore", "amount_vs_typical_log", "new_payee", "payee_age_young", "velocity_count_1h",
    "velocity_amount_1h", "velocity_count_24h", "velocity_amount_24h", "active_call_risk",
    "device_novel", "payee_in_antibody", "future_dated",
}  # fmt: skip
MONOTONE_DOWN = {"payee_age_days"}
PAYEE_AGE_FEATURES = ("payee_age_days", "payee_age_young")


def monotone_cst(names: tuple[str, ...] = MODEL_FEATURES) -> list[int]:
    return [1 if n in MONOTONE_UP else -1 if n in MONOTONE_DOWN else 0 for n in names]


def make_booster(names: tuple[str, ...] = MODEL_FEATURES) -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        max_iter=250, learning_rate=0.06, max_leaf_nodes=15, min_samples_leaf=30,
        l2_regularization=1.0, monotonic_cst=monotone_cst(names), early_stopping=False,
        random_state=0,
    )  # fmt: skip


def gather(seeds: tuple[int, ...]) -> list[LabelledTxn]:
    rows: list[LabelledTxn] = []
    for s in seeds:
        rows += build_stream(s, **STREAM_KW)
    return rows


def metrics_at(score: np.ndarray, rows: list[LabelledTxn], thr: float) -> dict[str, float]:
    y = np.array([r.label for r in rows])
    victim = np.array([r.role == "victim_transfer" for r in rows])
    hit = score >= thr
    tp = int((hit & (y == 1)).sum())
    fp = int((hit & (y == 0)).sum())
    return {
        "threshold": thr,
        "precision": tp / max(1, tp + fp),
        "recall_all_scam": tp / max(1, int((y == 1).sum())),
        "recall_victim_transfers": int((hit & victim).sum()) / max(1, int(victim.sum())),
        "recall_mule_forwards": int((hit & (y == 1) & ~victim).sum())
        / max(1, int(((y == 1) & ~victim).sum())),
        "held_benign_rate": fp / max(1, int((y == 0).sum())),
        "benign_held": fp,
    }


def policy_scores(model: GbmModel, rows: list[LabelledTxn]) -> np.ndarray:
    """Calibrated model score with the policy-overlay floors applied (what ``Scorer`` returns
    when it runs the model path; asserted equal to it on a subset in tests)."""
    x, _ = to_matrix(rows, MODEL_FEATURES)
    floors = np.array([max([o.floor for o in overlays(r.features)], default=0.0) for r in rows])
    f_model = model.proba(x)
    damped = np.array(
        [damp_model_score(r.features, float(s)) for r, s in zip(rows, f_model, strict=True)]
    )
    return np.maximum(damped, floors)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (c - h) / d, (c + h) / d


def evaluate(model: GbmModel, rows: list[LabelledTxn]) -> dict:
    y = np.array([r.label for r in rows])
    p = policy_scores(model, rows)
    raw = model.proba(to_matrix(rows, MODEL_FEATURES)[0])
    benign = y == 0
    held = int(((p >= HOLD_AT) & benign).sum())
    lo, hi = wilson(held, int(benign.sum()))
    per_rail = {}
    for rail in ("UPI", "IMPS", "NEFT"):
        m = np.array([r.txn.rail == rail and r.label == 1 for r in rows])
        per_rail[rail] = {
            "n_scam": int(m.sum()),
            "recall": float((p[m] >= HOLD_AT).mean()) if m.any() else None,
        }
    rel, ece_after = reliability(p, y)
    out = {
        "n": len(rows), "n_benign": int(benign.sum()), "n_scam": int((y == 1).sum()),
        "n_victim_transfers": sum(r.role == "victim_transfer" for r in rows),
        "average_precision": float(average_precision_score(y, p)),
        "hold": metrics_at(p, rows, HOLD_AT),
        "held_benign_wilson95": (lo, hi),
        "per_rail_recall": per_rail,
        "ece_after_overlays": ece_after,
        "model_only_hold": metrics_at(raw, rows, HOLD_AT),
        "step_up_benign_rate": float(((p >= STEP_UP_AT) & (p < HOLD_AT) & benign).sum() / benign.sum()),
        "step_or_hold_benign_rate": float(((p >= STEP_UP_AT) & benign).sum() / benign.sum()),
        "curve": [metrics_at(p, rows, t) for t in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95)],
    }  # fmt: skip
    return out


def reliability(p: np.ndarray, y: np.ndarray, bins: int = 10) -> tuple[list[dict], float]:
    edges = np.linspace(0, 1, bins + 1)
    rows, ece = [], 0.0
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        if m.sum() == 0:
            continue
        rows.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": int(m.sum()),
                     "predicted": float(p[m].mean()), "observed": float(y[m].mean())})  # fmt: skip
        ece += m.mean() * abs(p[m].mean() - y[m].mean())
    return rows, float(ece)


def fit(train_rows, calib_rows, names=MODEL_FEATURES) -> GbmModel:
    xt, yt = to_matrix(train_rows, names)
    booster = make_booster(names).fit(xt, yt)
    xc, yc = to_matrix(calib_rows, names)
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(
        booster.predict_proba(xc)[:, 1], yc
    )
    return GbmModel(booster, iso, MODEL_VERSION)


def train(out: Path = ARTIFACT_PATH) -> Path:
    tr, ca, ev = gather(TRAIN_SEEDS), gather(CALIB_SEEDS), gather(EVAL_SEEDS)
    model = fit(tr, ca)
    xc, yc = to_matrix(ca, MODEL_FEATURES)
    imp = permutation_importance(
        model.model, xc, yc, scoring="average_precision", n_repeats=5, random_state=0
    )
    importance = sorted(
        ((n, float(m)) for n, m in zip(MODEL_FEATURES, imp.importances_mean, strict=True)),
        key=lambda t: -t[1],
    )
    report = evaluate(model, ev)
    xe, ye = to_matrix(ev, MODEL_FEATURES)
    table, ece = reliability(model.proba(xe), ye)
    # ablations on the eval seeds: is payee age the only separator?
    ablation = {}
    for label, drop in (
        ("no_payee_age", PAYEE_AGE_FEATURES),
        ("no_call_risk", ("active_call_risk",)),
    ):
        keep = tuple(n for n in MODEL_FEATURES if n not in drop)
        m2 = fit(tr, ca, keep)
        xe2, _ = to_matrix(ev, keep)
        pe = np.clip(m2.calibrator.predict(m2.model.predict_proba(xe2)[:, 1]), 0, 1)
        ablation[label] = {
            "average_precision": float(average_precision_score(ye, pe)),
            "hold": metrics_at(pe, ev, HOLD_AT),
        }
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model_version": MODEL_VERSION, "sklearn_version": sklearn.__version__,
            "feature_names": MODEL_FEATURES, "calibrated_prevalence": CALIBRATED_PREVALENCE, "model": model.model, "calibrator": model.calibrator,
            "importance": importance, "eval": report, "reliability": table, "ece": ece,
            "ablation": ablation,
            "seeds": {"train": TRAIN_SEEDS, "calib": CALIB_SEEDS, "eval": EVAL_SEEDS},
        },
        out, compress=3,
    )  # fmt: skip
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    PIN_PATH.write_text(
        '"""Pinned integrity data for the model artifact. '
        'Written by ``python -m txn_guard.train``."""\n\n'
        f'ARTIFACT_SHA256 = "{sha}"\n'
    )
    print(f"trained {MODEL_VERSION} -> {out} ({out.stat().st_size} bytes) sha256={sha}")
    print(f"rows train={len(tr)} calib={len(ca)} eval={len(ev)}; ECE(eval)={ece:.4f}")
    for r in table:
        print(
            f"  {r['bin']} n={r['n']:6d} predicted={r['predicted']:.3f} observed={r['observed']:.3f}"
        )
    print("permutation importance (calib, AP drop):")
    for n, m in importance:
        print(f"  {n:24s} {m:.4f}")
    print("eval:", {k: v for k, v in report.items() if k != "curve"})
    for c in report["curve"]:
        print("  ", {k: round(v, 4) for k, v in c.items()})
    print("ablation:", ablation)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ARTIFACT_PATH)
    train(ap.parse_args().out)
