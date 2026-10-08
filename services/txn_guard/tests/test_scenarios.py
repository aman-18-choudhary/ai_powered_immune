"""Regression scenarios from review: rail blind spot, call-risk + amount anomaly, overlays.
All use the committed artifact. Payer: 60 prior txns, typical about Rs 500, to a known payee."""

from datetime import timedelta

import pytest
from scam_contracts.models import CallRisk

from txn_guard import model as model_mod
from txn_guard.decision import decide
from txn_guard.features import extract_features
from txn_guard.history import InMemoryHistoryStore
from txn_guard.model import Scorer, overlay_applied

from .conftest import T0


@pytest.fixture(scope="module")
def scorer():
    s = Scorer()
    assert not s.fallback_mode
    return s


@pytest.fixture
def store(make_txn):
    st = InMemoryHistoryStore()
    for i in range(60):
        st.record_txn(
            make_txn(
                amount=str(380 + (i * 29) % 240),
                ts=T0 - timedelta(days=20) + timedelta(hours=i * 7),
            )
        )
    return st


def _decide(scorer, store, txn, now=T0):
    score, reasons = scorer.score(extract_features(txn, store.context_for(txn, now)))
    return decide(score), score, reasons


def _call(store, score=0.9, ts=T0 - timedelta(minutes=2)):
    store.record_call_risk(
        CallRisk(
            call_id="c", victim_token="payer_1", score=score, reasons=[], model_version="x", ts=ts
        )
    )


CASES = [("UPI", "40000"), ("UPI", "99999"), ("IMPS", "40000"), ("IMPS", "99999"),
         ("IMPS", "200000"), ("IMPS", "500000"), ("NEFT", "40000"), ("NEFT", "99999"),
         ("NEFT", "200000")]  # fmt: skip


@pytest.mark.parametrize(("rail", "amount"), CASES)
def test_young_payee_large_amount_held_on_every_rail_without_call_risk(
    rail, amount, scorer, store, make_txn
):
    t = make_txn(amount=amount, age=3, payee="p_mule", rail=rail)
    d, score, _ = _decide(scorer, store, t)
    assert d == "hold_verify", (rail, amount, score)


def test_same_txn_same_decision_across_rails(scorer, store, make_txn):
    out = {
        r: _decide(scorer, store, make_txn(amount="45000", age=4, payee="p_mule", rail=r))[0]
        for r in ("UPI", "IMPS", "NEFT")
    }
    assert set(out.values()) == {"hold_verify"}


def test_young_payee_floor_has_own_reason_and_model_weights_untouched(scorer, store, make_txn):
    t = make_txn(amount="6000", age=20, payee="p_young")  # z>=3, young, new
    f = extract_features(t, store.context_for(t, T0))
    score, reasons = scorer.score(f)
    assert score >= 0.5
    model_score = scorer._model.proba(
        __import__("numpy").array([[f[n] for n in model_mod.MODEL_FEATURES]])
    )[0]
    if model_score < 0.5:  # overlay lifted it: own reason, flagged, weights = lift only
        codes = {r.code for r in reasons}
        assert "YOUNG_PAYEE_LARGE_AMOUNT_FLOOR" in codes and overlay_applied(reasons)
        floor = next(r for r in reasons if r.code == "YOUNG_PAYEE_LARGE_AMOUNT_FLOOR")
        assert abs(floor.weight - (score - model_score)) < 1e-3
        assert all(r.weight <= model_score + 1e-4 for r in reasons if r is not floor)


@pytest.mark.parametrize(
    ("rail", "amount", "age", "payee"),
    [
        ("NEFT", "18000", 3000, "payee_known"),  # EMI
        ("IMPS", "25000", 2500, "payee_known"),  # rent
        ("NEFT", "150000", 2000, "payee_known"),  # salary-type to old known payee
        ("NEFT", "150000", 2000, "p_salary_new"),  # salary-type, first time, 2000-day payee
        ("IMPS", "20000", 1460, "p_shop_new"),  # laptop to a 4-year-old new payee
    ],
)
def test_benign_imps_neft_not_held(rail, amount, age, payee, scorer, store, make_txn):
    d, score, _ = _decide(scorer, store, make_txn(amount=amount, age=age, payee=payee, rail=rail))
    assert d in ("allow", "step_up"), (rail, amount, score)


def test_call_risk_with_amount_anomaly_known_payee_at_least_step_up(scorer, store, make_txn):
    _call(store)
    d, score, reasons = _decide(scorer, store, make_txn(amount="40000"))  # known payee, z large
    assert d in ("step_up", "hold_verify") and score >= 0.5
    assert "CALL_RISK_AMOUNT_GUARD" in {r.code for r in reasons}
    assert d == "step_up"  # known, old, not recently new: step_up, not hold


