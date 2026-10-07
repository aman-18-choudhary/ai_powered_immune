"""Classifier layer: artifact loading, fallback, and held-out metrics (seeds not used in training)."""

import joblib
import numpy as np
import pytest
from sim_engine.calls import BENIGN_KINDS, BENIGN_P, LANGS, benign_call_chunks, scam_call_chunks

from call_guard.model import ARTIFACT_PATH, CLF_VERSION, Scorer, load_classifier
from call_guard.rules import CALL_RISK_THRESHOLD

HELD_OUT_SEEDS = (9001, 9002, 9003)  # training uses train.TRAIN_SEED, disjoint from these


def _calls(seed: int, n_scam: int, n_benign: int):
    rng = np.random.default_rng([seed, 77])
    scam, benign = [], []
    for _ in range(n_scam):
        lang = str(rng.choice(LANGS, p=(0.5, 0.3, 0.2)))
        scam.append(scam_call_chunks(rng, lang, mode="full"))
    for _ in range(n_benign):
        kind = str(rng.choice(BENIGN_KINDS, p=BENIGN_P))
        lang = str(rng.choice(LANGS, p=(0.5, 0.3, 0.2)))
        benign.append(benign_call_chunks(rng, kind, lang))
    return scam, benign


def test_artifact_small_and_versioned():
    assert ARTIFACT_PATH.exists() and ARTIFACT_PATH.stat().st_size < 1_000_000
    assert joblib.load(ARTIFACT_PATH)["model_version"] == CLF_VERSION == "clf-v1"


def test_fallback_to_rules_when_artifact_missing(tmp_path):
    assert load_classifier(tmp_path / "nope.joblib") is None
    s = Scorer(None)
    score, reasons, version = s.score_text("You are under digital arrest.")
    assert version == "rules-v1" and score >= 0.7 and reasons


def test_scorer_uses_classifier_version():
    s = Scorer(load_classifier())
    assert s.score_text("Hello")[2] == "clf-v1"


def _run(seed):
    s = Scorer(load_classifier())
    scam, benign = _calls(seed, 150, 300)
    tp = fn = fp = tn = 0
    call_hit = call_fp = 0
    for call in scam:
        for ch in call:
            if len(ch.split()) < 6:
                continue
            if s.score_text(ch)[0] >= CALL_RISK_THRESHOLD:
                tp += 1
            else:
                fn += 1
        call_hit += _session_max(s, call) >= CALL_RISK_THRESHOLD
    for call in benign:
        for ch in call:
            if s.score_text(ch)[0] >= CALL_RISK_THRESHOLD:
                fp += 1
            else:
                tn += 1
        call_fp += _session_max(s, call) >= CALL_RISK_THRESHOLD
    return tp, fn, fp, tn, call_hit / len(scam), call_fp / len(benign)


def _session_max(s: Scorer, chunks) -> float:
    from call_guard.session import accumulate

    state = None
    best = 0.0
    for i, ch in enumerate(chunks):
        score, reasons, _ = s.score_text(ch)
        state = accumulate(state, score, reasons, ts=30.0 * i)
        best = max(best, state.score)
    return best


@pytest.mark.parametrize("seed", HELD_OUT_SEEDS)
def test_held_out_metrics(seed):
    tp, fn, fp, tn, call_recall, call_fpr = _run(seed)
    print(
        f"seed={seed} chunk P={tp / max(tp + fp, 1):.3f} R={tp / (tp + fn):.3f} "
        f"FPR={fp / (fp + tn):.4f} call_recall={call_recall:.3f} call_FPR={call_fpr:.4f}"
    )
    assert fp / (fp + tn) < 0.01
    assert call_fpr < 0.01
    assert call_recall > 0.90


def test_bank_awareness_low_with_classifier():
    """Genuine bank-awareness lines from the (frozen, never-trained-on) independent set."""
    from tests.data.independent_eval import BENIGN_CALLS

    s = Scorer(load_classifier())
    keys = ("otp", "ओटीपी", "password")
    lines = [
        c for _, ch, hard in BENIGN_CALLS if hard for c in ch if any(k in c.lower() for k in keys)
    ]
    assert len(lines) >= 5
    assert max(s.score_text(t)[0] for t in lines if "courier" not in t.lower()) < 0.2


def test_training_hard_negatives_never_alert():
    from call_guard.hardneg import HARD_NEGATIVES

    s = Scorer(load_classifier())
    assert max(s.score_text(t)[0] for t in HARD_NEGATIVES) < 0.7


def test_short_first_contact_calls_cross_via_session():
    rng = np.random.default_rng([9100, 5])
    s = Scorer(load_classifier())
    hits = 0
    for _ in range(100):
        lang = str(rng.choice(LANGS, p=(0.5, 0.3, 0.2)))
        hits += _session_max(s, scam_call_chunks(rng, lang, mode="short")) >= CALL_RISK_THRESHOLD
    assert hits >= 90


# ----------------------------------------------------------------- artifact safety
def test_tampered_artifact_falls_back(tmp_path):
    raw = bytearray(ARTIFACT_PATH.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    bad = tmp_path / "clf.joblib"
    bad.write_bytes(bytes(raw))
    assert load_classifier(bad) is None


def test_sklearn_version_mismatch_falls_back(monkeypatch):
    import sklearn

    monkeypatch.setattr(sklearn, "__version__", "0.0.0")
    assert load_classifier() is None


def test_smoke_test_failure_falls_back(monkeypatch):
    import call_guard.model as m

    monkeypatch.setattr(m, "SMOKE_BENIGN", m.SMOKE_SCAM)  # benign probe now scores high -> reject
    assert m.load_classifier() is None


def test_predict_proba_failure_falls_back_to_rules():
    class Boom:
        version = "clf-v1"

        def predict_proba(self, text):
            raise RuntimeError("boom")

    s = Scorer(Boom())  # type: ignore[arg-type]
    score, reasons, version = s.score_text("You are under digital arrest. Do not tell anyone.")
    assert version == "rules-v1" and score >= 0.7 and reasons and s.clf_errors == 1


def test_fallback_mode_is_loud(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        s = Scorer(None)
    assert s.fallback_mode and "FALLBACK" in caplog.text


def test_calibrator_present_and_reliability_recorded():
    blob = joblib.load(ARTIFACT_PATH)
    assert blob["calibrator"] is not None and blob["reliability"] and blob["ece"] < 0.05


def test_advisory_text_cannot_alert_via_classifier():
    s = Scorer(load_classifier())
    text = "Public awareness: no officer can arrest you over a video call, it is fraud, disconnect and call 1930."
    assert s.score_text(text)[0] < 0.5


def test_dev_set_blend_metrics():
    from tests.data.dev_set import BENIGN, SCAM

    s = Scorer(load_classifier())
    assert sum(s.score_text(t)[0] >= 0.7 for t in SCAM) / len(SCAM) >= 0.9
    assert not [t for t in BENIGN if s.score_text(t)[0] >= 0.7]
