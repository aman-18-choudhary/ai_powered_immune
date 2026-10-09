import time
from datetime import timedelta

import pytest

from graph_twin import rings as rings_mod
from graph_twin.config import Settings
from graph_twin.graph import account_key, ns
from graph_twin.model import CASH_OUT, MULE
from graph_twin.rings import detect_rings, make_ring_id
from graph_twin.twin import GraphTwin

from .conftest import T0, antibody, decision, h, make_campaign, txn


def test_ring_found_in_synthetic_campaign_with_roles_and_stats(twin, clock):
    c = make_campaign(twin, clock, victims=9, mules=3)
    clock.set(T0 + timedelta(hours=1))
    twin.detect_dirty()
    assert len(twin.registry.rings) == 1
    r = next(iter(twin.registry.rings.values()))
    assert {k for k, v in r.members.items() if v == MULE} == {account_key(m) for m in c["mules"]}
    assert [k for k, v in r.members.items() if v == CASH_OUT] == [account_key(c["cash"])]
    assert r.victims == 9 and r.victims_signalled == 9
    assert r.inflow_paise == 9 * 50000 * 100  # exact paise
    assert r.status == "new" and 0.35 <= r.score <= 1 and 0 < r.confidence <= 1
    assert r.shared_device and r.passthrough_share == 1.0


def test_detect_rings_pure_function_matches_service_state(twin, clock):
    make_campaign(twin, clock)
    found = detect_rings(twin.tg.g, cfg=twin.cfg, now_us=None)
    assert len(found) == 1 and found[0].core
    assert found[0].ring_id == make_ring_id(found[0].core)


def test_ring_id_is_stable_as_ring_grows_and_merges_keep_oldest(twin, clock):
    c = make_campaign(twin, clock, victims=6, mules=3)
    twin.detect_dirty()
    (rid,) = twin.registry.rings
    r0 = twin.registry.rings[rid]
    core0 = set(r0.core)
    # a 4th mule joins (new victims, sweep to the same cash-out): same ring, same id, larger core
    m4 = h("late-mule")
    later = T0 + timedelta(hours=2)
    clock.set(later + timedelta(hours=1))
    for i in range(3):
        t = txn(h(f"lv{i}"), m4, later + timedelta(minutes=i), "40000")
        twin.apply_txn(t)
        twin.apply_decision(decision(t.txn_id, "hold_verify"))
    twin.apply_txn(txn(m4, c["cash"], later + timedelta(minutes=10), 120000, rail="IMPS"))
    twin.detect_dirty()
    assert list(twin.registry.rings) == [rid]
    r1 = twin.registry.rings[rid]
    assert r1.core > core0 and account_key(m4) in r1.core
    assert r1.version > 1 and r1.status == "growing"
    assert r1.first_detected_us == r0.first_detected_us


def test_two_rings_that_share_a_core_member_merge_into_the_oldest(twin, clock):
    a = make_campaign(twin, clock, tag="A", victims=6, mules=3, start=T0)
    twin.detect_dirty()
    b = make_campaign(twin, clock, tag="B", victims=6, mules=3, start=T0 + timedelta(hours=3))
    clock.set(T0 + timedelta(hours=5))
    twin.detect_dirty()
    assert len(twin.registry.rings) == 2
    id_a = twin.registry.member_index[account_key(a["cash"])]
    id_b = twin.registry.member_index[account_key(b["cash"])]
    assert id_a != id_b
    # B's mule 0 now ALSO sweeps to A's cash-out: the two campaigns share infrastructure
    twin.apply_txn(txn(b["mules"][0], a["cash"], T0 + timedelta(hours=4), 100000))
    twin.apply_txn(txn(h("bv"), b["mules"][0], T0 + timedelta(hours=3, minutes=40), 100000))
    twin.detect_dirty()
    assert list(twin.registry.rings) == [id_a]  # the older id survives
    assert twin.registry.resolve(id_b).ring_id == id_a  # the absorbed id still resolves
    merged = twin.registry.rings[id_a]
    assert account_key(b["cash"]) in merged.core and account_key(a["cash"]) in merged.core
    assert id_b in merged.merged_from


def test_status_lifecycle_new_growing_stable(twin, clock):
    make_campaign(twin, clock, victims=6, mules=3)
    twin.detect_dirty()
    (r,) = twin.registry.rings.values()
    assert r.status == "new"
    clock.set(clock.now + timedelta(seconds=twin.cfg.stable_after_s + 1))
    twin.sweep()
    assert r.status == "stable"