def test_five_split_sequence_all_held(scorer, store, make_txn):
    _call(store)
    decisions = []
    for i in range(5):
        ts = T0 + timedelta(minutes=i)
        t = make_txn(amount="99999", age=1200, payee="p_old_new_to_payer", ts=ts)
        score, _ = scorer.score(extract_features(t, store.context_for(t, ts)))
        decisions.append(decide(score))
        store.record_txn(t)  # recorded as known after the first
        _call(store, ts=ts)
    assert decisions == ["hold_verify"] * 5


def test_repeat_large_to_known_payee_within_hour_held(scorer, store, make_txn):
    _call(store)
    first = make_txn(amount="40000")  # known payee: step_up
    assert _decide(scorer, store, first)[0] == "step_up"
    store.record_txn(first)
    _call(store, ts=T0 + timedelta(minutes=1))
    again = make_txn(amount="40000", ts=T0 + timedelta(minutes=2))
    d = decide(scorer.score(extract_features(again, store.context_for(again, again.ts)))[0])
    assert d == "hold_verify"


def test_call_risk_alone_ordinary_amount_known_payee_at_most_step_up(scorer, store, make_txn):
    _call(store, 0.95)
    assert _decide(scorer, store, make_txn(amount="500"))[0] in ("allow", "step_up")


def test_guard_ignores_small_absolute_amounts(scorer, store, make_txn):
    _call(store, 0.95)
    d, _, reasons = _decide(scorer, store, make_txn(amount="1500"))  # 3x typical, but small
    assert d in ("allow", "step_up")
    assert "CALL_RISK_AMOUNT_GUARD" not in {r.code for r in reasons}


def test_overlay_reason_detail_has_no_raw_ids(scorer, store, make_txn):
    _call(store)
    t = make_txn(amount="45000", age=3, payee="p_secret_payee", device="dev_secret")
    _, _, reasons = _decide(scorer, store, t)
    text = " ".join(r.detail for r in reasons)
    assert "p_secret_payee" not in text and "dev_secret" not in text and "payer_1" not in text


def test_rail_blind_spot_fails_the_load(monkeypatch):
    monkeypatch.setattr(model_mod, "SMOKE_RAIL_SPREAD_MAX", -1.0)
    assert Scorer().fallback_mode


def test_allow_verdict_drops_negligible_reasons(scorer, store, make_txn):
    d, _, reasons = _decide(scorer, store, make_txn(amount="500"))
    assert d == "allow" and all(r.weight >= 0.01 or r.code == "NO_RISK_INDICATORS" for r in reasons)


# ---- fix round 2: test-then-escalate bypass, established payees, extreme amounts ----
def _typical_store(make_txn, amount: str):
    st = InMemoryHistoryStore()
    for i in range(60):
        st.record_txn(make_txn(amount=amount, ts=T0 - timedelta(days=20) + timedelta(hours=i * 7)))
    return st


@pytest.mark.parametrize(
    "delay",
    [timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2), timedelta(days=1),
     timedelta(days=3)],
)  # fmt: skip
@pytest.mark.parametrize(("age", "expected"), [(3, "hold_verify"), (10, "hold_verify"), (25, None)])
def test_test_then_escalate_is_not_a_bypass(delay, age, expected, scorer, store, make_txn):
    store.record_txn(make_txn(amount="1000", age=age, payee="p_young", ts=T0 - delay))
    t = make_txn(amount="90000", age=age, payee="p_young")
    d, score, reasons = _decide(scorer, store, t)
    if expected:
        assert d == expected, (delay, age, score)
    else:
        assert d in ("step_up", "hold_verify"), (delay, age, score)
    assert overlay_applied(reasons) or score >= 0.5


def _pay(store, make_txn, n, first_days_ago, max_amt="20000", age=10):
    for i in range(n):
        d = first_days_ago * (1 - i / max(1, n - 1)) if n > 1 else first_days_ago
        amt = max_amt if i == 0 else str(min(5000, int(max_amt)))
        store.record_txn(
            make_txn(
                amount=amt, age=age, payee="p_contractor", ts=T0 - timedelta(days=d, minutes=1)
            )
        )


@pytest.mark.parametrize(
    ("n", "days", "est"), [(2, 8, 0.0), (3, 8, 1.0), (3, 6, 0.0), (4, 7.5, 1.0)]
)
def test_established_payee_boundaries(n, days, est, store, make_txn):
    _pay(store, make_txn, n, days)
    t = make_txn(amount="30000", age=10, payee="p_contractor")
    assert extract_features(t, store.context_for(t, T0))["payee_established"] == est


@pytest.mark.parametrize("amount", ["30000", "40000"])
def test_established_young_payee_contractor_not_held(amount, scorer, store, make_txn):
    _pay(store, make_txn, 4, 12, max_amt="20000")
    d, score, reasons = _decide(
        scorer, store, make_txn(amount=amount, age=10, payee="p_contractor")
    )
    assert d in ("allow", "step_up"), (amount, score)
    assert "YOUNG_PAYEE_LARGE_AMOUNT_FLOOR" not in {r.code for r in reasons}


