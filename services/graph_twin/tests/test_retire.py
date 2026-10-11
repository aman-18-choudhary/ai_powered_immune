"""Rings are re-gated on refresh: when the evidence goes away the ring is retired (tombstone,
audit entry, hidden from the default list and from takedown), and it comes back under the same id
when the signals return."""

import json
from datetime import timedelta

import httpx
import pytest

from graph_twin.api import create_app
from graph_twin.config import Settings
from graph_twin.graph import account_key
from graph_twin.store import GraphStore
from graph_twin.twin import GraphTwin

from .conftest import T0, antibody, decision, h, make_campaign, txn

OFFICER = {"X-Principal-Role": "officer", "X-Principal-Sub": "officer-1"}


def _clear_flags(twin, camp) -> None:
    for tid in camp["txns"]:
        twin.apply_decision(decision(tid, "allow", seq=2))


def _expire(twin, clock, camp, passes: int = 3) -> list:
    """Run refresh passes across the retirement grace period (stream time) and collect audit."""
    rows = []
    for _ in range(passes):
        clock.set(clock.now + timedelta(seconds=400))
        twin.dirty.update(account_key(m) for m in camp["mules"])
        rows += twin.detect_dirty().audit
    return rows


def _kinds(rows) -> list[str]:
    return [json.loads(r.body)["event_type"] for r in rows]


def test_ring_is_retired_when_decisions_are_downgraded(twin, clock):
    camp = make_campaign(twin, clock, victims=6, mules=3)
    twin.detect_dirty()
    (rid,) = twin.registry.rings
    assert twin.registry.rings[rid].status == "new"
    _clear_flags(twin, camp)
    res = twin.detect_dirty()
    assert twin.registry.rings[rid].status == "new"  # grace: one failing pass is not enough
    rows = res.audit + _expire(twin, clock, camp)
    r = twin.registry.rings[rid]
    assert r.status == "retired" and r.victims_signalled == 0
    assert _kinds(rows).count("ring.retired") == 1
    assert twin.published[rid].status == "retired"
    # idempotent: another pass changes nothing and emits nothing
    version = r.version
    twin.dirty.update(account_key(m) for m in camp["mules"])
    assert twin.detect_dirty().audit == []
    assert twin.registry.rings[rid].version == version


def test_retirement_survives_restart_without_a_second_audit_entry(twin, clock, store):
    camp = make_campaign(twin, clock, victims=6, mules=3)
    twin.detect_dirty()
    _clear_flags(twin, camp)
    twin.detect_dirty()
    _expire(twin, clock, camp)
    (rid,) = twin.registry.rings
    twin2 = GraphTwin(store, Settings(), clock)
    assert twin2.registry.rings[rid].status == "retired"
    res = twin2.detect_dirty()
    assert twin2.registry.rings[rid].status == "retired" and "ring.retired" not in _kinds(res.audit)
    assert sum("ring.retired" in r["body"] for r in store.pending_audit(100)) == 1


def test_ring_retired_when_antibody_is_revoked(twin, clock):
    camp = make_campaign(twin, clock, victims=6, mules=3, flag=False)
    for i, m in enumerate(camp["mules"]):
        twin.apply_antibody(antibody(m, f"ab{i}"))
    twin.detect_dirty()
    (rid,) = twin.registry.rings
    assert twin.registry.rings[rid].antibody
    # a confirmed antibody keeps the ring alive
    twin.detect_dirty()
    assert twin.registry.rings[rid].status != "retired"
    for i, m in enumerate(camp["mules"]):
        twin.apply_antibody(antibody(m, f"ab{i}", revoked=True))
    rows = twin.detect_dirty().audit + _expire(twin, clock, camp)
    assert twin.registry.rings[rid].status == "retired"
    assert "ring.retired" in _kinds(rows)


def test_confirmed_antibody_member_blocks_retirement(twin, clock):
    camp = make_campaign(twin, clock, victims=6, mules=3)
    twin.apply_antibody(antibody(camp["mules"][0], "ab-keep"))
    twin.detect_dirty()
    (rid,) = twin.registry.rings
    _clear_flags(twin, camp)
    twin.detect_dirty()
    _expire(twin, clock, camp)
    assert twin.registry.rings[rid].status != "retired"


def test_retired_ring_returns_under_the_same_id_when_signals_return(twin, clock):
    camp = make_campaign(twin, clock, victims=6, mules=3)
    twin.detect_dirty()
    (rid,) = twin.registry.rings
    _clear_flags(twin, camp)
    twin.detect_dirty()
    _expire(twin, clock, camp)
    assert twin.registry.rings[rid].status == "retired"
    for tid in camp["txns"]:  # the flags come back (later decision_seq)
        twin.apply_decision(decision(tid, "hold_verify", seq=3))
    res = twin.detect_dirty()
    r = twin.registry.rings[rid]
    assert list(twin.registry.rings) == [rid] and r.status == "growing"
    assert "ring.status_changed" in _kinds(res.audit)


