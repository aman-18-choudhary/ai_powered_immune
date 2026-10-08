import math
from datetime import timedelta

import pytest
from scam_contracts.models import CallRisk

from txn_guard import model as model_mod
from txn_guard.decision import HOLD_AT, STEP_UP_AT, decide, make_decision
from txn_guard.features import extract_features
from txn_guard.model import ARTIFACT_PATH, RULES_VERSION, Scorer

from .conftest import T0


def test_constants():
    assert (STEP_UP_AT, HOLD_AT) == (0.5, 0.8)


def test_boundary_scores():
    assert decide(0.0) == "allow"
    assert decide(0.49) == "allow"
    assert decide(0.5) == "step_up"
    assert decide(0.79) == "step_up"
    assert decide(0.8) == "hold_verify"
    assert decide(1.0) == "hold_verify"
    assert decide(float("nan")) == "step_up"  # unknown score: friction, not a silent allow


def _risk(score=0.9, payer="payer_1", ts=T0 - timedelta(minutes=2)):
    return CallRisk(
        call_id="c", victim_token=payer, score=score, reasons=[], model_version="x", ts=ts
    )


def _score(scorer, store, txn):
    return scorer.score(extract_features(txn, store.context_for(txn, T0)))


@pytest.fixture(scope="module")
def scorer():
    s = Scorer()
    assert not s.fallback_mode, "committed artifact must load"
    return s


@pytest.mark.parametrize("use_model", [True, False])
def test_call_risk_raises_score(use_model, scorer, make_txn, warm_store):
    s = scorer if use_model else Scorer(path=ARTIFACT_PATH.parent / "missing.joblib")
    t = make_txn(amount="1500", age=6)  # known payee, young account: unsaturated base score
    base, _ = _score(s, warm_store, t)
    warm_store.record_call_risk(_risk())
    withcall, reasons = _score(s, warm_store, t)
    assert withcall > base
    assert "ACTIVE_SCAM_CALL" in {r.code for r in reasons}


@pytest.mark.parametrize("use_model", [True, False])
def test_call_risk_plus_anomaly_reaches_hold(use_model, scorer, make_txn, warm_store):
    s = scorer if use_model else Scorer(path=ARTIFACT_PATH.parent / "missing.joblib")
    warm_store.record_call_risk(_risk(0.85))
    young = make_txn(amount="45000", age=5, payee="p_mule")
    assert decide(_score(s, warm_store, young)[0]) == "hold_verify"
    big_old = make_txn(amount="95000", age=800, payee="p_old_new_to_payer")  # amount anomaly only
    assert decide(_score(s, warm_store, big_old)[0]) == "hold_verify"


@pytest.mark.parametrize("use_model", [True, False])
def test_call_risk_alone_with_known_payee_at_most_step_up(use_model, scorer, make_txn, warm_store):
    s = scorer if use_model else Scorer(path=ARTIFACT_PATH.parent / "missing.joblib")
    warm_store.record_call_risk(_risk(0.95))
    t = make_txn(amount="500")  # known payee, ordinary amount, old payee account
    score, _ = _score(s, warm_store, t)
    assert decide(score) in ("allow", "step_up")


def test_ordinary_txn_allowed_with_reasons(scorer, make_txn, warm_store):
    t = make_txn(amount="450")
    score, reasons = _score(scorer, warm_store, t)
    assert decide(score) == "allow" and reasons and 0.0 <= score <= 1.0
    assert reasons[0].code == "NO_RISK_INDICATORS"


def test_reasons_carry_no_raw_identifiers(scorer, make_txn, warm_store):
    warm_store.record_call_risk(_risk())
    t = make_txn(amount="45000", age=3, payee="p_secret_payee", device="dev_secret")
    _, reasons = _score(scorer, warm_store, t)
    text = " ".join(r.detail + r.code for r in reasons)
    for raw in ("p_secret_payee", "dev_secret", "payer_1", t.txn_id, "bank_x"):
        assert raw not in text
    assert 1 <= len(reasons) <= 4 and all(r.detail and r.weight >= 0 for r in reasons)


def test_fallback_when_model_missing(tmp_path, make_txn, warm_store):
    s = Scorer(path=tmp_path / "nope.joblib")
    assert s.fallback_mode and s.model_version == RULES_VERSION == "rules-fallback-v1"
    t = make_txn(amount="45000", age=3, payee="p_new")
    score, reasons, version = s.score_with_version(
        extract_features(t, warm_store.context_for(t, T0))
    )
    assert version == "rules-fallback-v1" and reasons and 0 <= score <= 1


