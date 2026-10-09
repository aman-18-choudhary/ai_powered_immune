"""Benign look-alikes must not become rings: merchants that settle to one account, with a false
positive flag or two, are pass-through accounts with many payers and a tiny flagged share."""

from datetime import timedelta

from .conftest import T0, decision, h, txn


def _merchant(twin, name: str, settle: str, payers: int, fp: int, start=T0) -> None:
    """``payers`` customers over ~40 min, a settlement of ~93% within the hour, ``fp`` of the
    payments flagged step_up by the (imperfect) upstream model."""
    ids = []
    for i in range(payers):
        t = txn(h(f"{name}-cust{i}"), h(name), start + timedelta(seconds=8 * i), 500, age=900)
        twin.apply_txn(t)
        ids.append(t.txn_id)
    for k in range(fp):
        twin.apply_decision(decision(ids[10 + 17 * k], "step_up"))
    twin.apply_txn(
        txn(h(name), h(settle), start + timedelta(minutes=55), payers * 465, rail="IMPS", age=900)
    )


def test_two_merchants_settling_to_one_account_with_one_fp_each_are_no_ring(twin, clock):
    clock.set(T0 + timedelta(hours=3))
    _merchant(twin, "m1", "settle", 300, 1)
    _merchant(twin, "m2", "settle", 300, 1)
    twin.detect_dirty()
    assert not twin.registry.rings


def test_three_sub_merchants_to_one_aggregator_with_two_fp_each_are_no_ring(twin, clock):
    clock.set(T0 + timedelta(hours=3))
    for n in ("s1", "s2", "s3"):
        _merchant(twin, n, "aggregator", 300, 2)
    twin.detect_dirty()
    assert not twin.registry.rings


def test_a_small_flagged_share_does_not_seed_even_with_many_false_positives(twin, clock):
    """30 flags over 600 payers per merchant is 5%: below GRAPH_MIN_FLAGGED_SHARE (10%)."""
    clock.set(T0 + timedelta(hours=3))
    for n in ("p1", "p2"):
        _merchant(twin, n, "agg", 600, 20)
    twin.detect_dirty()
    assert not twin.registry.rings


def test_campaign_is_detected_early_when_every_payer_of_a_mule_is_a_victim(twin, clock):
    """Positive control: 25 victims over 2 mules, mules sweep to one cash-out as money arrives.
    Detection (after every event) must fire by victim 4 (two payers per mule are needed)."""
    cash = h("pc-cash")
    ms = [h(f"pc-m{i}") for i in range(2)]
    found_at = None
    for i in range(25):
        ts = T0 + timedelta(minutes=6 * i)
        clock.set(ts + timedelta(minutes=30))
        t = txn(h(f"pc-v{i}"), ms[i % 2], ts, 50000, age=4)
        twin.apply_txn(t)
        twin.apply_decision(decision(t.txn_id, "hold_verify"))
        twin.apply_txn(
            txn(ms[i % 2], cash, ts + timedelta(minutes=3), 47000, rail="IMPS", device="dev_pc")
        )
        twin.detect_dirty()
        if twin.registry.rings and found_at is None:
            found_at = i + 1
    assert found_at is not None and found_at <= 4
    assert len(twin.registry.rings) == 1
