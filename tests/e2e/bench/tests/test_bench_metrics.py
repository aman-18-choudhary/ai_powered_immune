import math
from datetime import timedelta
from decimal import Decimal

import pytest
from helpers import T0, call_risk, campaign, decision, truth_of, txn

from scam_bench.metrics import (
    Metrics,
    campaign_protection,
    compute_metrics,
    lead_time,
    lead_time_detail,
    percentile,
    victims_protected_fraction,
    wilson_interval,
)


def _world():
    camp = campaign("c1", 3)  # 3 victim transfers + 1 mule forward; rails all UPI
    benign = [txn(f"b{i}", rail=("UPI", "IMPS", "NEFT")[i % 3], minutes=i) for i in range(30)]
    txns = {t.txn_id: t for t in camp.txns + benign}
    return camp, benign, txns, truth_of(camp)


def test_perfect_detection_metrics():
    camp, benign, txns, truth = _world()
    ds = [decision(t, "hold_verify") for t in camp.txns] + [decision(t, "allow") for t in benign]
    m = compute_metrics(ds, truth, txns)
    assert isinstance(m, Metrics)
    assert (m.precision, m.recall, m.f1, m.fpr, m.held_benign_rate) == (1.0, 1.0, 1.0, 0.0, 0.0)
    assert (m.tp, m.fp, m.fn, m.tn) == (4, 0, 0, 30)


def test_fpr_computed_on_benign_only():
    camp, benign, txns, truth = _world()
    ds = [decision(t, "hold_verify") for t in camp.txns]  # all scam caught
    ds += [decision(t, "hold_verify") for t in benign[:3]]  # 3 of 30 benign held
    ds += [decision(t, "allow") for t in benign[3:]]
    m = compute_metrics(ds, truth, txns)
    assert m.fpr == pytest.approx(3 / 30)  # not 3 / 34
    assert m.held_benign_rate == pytest.approx(3 / 30)
    assert m.precision == pytest.approx(4 / 7)
    assert m.recall == 1.0


def test_step_up_vs_hold_operating_points():
    camp, benign, txns, truth = _world()
    ds = [decision(camp.txns[0], "hold_verify"), decision(camp.txns[1], "step_up")]
    ds += [decision(t, "allow") for t in camp.txns[2:]]
    ds += [decision(benign[0], "step_up"), decision(benign[1], "hold_verify")]
    ds += [decision(t, "allow") for t in benign[2:]]
    m = compute_metrics(ds, truth, txns)
    assert (m.tp, m.fp) == (1, 1)  # hold only
    assert m.recall == pytest.approx(1 / 4)
    assert (m.flagged_tp, m.flagged_fp) == (2, 2)  # step_up counts as flagged
    assert m.flagged_recall == pytest.approx(2 / 4)
    assert m.flagged_precision == pytest.approx(2 / 4)
    assert m.flagged_fpr == pytest.approx(2 / 30)
    assert m.fpr == pytest.approx(1 / 30)
    assert m.step_up_benign_rate == pytest.approx(1 / 30)


def test_empty_denominators_are_none_not_errors():
    camp, benign, txns, truth = _world()
    only_benign = compute_metrics([decision(t, "allow") for t in benign], truth, txns)
    assert only_benign.recall is None and only_benign.f1 is None
    assert only_benign.precision is None  # nothing predicted positive
    assert only_benign.fpr == 0.0
    only_scam = compute_metrics([decision(t, "hold_verify") for t in camp.txns], truth, txns)
    assert only_scam.fpr is None and only_scam.held_benign_rate is None
    assert only_scam.precision == 1.0
    empty = compute_metrics([], truth, txns)
    for v in (empty.precision, empty.recall, empty.f1, empty.fpr, empty.flagged_precision):
        assert v is None
    assert wilson_interval(0, 0) is None
    assert empty.ci["fpr"] is None


