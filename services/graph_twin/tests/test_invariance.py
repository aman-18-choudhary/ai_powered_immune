"""Ring membership, victims and inflow must not depend on how often detection runs or on the
arrival order inside a small window (production batches every 0.2 s; the evaluation runs after
every event)."""

import random
from datetime import timedelta

import pytest

from graph_twin.config import Settings
from graph_twin.evaluate import make_decision, signal_level
from graph_twin.store import GraphStore
from graph_twin.twin import GraphTwin

from .conftest import Clock


@pytest.fixture(scope="module")
def mixed_world():
    import numpy as np
    from sim_engine.benign import gen_benign_txns
    from sim_engine.scam import gen_scam_campaign
    from sim_engine.world import build_world

    seed = 7311
    world = build_world(seed, 350)
    benign = list(gen_benign_txns(world, 5, seed))
    rng = np.random.default_rng([seed, 1])
    txns = list(benign)
    for k in range(5):
        start = world.start + timedelta(
            days=int(rng.integers(1, 4)), hours=float(rng.uniform(8, 18))
        )
        txns += gen_scam_campaign(world, f"inv{k}", 16, seed, start_ts=start).txns
    return world.start, sorted(txns, key=lambda t: (t.ts, t.txn_id))


@pytest.fixture(scope="module")
def reference(mixed_world):
    return _run(*mixed_world, every=1)


def _run(start, txns, *, every: int | None, order: str = "time") -> set:
    clock = Clock(start)
    tw = GraphTwin(GraphStore("sqlite://"), Settings(), clock)
    try:
        seq = list(txns)
        if order == "shuffled":
            rng = random.Random(5)
            out, bucket, edge = [], [], None
            for t in seq:
                b = int(t.ts.timestamp() // 300)
                if edge is not None and b != edge:
                    rng.shuffle(bucket)
                    out += bucket
                    bucket = []
                edge = b
                bucket.append(t)
            rng.shuffle(bucket)
            seq = out + bucket
        if order == "decisions_last":
            for t in seq:
                clock.set(max(clock.now, t.ts))
                tw.apply_txn(t)
            for t in seq:
                lvl = signal_level(t)
                if lvl != "allow":
                    tw.apply_decision(make_decision(t, lvl))
            tw.detect_dirty()
        else:
            n = 0
            for t in seq:
                clock.set(max(clock.now, t.ts))
                tw.apply_txn(t)
                lvl = signal_level(t)
                if lvl != "allow":
                    tw.apply_decision(make_decision(t, lvl))
                n += 1
                if every and n % every == 0:
                    tw.detect_dirty()
            tw.detect_dirty()
        return {
            (
                frozenset(k for k, r in ring.members.items() if r in ("mule", "cash_out")),
                ring.victims,
                ring.inflow_paise,
            )
            for ring in tw.registry.rings.values()
        }
    finally:
        tw.store.close()


@pytest.mark.parametrize(
    "variant",
    [
        {"every": 200},
        {"every": 500},
        {"every": None},  # one final detect pass
        {"every": 1, "order": "shuffled"},
        {"every": None, "order": "decisions_last"},
    ],
    ids=["every-200", "every-500", "final-only", "shuffled-5min", "decisions-last"],
)
def test_membership_victims_and_inflow_do_not_depend_on_detection_cadence(
    mixed_world, reference, variant
):
    start, txns = mixed_world
    assert len(reference) == 5  # five campaigns, nothing else
    assert _run(start, txns, **variant) == reference


def test_a_relay_looking_victim_does_not_pull_in_a_legitimate_merchant(twin, clock):
    """A victim that also forwards a slice to a busy merchant: the merchant (84 payers, a sliver
    funded by the ring) is no member and adds no victims or inflow, whatever the cadence."""
    from datetime import timedelta

    from .conftest import T0, decision, h, make_campaign, txn

    make_campaign(twin, clock, victims=6, mules=3)
    clock.set(T0 + timedelta(hours=2))
    for i in range(84):
        twin.apply_txn(txn(h(f"mc{i}"), h("legit-shop"), T0 + timedelta(seconds=5 * i), 700))
    v = h("cv0")
    for j in range(2):
        t = txn(h(f"vin{j}"), v, T0 - timedelta(minutes=10), 30000, age=3)
        twin.apply_txn(t)
        if j == 0:
            twin.apply_decision(decision(t.txn_id, "step_up"))
    twin.apply_txn(txn(v, h("legit-shop"), T0 + timedelta(minutes=5), 14000))
    twin.detect_dirty()
    from graph_twin.graph import account_key

    shop = account_key(h("legit-shop"))
    for r in twin.registry.rings.values():
        assert shop not in r.members and r.victims < 30