def test_retired_rings_leave_default_list_and_takedown(db_url, clock):
    import asyncio

    async def go() -> None:
        app = create_app(db_url, clock=clock, settings=Settings(), background=False)
        twin, svc = app.state.twin, app.state.service
        camp = make_campaign(twin, clock, victims=6, mules=3)
        await svc.flush()
        (rid,) = twin.published
        _clear_flags(twin, camp)
        await svc.flush()
        for _ in range(3):
            clock.set(clock.now + timedelta(seconds=400))
            twin.dirty.update(account_key(m) for m in camp["mules"])
            await svc.flush()
        import os

        os.environ["TRUST_GATEWAY_HEADERS"] = "1"
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        assert (await c.get("/rings", headers=OFFICER)).json()["count"] == 0
        listed = (await c.get("/rings?status=retired", headers=OFFICER)).json()
        assert [x["ring_id"] for x in listed["items"]] == [rid]
        assert (await c.get(f"/rings/{rid}", headers=OFFICER)).json()["status"] == "retired"
        r = await c.post(f"/rings/{rid}/simulate", json={"k": 1}, headers=OFFICER)
        assert r.status_code == 409
        assert (await c.get(f"/rings/{rid}/best-takedown", headers=OFFICER)).status_code == 409
        await svc.stop()
        twin.store.close()

    asyncio.run(go())


def test_helpers_import():
    assert GraphStore and T0 and h and txn


def _dilution_campaign(twin, clock, flagged: int, victims: int = 80):
    """2 young mules + 1 cash-out; only the first ``flagged`` victims are flagged hold_verify,
    the model allows the rest: each mule's flagged share falls below 10% over time."""
    cash = h("dil-cash")
    ms = [h("dil-m0"), h("dil-m1")]
    detected_at = retired_at = None
    for i in range(victims):
        ts = T0 + timedelta(minutes=6 * i)
        clock.set(ts + timedelta(minutes=30))
        t = txn(h(f"dil-v{i}"), ms[i % 2], ts, 50000, age=4)
        twin.apply_txn(t)
        if i < flagged:
            twin.apply_decision(decision(t.txn_id, "hold_verify"))
        twin.apply_txn(
            txn(ms[i % 2], cash, ts + timedelta(minutes=3), 47000, rail="IMPS", device="dev_dil")
        )
        twin.detect_dirty()
        rings = list(twin.registry.rings.values())
        if rings and detected_at is None:
            detected_at = i + 1
        if rings and rings[0].status == "retired" and retired_at is None:
            retired_at = i + 1
    return detected_at, retired_at


@pytest.mark.parametrize("flagged", [4, 6])
def test_dilution_by_unflagged_payers_never_retires_a_live_campaign(twin, clock, flagged):
    detected_at, retired_at = _dilution_campaign(twin, clock, flagged)
    (r,) = twin.registry.rings.values()
    assert detected_at is not None and detected_at <= flagged
    assert retired_at is None and r.status != "retired"
    assert r.victims == 80 and r.victims_signalled == flagged


async def test_diluted_ring_still_simulates(db_url, clock):
    app = create_app(db_url, clock=clock, settings=Settings(), background=False)
    twin, svc = app.state.twin, app.state.service
    _dilution_campaign(twin, clock, 4)
    await svc.flush()
    (rid,) = twin.published
    import os

    os.environ["TRUST_GATEWAY_HEADERS"] = "1"
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    assert (await c.get("/rings", headers=OFFICER)).json()["count"] == 1
    r = await c.post(f"/rings/{rid}/simulate", json={"k": 1}, headers=OFFICER)
    assert r.status_code == 200
    await svc.stop()
    twin.store.close()


def test_flapping_signal_retires_at_most_once_per_grace_window(twin, clock):
    camp = make_campaign(twin, clock, victims=6, mules=3)
    twin.detect_dirty()
    (rid,) = twin.registry.rings
    rows = []
    for i in range(12):  # signal off / on every ~100 s: never off for a whole grace window
        level = "allow" if i % 2 == 0 else "hold_verify"
        for tid in camp["txns"]:
            twin.apply_decision(decision(tid, level, seq=2 + i))
        clock.set(clock.now + timedelta(seconds=100))
        twin.dirty.update(account_key(m) for m in camp["mules"])
        rows += twin.detect_dirty().audit
    assert twin.registry.rings[rid].status != "retired"
    assert _kinds(rows).count("ring.retired") == 0
    # a sustained loss of evidence retires once (and only once)
    for tid in camp["txns"]:
        twin.apply_decision(decision(tid, "allow", seq=100))
    rows = _expire(twin, clock, camp, passes=6)
    assert _kinds(rows).count("ring.retired") == 1
    assert twin.registry.rings[rid].status == "retired"


def test_grace_timer_survives_restart(twin, clock, store):
    camp = make_campaign(twin, clock, victims=6, mules=3)
    twin.detect_dirty()
    (rid,) = twin.registry.rings
    _clear_flags(twin, camp)
    twin.detect_dirty()  # failing pass 1 at t
    clock.set(clock.now + timedelta(seconds=400))
    twin.dirty.update(account_key(m) for m in camp["mules"])
    twin.detect_dirty()  # failing pass 2, grace not yet elapsed
    assert twin.registry.rings[rid].status != "retired"
    twin2 = GraphTwin(store, Settings(), clock)  # restart
    clock.set(clock.now + timedelta(seconds=400))
    twin2.dirty.update(account_key(m) for m in camp["mules"])
    twin2.detect_dirty()
    assert twin2.registry.rings[rid].status == "retired"
