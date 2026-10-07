"""Train the ``clf-v1`` chunk classifier (TF-IDF + logistic regression + isotonic calibration).

Needs the optional ``train`` extra (sim-engine); the service itself never imports it.

    pip install -e "services/call_guard[train]" && python -m call_guard.train

Data: simulator chunks (seed ``TRAIN_SEED``) + authored scam/benign text (``authored.py``) +
hand-written hard negatives (``hardneg.py``). Chunks under ``MIN_WORDS`` words are dropped.
Authored benign lines are split 60/20/20 into train / calibration / report sets; simulator and
authored-scam data use disjoint seeds for each split. The reliability table (binned predicted
vs observed) is computed on the report split and stored in the artifact.
"""

import argparse
import hashlib
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from . import authored
from .hardneg import HARD_NEGATIVES
from .model import ARTIFACT_PATH, CLF_VERSION

TRAIN_SEED, CALIB_SEED, REPORT_SEED = 1000, 2000, 3000
MIN_WORDS = 4
AUTHORED_WEIGHT = 3.0
PIN_PATH = Path(__file__).parent / "artifact_pin.py"


def sim_chunks(seed: int, n_scam: int, n_benign: int) -> tuple[list[str], list[int]]:
    from sim_engine.calls import (  # optional dependency, training only
        BENIGN_KINDS,
        BENIGN_P,
        LANG_P,
        LANGS,
        benign_call_chunks,
        scam_call_chunks,
    )

    rng = np.random.default_rng([seed, 41])
    texts: list[str] = []
    labels: list[int] = []
    for _ in range(n_scam):
        lang = str(rng.choice(LANGS, p=LANG_P))
        for ch in scam_call_chunks(rng, lang):
            if len(ch.split()) >= MIN_WORDS:
                texts.append(ch)
                labels.append(1)
    for _ in range(n_benign):
        kind = str(rng.choice(BENIGN_KINDS, p=BENIGN_P))
        lang = str(rng.choice(LANGS, p=LANG_P))
        for ch in benign_call_chunks(rng, kind, lang):
            if len(ch.split()) >= MIN_WORDS:
                texts.append(ch)
                labels.append(0)
    return texts, labels


def split_benign() -> tuple[list[str], list[str], list[str]]:
    lines = [*authored.BENIGN, *HARD_NEGATIVES]
    rng = np.random.default_rng(7)
    idx = rng.permutation(len(lines))
    n_tr, n_ca = int(0.6 * len(lines)), int(0.2 * len(lines))
    pick = lambda ids: [lines[i] for i in ids]  # noqa: E731
    return pick(idx[:n_tr]), pick(idx[n_tr : n_tr + n_ca]), pick(idx[n_tr + n_ca :])


def build_split(seed: int, benign_lines: list[str], n_sim: tuple[int, int], n_auth_scam: int):
    st, sl = sim_chunks(seed, *n_sim)
    rng = np.random.default_rng([seed, 43])
    a_scam = authored.scam_chunks(rng, n_auth_scam)
    texts = [*st, *a_scam, *benign_lines]
    labels = [*sl, *[1] * len(a_scam), *[0] * len(benign_lines)]
    weights = [*[1.0] * len(st), *[AUTHORED_WEIGHT] * (len(a_scam) + len(benign_lines))]
    return texts, labels, weights


def make_pipeline() -> Pipeline:
    return Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    token_pattern=r"[^\s.,?!।:;\"'()]+",  # keeps Devanagari matras inside tokens
                    lowercase=True,
                    ngram_range=(1, 2),
                    min_df=2,
                    max_features=8000,
                    sublinear_tf=True,
                    dtype=np.float32,
                ),
            ),
            ("lr", LogisticRegression(C=3.0, max_iter=2000)),
        ]
    )


def reliability(probs: np.ndarray, y: np.ndarray, bins: int = 10) -> tuple[list[dict], float]:
    edges = np.linspace(0, 1, bins + 1)
    rows, ece = [], 0.0
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (probs >= lo) & ((probs < hi) if hi < 1 else (probs <= hi))
        if m.sum() == 0:
            continue
        pred, obs = float(probs[m].mean()), float(y[m].mean())
        rows.append(
            {"bin": f"{lo:.1f}-{hi:.1f}", "n": int(m.sum()), "predicted": pred, "observed": obs}
        )
        ece += m.mean() * abs(pred - obs)
    return rows, float(ece)


def train(out: Path = ARTIFACT_PATH, seed: int = TRAIN_SEED) -> Path:
    b_tr, b_ca, b_te = split_benign()
    tx, ty, tw = build_split(seed, b_tr * 8, (500, 900), 2500)
    pipe = make_pipeline().fit(tx, ty, lr__sample_weight=tw)
    cx, cy, _ = build_split(CALIB_SEED, b_ca * 3, (250, 450), 800)
    raw_c = pipe.predict_proba(cx)[:, 1]
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(raw_c, cy)
    rx, ry, _ = build_split(REPORT_SEED, b_te, (250, 450), 800)
    cal = np.clip(iso.predict(pipe.predict_proba(rx)[:, 1]), 0, 1)
    table, ece = reliability(cal, np.array(ry))
    raw_table, raw_ece = reliability(pipe.predict_proba(rx)[:, 1], np.array(ry))
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model_version": CLF_VERSION,
            "sklearn_version": sklearn.__version__,
            "pipeline": pipe,
            "calibrator": iso,
            "reliability": table,
            "ece": ece,
        },
        out,
        compress=3,
    )
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    PIN_PATH.write_text(
        '"""Pinned integrity data for the classifier artifact. '
        'Written by ``python -m call_guard.train``."""\n\n'
        f'ARTIFACT_SHA256 = "{sha}"\n'
    )
    print(f"trained {CLF_VERSION} -> {out} ({out.stat().st_size} bytes) sha256={sha}")
    print(f"report split n={len(ry)} ECE calibrated={ece:.4f} raw={raw_ece:.4f}")
    for r in table:
        print(
            f"  {r['bin']}  n={r['n']:5d}  predicted={r['predicted']:.3f}  observed={r['observed']:.3f}"
        )
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ARTIFACT_PATH)
    ap.add_argument("--seed", type=int, default=TRAIN_SEED)
    a = ap.parse_args()
    train(a.out, a.seed)
