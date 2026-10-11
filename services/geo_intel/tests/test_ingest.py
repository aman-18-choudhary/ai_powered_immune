import asyncio
from datetime import timedelta

import pytest
from scam_contracts.models import Reason
from scam_contracts.topics import Topics
from svckit.bus import InMemoryBus

from geo_intel.config import Settings
from geo_intel.service import GeoService

from .conftest import T0, Clock, complaint


@pytest.fixture
def svc(clock, mkstore):
    return GeoService(mkstore(), Settings(retention_days=1, retry_backoff_s=0.0), None, clock)


def test_ingest_is_idempotent_by_complaint_id(svc):
    c = complaint(cid="dup-00000001")
    assert svc.ingest(c) == "ingested"
    assert svc.ingest(c) == "duplicate"
    # same id, different content: the first writer wins, no second row
    assert svc.ingest(complaint(cid="dup-00000001", city="DEL", kind="cash_out")) == "duplicate"
    assert svc.store.count() == 1 and svc.counters["duplicates"] == 2


def test_state_only_event_lands_on_the_state_anchor_with_precision_flag(svc):
    svc.ingest(complaint(city=None, state="TN", cid="state-0001"))
    (p,) = svc.store.points(since=None, before=None, limit=5)
    assert p["city_code"] == "MAA" and p["precision"] == "state"


def test_city_wins_over_state(svc):
    svc.ingest(complaint(city="CJB", state="MH", cid="both-00001"))
    (p,) = svc.store.points(since=None, before=None, limit=5)
    assert p["city_code"] == "CJB" and p["precision"] == "city"


def test_out_of_order_arrival_keeps_all_and_aggregates_equal(clock, mkstore):
    evs = [complaint(T0 - timedelta(minutes=m), "MUM") for m in (1, 30, 5, 59, 12)]

    def run(order):
        st = mkstore()
        sv = GeoService(st, Settings(), None, clock)
        for e in order:
            sv.ingest(e)
        rows = st.window_counts(T0 - timedelta(hours=8), T0 - timedelta(hours=2),
                                T0 - timedelta(hours=1), T0)  # fmt: skip
        return rows, sv.watermark()

    a, wa = run(evs)
    b, wb = run(list(reversed(evs)))
    assert a == b and wa == wb == T0 - timedelta(minutes=1)


def test_future_and_expired_events_are_dropped_and_counted(svc, clock):
    assert svc.ingest(complaint(T0 + timedelta(hours=2))) == "dropped_future"
    assert svc.ingest(complaint(T0)) == "ingested"
    assert svc.ingest(complaint(T0 - timedelta(days=2))) == "dropped_old"  # retention 1 day
    assert svc.ingest(complaint(T0 - timedelta(hours=23))) == "ingested"
    assert svc.counters["dropped_future"] == 1 and svc.counters["dropped_old"] == 1


def test_prune_bounds_storage_to_the_retention_window(clock, mkstore):
    st = mkstore()
    sv = GeoService(st, Settings(retention_days=1, prune_batch=7), None, clock)
    for i in range(40):  # one an hour over 40 hours, ingested oldest first so none is dropped
        sv.ingest(complaint(T0 - timedelta(hours=39 - i)))
    assert st.count() == 40
    assert sv.prune_once() == 15  # strictly older than 24 h behind the newest: 15 rows
    assert st.count() == 25


def test_timestamps_are_stored_and_returned_as_utc(svc):
    from datetime import timezone

    ist = timezone(timedelta(hours=5, minutes=30))
    svc.ingest(complaint(T0.astimezone(ist), cid="tz-000001"))
    assert svc.store.max_ts() == T0 and svc.store.max_ts().utcoffset() == timedelta(0)


def test_watermark_survives_restart(clock, mkstore):
    st = mkstore()
    GeoService(st, Settings(), None, clock).ingest(complaint(T0 - timedelta(minutes=3)))
    assert GeoService(st, Settings(), None, clock).watermark() == T0 - timedelta(minutes=3)


async def _until(pred, n=300):
    for _ in range(n):
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


async def test_consumer_ingests_dedupes_and_dead_letters_malformed(mkstore):
    bus = InMemoryBus()
    st = mkstore()
    sv = GeoService(st, Settings(retry_backoff_s=0.0), bus, Clock())
    sv.start(consumers=True, loops=False)
    c = complaint(cid="bus-00000001")
    await bus.publish(Topics.COMPLAINTS, c.complaint_id, c)
    await bus.publish(Topics.COMPLAINTS, c.complaint_id, c)  # redelivery
    await bus.publish(
        Topics.COMPLAINTS, "k", Reason(code="x", weight=0.0, detail="not a complaint")
    )
    await _until(lambda: sv.counters["ingested"] == 1 and sv.counters["duplicates"] == 1)
    await _until(lambda: len(bus.messages(Topics.COMPLAINTS + Topics.DLQ_SUFFIX)) == 1)
    await sv.stop()
    assert sv.tasks == []


async def test_store_failure_is_retried_then_dead_lettered(mkstore):
    bus = InMemoryBus()
    st = mkstore()
    sv = GeoService(st, Settings(max_retries=2, retry_backoff_s=0.0), bus, Clock())
    sv.start(consumers=True, loops=False)
    st.fail_next = 100  # persistent outage
    c = complaint(cid="fail-0000001")
    await bus.publish(Topics.COMPLAINTS, c.complaint_id, c)
    await _until(lambda: len(bus.messages(Topics.COMPLAINTS + Topics.DLQ_SUFFIX)) == 1)
    assert sv.counters["store_errors"] == 2 and sv.counters["ingested"] == 0
    st.fail_next = 0
    await sv.stop()


async def test_transient_store_failure_succeeds_on_retry(mkstore):
    bus = InMemoryBus()
    st = mkstore()
    sv = GeoService(st, Settings(max_retries=3, retry_backoff_s=0.0), bus, Clock())
    sv.start(consumers=True, loops=False)
    st.fail_next = 1
    c = complaint(cid="flaky-000001")
    await bus.publish(Topics.COMPLAINTS, c.complaint_id, c)
    await _until(lambda: sv.counters["ingested"] == 1)
    assert bus.messages(Topics.COMPLAINTS + Topics.DLQ_SUFFIX) == []
    await sv.stop()


async def test_prune_loop_survives_store_errors(mkstore):
    st = mkstore()
    sv = GeoService(st, Settings(prune_interval_s=0.01), None, Clock())
    sv.ingest(complaint())
    st.fail_next = 3
    sv.start(consumers=False, loops=True)
    await _until(lambda: sv.counters["prune_errors"] >= 1)
    await sv.stop()
