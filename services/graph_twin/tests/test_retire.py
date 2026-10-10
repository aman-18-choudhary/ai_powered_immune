"""Rings are re-gated on refresh: when the evidence goes away the ring is retired (tombstone,
audit entry, hidden from the default list and from takedown), and it comes back under the same id
when the signals return."""

import json

import httpx

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


def _kinds(rows) -> list[str]:
    return [json.loads(r.body)["event_type"] for r in rows]


def test_ring_is_retired_when_decisions_are_downgraded(twin, clock):
    camp = make_campaign(twin, clock, victims=6, mules=3)
    twin.detect_dirty()
    (rid,) = twin.registry.rings
    assert twin.registry.rings[rid].status == "new"
    _clear_flags(twin, camp)
    res = twin.detect_dirty()
    r = twin.registry.rings[rid]
    assert r.status == "retired" and r.victims_signalled == 0
    assert "ring.retired" in _kinds(res.audit)
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
    res = twin.detect_dirty()
    assert twin.registry.rings[rid].status == "retired"
    assert "ring.retired" in _kinds(res.audit)


def test_confirmed_antibody_member_blocks_retirement(twin, clock):
    camp = make_campaign(twin, clock, victims=6, mules=3)
    twin.apply_antibody(antibody(camp["mules"][0], "ab-keep"))
    twin.detect_dirty()
    (rid,) = twin.registry.rings
    _clear_flags(twin, camp)
    twin.detect_dirty()
    assert twin.registry.rings[rid].status != "retired"


def test_retired_ring_returns_under_the_same_id_when_signals_return(twin, clock):
    camp = make_campaign(twin, clock, victims=6, mules=3)
    twin.detect_dirty()
    (rid,) = twin.registry.rings
    _clear_flags(twin, camp)
    twin.detect_dirty()
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
