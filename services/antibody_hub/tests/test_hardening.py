import asyncio
import logging
import sqlite3
import threading

import httpx
import pytest
from scam_contracts.topics import Topics
from sqlalchemy import func, select

from antibody_hub.api import create_app
from antibody_hub.bloom import BloomFilter
from antibody_hub.store import (
    AntibodyStore,
    antibodies,
    audit_outbox,
    corroborations,
    protected_hashes,
)

from .conftest import ADMIN, ANALYST, BANK_A, events, hdr, mule

RAW_VALUES = [
    "123456789012",
    "1234-5678-9012",
    "1234 5678 9012 3456",
    "+919876543210",
    "+91 98765 43210",
    "9876543210",
    "bob@okbank",
    "alice@example.com",
    "HDFC0001234",
]


def service_logs(caplog) -> str:
    """Log text from the service and libraries, excluding the test HTTP client's own request log."""
    return "\n".join(r.getMessage() for r in caplog.records if r.name != "httpx")


def body(h=None, **kw):
    return {"kind": "mule_account", "key_hash": h or mule(), "source_bank": "bank_a"} | kw


@pytest.mark.parametrize("raw", RAW_VALUES)
async def test_free_text_fields_reject_raw_identifiers(client, app, db_url, tmp_path, caplog, raw):
    caplog.set_level(logging.DEBUG)
    text = f"acct {raw} flagged"
    # evidence_ref
    r = await client.post("/antibodies", json=body(evidence_ref=text), headers=ANALYST)
    assert r.status_code == 422 and raw not in r.text
    r = await client.post("/antibodies", json=body(evidence_ref=raw), headers=ANALYST)
    assert r.status_code == 422 and raw not in r.text
    assert (await client.get("/antibodies?state=all", headers=ANALYST)).json() == []
    # revoke reason
    ab = (await client.post("/antibodies", json=body(), headers=ANALYST)).json()
    r = await client.delete(
        f"/antibodies/{ab['antibody_id']}", params={"reason": text}, headers=ANALYST
    )
    assert r.status_code == 422 and raw not in r.text
    assert (await client.get(f"/antibodies/{ab['antibody_id']}", headers=ANALYST)).json()[
        "revoked"
    ] is False
    # protected note
    h = mule("merchant")
    r = await client.post("/protected", json={"key_hash": h, "note": text}, headers=ADMIN)
    assert r.status_code == 422 and raw not in r.text
    assert (await client.post("/antibodies", json=body(h), headers=ANALYST)).status_code == 201
    con = sqlite3.connect(tmp_path / "hub.db")
    dump = "\n".join(con.iterdump())
    con.close()
    assert raw not in dump and raw not in service_logs(caplog)


async def test_clean_free_text_accepted(client):
    ab = (
        await client.post("/antibodies", json=body(evidence_ref="case:2026-0042"), headers=ANALYST)
    ).json()
    r = await client.delete(
        f"/antibodies/{ab['antibody_id']}",
        params={"reason": "false positive, case 42"},
        headers=ANALYST,
    )
    assert r.status_code == 200
    r = await client.post(
        "/protected", json={"key_hash": mule("m"), "note": "big retailer"}, headers=ADMIN
    )
    assert r.status_code == 201


async def test_422_bodies_never_echo_input(client, caplog):
    caplog.set_level(logging.DEBUG)
    raw = "123456789012345678"
    resps = [
        await client.post("/antibodies", json=body(raw), headers=ANALYST),
        await client.post("/antibodies", json=body(source_bank=raw, kind=raw), headers=ANALYST),
        await client.post("/antibodies", json=body(evidence_ref=raw), headers=ANALYST),
        await client.post("/protected", json={"key_hash": raw}, headers=ADMIN),
        await client.delete("/antibodies/x", params={"reason": raw}, headers=ANALYST),
        await client.delete(f"/protected/{raw}", headers=ADMIN),
        await client.post("/antibodies", content=raw, headers=ANALYST),
    ]
    for r in resps:
        assert r.status_code == 422, r.text
        assert raw not in r.text
        for item in r.json()["detail"]:
            assert set(item) == {"loc", "msg", "type"}
    assert raw not in service_logs(caplog)


