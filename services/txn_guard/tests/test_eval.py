"""Held-out simulator evaluation of the committed artifact (seeds disjoint from training) and
checks that the training data is realistically imperfect."""

import joblib
import numpy as np
import pytest

from txn_guard.decision import HOLD_AT, STEP_UP_AT, decide, make_decision
from txn_guard.model import ARTIFACT_PATH, Scorer
from txn_guard.simdata import build_stream
from txn_guard.train import (
    CALIB_SEEDS,
    EVAL_SEEDS,
    STREAM_KW,
    TRAIN_SEEDS,
    metrics_at,
    policy_scores,
    wilson,
)

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def stream():
    rows = []
    for seed in EVAL_SEEDS:  # all four held-out seeds
        rows += build_stream(seed, **STREAM_KW)
    return rows


@pytest.fixture(scope="module")
def scores(stream):
    model = Scorer()._model
    assert model is not None
    return policy_scores(model, stream)


def test_seeds_disjoint():
    sets = [set(TRAIN_SEEDS), set(CALIB_SEEDS), set(EVAL_SEEDS)]
    assert sum(len(x) for x in sets) == len(set().union(*sets))


def test_call_risk_is_imperfect_in_data(stream):
    victims = [r for r in stream if r.role == "victim_transfer"]
    benign = [r for r in stream if r.role == "benign"]
    v_rate = np.mean([r.features["active_call_risk"] >= 0.7 for r in victims])
    b_rate = np.mean([r.features["active_call_risk"] >= 0.7 for r in benign])
    assert 0.6 < v_rate < 0.95  # recall ~85% minus detection lag
    assert 0.005 < b_rate < 0.03  # spurious risk on benign traffic


def test_heldout_metrics_at_hold_threshold(stream, scores):
    m = metrics_at(scores, stream, HOLD_AT)
    assert m["recall_victim_transfers"] >= 0.80
    assert m["held_benign_rate"] < 0.001  # plan target
    n_benign = sum(r.label == 0 for r in stream)
    assert wilson(m["benign_held"], n_benign)[1] < 0.001  # upper 95% bound also under 0.1%
    benign = np.array([r.label == 0 for r in stream])
    assert ((scores >= STEP_UP_AT) & (scores < HOLD_AT) & benign).sum() / benign.sum() < 0.02


def test_real_scorer_path_matches_vectorised_scores(stream, scores):
    """Scorer.score / make_decision (incl. overlays) agree with the vectorised eval path."""
    scorer = Scorer()
    rng = np.random.default_rng(0)
    idx = set(np.flatnonzero(scores >= STEP_UP_AT).tolist())  # every flagged txn
    idx |= set(rng.choice(len(stream), 800, replace=False).tolist())
    for i in sorted(idx):
        r = stream[i]
        s, reasons = scorer.score(r.features)
        assert abs(s - scores[i]) < 1e-9 and reasons
        d = make_decision(r.txn.txn_id, r.features, scorer, ts=r.txn.ts)
        assert d.decision == decide(scores[i]) and d.reasons


def test_payee_age_is_not_the_only_separator():
    blob = joblib.load(ARTIFACT_PATH)
    imp = dict(blob["importance"])
    non_age = sum(
        v for k, v in imp.items() if k not in ("payee_age_days", "payee_age_young") and v > 0
    )
    assert non_age > imp["payee_age_days"] * 0.5
    ab = blob["ablation"]["no_payee_age"]["hold"]
    assert ab["recall_victim_transfers"] >= 0.6  # still catches most victims without payee age
    assert blob["calibrated_prevalence"] == 0.01


def test_per_rail_benign_rates(stream, scores):
    """Benign NEFT must not be penalised for its naturally larger amounts (task 7b)."""
    for rail, max_hold, max_flag in (
        ("UPI", 0.0005, 0.002),
        ("IMPS", 0.002, 0.012),
        ("NEFT", 0.002, 0.010),
    ):
        idx = [i for i, r in enumerate(stream) if r.label == 0 and r.txn.rail == rail]
        hold = np.mean([scores[i] >= HOLD_AT for i in idx])
        flag = np.mean([scores[i] >= STEP_UP_AT for i in idx])
        assert hold <= max_hold and flag <= max_flag, (rail, hold, flag)
