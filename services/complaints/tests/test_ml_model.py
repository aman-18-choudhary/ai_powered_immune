"""Category classifier: artifact integrity (pin, sklearn version, smoke test), lazy import,
abstention, text-only behaviour and held-out metric regression guards (SYNTHETIC data)."""

import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import joblib
import pytest

from complaints.ml import artifact_pin, category_model, synth
from complaints.ml.category_model import (
    ARTIFACT_PATH,
    UNDETERMINED,
    load_category_model,
    load_with_status,
)

METRICS = json.loads((ARTIFACT_PATH.parent / "category-v1.metrics.json").read_text())
ROWS = synth.read_csv(synth.DEFAULT_CSV)
TEST = [r for r in ROWS if r["split"] == "test"]


@pytest.fixture(scope="module")
def model():
    m = load_category_model()
    assert m is not None
    return m


def test_artifact_is_small_and_pinned():
    assert ARTIFACT_PATH.stat().st_size < 3 * 1024 * 1024
    assert hashlib.sha256(ARTIFACT_PATH.read_bytes()).hexdigest() == artifact_pin.ARTIFACT_SHA256
    assert METRICS["artifact"]["sha256"] == artifact_pin.ARTIFACT_SHA256


def test_loads_and_passes_smoke(model):
    res = load_with_status()
    assert res.status == "loaded" and res.pin_ok and res.reason is None
    assert model.predict(category_model.SMOKE_PHISHING).argmax_label == "PHISHING"
    assert model.predict(category_model.SMOKE_BENIGN).label in ("OTHER", UNDETERMINED)


def test_wrong_pin_is_unavailable_never_a_benign_label():
    res = load_with_status(expected_sha256="0" * 64)
    assert res.model is None and res.status == "unavailable" and res.reason == "pin_mismatch"
    assert load_with_status(expected_sha256="").reason == "pin_mismatch"