# ------------------------------------------------------------------ protected
async def test_concurrent_add_protected_idempotent_single_revoke(client, app, bus):
    ab = (await client.post("/antibodies", json=body(), headers=ANALYST)).json()
    rs = await asyncio.gather(
        *[
            client.post("/protected", json={"key_hash": ab["key_hash"]}, headers=ADMIN)
            for _ in range(6)
        ]
    )
    assert sorted(r.status_code for r in rs) == [200] * 5 + [201]
    assert sum(len(r.json()["revoked_antibodies"]) for r in rs) == 1
    await app.state.hub.drain()
    assert [e["revoked"] for e in events(bus)] == [False, True]


def test_threads_add_protected_same_hash(db_url, clock):
    store = AntibodyStore(db_url, clock=clock)
    store.submit("mule_account", mule(), "bank_a", "a")
    out, barrier = [], threading.Barrier(6)

    def go():
        barrier.wait()
        out.append(store.add_protected(mule(), "admin", None))

    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(c for c, _ in out) == [False] * 5 + [True]
    assert sum(len(r) for _, r in out) == 1


async def test_remove_protected_idempotent_audited(client, bus, app):
    h = mule("shop")
    await client.post("/protected", json={"key_hash": h}, headers=ADMIN)
    assert (await client.post("/antibodies", json=body(h), headers=ANALYST)).status_code == 409
    assert (await client.delete(f"/protected/{h}", headers=ANALYST)).status_code == 403
    assert (await client.delete(f"/protected/{h}", headers=ADMIN)).json()["removed"] is True
    assert (await client.delete(f"/protected/{h}", headers=ADMIN)).json()["removed"] is False
    assert (await client.post("/antibodies", json=body(h), headers=ANALYST)).status_code == 201
    types = [e["event_type"] for e in events(bus, Topics.LEDGER)]
    assert types.count("protected.added") == 1 and types.count("protected.removed") == 1


# ------------------------------------------------------------------ outbox policy
async def test_poison_row_parks_only_its_key_and_rest_continue(client, app, bus):
    hub = app.state.hub
    hub.store.max_attempts = 2
    poisoned = mule("poison")
    orig = bus.publish_raw

    async def selective(topic, key, raw):
        if key == poisoned:
            raise ConnectionError("poison")
        await orig(topic, key, raw)

    bus.publish_raw = selective
    await client.post("/antibodies", json=body(poisoned), headers=ANALYST)
    ab2 = (await client.post("/antibodies", json=body(mule("fine")), headers=ANALYST)).json()
    await hub.drain()
    assert hub.store.count_parked() == 1
    ab1 = (await client.get("/antibodies", headers=ANALYST)).json()[0]
    # a later tombstone for the poisoned key is held back (ordering), others still flow
    await client.delete(
        f"/antibodies/{ab1['antibody_id']}", params={"reason": "r"}, headers=ANALYST
    )
    await client.delete(
        f"/antibodies/{ab2['antibody_id']}", params={"reason": "r"}, headers=ANALYST
    )
    await hub.drain()
    got = [(e["key_hash"], e["revoked"]) for e in events(bus)]
    assert (poisoned, True) not in got and (poisoned, False) not in got
    assert (ab2["key_hash"], False) in got and (ab2["key_hash"], True) in got
    r = await client.get("/metrics", headers=ANALYST)
    assert "antibody_hub_outbox_parked 1" in r.text


async def test_sent_outbox_rows_purged_after_retention(client, app, clock):
    await client.post("/antibodies", json=body(), headers=ANALYST)
    hub = app.state.hub
    await hub.sweep()

    def count():
        with hub.store.engine.connect() as c:
            return c.execute(select(func.count()).select_from(audit_outbox)).scalar()

    assert count() == 2
    clock.advance(days=8)
    await hub.sweep()
    assert count() == 0


# ------------------------------------------------------------------ bloom
async def test_bloom_cached_by_version_and_304_does_not_build(client, app):
    hub = app.state.hub
    builds = []
    orig = hub._build_bloom
    hub._build_bloom = lambda v: (builds.append(v), orig(v))[1]
    await client.post("/antibodies", json=body(), headers=ANALYST)
    r1 = await client.get("/antibodies/bloom?bank_id=bank_a", headers=BANK_A)
    r2 = await client.get("/antibodies/bloom?bank_id=bank_a", headers=BANK_A)
    assert r1.json() == r2.json() and len(builds) == 1
    r3 = await client.get(
        "/antibodies/bloom?bank_id=bank_a", headers=BANK_A | {"If-None-Match": r1.headers["etag"]}
    )
    assert r3.status_code == 304 and len(builds) == 1
    await client.post("/antibodies", json=body(mule("two")), headers=ANALYST)
    await client.get("/antibodies/bloom?bank_id=bank_a", headers=BANK_A)
    assert len(builds) == 2


