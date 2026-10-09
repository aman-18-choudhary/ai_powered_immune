"""Opt-in Postgres tests: set GRAPH_TEST_POSTGRES_URL (see scripts/with_postgres.sh)."""

import os
import threading
from datetime import timedelta

import pytest

from graph_twin.config import Settings
from graph_twin.graph import account_key
from graph_twin.store import GraphStore, metadata
from graph_twin.twin import GraphTwin

from .conftest import T0, Clock, antibody, decision, h, make_campaign, txn

URL = os.getenv("GRAPH_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="GRAPH_TEST_POSTGRES_URL not set")


@pytest.fixture
def pg():
    store = GraphStore(URL)
    metadata.drop_all(store.engine)
    store.create_schema()
    yield store
    metadata.drop_all(store.engine)
    store.close()


def test_ingestion_ring_and_restart_on_postgres(pg):
    clock = Clock()
    a = GraphTwin(pg, Settings(), clock)
    camp = make_campaign(a, clock, victims=9, mules=3)
    t = txn(h("dup"), camp["mules"][0], T0, "1234.56")
    assert a.apply_txn(t) and not a.apply_txn(t)
    a.apply_decision(decision(t.txn_id, "hold_verify"))
    assert not a.apply_decision(decision(t.txn_id, "hold_verify"))
    a.apply_antibody(antibody(camp["mules"][0], created=T0))
    a.detect_dirty()
    (rid,) = a.registry.rings
    b = GraphTwin(pg, Settings(), clock)  # restart from the database
    assert list(b.registry.rings) == [rid]
    assert b.tg.g.number_of_edges() == a.tg.g.number_of_edges()
    e = b.tg.g.edges[account_key(h("dup")), account_key(camp["mules"][0])]["s"]
    assert e.total == 123456 and e.susp == 1


def test_two_instances_racing_on_the_same_txn_count_it_once(pg):
    clock = Clock()
    twins = [GraphTwin(pg, Settings(), clock) for _ in range(4)]
    t = txn(h("v"), h("m"), T0, "500")
    barrier = threading.Barrier(len(twins))
    results = []

    def go(tw):
        barrier.wait()
        results.append(tw.apply_txn(t))

    threads = [threading.Thread(target=go, args=(tw,)) for tw in twins]
    [x.start() for x in threads]
    [x.join() for x in threads]
    assert results.count(True) == 1
    rows = GraphTwin(pg, Settings(), clock).tg.g.edges[account_key(h("v")), account_key(h("m"))][
        "s"
    ]
    assert rows.count == 1


def test_audit_outbox_dedupes_and_pending_buffer_bound_on_postgres(pg):
    clock = Clock()
    tw = GraphTwin(pg, Settings(max_pending_decisions=5), clock)
    for i in range(12):
        tw.apply_decision(decision(f"missing_{i}", "step_up", 1, T0 + timedelta(seconds=i)))
    with pg.engine.connect() as c:
        assert pg.count_pending(c) <= 5
    make_campaign(tw, clock, victims=9, mules=3)
    tw.detect_dirty()
    tw.detect_dirty()
    assert pg.count_audit_pending() == 1
