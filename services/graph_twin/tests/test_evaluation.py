"""Simulator evaluation (numbers are reproduced in the README): 5 campaigns x 25 victims mixed
into a benign week, three seeds disjoint from every training seed."""

import pytest

from graph_twin.evaluate import EVAL_SEEDS, evaluate


@pytest.fixture(scope="module")
def results():
    return {s: evaluate(s) for s in EVAL_SEEDS}


def test_eval_seeds_are_disjoint_from_training_seeds():
    train = pytest.importorskip("txn_guard.train")
    cg = pytest.importorskip("call_guard.train")
    forbidden = {
        *train.TRAIN_SEEDS, *train.CALIB_SEEDS, *train.EVAL_SEEDS,
        cg.TRAIN_SEED, cg.CALIB_SEED, cg.REPORT_SEED,
    }  # fmt: skip
    assert not forbidden & set(EVAL_SEEDS)


def test_member_level_precision_recall_and_no_benign_rings(results):
    for seed, r in results.items():
        assert r["benign_rings"] == 0, seed
        assert r["rings"] == r["campaigns"] == 5, seed
        assert r["member_precision"] >= 0.95 and r["member_recall"] >= 0.95, (seed, r)
        for cid, c in r["per_campaign"].items():
            assert c["rings"] == 1 and c["recall"] >= 0.9, (seed, cid, c)


def test_time_to_detection_in_victims(results):
    before = [
        c["victims_before_detection"] for r in results.values() for c in r["per_campaign"].values()
    ]
    assert None not in before and max(before) <= 10 and sum(before) / len(before) <= 6
    print("victims before detection:", sorted(before))


def test_greedy_is_within_ten_percent_of_optimal_on_simulator_campaigns(results):
    gaps = []
    for r in results.values():
        for cid, row in r["takedown"].items():
            for k in ("k1", "k2", "k3"):
                e = row[k]
                if e["optimal_pct"] is None:
                    continue
                gaps.append(e["gap_pct_points"])
                assert e["greedy_pct"] >= 0.9 * e["optimal_pct"], (cid, k, e)
    assert gaps
    print("greedy-vs-optimal gap (percentage points):", sorted(set(gaps)))


def test_freeze_at_detection_projects_a_large_reduction(results):
    for r in results.values():
        for row in r["takedown"].values():
            f = row["freeze_at_detection"]
            assert f["reduction_pct"] >= 50.0
