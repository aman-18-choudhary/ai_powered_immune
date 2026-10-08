import logging

import pytest
from sqlalchemy import update

from antibody_hub.api import contains_identifier
from antibody_hub.store import audit_outbox

from .conftest import ADMIN, ANALYST, hdr, mule

ZW = "​"
BAD = [
    "(022) 2345 6789",
    "(98765) 43210",
    "(98765)43210",
    "9876  543210",
    "9876. 543210",
    "1234 - 5678 - 9012",
    "9876 543210",
    "9876 543210",
    "9876\t543210",
    "9876\n543210",
    "9876/543210",
    "9876,543210",
    "9876_543210",
    "9876\x00543210",
    f"9876{ZW}543210",
    "①②③④⑤⑥⑦⑧⑨①",  # circled digits
    "¹²³⁴⁵⁶⁷⁸⁹⁰",  # superscripts
    "＋919876543210",
    "bob＠okaxis",
    "name @ okaxis",
    "name  @  okaxis",
    "ABCDE1234F",
    "abcde1234f",
    "HDFC0001234",
    "call 0091 98765 43210",
    "+91 98765 43210",
    "123456789",
    "ref 1234 5678 9",
    "9.8.7.6.5.4.3.2.1",
]
GOOD = [
    "case-77 reviewed twice on 12 Oct",
    "ticket 4521",
    "3 banks reported",
    "false positive per case 42",
    "merchant verified by phone call",
    "reviewed on 2026-03-10",
]


@pytest.mark.parametrize("text", BAD)
def test_guard_rejects(text):
    assert contains_identifier(text), repr(text)


@pytest.mark.parametrize("text", GOOD)
def test_guard_accepts(text):
    assert not contains_identifier(text), text


@pytest.mark.parametrize("text", BAD)
async def test_guard_applies_to_all_three_fields(client, text):
    h = {"key_hash": mule(), "kind": "mule_account", "source_bank": "bank_a"}
    r1 = await client.post("/antibodies", json=h | {"evidence_ref": text}, headers=ANALYST)
    r2 = await client.post("/protected", json={"key_hash": mule("m"), "note": text}, headers=ADMIN)
    ab = (await client.post("/antibodies", json=h, headers=ANALYST)).json()
    r3 = await client.post(
        f"/antibodies/{ab['antibody_id']}/revoke", json={"reason": text}, headers=ANALYST
    )
    assert (r1.status_code, r2.status_code, r3.status_code) == (422, 422, 422)


# ------------------------------------------------------------------ logging
async def test_no_request_data_in_any_log(client, caplog):
    caplog.set_level(logging.DEBUG)
    raw = "987654321098"
    h = {"key_hash": mule(), "kind": "mule_account", "source_bank": "bank_a"}
    ab = (await client.post("/antibodies", json=h, headers=ANALYST)).json()
    await client.post(
        f"/antibodies/{ab['antibody_id']}/revoke", json={"reason": f"acct {raw}"}, headers=ANALYST
    )
    await client.delete(f"/antibodies/{ab['antibody_id']}?reason=acct+{raw}", headers=ANALYST)
    await client.get(f"/antibodies/exact?bank_id=bank_a&since=zz{raw}", headers=ANALYST)
    own = [r for r in caplog.records if r.name != "httpx"]
    text = "\n".join(r.getMessage() for r in own)
    assert raw not in text and "reason" not in text and "?" not in text
    access = [r.getMessage() for r in own if r.name == "antibody_hub.access"]
    assert any("route=/antibodies/{ab_id}/revoke status=422" in m for m in access)
    assert all(m.startswith("method=") and "rid=" in m for m in access)
    assert not [r for r in caplog.records if r.name == "uvicorn.access"]