def test_tampered_file_is_unavailable(tmp_path: Path):
    p = tmp_path / "a.joblib"
    raw = bytearray(ARTIFACT_PATH.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    p.write_bytes(bytes(raw))
    res = load_with_status(p)
    assert res.model is None and res.reason == "pin_mismatch"


def test_missing_artifact_is_unavailable(tmp_path: Path):
    res = load_with_status(tmp_path / "nope.joblib")
    assert res.model is None and res.reason == "missing_artifact"


def _repack(tmp_path: Path, **over) -> tuple[Path, str]:
    blob = joblib.load(io.BytesIO(ARTIFACT_PATH.read_bytes()))
    blob.update(over)
    p = tmp_path / "x.joblib"
    joblib.dump(blob, p, compress=3)
    return p, hashlib.sha256(p.read_bytes()).hexdigest()


def test_sklearn_version_mismatch_is_unavailable(tmp_path: Path):
    p, sha = _repack(tmp_path, sklearn_version="0.0.1")
    res = load_with_status(p, sha)  # pin matches, version does not
    assert res.model is None and res.reason == "sklearn_mismatch"


def test_failed_smoke_test_is_unavailable(tmp_path: Path):
    blob = joblib.load(io.BytesIO(ARTIFACT_PATH.read_bytes()))
    shifted = blob["classes"][1:] + blob["classes"][:1]  # wrong label mapping
    p, sha = _repack(tmp_path, classes=shifted)
    res = load_with_status(p, sha)
    assert res.model is None and res.reason == "smoke_test_failed"


def test_nothing_heavy_is_imported_at_module_import():
    code = (
        "import sys, complaints.ml.category_model as m; "
        "assert not any(k.split('.')[0] in ('sklearn','joblib','numpy','scipy') "
        "for k in sys.modules), 'heavy import'; "
        "r = m.load_with_status(__import__('pathlib').Path('/nonexistent/x.joblib')); "
        "assert r.status == 'unavailable' and r.reason == 'missing_artifact'; "
        "assert not any(k.split('.')[0] in ('sklearn','joblib','numpy','scipy') "
        "for k in sys.modules), 'heavy import on missing artifact'"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_text_only_prediction_is_deterministic(model):
    t = "I got a call from the CBI saying I am under digital arrest and must pay Rs 50,000."
    a, b = model.predict(t), model.predict(t)
    assert a == b
    import inspect

    assert list(inspect.signature(model.predict).parameters) == ["text"]
    assert a.confidence == max(p for _, p in a.top)


def test_low_confidence_abstains_and_threshold_env_applies(model, monkeypatch):
    junk = model.predict("asdf qwer zxcv uiop")
    assert junk.abstained == (junk.confidence < model.min_confidence)
    monkeypatch.setenv("CATEGORY_MIN_CONFIDENCE", "0.99")
    strict = load_category_model()
    assert strict is not None
    p = strict.predict(
        "A caller said I am under digital arrest, transfer money to RBI safe account"
    )
    assert p.label == UNDETERMINED and p.abstained and p.argmax_label == "DIGITAL_ARREST"
    monkeypatch.setenv("CATEGORY_MIN_CONFIDENCE", "garbage")
    assert load_category_model().min_confidence == model.min_confidence


def test_headline_metrics_regression_guards(model):
    from sklearn.metrics import accuracy_score, f1_score

    preds = model.predict_many([r["text"] for r in TEST])
    y = [r["label"] for r in TEST]
    argm = [p.argmax_label for p in preds]
    f1 = f1_score(y, argm, average="macro")
    assert f1 >= 0.65, f1  # actual run 0.715 (synthetic, group split)
    assert accuracy_score(y, argm) >= 0.65
    ans = [(p.argmax_label, t) for p, t in zip(preds, y, strict=True) if not p.abstained]
    abst = 1 - len(ans) / len(preds)
    assert 0.05 <= abst <= 0.6, abst
    assert sum(a == t for a, t in ans) / len(ans) >= 0.75  # accuracy on non-abstained
    assert abs(f1 - METRICS["headline_test_group_split"]["macro_f1"]) < 1e-6  # metrics file fresh


def test_hard_negatives_are_rarely_flagged_as_fraud_categories(model):
    hn = [r["text"] for r in TEST if r["hard_negative"] == "true"]
    preds = model.predict_many(hn)
    flagged = [p for p in preds if not p.abstained and p.label != "OTHER"]
    assert len(hn) >= 20 and len(flagged) / len(hn) <= 0.30


def test_per_slice_sanity(model):
    for lang, floor in (("en", 0.6), ("hi", 0.6), ("dv", 0.45)):
        rs = [r for r in TEST if r["language"] == lang]
        preds = model.predict_many([r["text"] for r in rs])
        acc = sum(p.argmax_label == r["label"] for p, r in zip(preds, rs, strict=True)) / len(rs)
        assert acc >= floor, (lang, acc)


def test_noisy_text_degrades_gracefully(model):
    import random

    rng = random.Random(5)
    rs = TEST[:300]
    noisy = [synth.perturb(r["text"], rng, 0.15) for r in rs]
    p = model.predict_many(noisy)
    acc = sum(x.argmax_label == r["label"] for x, r in zip(p, rs, strict=True)) / len(rs)
    assert acc >= 0.55


def test_metrics_file_documents_leakage_gap_and_synthetic_label():
    assert METRICS["DATA_IS_100_PERCENT_SYNTHETIC"] is True
    lk = METRICS["leaky_random_split"]
    assert "NOT_THE_HEADLINE" in lk
    assert lk["macro_f1"] > METRICS["headline_test_group_split"]["macro_f1"] + 0.15
    assert METRICS["optional_fields"]["structured_fields_used_as_features"] is False
    assert set(METRICS["language_slices"]) == {"en", "hi", "dv"}
    cm = METRICS["headline_test_group_split"]["confusion_matrix"]
    assert len(cm["rows_true_cols_pred"]) == 9


def test_training_splits_are_grouped_with_no_overlap():
    d = METRICS["dataset"]
    assert sum(d["rows_per_split"].values()) == d["rows"] == len(ROWS)
    assert (
        METRICS["dataset"]["sha256"] == hashlib.sha256(synth.DEFAULT_CSV.read_bytes()).hexdigest()
    )