def test_escalation_to_young_payee_beyond_10x_prior_max(scorer, store, make_txn):
    _pay(store, make_txn, 4, 12, max_amt="2000")
    d, _, reasons = _decide(scorer, store, make_txn(amount="30000", age=10, payee="p_contractor"))
    assert d in ("step_up", "hold_verify")
    assert "PAYEE_AMOUNT_ESCALATION" in {r.code for r in reasons}
    # established payee: escalation is step_up only (hold needs a not-established payee)
    d2, _, _ = _decide(scorer, store, make_txn(amount="60000", age=10, payee="p_contractor"))
    assert d2 in ("step_up", "hold_verify")


@pytest.mark.parametrize(("rail", "amount"), [("UPI", "95000"), ("NEFT", "300000")])
def test_extreme_amount_to_new_old_payee_is_step_up(rail, amount, scorer, store, make_txn):
    d, score, reasons = _decide(
        scorer, store, make_txn(amount=amount, age=40, payee="p_new40", rail=rail)
    )
    assert d == "step_up", (rail, score)
    assert "NEW_PAYEE_EXTREME_AMOUNT" in {r.code for r in reasons}


def test_salary_and_business_payments_still_allowed(scorer, make_txn):
    d, _, _ = _decide(scorer, _typical_store(make_txn, "40000"),
                      make_txn(amount="150000", age=2000, payee="p_salary", rail="NEFT"))  # fmt: skip
    assert d == "allow"
    d, _, _ = _decide(scorer, _typical_store(make_txn, "50000"),
                      make_txn(amount="90000", age=900, payee="p_vendor", rail="NEFT"))  # fmt: skip
    assert d == "allow"


# ---- 7b: rail-scaled absolute thresholds (NEFT benign false holds) ----
def _neft_store(make_txn, typical="25000"):
    st = InMemoryHistoryStore()
    for i in range(60):
        st.record_txn(
            make_txn(
                amount=typical, rail="NEFT", ts=T0 - timedelta(days=20) + timedelta(hours=i * 7)
            )
        )
    return st


@pytest.mark.parametrize(
    ("label", "amount", "age", "payee"),
    [
        ("EMI to new loan account", "18000", 20, "p_emi"),
        ("rent to new landlord", "25000", 12, "p_rent"),
        ("salary-sized to young account", "150000", 15, "p_salary"),
        ("business payment to young vendor", "90000", 25, "p_vendor"),
        ("supplier payment, 40-day payee", "120000", 40, "p_supplier"),
    ],
)
def test_benign_neft_to_young_or_new_payees_not_held(label, amount, age, payee, scorer, make_txn):
    d, score, _ = _decide(
        scorer, _neft_store(make_txn), make_txn(amount=amount, age=age, payee=payee, rail="NEFT")
    )
    assert d in ("allow", "step_up"), (label, score)


@pytest.mark.parametrize(("amount", "age"), [("200000", 2), ("300000", 5), ("500000", 3)])
def test_anomalous_neft_young_payee_still_held(amount, age, scorer, store, make_txn):
    t = make_txn(amount=amount, age=age, payee="p_mule", rail="NEFT")  # typical Rs 500 payer
    d, score, _ = _decide(scorer, store, t)
    assert d == "hold_verify", (amount, age, score)


def test_neft_3L_to_40_day_payee_extreme_still_step_up(scorer, store, make_txn):
    d, _, reasons = _decide(
        scorer, store, make_txn(amount="300000", age=40, payee="p_n", rail="NEFT")
    )
    assert d in ("step_up", "hold_verify")
    assert "NEW_PAYEE_EXTREME_AMOUNT" in {r.code for r in reasons}


def test_policy_thresholds_are_rail_scaled_but_booster_is_rail_invariant(scorer):
    import numpy as np

    from txn_guard.policy import RAIL_AMOUNT_SCALE, rail_scale

    assert RAIL_AMOUNT_SCALE["UPI"] == RAIL_AMOUNT_SCALE["IMPS"] == 1.0
    assert RAIL_AMOUNT_SCALE["NEFT"] > 1.0
    assert (rail_scale(0.0), rail_scale(1.0), rail_scale(2.0)) == (
        1.0, 1.0, RAIL_AMOUNT_SCALE["NEFT"],
    )  # fmt: skip
    scam = model_mod.SMOKE_SCAM
    rows = np.array(
        [[(scam | {"rail": r})[k] for k in model_mod.MODEL_FEATURES] for r in (0.0, 1.0, 2.0)]
    )
    p = scorer._model.proba(rows)
    assert p.max() - p.min() == 0.0 and p.min() >= 0.5