# ------------------------------------------------------------------ cross-bank scoping
async def test_other_banks_analyst_never_sees_originating_bank_details(client):
    a = hdr("analyst", "alice-pseudo", "bank_a")
    b = hdr("analyst", "bob-pseudo", "bank_b")
    h = {"key_hash": mule(), "kind": "mule_account", "source_bank": "bank_a"}
    ab = (
        await client.post("/antibodies", json=h | {"evidence_ref": "case:A-77"}, headers=a)
    ).json()
    secrets = ["bank_a", "alice-pseudo", "case:A-77", "confirmed_by", "evidence_ref", "source_bank"]
    ident = ab["antibody_id"]
    resps = [
        await client.get("/antibodies", headers=b),
        await client.get(f"/antibodies/{ident}", headers=b),
        await client.post("/antibodies", json=h | {"source_bank": "bank_b"}, headers=b),
        await client.post(f"/antibodies/{ident}/revoke", json={"reason": "mistake"}, headers=b),
        await client.get("/antibodies?state=all", headers=b),
    ]
    for r in resps:
        assert r.status_code == 200, r.text
        for sec in secrets:
            assert sec not in r.text, (sec, r.text)
    one = resps[1].json()
    assert one["key_hash"] == ab["key_hash"] and one["active"] is True and one["revoked"] is False
    assert resps[3].json()["revoked"] is True and resps[3].json()["active"] is False
    # own bank, officer and admin keep the full view
    for who in (a, hdr("officer", "o1"), ADMIN):
        full = (await client.get(f"/antibodies/{ident}", headers=who)).json()
        assert full["source_bank"] == "bank_a" and full["confirmed_by"] == "alice-pseudo"
        assert full["evidence_ref"] == "case:A-77"
    assert (await client.get(f"/antibodies/{ident}", headers=ADMIN)).json()[
        "revoked_by"
    ] == "bob-pseudo"


# ------------------------------------------------------------------ source_bank
@pytest.mark.parametrize("bad", ["Bank A", "x", "bank a", "9876543210 hdfc", "a" * 33, "b@nk"])
async def test_source_bank_charset_when_registry_empty(db_url, bus, clock, bad):
    import httpx

    from antibody_hub.api import create_app

    app = create_app(database_url=db_url, bus=bus, clock=clock, banks=[], drain_interval_s=0)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        h = {"key_hash": mule(), "kind": "mule_account"}
        assert (
            await c.post("/antibodies", json=h | {"source_bank": bad}, headers=ADMIN)
        ).status_code == 422
        assert (
            await c.post("/antibodies", json=h | {"source_bank": "bank_z"}, headers=ADMIN)
        ).status_code == 201


# ------------------------------------------------------------------ outbox parking
async def test_park_requires_attempts_and_elapsed_time_then_unpark(client, app, bus, clock):
    hub = app.state.hub
    hub.store.park_attempts = 2
    hub.store.park_after_s = 900
    poisoned = mule("poison")
    orig = bus.publish_raw
    down = {"on": True}

    async def flaky(topic, key, raw):
        if down["on"] and key == poisoned:
            raise ConnectionError("blip")
        await orig(topic, key, raw)

    bus.publish_raw = flaky
    h = {"key_hash": poisoned, "kind": "mule_account", "source_bank": "bank_a"}
    await client.post("/antibodies", json=h, headers=ANALYST)
    for _ in range(5):  # many failed drains inside the blip window: still not parked
        await hub.drain()
    assert hub.store.count_parked() == 0
    clock.advance(seconds=901)
    await hub.drain()
    assert hub.store.count_parked() == 1
    assert [e for e in await _antibody_events(bus)] == []
    # non-admin cannot unpark; admin can; row then delivers
    assert (await client.post("/admin/outbox/unpark", headers=ANALYST)).status_code == 403
    down["on"] = False
    r = await client.post("/admin/outbox/unpark", headers=ADMIN)
    assert r.json() == {"unparked": 1}
    assert hub.store.count_parked() == 0 and len(await _antibody_events(bus)) == 1
    assert (
        "antibody_hub_outbox_unparked_total 1" in (await client.get("/metrics", headers=ADMIN)).text
    )


async def _antibody_events(bus):
    from .conftest import events

    return events(bus)


def test_held_keys_filtered_before_limit(db_url, clock):
    from antibody_hub.store import AntibodyStore

    store = AntibodyStore(db_url, clock=clock)
    store.submit("mule_account", mule("held"), "bank_a", "a")
    store.revoke(store.listing("all", 10)[0]["antibody_id"], "a", "r")
    store.submit("mule_account", mule("other"), "bank_a", "a")
    rows = store.pending(100)
    held_rows = [r for r in rows if r[2] == mule("held")]
    assert len(held_rows) == 2
    with store.engine.begin() as c:
        c.execute(
            update(audit_outbox).where(audit_outbox.c.id == held_rows[0][0]).values(parked=True)
        )
    got = store.pending(2)  # LIMIT 2 must not be consumed by the held key's second row
    assert got and all(r[2] != mule("held") for r in got) and len(got) == 2