async def test_bloom_version_changes_when_one_revoked_and_one_created(client, clock):
    a = (await client.post("/antibodies", json=body(mule("1")), headers=ANALYST)).json()
    v1 = (await client.head("/antibodies/bloom?bank_id=bank_a", headers=BANK_A)).headers["etag"]
    await client.delete(f"/antibodies/{a['antibody_id']}", params={"reason": "r"}, headers=ANALYST)
    await client.post("/antibodies", json=body(mule("2")), headers=ANALYST)
    r = await client.get("/antibodies/bloom?bank_id=bank_a", headers=BANK_A)
    assert r.headers["etag"] != v1
    bf = BloomFilter.from_snapshot(r.json())
    assert bf.contains(mule("2")) and not bf.contains(mule("1"))


async def test_bloom_head_has_etag_no_body(client):
    await client.post("/antibodies", json=body(), headers=ANALYST)
    g = await client.get("/antibodies/bloom?bank_id=bank_a", headers=BANK_A)
    h = await client.head("/antibodies/bloom?bank_id=bank_a", headers=BANK_A)
    assert h.status_code == 200 and h.headers["etag"] == g.headers["etag"] and h.content == b""
    assert (
        await client.head("/antibodies/bloom?bank_id=bank_zzz", headers=ADMIN)
    ).status_code == 403


# ------------------------------------------------------------------ cross-bank
async def test_second_bank_gets_limited_view_and_corroboration_audited(client, app, bus):
    first = (
        await client.post("/antibodies", json=body(), headers=hdr("analyst", "a1", "bank_a"))
    ).json()
    b = hdr("analyst", "b1", "bank_b")
    r = await client.post("/antibodies", json=body(source_bank="bank_b"), headers=b)
    assert r.status_code == 200
    assert set(r.json()) == {"antibody_id", "active", "expires_at"}
    assert r.json()["antibody_id"] == first["antibody_id"]
    assert "bank_a" not in r.text and "confirmed_by" not in r.text
    await client.post("/antibodies", json=body(source_bank="bank_b"), headers=b)  # repeat
    assert len(events(bus)) == 1  # no new bus event
    led = [e["event_type"] for e in events(bus, Topics.LEDGER)]
    assert led.count("antibody.corroborated") == 1
    con = app.state.hub.store.engine.connect()
    assert con.execute(select(func.count()).select_from(corroborations)).scalar() == 1
    # same bank still gets the full view
    same = await client.post("/antibodies", json=body(), headers=hdr("analyst", "a2", "bank_a"))
    assert same.json()["confirmed_by"] == "a1"


async def test_cross_bank_revoke_audited_with_bank(client, app, bus):
    ab = (await client.post("/antibodies", json=body(), headers=ANALYST)).json()
    r = await client.delete(
        f"/antibodies/{ab['antibody_id']}",
        params={"reason": "r"},
        headers=hdr("analyst", "b1", "bank_b"),
    )
    assert r.status_code == 200 and r.json()["revoked_by_bank"] == "bank_b"
    types = [e["event_type"] for e in events(bus, Topics.LEDGER)]
    assert "antibody.revoked.cross_bank" in types and "antibody.revoked" not in types
    # same-bank revoke is a plain revoke
    ab2 = (await client.post("/antibodies", json=body(mule("x")), headers=ANALYST)).json()
    await client.delete(
        f"/antibodies/{ab2['antibody_id']}",
        params={"reason": "r"},
        headers=hdr("analyst", "a", "bank_a"),
    )
    assert "antibody.revoked" in [e["event_type"] for e in events(bus, Topics.LEDGER)]


def test_create_schema_idempotent(db_url, clock):
    store = AntibodyStore(db_url, clock=clock)
    store.create_schema()
    store.create_schema()
    assert store.count_active() == 0


async def test_app_respects_create_schema_flag(db_url, bus, clock, monkeypatch):
    AntibodyStore(db_url, clock=clock).close()  # "migrate"
    monkeypatch.setenv("HUB_CREATE_SCHEMA", "0")
    app = create_app(
        database_url=db_url, bus=bus, clock=clock, banks=["bank_a"], drain_interval_s=0
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.post("/antibodies", json=body(), headers=ANALYST)).status_code == 201
    _ = (antibodies, protected_hashes)
