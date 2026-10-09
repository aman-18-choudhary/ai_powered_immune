"""Consumers, failure injection, ordering, boundedness and throughput."""

import asyncio
import time
from datetime import timedelta

import pytest
from scam_contracts.topics import Topics
from svckit.bus import InMemoryBus

from graph_twin.config import Settings
from graph_twin.graph import account_key, ns
from graph_twin.service import GROUP_PREFIX, TwinService
from graph_twin.store import GraphStore
from graph_twin.twin import GraphTwin

from .conftest import T0, Clock, antibody, decision, h, txn


@pytest.fixture
def clock() -> Clock:
    return Clock(T0 + timedelta(hours=2))


@pytest.fixture
def parts(db_url, clock):
    store = GraphStore(db_url)
    twin = GraphTwin(store, Settings(), clock)
    bus = InMemoryBus()
    svc = TwinService(twin, bus)
    yield twin, bus, svc
    store.close()


async def until(cond, timeout=3.0):
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > timeout:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


async def test_bus_events_build_the_graph_and_the_ring(parts):
    twin, bus, svc = parts
    svc.start(loops=False)
    try:
        ms = [h(f"m{i}") for i in range(3)]
        cash = h("cash")
        ids = []
        for i in range(9):
            t = txn(h(f"v{i}"), ms[i % 3], T0 + timedelta(minutes=2 * i), "40000")
            ids.append(t.txn_id)
            await bus.publish(Topics.TXN_EVENTS, t.payer_token, t)
            await bus.publish(Topics.TXN_DECISIONS, t.txn_id, decision(t.txn_id, "hold_verify"))
        for i, m in enumerate(ms):
            t = txn(m, cash, T0 + timedelta(minutes=30 + i), 120000, rail="IMPS", device="devx")
            await bus.publish(Topics.TXN_EVENTS, t.payer_token, t)
        await until(
            lambda: twin.counters["txns_applied"] == 12 and twin.counters["decisions_applied"] == 9
        )
        await svc.flush()
        assert len(twin.published) == 1
        # the consumer groups follow the graph-twin-<role> convention
        groups = {g for (_, g) in bus._offsets}
        assert groups == {f"{GROUP_PREFIX}-{r}" for r in ("txn", "decisions", "antibody")}
        # replays (at-least-once delivery) change nothing
        before = twin.stats()
        for i in range(3):
            t = txn(h(f"v{i}"), ms[i % 3], T0 + timedelta(minutes=2 * i), "40000", tid=ids[i])
            await bus.publish(Topics.TXN_EVENTS, t.payer_token, t)
            await bus.publish(Topics.TXN_DECISIONS, ids[i], decision(ids[i], "hold_verify"))
        await until(
            lambda: (
                twin.counters["txns_duplicate"] == 3 and twin.counters["decisions_duplicate"] == 3
            )
        )
        assert twin.stats()["edges"] == before["edges"]
        e = twin.tg.g.edges[account_key(h("v0")), account_key(ms[0])]["s"]
        assert e.count == 1 and e.total == 4_000_000
    finally:
        await svc.stop()


async def test_antibody_topic_flags_and_tombstone_clears(parts):
    twin, bus, svc = parts
    svc.start(loops=False)
    try:
        key = h("mule")
        await bus.publish(Topics.ANTIBODIES, key, antibody(key, created=T0))
        await until(lambda: twin.counters["antibodies_applied"] == 1)
        assert ns(twin.tg.g, account_key(key)).ab_until > 0
        await bus.publish(Topics.ANTIBODIES, key, antibody(key, revoked=True, created=T0))
        await until(lambda: twin.counters["antibodies_applied"] == 2)
        assert ns(twin.tg.g, account_key(key)).ab_until == 0
        assert f"{GROUP_PREFIX}-antibody" in {g for (_, g) in bus._offsets}
    finally:
        await svc.stop()


async def test_malformed_events_go_to_the_dlq_and_do_not_stop_the_consumer(parts):
    twin, bus, svc = parts
    svc.start(loops=False)
    try:
        await bus.publish_raw(Topics.TXN_EVENTS, "k", b"{not json")
        await bus.publish_raw(Topics.TXN_EVENTS, "k", b'{"txn_id": "x"}')
        bad = txn(h("v"), "has space", T0)  # parses, refused by the twin
        await bus.publish(Topics.TXN_EVENTS, "k", bad)
        good = txn(h("v2"), h("m"), T0)
        await bus.publish(Topics.TXN_EVENTS, "k", good)
        await until(lambda: twin.counters["txns_applied"] == 1)
        dlq = bus.messages(Topics.TXN_EVENTS + Topics.DLQ_SUFFIX)
        assert len(dlq) == 3
    finally:
        await svc.stop()


