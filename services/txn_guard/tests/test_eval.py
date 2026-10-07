"""Held-out simulator evaluation of the committed artifact (seeds disjoint from training) and
checks that the training data is realistically imperfect."""

import joblib
import numpy as np
import pytest

from txn_guard.decision import HOLD_AT, STEP_UP_AT
from txn_guard.features import FEATURE_NAMES
from txn_guard.model import ARTIFACT_PATH, Scorer
from txn_guard.simdata import build_stream
from txn_guard.train import CALIB_SEEDS, EVAL_SEEDS, STREAM_KW, TRAIN_SEEDS, guarded, metrics_at

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def stream():
    return build_stream(EVAL_SEEDS[0], **STREAM_KW) + build_stream(EVAL_SEEDS[1], **STREAM_KW)


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


def test_heldout_metrics_at_hold_threshold(stream):
    model = Scorer()._model
    assert model is not None
    x = np.array([[r.features[n] for n in FEATURE_NAMES] for r in stream])
    m = metrics_at(guarded(model, x), stream, HOLD_AT)
    assert m["recall_victim_transfers"] >= 0.80
    assert m["held_benign_rate"] < 0.001
    p = guarded(model, x)
    benign = np.array([r.label == 0 for r in stream])
    assert ((p >= STEP_UP_AT) & (p < HOLD_AT) & benign).sum() / benign.sum() < 0.02


def test_payee_age_is_not_the_only_separator():
    blob = joblib.load(ARTIFACT_PATH)
    imp = dict(blob["importance"])
    non_age = sum(
        v for k, v in imp.items() if k not in ("payee_age_days", "payee_age_young") and v > 0
    )
    assert non_age > imp["payee_age_days"]  # other features carry more AP than payee age alone
    ab = blob["ablation"]["no_payee_age"]["hold"]
    assert ab["recall_victim_transfers"] >= 0.7  # still catches most victims without payee age