def test_no_ring_from_structure_alone(twin, clock):
    make_campaign(twin, clock, flag=False)  # same shape, no signals, no antibody
    twin.detect_dirty()
    assert not twin.registry.rings
    assert detect_rings(twin.tg.g, cfg=twin.cfg) == []


def test_antibody_seeds_a_ring_with_two_accounts(twin, clock):
    c = make_campaign(twin, clock, victims=4, mules=1, flag=False)
    twin.apply_antibody(antibody(c["mules"][0], created=T0))
    twin.detect_dirty()
    (r,) = twin.registry.rings.values()
    assert r.antibody and len(r.members) == 2  # mule + cash-out
    twin.apply_antibody(antibody(c["mules"][0], revoked=True))  # tombstone clears the seed
    assert ns(twin.tg.g, account_key(c["mules"][0])).ab_until == 0
    assert detect_rings(twin.tg.g, cfg=twin.cfg) == []  # nothing left to seed a fresh detection


def test_single_signalled_payer_is_not_enough(twin, clock):
    make_campaign(twin, clock, victims=1, mules=3)
    twin.detect_dirty()
    assert not twin.registry.rings


def test_hero_campaign_ring_found_with_antibody():
    from sim_engine.replay import build_hero_scenario
    from sim_engine.world import build_world

    from graph_twin.evaluate import replay, signal_level
    from graph_twin.store import GraphStore

    world = build_world(5, 600)
    sc = build_hero_scenario(world, seed=3)
    meta = sc.metadata
    from .conftest import Clock

    clock = Clock(sc.txns[0].ts)
    tw = GraphTwin(GraphStore("sqlite://"), Settings(), clock)
    shared_ts = None
    for t in sorted(sc.txns, key=lambda x: x.ts):
        if t.payee_hash == meta.shared_mule_payee_hash and signal_level(t) != "allow":
            shared_ts = t.ts
            break
    replay(tw, clock, [t for t in sc.txns if shared_ts is None or t.ts <= shared_ts])
    clock.set(sc.txns[-1].ts)
    tw.apply_antibody(
        antibody(meta.shared_mule_payee_hash, created=shared_ts or clock.now, days=14)
    )
    replay(tw, clock, [t for t in sc.txns if shared_ts is not None and t.ts > shared_ts])
    tw.detect_dirty()
    (ring,) = tw.registry.rings.values()
    core_hashes = {k[2:] for k in ring.core}
    assert meta.shared_mule_payee_hash in core_hashes
    assert meta.cashout_payee_hash in core_hashes
    assert ring.antibody and ring.victims == 2
    tw.store.close()


def test_incremental_detection_only_expands_the_touched_region(twin, clock, monkeypatch):
    make_campaign(twin, clock, tag="A")
    for i in range(300):  # unrelated busy world: 300 citizens paying 20 merchants
        twin.apply_txn(txn(h(f"u{i}"), h(f"shop{i % 20}"), T0 + timedelta(seconds=i)))
    twin.detect_dirty()
    sizes = []
    real = rings_mod.expand

    def spy(g, starts, cfg, now_us):
        out = real(g, starts, cfg, now_us)
        sizes.append(len(out))
        return out

    monkeypatch.setattr(rings_mod, "expand", spy)
    make_campaign(twin, clock, tag="B", start=T0 + timedelta(hours=1))
    twin.apply_txn(txn(h("u1"), h("shop1"), T0 + timedelta(hours=2)))
    twin.detect_dirty()
    assert sizes and max(sizes) < 40  # never the 300+ unrelated nodes


def test_detection_latency_after_the_qualifying_event(twin, clock):
    """The ring is present within 1 s of the Nth qualifying event (here: the second flagged
    victim payment, arriving after the mules have swept)."""
    c = make_campaign(twin, clock, victims=6, mules=3, flag=False)
    twin.apply_decision(decision(c["txns"][0], "hold_verify"))
    twin.detect_dirty()
    assert not twin.registry.rings  # one flagged payer is not enough
    t0 = time.perf_counter()
    twin.apply_decision(decision(c["txns"][1], "hold_verify"))
    twin.detect_dirty()
    assert twin.registry.rings
    assert time.perf_counter() - t0 < 1.0


@pytest.mark.parametrize("min_size", [3, 4])
def test_min_size_counts_accounts_excluding_victims(twin, clock, min_size):
    make_campaign(twin, clock, victims=6, mules=2)  # 2 mules + cash-out = 3 accounts
    found = detect_rings(twin.tg.g, min_size=min_size, cfg=twin.cfg)
    assert len(found) == (1 if min_size == 3 else 0)