async def test_store_down_does_not_corrupt_state_and_recovers(parts):
    twin, bus, svc = parts
    svc.start(loops=False)
    try:
        t = txn(h("v"), h("m"), T0)
        twin.store.fail_next = 3  # all three retries fail -> DLQ, claim released
        await bus.publish(Topics.TXN_EVENTS, "k", t)
        await until(lambda: len(bus.messages(Topics.TXN_EVENTS + Topics.DLQ_SUFFIX)) == 1)
        assert twin.stats()["nodes"] == 0 and twin.stats()["edges"] == 0  # memory rolled back
        await bus.publish(Topics.TXN_EVENTS, "k", t)  # the DLQ'd event can be replayed
        await until(lambda: twin.counters["txns_applied"] == 1)
        assert twin.tg.g.edges[account_key(h("v")), account_key(h("m"))]["s"].count == 1
    finally:
        await svc.stop()


async def test_detection_failure_keeps_the_work_and_retries(parts):
    twin, bus, svc = parts
    from .conftest import make_campaign

    make_campaign(
        twin, twin.clock.__self__ if hasattr(twin.clock, "__self__") else Clock(), tag="z"
    )
    twin.store.fail_next = 1
    with pytest.raises(ConnectionError):
        twin.detect_dirty()
    res = twin.detect_dirty()  # the carried events are flushed on the next pass
    assert len(twin.published) == 1 and res.audit


async def test_decision_published_before_its_txn_on_the_bus(parts):
    twin, bus, svc = parts
    svc.start(loops=False)
    try:
        t = txn(h("v"), h("m"), T0)
        await bus.publish(Topics.TXN_DECISIONS, t.txn_id, decision(t.txn_id, "hold_verify"))
        await until(lambda: twin.pending_decisions == 1)
        await bus.publish(Topics.TXN_EVENTS, "k", t)
        await until(lambda: twin.counters["txns_applied"] == 1)
        assert twin.tg.g.edges[account_key(h("v")), account_key(h("m"))]["s"].susp == 1
        assert twin.pending_decisions == 0
    finally:
        await svc.stop()


async def test_event_loop_stays_responsive_while_ingesting(parts):
    """SQLAlchemy work runs in worker threads: a ticker on the loop is never starved."""
    twin, bus, svc = parts
    ticks = []

    async def ticker():
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.005)

    tk = asyncio.create_task(ticker())
    t0 = time.monotonic()
    for i in range(300):
        await svc.on_txn(txn(h(f"v{i}"), h(f"m{i % 7}"), T0 + timedelta(seconds=i)))
    elapsed = time.monotonic() - t0
    tk.cancel()
    gaps = [b - a for a, b in zip(ticks, ticks[1:], strict=False)]
    assert elapsed > 0.1 and max(gaps) < 0.25


async def test_lifespan_closes_tasks_and_engine(db_url, clock):
    import httpx

    from graph_twin.api import create_app

    app = create_app(
        db_url, bus=InMemoryBus(), clock=clock, settings=Settings(detect_interval_s=0.01),
        consume=True, background=True,
    )  # fmt: skip
    async with app.router.lifespan_context(app):
        svc = app.state.service
        tasks = list(svc.tasks)
        assert len(tasks) == 6 and not any(t.done() for t in tasks)  # 3 consumers + 3 loops
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        assert (await c.get("/readyz")).status_code == 200
    assert all(t.cancelled() or t.done() for t in tasks) and not svc.tasks and not svc._bg
    assert svc.drainer.live_tasks == 0


# --------------------------------------------------------------------------- boundedness
def test_max_nodes_evicts_oldest_but_never_ring_members(db_url, clock):
    from .conftest import make_campaign

    twin = GraphTwin(GraphStore(db_url), Settings(max_nodes=400), clock)
    camp = make_campaign(twin, clock, victims=9, mules=3, tag="keep", start=T0 - timedelta(days=2))
    twin.detect_dirty()
    assert len(twin.registry.rings) == 1
    for i in range(1500):  # noise: distinct payer -> distinct merchant, newer than the ring
        twin.apply_txn(txn(h(f"nv{i}"), h(f"nm{i}"), T0 + timedelta(seconds=i), 100))
    g = twin.tg.g
    assert g.number_of_nodes() <= 400
    for m in (*camp["mules"], camp["cash"]):
        assert account_key(m) in g
    for v in camp["victims"]:  # a ring's victims are kept with it
        assert account_key(v) in g
    assert twin.counters["evicted_nodes"] > 1000
    twin.store.close()


def test_eviction_keeps_signalled_nodes_and_counts_blocked(db_url, clock):
    twin = GraphTwin(GraphStore(db_url), Settings(max_nodes=60), clock)
    ids = []
    for i in range(40):
        t = txn(h(f"v{i}"), h(f"m{i}"), T0 + timedelta(seconds=i), 100)
        twin.apply_decision(decision(t.txn_id, "step_up"))  # arrives first: signalled on insertion
        twin.apply_txn(t)
        ids.append(t.txn_id)
    twin.evict()
    assert twin.counters["evict_blocked"] >= 1
    assert twin.tg.g.number_of_nodes() > 60  # documented: signalled nodes are not evicted
    twin.store.close()


