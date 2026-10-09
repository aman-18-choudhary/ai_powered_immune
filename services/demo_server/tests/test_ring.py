import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from scam_contracts.models import Transaction

from demo_server.ring import RingGraph, inflow_bucket, inr

T0 = datetime(2026, 1, 6, 8, 0, tzinfo=UTC)
_n = 0


def h(c: str) -> str:
    return hashlib.sha256(c.encode()).hexdigest()


def tx(
    payer: str, payee: str, amount: int, sec: int = 0, flagged_payer_token: str = "p"
) -> Transaction:
    global _n
    _n += 1
    return Transaction(
        txn_id=f"txn_{_n:016x}", idempotency_key=f"k{_n}", bank_id="bank_x",
        payer_token=f"tok_{payer}", payee_hash=h(payee), rail="UPI",
        amount_inr=Decimal(amount), ts=T0 + timedelta(seconds=sec), payee_account_age_days=10,
        device_id_token="dev", payer_account_ref=h(payer),
    )  # fmt: skip


def chain() -> RingGraph:
    """v1,v2 -> m1,m2 -> c, flows 1000 each (all within a day)."""
    g = RingGraph(horizon_days=7)
    g.add_txn(tx("a", "m1", 1000, 0), flagged=True)
    g.add_txn(tx("b", "m2", 1000, 60), flagged=False)
    g.add_txn(tx("m1", "c", 1000, 600))
    g.add_txn(tx("m2", "c", 1000, 700))
    return g


def test_inr_and_bucket():
    assert inr(123456) == "1234.56" and inr(5) == "0.05" and inr(0) == "0.00"
    assert inflow_bucket(5_000_00) == "<10k" and inflow_bucket(20_000_000_00) == ">=10m"


def test_roles_tiers_and_ids_are_opaque():
    snap = chain().snapshot()
    assert snap is not None
    by_role = {}
    for n in snap["nodes"]:
        assert len(n["id"]) == 12 and all(c in "0123456789abcdef" for c in n["id"])
        by_role.setdefault(n["role"], []).append(n["tier"])
    assert sorted(by_role) == ["cash_out", "mule", "victim"]
    assert by_role["victim"] == [0, 0] and by_role["cash_out"] == [3] and by_role["mule"] == [1, 1]
    assert snap["victims"] == 2 and snap["inflow_inr"] == "2000.00"
    assert snap["inflow_bucket"] == "<10k"
    assert all(isinstance(e["total_inr"], str) for e in snap["edges"])


def test_harm_is_max_flow_of_projected_inflow():
    g = chain()
    # each edge 1000 INR/day-span, horizon 7 d, observed span < 1 day: 7000 per edge, 2 chains
    assert g.snapshot()["harm_inr"] == "14000.00"
    r = g.simulate_takedown([])
    assert r.harm_before_paise == r.harm_after_paise == 2 * 7000 * 100
    assert r.reduction_pct == 0.0


def test_bottleneck_limits_harm():
    g = RingGraph(7)
    g.add_txn(tx("a", "m", 1000, 0), flagged=True)
    g.add_txn(tx("m", "c", 800, 600))  # forwards 80%: still a relay; edge m->c is the bottleneck
    r = g.simulate_takedown([])
    assert r.harm_before_paise == 800 * 7 * 100


def test_remove_one_mule_halves_and_cash_out_kills_all():
    g = chain()
    snap = g.snapshot()
    ids = {n["role"]: n["id"] for n in snap["nodes"] if n["role"] != "mule"}
    mules = sorted(n["id"] for n in snap["nodes"] if n["role"] == "mule")
    one = g.simulate_takedown([mules[0]])
    assert one.reduction_pct == 50.0 and len(one.cut_edges) == 2
    allc = g.simulate_takedown([ids["cash_out"]])
    assert allc.harm_after_paise == 0 and allc.reduction_pct == 100.0
    j = allc.to_json()
    assert j["harm_after"] == "0.00" and isinstance(j["harm_before"], str)


def test_unknown_or_victim_id_rejected():
    g = chain()
    victim = next(n["id"] for n in g.snapshot()["nodes"] if n["role"] == "victim")
    with pytest.raises(ValueError):
        g.simulate_takedown(["0" * 12])
    with pytest.raises(ValueError):
        g.simulate_takedown([victim])


def test_greedy_picks_cash_out_first_and_stops_early():
    g = chain()
    cash = next(n["id"] for n in g.snapshot()["nodes"] if n["role"] == "cash_out")
    r = g.best_takedown(3)
    assert list(r.removed) == [cash] and r.harm_after_paise == 0


def test_greedy_matches_exhaustive_on_small_rings_and_is_monotone():
    g = RingGraph(7)
    # three mules with unequal flows into two cash-outs
    for i, (m, c, amt) in enumerate([("m1", "c1", 3000), ("m2", "c1", 2000), ("m3", "c2", 1000)]):
        g.add_txn(tx(f"v{i}", m, amt, i * 10), flagged=(i == 0))
        g.add_txn(tx(m, c, amt, 500 + i * 10))
    red = []
    for k in (1, 2, 3):
        gr = g.best_takedown(k)
        ex = g.best_takedown_exhaustive(k)
        assert ex.harm_after_paise <= gr.harm_after_paise  # optimal is never worse
        red.append(gr.reduction_pct)
    assert red == sorted(red)  # monotone in k
    with pytest.raises(ValueError):
        g.best_takedown_exhaustive(4)


def test_deterministic_regardless_of_insertion_order():
    txns = [
        tx("a", "m1", 1000, 0), tx("b", "m2", 900, 5), tx("m1", "c", 1000, 600),
        tx("m2", "c", 900, 700),
    ]  # fmt: skip
    g1, g2 = RingGraph(), RingGraph()
    for t in txns:
        g1.add_txn(t, flagged=(t.payee_hash == h("m1")))
    for t in reversed(txns):
        g2.add_txn(t, flagged=(t.payee_hash == h("m1")))
    s1, s2 = g1.snapshot(), g2.snapshot()
    s1.pop("version")
    s2.pop("version")
    assert s1 == s2
    assert g1.best_takedown(2).to_json() == g2.best_takedown(2).to_json()


def test_add_txn_idempotent_and_no_ring_without_seed():
    g = RingGraph()
    t = tx("a", "m", 100)
    assert g.add_txn(t) and not g.add_txn(t)
    assert g.snapshot() is None
    g.mark_antibody(h("m"))
    assert g.snapshot() is not None  # an antibody seeds a ring


def test_ring_grows_as_forwards_are_observed():
    g = RingGraph()
    g.add_txn(tx("a", "m1", 1000), flagged=True)
    assert len(g.snapshot()["members"]) == 1
    g.add_txn(tx("b", "m2", 900, 5))
    g.add_txn(tx("m1", "c", 1000, 600))
    s = g.snapshot()
    assert len(s["members"]) == 2  # m1 and its sink
    g.add_txn(tx("m2", "c", 900, 700))
    assert len(g.snapshot()["members"]) == 3  # sibling relay joins via the shared sink
