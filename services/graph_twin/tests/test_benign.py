"""Benign immunity: signals are required, one false positive is not a ring, structure alone is
not a ring."""

from datetime import timedelta

import pytest

from graph_twin.config import Settings
from graph_twin.evaluate import replay
from graph_twin.rings import detect_rings
from graph_twin.store import GraphStore
from graph_twin.twin import GraphTwin

from .conftest import T0, Clock, decision, h, txn


@pytest.fixture(scope="module")
def benign_world():
    from sim_engine.benign import gen_benign_txns
    from sim_engine.world import build_world

    world = build_world(7301, 500)
    return list(gen_benign_txns(world, 7, 7301)), world.start


def test_simulator_benign_week_yields_zero_rings_even_with_false_positive_holds(benign_world):
    txns, start = benign_world
    clock = Clock(start)
    tw = GraphTwin(GraphStore("sqlite://"), Settings(), clock)
    try:
        replay(tw, clock, txns, detect_every=25)
        flagged = sum(1 for _ in tw.registry.rings)
        seeds = sum(1 for n in tw.tg.g.nodes if tw.tg.g.nodes[n]["s"].susp_in > 0)
        assert len(txns) > 3000
        assert flagged == 0
        assert detect_rings(tw.tg.g, cfg=tw.cfg) == []
        print("benign false-positive seeds:", seeds)
    finally:
        tw.store.close()


def test_popular_merchant_with_huge_fan_in_and_no_signals_is_no_ring(twin, clock):
    clock.set(T0 + timedelta(hours=3))
    for i in range(400):
        twin.apply_txn(txn(h(f"cust{i}"), h("bigshop"), T0 + timedelta(seconds=20 * i), 250))
    # the merchant settles to its own bank account within the hour
    twin.apply_txn(txn(h("bigshop"), h("settle"), T0 + timedelta(hours=2), 99000, rail="IMPS"))
    twin.detect_dirty()
    assert not twin.registry.rings and detect_rings(twin.tg.g, cfg=twin.cfg) == []


def test_merchant_with_one_false_positive_hold_is_no_ring(twin, clock):
    clock.set(T0 + timedelta(hours=3))
    ids = []
    for i in range(60):
        t = txn(h(f"cust{i}"), h("shop"), T0 + timedelta(seconds=30 * i), 900)
        twin.apply_txn(t)
        ids.append(t.txn_id)
    twin.apply_decision(decision(ids[7], "hold_verify"))  # one false positive
    twin.apply_txn(txn(h("shop"), h("settle"), T0 + timedelta(hours=1), 50000, rail="IMPS"))
    twin.detect_dirty()
    assert not twin.registry.rings


def test_merchant_with_two_false_positives_and_a_payout_chain_is_still_no_ring(twin, clock):
    """Two flagged payers meet the signal gate, but a merchant and its settlement account are two
    accounts, below the three-account minimum."""
    clock.set(T0 + timedelta(hours=3))
    ids = []
    for i in range(40):
        t = txn(h(f"cust{i}"), h("shop"), T0 + timedelta(seconds=30 * i), 900)
        twin.apply_txn(t)
        ids.append(t.txn_id)
    for k in (3, 11):
        twin.apply_decision(decision(ids[k], "step_up"))
    twin.apply_txn(txn(h("shop"), h("settle"), T0 + timedelta(hours=1), 33000, rail="IMPS"))
    twin.detect_dirty()
    assert not twin.registry.rings


def test_payroll_chain_of_established_payees_without_signals_is_no_ring(twin, clock):
    clock.set(T0 + timedelta(hours=3))
    twin.apply_txn(txn(h("company"), h("payroll"), T0, 900000, rail="NEFT", age=2000))
    for i in range(8):
        twin.apply_txn(
            txn(h("payroll"), h(f"emp{i}"), T0 + timedelta(minutes=10 + i), 100000, age=1500)
        )
    twin.apply_txn(txn(h("emp0"), h("landlord"), T0 + timedelta(minutes=40), 90000, age=2000))
    twin.detect_dirty()
    assert not twin.registry.rings and detect_rings(twin.tg.g, cfg=twin.cfg) == []


def test_chain_with_a_single_flag_is_no_ring(twin, clock):
    """A -> B -> C within the hour, B received one flagged payment: 1 signalled payer, 2 accounts."""
    clock.set(T0 + timedelta(hours=3))
    t = txn(h("a"), h("b"), T0, 40000, age=3)
    twin.apply_txn(t)
    twin.apply_decision(decision(t.txn_id, "hold_verify"))
    twin.apply_txn(txn(h("b"), h("c"), T0 + timedelta(minutes=20), 39000, age=3))
    twin.detect_dirty()
    assert not twin.registry.rings