def test_fallback_when_artifact_tampered(tmp_path):
    p = tmp_path / "gbm-v1.joblib"
    p.write_bytes(ARTIFACT_PATH.read_bytes() + b"x")
    assert Scorer(path=p).fallback_mode


def test_fallback_when_sklearn_version_mismatch(monkeypatch):
    import sklearn

    monkeypatch.setattr(sklearn, "__version__", "0.0.1")
    assert Scorer().fallback_mode


def test_fallback_on_wrong_pin_or_smoke_failure(monkeypatch):
    assert Scorer(expected_sha256="0" * 64).fallback_mode
    monkeypatch.setattr(model_mod, "SMOKE_SCAM_MIN", 1.01)
    assert Scorer().fallback_mode


def test_runtime_predict_failure_falls_back_per_call(make_txn, warm_store, caplog):
    s = Scorer()
    assert not s.fallback_mode

    def boom(*_a, **_k):
        raise RuntimeError("predict exploded")

    s._model.model.predict_proba = boom  # type: ignore[union-attr]
    t = make_txn(amount="45000", age=3, payee="p_new")
    score, reasons, version = s.score_with_version(
        extract_features(t, warm_store.context_for(t, T0))
    )
    assert version == "rules-fallback-v1" and reasons and s.model_errors == 1
    assert 0 <= score <= 1


def test_future_dated_is_suspicious_but_scored(scorer, make_txn, warm_store):
    t = make_txn(ts=T0 + timedelta(hours=2))
    score, reasons = scorer.score(extract_features(t, warm_store.context_for(t, T0)))
    assert decide(score) in ("step_up", "hold_verify")
    assert "FUTURE_DATED_TIMESTAMP" in {r.code for r in reasons}


def test_future_dated_and_day_boundary_ts_scored(scorer, make_txn, warm_store):
    from datetime import datetime, timezone

    ist = timezone(timedelta(hours=5, minutes=30))
    for ts in (
        datetime(2026, 3, 10, 23, 59, 59, tzinfo=ist),
        datetime(2026, 3, 11, 0, 0, 0, tzinfo=ist),
        T0 + timedelta(days=3650),
    ):
        t = make_txn(ts=ts)
        score, reasons = scorer.score(extract_features(t, warm_store.context_for(t, T0)))
        assert math.isfinite(score) and 0 <= score <= 1 and reasons


def test_scorer_sanitises_nonfinite_features(scorer, make_txn, ctx_empty):
    f = extract_features(make_txn(), ctx_empty)
    f["amount_zscore"] = float("nan")
    f["velocity_amount_1h"] = float("inf")
    score, reasons = scorer.score(f)
    assert math.isfinite(score) and 0 <= score <= 1 and reasons


def test_make_decision_shape_and_logging(scorer, make_txn, warm_store, caplog):
    import logging

    t = make_txn(amount="45000", age=3, payee="p_secretive")
    f = extract_features(t, warm_store.context_for(t, T0))
    with caplog.at_level(logging.INFO, logger="txn_guard"):
        d = make_decision(t.txn_id, f, scorer, ts=T0)
    assert d.txn_id == t.txn_id and d.model_version == "gbm-v1" and d.reasons
    assert d.decision == decide(d.score)
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert t.txn_id in logged and "p_secretive" not in logged and "payer_1" not in logged


def test_reasons_off_and_batch_paths_give_identical_decisions(make_txn):
    from txn_guard.decision import make_decisions
    from txn_guard.history import InMemoryHistoryStore

    scorer = Scorer()
    store = InMemoryHistoryStore()
    items = []
    for i, (amt, age) in enumerate([("450", 900), ("95000", 3), ("30000", 900), ("1200", 5)]):
        t = make_txn(amount=amt, age=age, payee=f"p{i}", ts=T0 + timedelta(minutes=i))
        store.record_call_risk(
            CallRisk(call_id="c", victim_token="payer_1", score=0.9, reasons=[],
                     model_version="t", ts=T0 + timedelta(minutes=i))
        )  # fmt: skip
        f = extract_features(t, store.context_for(t, t.ts))
        items.append((t.txn_id, f, t.ts))
        store.record_txn(t)
    full = [make_decision(i, f, scorer, ts) for i, f, ts in items]
    lean = [make_decision(i, f, scorer, ts, with_reasons=False) for i, f, ts in items]
    batch = make_decisions(items, scorer)
    for a, b, c in zip(full, lean, batch, strict=True):
        assert (a.decision, a.score) == (b.decision, b.score) == (c.decision, c.score)
