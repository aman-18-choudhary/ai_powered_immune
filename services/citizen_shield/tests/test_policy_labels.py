"""Verdict labels after the accuracy batch (2026-10-18): never "safe"."""

from citizen_shield.policy import (
    INSUFFICIENT,
    NO_INDICATORS,
    WEAK,
    label_for,
    refine_label,
)


def test_no_label_is_called_safe():
    for score in (0.0, 0.1, 0.34, 0.35, 0.69, 0.7, 1.0):
        assert label_for(score) != "safe"
    assert "safe" not in (NO_INDICATORS, WEAK, INSUFFICIENT)


def test_weak_only_when_a_real_cue_was_found():
    assert refine_label(NO_INDICATORS, []) == NO_INDICATORS
    assert refine_label(NO_INDICATORS, ["NO_RISK_INDICATORS"]) == NO_INDICATORS
    assert refine_label(NO_INDICATORS, ["URGENCY"]) == WEAK
    assert refine_label("suspicious", ["URGENCY"]) == "suspicious"


def test_too_short_without_a_cue_is_insufficient_not_no_indicators():
    assert refine_label(NO_INDICATORS, [], n_words=2) == INSUFFICIENT
    assert refine_label(NO_INDICATORS, ["URGENCY"], n_words=2) == WEAK  # a cue still counts
    assert refine_label(NO_INDICATORS, [], n_words=12) == NO_INDICATORS
