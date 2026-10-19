"""Phase 5: held-out metrics for the shipped clf-v1 artifact (call_guard.evaluate)."""

import json

import numpy as np

from call_guard import artifact_pin
from call_guard.evaluate import OUT, binary


def test_binary_metrics_counts_and_ratios():
    p = np.array([0.9, 0.8, 0.2, 0.6, 0.1])
    y = np.array([1, 0, 1, 0, 0])
    m = binary(p, y, 0.5)
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (1, 2, 1, 1)
    assert m["precision"] == round(1 / 3, 4) and m["recall"] == 0.5
    assert m["false_positive_rate"] == round(2 / 3, 4)


def test_the_shipped_metrics_describe_the_pinned_artifact_honestly():
    m = json.loads(OUT.read_text("utf-8"))
    assert m["artifact"]["sha256"] == artifact_pin.ARTIFACT_SHA256
    assert m["DATA_IS_100_PERCENT_SYNTHETIC"] is True
    assert m["report_split_same_generators"]["is_headline"] is False
    assert set(m["independent_accuracy_sets"]) == {"accuracy_probes_v1.jsonl",
                                                   "accuracy_probes_v2.jsonl"}  # fmt: skip
