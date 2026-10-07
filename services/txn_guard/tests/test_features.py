import math
from datetime import UTC, datetime, timedelta, timezone

from scam_contracts.models import CallRisk

from txn_guard.features import FEATURE_NAMES, extract_features
from txn_guard.history import Context, InMemoryHistoryStore

from .conftest import T0

IST = timezone(timedelta(hours=5, minutes=30))


def _risk(ts, score, payer="payer_1"):
    return CallRisk(
        call_id="c", victim_token=payer, score=score, reasons=[], model_version="x", ts=ts
    )


def test_no_history_never_divides_by_zero(make_txn, ctx_empty):
    f = extract_features(make_txn(), ctx_empty)
    assert list(f) == list(FEATURE_NAMES)
    assert all(math.isfinite(v) for v in f.values())
    assert f["amount_zscore"] == 0.0 and f["new_payee"] == 1.0 and f["device_novel"] == 0.0
    assert f["velocity_count_1h"] == 0.0 and f["active_call_risk"] == 0.0


def test_pure_and_deterministic(make_txn, warm_store):
    t = make_txn(amount="90000", age=3, payee="p_new")
    a = extract_features(t, warm_store.context_for(t, T0))
    b = extract_features(t, warm_store.context_for(t, T0))
    assert a == b


def test_amount_anomaly_and_new_payee(make_txn, warm_store):
    ordinary = make_txn(amount="500")
    anomalous = make_txn(amount="95000", age=4, payee="p_new")
    fo = extract_features(ordinary, warm_store.context_for(ordinary, T0))
    fa = extract_features(anomalous, warm_store.context_for(anomalous, T0))
    assert abs(fo["amount_zscore"]) < 3 and fa["amount_zscore"] > 5
    assert fa["amount_vs_typical_log"] > 4 and fo["amount_vs_typical_log"] < 1
    assert fa["new_payee"] == 1.0 and fo["new_payee"] == 0.0 and fa["payee_age_days"] == 4.0
    assert fa["payee_age_young"] == 1.0 and fo["payee_age_young"] == 0.0


def test_active_call_risk_window_and_max(make_txn, warm_store):
    t = make_txn()
    warm_store.record_call_risk(_risk(T0 - timedelta(minutes=14), 0.75))
    warm_store.record_call_risk(_risk(T0 - timedelta(minutes=3), 0.9))
    warm_store.record_call_risk(_risk(T0 - timedelta(minutes=16), 0.99))  # too old
    warm_store.record_call_risk(_risk(T0 - timedelta(minutes=2), 0.99, payer="other"))
    assert extract_features(t, warm_store.context_for(t, T0))["active_call_risk"] == 0.9
    t2 = make_txn(ts=T0 + timedelta(minutes=30))  # all risks stale by then
    assert extract_features(t2, warm_store.context_for(t2, T0))["active_call_risk"] == 0.0


def test_velocity_windows(make_txn):
    s = InMemoryHistoryStore()
    for m in (100, 50, 20, 10):
        s.record_txn(make_txn(amount="1000", ts=T0 - timedelta(minutes=m)))
    s.record_txn(make_txn(amount="1000", ts=T0 - timedelta(hours=5)))
    t = make_txn(amount="1000")
    f = extract_features(t, s.context_for(t, T0))
    assert f["velocity_count_1h"] == 3.0 and f["velocity_count_24h"] == 5.0
    assert math.isclose(f["velocity_amount_1h"], math.log1p(3000.0))
    assert math.isclose(f["velocity_amount_24h"], math.log1p(5000.0))


def test_device_novelty_and_antibody_placeholder(make_txn, warm_store):
    t = make_txn(device="dev_new")
    f = extract_features(t, warm_store.context_for(t, T0))
    assert f["device_novel"] == 1.0 and f["payee_in_antibody"] == 0.0
    g = extract_features(make_txn(), warm_store.context_for(make_txn(), T0))
    assert g["device_novel"] == 0.0


def test_ist_hour_and_day_boundary(make_txn, ctx_empty):
    before = make_txn(ts=datetime(2026, 3, 10, 23, 59, 59, tzinfo=IST))
    after = make_txn(ts=datetime(2026, 3, 11, 0, 0, 0, tzinfo=IST))
    utc_midnight_ist = make_txn(ts=datetime(2026, 3, 10, 18, 30, 0, tzinfo=UTC))  # 00:00 IST
    c = Context(now=datetime(2026, 3, 11, 1, 0, tzinfo=IST))
    fb, fa, fu = (extract_features(t, c) for t in (before, after, utc_midnight_ist))
    assert (fb["hour_ist"], fb["night"]) == (23.0, 0.0)
    assert (fa["hour_ist"], fa["night"]) == (0.0, 1.0)
    assert fu["hour_ist"] == 0.0 and fu["night"] == 1.0


def test_future_dated_flagged_not_crashing(make_txn):
    now = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    ok = make_txn(ts=now + timedelta(minutes=4))
    bad = make_txn(ts=now + timedelta(minutes=6))
    far = make_txn(ts=now + timedelta(days=400))
    c = Context(now=now)
    assert extract_features(ok, c)["future_dated"] == 0.0
    assert extract_features(bad, c)["future_dated"] == 1.0
    f = extract_features(far, c)
    assert f["future_dated"] == 1.0 and all(math.isfinite(v) for v in f.values())


def test_nan_inf_sanitised(make_txn):
    c = Context(
        now=T0,
        payer_n=10,
        payer_log_mean=float("nan"),
        payer_log_std=float("inf"),
        recent=((T0 - timedelta(minutes=5), float("inf")),),
        call_risks=((T0 - timedelta(minutes=1), float("nan")),),
    )
    f = extract_features(make_txn(), c)
    assert all(math.isfinite(v) for v in f.values())
    assert f["active_call_risk"] == 0.0