def test_wilson_interval_sanity():
    lo, hi = wilson_interval(50, 100)
    assert (
        lo < 0.5 < hi
        and lo == pytest.approx(0.4038, abs=1e-3)
        and hi == pytest.approx(0.5962, abs=1e-3)
    )
    lo0, hi0 = wilson_interval(0, 1000)  # zero events: lower bound exactly 0, upper > 0
    assert lo0 == 0.0 and 0 < hi0 < 0.01
    lo1, hi1 = wilson_interval(10, 10)
    assert hi1 == 1.0 and lo1 < 1.0
    assert wilson_interval(5, 50)[1] - wilson_interval(5, 50)[0] > (
        wilson_interval(50, 500)[1] - wilson_interval(50, 500)[0]
    )
    with pytest.raises(ValueError):
        wilson_interval(11, 10)


def test_cis_present_and_bracket_point_estimates():
    camp, benign, txns, truth = _world()
    ds = (
        [decision(t, "hold_verify") for t in camp.txns[:3]]
        + [decision(camp.txns[3], "allow")]
        + [decision(t, "allow") for t in benign]
    )
    m = compute_metrics(ds, truth, txns)
    for name in ("precision", "recall", "fpr", "held_benign_rate"):
        lo, hi = m.ci[name]
        assert lo <= getattr(m, name) <= hi


def test_per_rail_breakdown_sums_to_totals():
    camp, benign, txns, truth = _world()
    ds = [decision(t, "hold_verify") for t in camp.txns[:2]] + [decision(camp.txns[2], "allow")]
    ds += [decision(camp.txns[3], "step_up")]
    ds += [decision(t, "hold_verify" if i % 7 == 0 else "allow") for i, t in enumerate(benign)]
    m = compute_metrics(ds, truth, txns)
    assert set(m.by_rail) == {"UPI", "IMPS", "NEFT"}
    for f in ("tp", "fp", "fn", "tn", "flagged_tp", "flagged_fp", "n_benign", "n_scam"):
        assert sum(getattr(r, f) for r in m.by_rail.values()) == getattr(m, f)
    assert m.by_rail["NEFT"].n_scam == 0 and m.by_rail["NEFT"].recall is None


def test_per_role_breakdown():
    camp, benign, txns, truth = _world()
    ds = [
        decision(t, "hold_verify")
        for t in camp.txns
        if truth.txn_role(t.txn_id) == "victim_transfer"
    ]
    ds += [decision(camp.txns[3], "step_up")]
    ds += [decision(t, "allow") for t in benign]
    m = compute_metrics(ds, truth, txns)
    v, mule = m.by_role["victim_transfer"], m.by_role["mule_forward"]
    assert (v.n, v.held, v.recall) == (3, 3, 1.0)
    assert (mule.n, mule.held, mule.recall) == (1, 0, 0.0)
    assert mule.flagged_recall == 1.0
    assert v.n + mule.n == m.n_scam


def test_without_txns_no_rail_breakdown():
    camp, benign, _, truth = _world()
    m = compute_metrics([decision(t, "allow") for t in benign], truth)
    assert m.by_rail == {}


def test_lead_time_positive_when_early():
    camp = campaign("c1", 12)  # victims every 10 min -> 10th victim at +90 min
    truth = truth_of(camp)
    ds = [decision(camp.txns[2], "hold_verify")]  # detected at +20 min
    lt = lead_time("c1", ds, truth)
    assert lt == timedelta(minutes=70)
    detail = lead_time_detail("c1", ds, truth)
    assert detail.campaign_detected_before_mass is True


def test_lead_time_negative_when_late_and_none_when_undetected():
    camp = campaign("c1", 12)
    truth = truth_of(camp)
    late = lead_time("c1", [decision(camp.txns[11], "hold_verify")], truth)
    assert late == timedelta(minutes=-20)
    assert (
        lead_time_detail(
            "c1", [decision(camp.txns[11], "hold_verify")], truth
        ).campaign_detected_before_mass
        is False
    )
    assert lead_time("c1", [decision(camp.txns[0], "allow")], truth) is None
    d = lead_time_detail("c1", [], truth)
    assert d.reaches_mass and not d.detected and not d.campaign_detected_before_mass