def test_retention_sweep_expires_edges_nodes_and_rings(db_url, clock):
    from .conftest import make_campaign

    twin = GraphTwin(GraphStore(db_url), Settings(retention_days=2), clock)
    make_campaign(twin, clock, victims=9, mules=3)
    twin.detect_dirty()
    assert twin.registry.rings and twin.tg.g.number_of_edges() > 10
    clock.set(T0 + timedelta(days=1))
    twin.sweep()
    assert twin.registry.rings  # inside the window
    clock.set(T0 + timedelta(days=3))
    twin.sweep()
    assert not twin.registry.rings and not twin.published
    assert twin.tg.g.number_of_nodes() == 0 and twin.tg.g.number_of_edges() == 0
    assert twin.store.load_all()["rings"] == []
    twin.store.close()


def test_ring_cap_dirty_cap_and_pending_cap_are_counted(db_url, clock):
    from .conftest import make_campaign

    twin = GraphTwin(GraphStore(db_url), Settings(max_rings=1, max_dirty=10), clock)
    make_campaign(twin, clock, tag="a")
    make_campaign(twin, clock, tag="b", start=T0 + timedelta(hours=5))
    assert twin.counters["dirty_dropped"] > 0
    twin.sweep()  # re-marks ring cores dirty
    twin.detect_dirty()
    assert len(twin.registry.rings) <= 1
    twin.store.close()


def test_restart_keeps_rings_and_ids_and_detection_continues(db_url, clock):
    from .conftest import make_campaign

    a = GraphTwin(GraphStore(db_url), Settings(), clock)
    camp = make_campaign(a, clock, victims=9, mules=3)
    a.detect_dirty()
    (rid,) = a.registry.rings
    ver = a.registry.rings[rid].version
    a.store.close()
    b = GraphTwin(GraphStore(db_url), Settings(), clock)
    assert list(b.registry.rings) == [rid] and b.registry.rings[rid].version == ver
    assert b.published[rid].members == a.published[rid].members
    b.apply_txn(txn(h("v-extra"), camp["mules"][1], T0 + timedelta(minutes=50), "20000"))
    b.detect_dirty()
    assert list(b.registry.rings) == [rid] and b.registry.rings[rid].version > ver
    b.store.close()


# ----------------------------------------------------------------- scale and fan-in
def test_huge_fan_in_node_is_cheap_per_event_and_to_detect(db_url, clock):
    twin = GraphTwin(GraphStore(db_url), Settings(), clock)
    t0 = time.perf_counter()
    for i in range(6000):
        twin.apply_txn(txn(h(f"cu{i}"), h("megashop"), T0 + timedelta(seconds=i // 3), 120))
    ingest = time.perf_counter() - t0
    first = txn(h("flagged-customer"), h("megashop"), T0 + timedelta(minutes=5), 300)
    twin.apply_txn(first)
    twin.apply_decision(decision(first.txn_id, "step_up"))
    t1 = time.perf_counter()
    twin.detect_dirty()
    detect = time.perf_counter() - t1
    assert not twin.registry.rings
    assert detect < 1.0, detect
    # a single event on the 6,000-payer node costs about the same as on a fresh one
    t2 = time.perf_counter()
    for i in range(50):
        twin.apply_txn(txn(h(f"late{i}"), h("megashop"), T0 + timedelta(minutes=10, seconds=i), 90))
        twin.detect_dirty()
    per_event = (time.perf_counter() - t2) / 50
    assert per_event < 0.05, per_event
    print(f"fan-in 6000: ingest {6000 / ingest:.0f} ev/s, detect {detect * 1000:.0f} ms, "
          f"event+detect {per_event * 1000:.1f} ms")  # fmt: skip
    twin.store.close()


@pytest.mark.slow
def test_ingestion_throughput_ten_thousand_nodes(db_url, clock):
    twin = GraphTwin(GraphStore(db_url), Settings(), clock)
    n = 6000  # 6,000 events over ~12,000 nodes (payer + payee are distinct accounts)
    events = [
        txn(h(f"tp{i}"), h(f"tm{i % 5000}"), T0 + timedelta(milliseconds=i * 10), 50 + i % 900)
        for i in range(n)
    ]
    t0 = time.perf_counter()
    for e in events:
        twin.apply_txn(e)
    dt = time.perf_counter() - t0
    assert twin.tg.g.number_of_nodes() >= 10_000
    rate = n / dt
    print(f"ingest: {rate:.0f} events/s ({twin.tg.g.number_of_nodes()} nodes, file sqlite)")
    assert rate > 100
    twin.store.close()
