"""Train the ``clf-v1`` chunk classifier from simulator transcripts.

Needs the optional ``train`` extra (sim-engine); the service itself never imports it.

    pip install -e "services/call_guard[train]" && python -m call_guard.train

Positive class: chunks of scam calls; negative: chunks of benign calls plus the hand-written
hard negatives in ``hardneg.py``. Chunks under ``MIN_WORDS`` words are dropped (victim
interjections and fillers carry no label signal). Held-out evaluation uses other seeds.
"""

import argparse
from pathlib import Path

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from .hardneg import HARD_NEGATIVES
from .model import ARTIFACT_PATH, CLF_VERSION

TRAIN_SEED = 1000
MIN_WORDS = 4
HARD_NEG_REPEAT = 5


def build_dataset(seed: int, n_scam: int, n_benign: int) -> tuple[list[str], list[int]]:
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
    texts += HARD_NEGATIVES * HARD_NEG_REPEAT
    labels += [0] * (len(HARD_NEGATIVES) * HARD_NEG_REPEAT)
    return texts, labels


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
                    max_features=6000,
                    sublinear_tf=True,
                    dtype=np.float32,
                ),
            ),
            ("lr", LogisticRegression(C=4.0, max_iter=1000)),
        ]
    )


def train(out: Path = ARTIFACT_PATH, seed: int = TRAIN_SEED) -> Path:
    texts, labels = build_dataset(seed, n_scam=1200, n_benign=2500)
    pipe = make_pipeline().fit(texts, labels)
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model_version": CLF_VERSION, "pipeline": pipe}, out, compress=3)
    print(f"trained {CLF_VERSION} on {len(texts)} chunks -> {out} ({out.stat().st_size} bytes)")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ARTIFACT_PATH)
    ap.add_argument("--seed", type=int, default=TRAIN_SEED)
    a = ap.parse_args()
    train(a.out, a.seed)