def test_lead_time_none_for_campaign_below_ten_victims():
    camp = campaign("small", 9)
    truth = truth_of(camp)
    ds = [decision(camp.txns[0], "hold_verify")]
    assert lead_time("small", ds, truth) is None
    d = lead_time_detail("small", ds, truth)
    assert d.reaches_mass is False and d.detected is True
    assert d.campaign_detected_before_mass is False


def test_lead_time_other_campaigns_and_benign_ignored_and_calls_optional():
    c1, c2 = campaign("c1", 12), campaign("c2", 12)
    truth = truth_of(c1, c2)
    b = txn("benign", minutes=0)
    ds = [decision(b, "hold_verify"), decision(c2.txns[0], "hold_verify")]
    assert lead_time("c1", ds, truth) is None
    # call-risk detections count only when include_calls and the call belongs to the campaign
    c1.calls.append(_call("c1-call", c1))
    truth = truth_of(c1, c2)
    risks = [call_risk("c1-call", 5), call_risk("unknown-call", 1)]
    assert lead_time("c1", risks, truth) is None
    assert lead_time("c1", risks, truth, include_calls=True) == timedelta(minutes=85)
    low = [call_risk("c1-call", 5, score=0.3)]
    assert lead_time("c1", low, truth, include_calls=True) is None


def _call(call_id, camp):
    from scam_contracts.models import CallEvent

    return CallEvent(
        call_id=call_id, idempotency_key="k", victim_token="v", caller_number_hash="h",
        ts=T0, transcript_chunk="x", channel="pstn", lang="en",
    )  # fmt: skip


def test_victims_protected_fraction_hand_built():
    camp = campaign("c1", 5)  # victims v0..v4 at +0,10,20,30,40 min, Rs 1000..5000
    truth = truth_of(camp)
    victims = [t for t in camp.txns if t.txn_id.startswith("c1-v")]
    txns = {t.txn_id: t for t in camp.txns}
    ds = [
        decision(victims[0], "allow"),  # missed, before detection
        decision(victims[1], "hold_verify"),  # first detection at +10
        decision(victims[2], "allow"),  # after detection but not held
        decision(victims[3], "hold_verify"),
        decision(victims[4], "step_up"),  # step_up does not protect
        decision(camp.txns[5], "hold_verify"),  # mule forward: not a victim transfer
    ]
    assert victims_protected_fraction("c1", ds, txns, truth) == pytest.approx(1 / 3)
    p = campaign_protection("c1", ds, txns, truth)
    assert p.victim_txns_after_detection == 3 and p.victim_txns_held_after_detection == 1
    assert p.money_at_risk_inr == Decimal("12000")
    assert p.money_prevented_inr == Decimal("4000")
    assert isinstance(p.money_prevented_inr, Decimal)


def test_victims_protected_none_without_detection_or_victims():
    camp = campaign("c1", 3)
    truth = truth_of(camp)
    txns = {t.txn_id: t for t in camp.txns}
    assert victims_protected_fraction("c1", [], txns, truth) is None
    # detection on the last victim transfer: nothing left to protect after it
    last_victim = [t for t in camp.txns if t.txn_id == "c1-v2"][0]
    assert (
        victims_protected_fraction("c1", [decision(last_victim, "hold_verify")], txns, truth)
        is None
    )


def test_victims_protected_uses_call_detection_when_asked():
    camp = campaign("c1", 3)
    camp.calls.append(_call("c1-call", camp))
    truth = truth_of(camp)
    txns = {t.txn_id: t for t in camp.txns}
    ds = [decision(t, "hold_verify") for t in camp.txns if t.txn_id in ("c1-v1", "c1-v2")]
    risks = [call_risk("c1-call", -1)]  # alert before every victim transfer
    f = victims_protected_fraction("c1", ds + risks, txns, truth, include_calls=True)
    assert f == pytest.approx(2 / 3)


def test_percentile():
    assert percentile([], 50) is None
    xs = list(range(1, 101))
    assert percentile(xs, 50) == pytest.approx(50.5)
    assert percentile(xs, 99) == pytest.approx(99.01)
    assert not math.isnan(percentile([3.0], 99))
