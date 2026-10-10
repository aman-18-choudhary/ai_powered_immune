import random
from datetime import timedelta
from decimal import Decimal

import pytest

from graph_twin.config import Settings
from graph_twin.graph import account_key
from graph_twin.takedown import (
    TooLarge,
    best_takedown,
    best_takedown_exhaustive,
    extract_flow_input,
    simulate_takedown,
    takedown_timeline,
)

from .conftest import T0, h, make_campaign, txn


@pytest.fixture
def ring_setup(twin, clock):
    c = make_campaign(twin, clock, victims=6, mules=3)
    twin.detect_dirty()
    (ring,) = twin.registry.rings.values()
    return twin, ring, c


def test_harm_is_the_exact_projected_flow(ring_setup):
    twin, ring, c = ring_setup
    res = simulate_takedown(twin.tg.g, ring, [], twin.cfg)
    # 6 victims x INR 50,000 within the 1-day floor window, projected 7 days, all swept: exact
    assert res.harm_before_inr == Decimal("2100000.00")
    assert res.harm_after_inr == res.harm_before_inr and res.reduction_pct == 0.0
    assert isinstance(res.harm_before_inr, Decimal)


def test_horizon_scales_the_projection(ring_setup):
    twin, ring, _ = ring_setup
    cfg = Settings(harm_horizon_days=1)
    assert simulate_takedown(twin.tg.g, ring, [], cfg).harm_before_inr == Decimal("300000.00")


def test_removing_all_cash_out_nodes_reduces_harm_to_zero(ring_setup):
    twin, ring, c = ring_setup
    res = simulate_takedown(twin.tg.g, ring, [account_key(c["cash"])], twin.cfg)
    assert res.harm_after_inr == 0 and res.reduction_pct == 100.0
    assert res.edges_cut and all(account_key(c["cash"]) in e for e in res.edges_cut)


def test_removing_an_irrelevant_node_changes_nothing(ring_setup):
    twin, ring, _ = ring_setup
    res = simulate_takedown(twin.tg.g, ring, [account_key(h("not-in-ring"))], twin.cfg)
    assert res.reduction_pct == 0.0 and res.edges_cut == () and res.removed == ()


def test_removing_a_victim_reduces_only_a_little(ring_setup):
    twin, ring, c = ring_setup
    res = simulate_takedown(twin.tg.g, ring, [account_key(c["victims"][0])], twin.cfg)
    assert 0 < res.reduction_pct < 25  # one of six victims: 1/6 of the flow


def test_removing_a_mule_cuts_its_share(ring_setup):
    twin, ring, c = ring_setup
    res = simulate_takedown(twin.tg.g, ring, [account_key(c["mules"][0])], twin.cfg)
    assert res.reduction_pct == pytest.approx(33.33, abs=0.01)


def test_reduction_is_monotone_as_the_removal_set_grows(ring_setup):
    twin, ring, c = ring_setup
    nodes = [account_key(x) for x in (*c["victims"], *c["mules"], c["cash"], "extra")]
    rnd = random.Random(5)
    for _ in range(10):
        rnd.shuffle(nodes)
        last = -1.0
        for i in range(len(nodes) + 1):
            r = simulate_takedown(twin.tg.g, ring, nodes[:i], twin.cfg).reduction_pct
            assert r >= last
            last = r


def test_greedy_k1_beats_a_random_node_and_is_the_cash_out(ring_setup):
    twin, ring, c = ring_setup
    best = best_takedown(twin.tg.g, ring, 1, twin.cfg)
    assert best == [account_key(c["cash"])]
    best_red = simulate_takedown(twin.tg.g, ring, best, twin.cfg).reduction_pct
    rnd = random.Random(3)
    pool = [account_key(x) for x in (*c["victims"], *c["mules"])]
    for _ in range(8):
        other = simulate_takedown(twin.tg.g, ring, [rnd.choice(pool)], twin.cfg).reduction_pct
        assert best_red > other


def test_greedy_is_deterministic_and_matches_exhaustive_here(ring_setup):
    twin, ring, _ = ring_setup
    for k in (1, 2, 3):
        a = best_takedown(twin.tg.g, ring, k, twin.cfg)
        assert a == best_takedown(twin.tg.g, ring, k, twin.cfg)
        ex = best_takedown_exhaustive(twin.tg.g, ring, k, twin.cfg)
        ra = simulate_takedown(twin.tg.g, ring, a, twin.cfg).reduction_pct
        rb = simulate_takedown(twin.tg.g, ring, ex, twin.cfg).reduction_pct
        assert ra >= 0.9 * rb


def test_exhaustive_refuses_big_inputs(ring_setup):
    twin, ring, _ = ring_setup
    with pytest.raises(ValueError):
        best_takedown_exhaustive(twin.tg.g, ring, 4, twin.cfg)


def test_ring_too_large_for_the_flow_network(ring_setup):
    twin, ring, _ = ring_setup
    with pytest.raises(TooLarge):
        extract_flow_input(twin.tg.g, ring, Settings(max_ring_nodes=5))


def test_victims_beyond_the_cap_are_folded_not_dropped(ring_setup):
    twin, ring, c = ring_setup
    cfg = Settings(flow_victim_cap=2)
    fi = extract_flow_input(twin.tg.g, ring, cfg)
    assert sum(1 for s, _, _ in fi.edges if s not in ring.members) == 2
    assert simulate_takedown(twin.tg.g, ring, [], cfg).harm_before_inr == Decimal("2100000.00")


def test_longer_observed_window_lowers_the_projection(twin, clock):
    c = make_campaign(twin, clock, victims=6, mules=3)
    twin.detect_dirty()  # the ring exists before the late payment
    # the ring has been active for 3 days: the same totals imply a third of the daily rate
    later = T0 + timedelta(days=3)
    clock.set(later)
    twin.apply_txn(txn(h("late"), c["mules"][0], later, 1000))
    twin.detect_dirty()
    (ring,) = twin.registry.rings.values()
    res = simulate_takedown(twin.tg.g, ring, [], twin.cfg)
    assert res.harm_before_inr < Decimal("2100000.00") * Decimal("0.5")


def test_timeline_uses_only_edges_seen_before_the_freeze(twin, store, clock):
    c = make_campaign(twin, clock, victims=12, mules=3)
    twin.detect_dirty()
    (ring,) = twin.registry.rings.values()
    freeze = T0 + timedelta(minutes=12)  # after 6 victims, before the sweeps
    res = takedown_timeline(store, ring, freeze, [account_key(c["mules"][0])], twin.cfg)
    # before any sweep the money is parked in the mules: freezing one removes its third of it
    assert res.harm_before_inr > 0 and res.reduction_pct == pytest.approx(33.33, abs=0.01)
    assert res.observed_after_ts_inr == Decimal("600000")  # hindsight: swept to the cash-out later
    after_sweeps = T0 + timedelta(minutes=2 * 12 + 5 + 3 + 1)
    res2 = takedown_timeline(store, ring, after_sweeps, [account_key(c["cash"])], twin.cfg)
    assert res2.harm_before_inr > 0 and res2.harm_after_inr == 0
    assert res2.observed_after_ts_inr == Decimal(0)
    res3 = takedown_timeline(store, ring, T0 + timedelta(minutes=30), [], twin.cfg)
    assert res3.observed_after_ts_inr is not None and res3.observed_after_ts_inr >= 0
