from datetime import timedelta
from decimal import Decimal

import pytest

from graph_twin.graph import account_key, es, ns
from graph_twin.twin import EventRejected, GraphTwin

from .conftest import T0, antibody, decision, h, txn


def edge(twin, a, b):
    return es(twin.tg.g, account_key(a), account_key(b))


def test_replayed_txn_never_double_counts(twin):
    t = txn(h("v"), h("m"), T0, "1234.56")
    assert twin.apply_txn(t) is True
    for _ in range(5):
        assert twin.apply_txn(t) is False
    e = edge(twin, h("v"), h("m"))
    assert e.count == 1 and e.total == 123456  # integer paise, exact
    assert twin.counters["txns_duplicate"] == 5


def test_totals_match_decimal_sums_exactly(twin):
    amounts = [Decimal("0.10"), Decimal("0.20"), Decimal("33333.33"), Decimal("1.07")] * 50
    for i, a in enumerate(amounts):
        twin.apply_txn(txn(h("v"), h("m"), T0 + timedelta(seconds=i), a))
    e = edge(twin, h("v"), h("m"))
    assert Decimal(e.total) / 100 == sum(amounts, Decimal(0))  # no float drift


def test_decision_replay_and_latest_seq_wins(twin):
    t = txn(h("v"), h("m"), T0)
    twin.apply_txn(t)
    assert twin.apply_decision(decision(t.txn_id, "step_up", 1)) is True
    assert twin.apply_decision(decision(t.txn_id, "step_up", 1)) is False  # replay
    assert edge(twin, h("v"), h("m")).susp == 1
    assert twin.apply_decision(decision(t.txn_id, "hold_verify", 2)) is True  # upgrade: no recount
    assert edge(twin, h("v"), h("m")).susp == 1
    assert twin.apply_decision(decision(t.txn_id, "allow", 1)) is False  # stale seq ignored
    assert ns(twin.tg.g, account_key(h("m"))).susp_in == 1
    assert twin.apply_decision(decision(t.txn_id, "allow", 3)) is True  # a later allow clears
    assert edge(twin, h("v"), h("m")).susp == 0
    assert ns(twin.tg.g, account_key(h("m"))).susp_in == 0


def test_decision_before_txn_is_held_until_txn_arrives(twin):
    t = txn(h("v"), h("m"), T0)
    assert twin.apply_decision(decision(t.txn_id, "hold_verify", 1)) is True
    assert account_key(h("m")) not in twin.tg.g
    assert twin.pending_decisions == 1
    twin.apply_txn(t)
    assert edge(twin, h("v"), h("m")).susp == 1
    assert twin.pending_decisions == 0
    twin.apply_txn(t)
    assert edge(twin, h("v"), h("m")).susp == 1


def test_out_of_order_upgrade_before_first_decision(twin):
    t = txn(h("v"), h("m"), T0)
    twin.apply_decision(decision(t.txn_id, "hold_verify", 2))
    twin.apply_decision(decision(t.txn_id, "allow", 1))  # older seq after the newer: ignored
    twin.apply_txn(t)
    assert edge(twin, h("v"), h("m")).susp == 1


def test_pending_decision_buffer_is_bounded(store, clock):
    from graph_twin.config import Settings

    tw = GraphTwin(store, Settings(max_pending_decisions=20), clock)
    for i in range(60):
        tw.apply_decision(
            decision(f"never_arrives_{i}", "hold_verify", 1, T0 + timedelta(seconds=i))
        )
    assert tw.pending_decisions <= 20
    assert tw.counters["decisions_dropped"] == 40
    assert tw.store.count_pending(tw.store.engine.connect()) <= 20


def test_antibody_idempotent_revoke_sticky_and_out_of_order(twin):
    key = h("mule")
    assert twin.apply_antibody(antibody(key)) is True
    assert twin.apply_antibody(antibody(key)) is False
    n = ns(twin.tg.g, account_key(key))
    assert n.ab_until > 0
    assert twin.apply_antibody(antibody(key, revoked=True)) is True  # tombstone clears the flag
    assert ns(twin.tg.g, account_key(key)).ab_until == 0
    assert twin.apply_antibody(antibody(key)) is False  # replay of the create after the tombstone
    assert ns(twin.tg.g, account_key(key)).ab_until == 0
    # a tombstone that arrives BEFORE the create
    assert twin.apply_antibody(antibody(h("m2"), "ab2", revoked=True)) is True
    assert twin.apply_antibody(antibody(h("m2"), "ab2")) is False
    assert (
        account_key(h("m2")) not in twin.tg.g or ns(twin.tg.g, account_key(h("m2"))).ab_until == 0
    )


def test_antibody_extension_and_non_mule_kinds(twin):
    key = h("mule")
    twin.apply_antibody(antibody(key, days=1))
    assert twin.apply_antibody(antibody(key, days=10)) is True  # same id, later expiry
    assert twin.apply_antibody(antibody(key, days=3)) is False  # shorter: no change
    other = antibody(h("x"), "abx").model_copy(update={"kind": "device"})
    assert twin.apply_antibody(other) is False


def test_future_and_stale_events(twin, clock):
    with pytest.raises(EventRejected):
        twin.apply_txn(txn(h("v"), h("m"), T0 + timedelta(days=2)))
    assert twin.apply_txn(txn(h("v"), h("m"), T0 - timedelta(days=45))) is False
    assert twin.counters["txns_stale"] == 1


def test_window_edge_exactly_at_retention_boundary(twin):
    assert twin.apply_txn(txn(h("v"), h("m"), T0 - timedelta(days=30))) is True
    assert twin.apply_txn(txn(h("v"), h("m"), T0 - timedelta(days=30, microseconds=1))) is False


def test_rejects_bad_identifiers_without_echo(twin):
    bad = txn(h("v"), "has spaceé", T0)
    with pytest.raises(EventRejected) as ei:
        twin.apply_txn(bad)
    assert "space" not in str(ei.value)


def test_token_fallback_without_account_ref(twin):
    twin.apply_txn(txn("payer1", h("m"), T0, ref=None))
    assert "t:tok_payer1" in twin.tg.g
    assert ns(twin.tg.g, "t:tok_payer1").kind == "token"


def test_self_payment_ignored(twin):
    assert twin.apply_txn(txn(h("v"), h("v"), T0)) is False


def test_restart_rebuilds_the_same_graph(store, clock):
    a = GraphTwin(store, clock=clock)
    for i in range(20):
        t = txn(h(f"v{i % 5}"), h(f"m{i % 3}"), T0 + timedelta(minutes=i), 100 + i)
        a.apply_txn(t)
        a.apply_decision(decision(t.txn_id, "step_up" if i % 4 == 0 else "allow"))
    a.apply_antibody(antibody(h("m1")))
    b = GraphTwin(store, clock=clock)

    def dump(tw):
        g = tw.tg.g
        nodes = {n: (s.in_tot, s.out_tot, s.in_cnt, s.susp_in, s.ab_until, sorted(s.sig))
                 for n in g.nodes for s in [ns(g, n)]}  # fmt: skip
        edges = {(x, y): (e.count, e.total, e.first, e.last, e.rails, e.susp)
                 for x, y, d in g.edges(data=True) for e in [d["s"]]}  # fmt: skip
        return nodes, edges

    assert dump(a) == dump(b)
