import asyncio
import base64
import logging
import sqlite3
from datetime import timedelta

import httpx
from scam_contracts.hashing import keyed_hash
from scam_contracts.topics import Topics

from antibody_hub.api import create_app
from antibody_hub.bloom import BloomFilter

from .conftest import ADMIN, ANALYST, BANK_A, FED_KEY, events, hdr, mule

RAW_ACCOUNT = "50100123456789"
RAW_PHONE = "9876543210"


def body(h=None, **kw):
    return {"kind": "mule_account", "key_hash": h or mule(), "source_bank": "bank_a"} | kw


async def bloom(client, bank="bank_a", headers=BANK_A, **kw):
    r = await client.get(f"/antibodies/bloom?bank_id={bank}", headers=headers | kw)
    return r


# ---------------------------------------------------------------- privacy
async def test_raw_account_number_never_persisted(client, app, db_url, bus, caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    h = keyed_hash(RAW_ACCOUNT, "mule_account", FED_KEY)
    r = await client.post("/antibodies", json=body(h, evidence_ref="case-77"), headers=ANALYST)
    assert r.status_code == 201
    await app.state.hub.drain()
    await client.delete(f"/antibodies/{r.json()['antibody_id']}?reason=test", headers=ANALYST)
    await app.state.hub.drain()
    con = sqlite3.connect(tmp_path / "hub.db")
    dump = "\n".join(con.iterdump())
    con.close()
    published = "\n".join(
        raw.decode() for t in (Topics.ANTIBODIES, Topics.LEDGER) for _, raw in bus.messages(t)
    )
    for haystack in (dump, published, caplog.text, r.text):
        assert RAW_ACCOUNT not in haystack
    assert h in dump and h in published  # the hash itself is the shared datum
    # ledger payloads carry no full hash
    assert all(h not in raw.decode() for _, raw in bus.messages(Topics.LEDGER))


async def test_raw_identifier_shaped_key_hash_rejected(client):
    for bad in (RAW_ACCOUNT, RAW_PHONE, "+91" + RAW_PHONE, "A" * 64, "g" * 64, "ab" * 31):
        r = await client.post("/antibodies", json=body(bad), headers=ANALYST)
        assert r.status_code == 422, bad


async def test_unknown_kind_rejected(client):
    r = await client.post("/antibodies", json=body() | {"kind": "pan"}, headers=ANALYST)
    assert r.status_code == 422


# ---------------------------------------------------------------- authn / authz
async def test_unconfirmed_antibody_rejected(client):
    assert (await client.post("/antibodies", json=body())).status_code == 401
    h = {"X-Principal-Role": "analyst"}  # no sub
    assert (await client.post("/antibodies", json=body(), headers=h)).status_code == 401


async def test_headers_untrusted_without_env(client, monkeypatch):
    monkeypatch.delenv("TRUST_GATEWAY_HEADERS")
    assert (await client.post("/antibodies", json=body(), headers=ANALYST)).status_code == 401


async def test_gateway_secret_gate(client, monkeypatch):
    monkeypatch.setenv("HUB_GATEWAY_SECRET", "s3cret")
    monkeypatch.delenv("TRUST_GATEWAY_HEADERS")
    ok = ANALYST | {"X-Gateway-Secret": "s3cret"}
    assert (await client.post("/antibodies", json=body(), headers=ok)).status_code == 201
    bad = ANALYST | {"X-Gateway-Secret": "nope"}
    assert (await client.post("/antibodies", json=body(), headers=bad)).status_code == 401
    odd = [
        (b"x-gateway-secret", "s\u00e9\u00e7".encode("latin-1")),
        (b"x-principal-role", b"analyst"),
        (b"x-principal-sub", b"a"),
    ]
    assert (await client.post("/antibodies", json=body(), headers=odd)).status_code == 401
    assert (await client.post("/antibodies", json=body(), headers=ANALYST)).status_code == 401


async def test_non_ascii_header_values_do_not_500(client):
    h = [(b"x-principal-role", b"analyst"), (b"x-principal-sub", "ané".encode("latin-1"))]
    r = await client.post("/antibodies", json=body(), headers=h)
    assert r.status_code == 201 and r.json()["confirmed_by"] == "ané"


async def test_citizens_and_officers_cannot_create_or_revoke(client):
    for role in ("citizen", "officer", "bank"):
        r = await client.post("/antibodies", json=body(), headers=hdr(role, "u1", "bank_a"))
        assert r.status_code == 403, role
    created = (await client.post("/antibodies", json=body(), headers=ANALYST)).json()
    for role in ("citizen", "officer", "bank"):
        r = await client.delete(
            f"/antibodies/{created['antibody_id']}?reason=x", headers=hdr(role, "u1", "bank_a")
        )
        assert r.status_code == 403, role


async def test_confirmed_by_cannot_be_spoofed_via_body(client):
    r = await client.post("/antibodies", json=body() | {"confirmed_by": "ceo"}, headers=ANALYST)
    assert r.status_code == 201
    assert r.json()["confirmed_by"] == "analyst-1"


async def test_source_bank_must_match_principal_bank(client):
    r = await client.post(
        "/antibodies", json=body(), headers=hdr("analyst", "a", "bank_b")
    )  # claims bank_a but principal is bank_b
    assert r.status_code == 403
    r = await client.post("/antibodies", json=body(), headers=hdr("analyst", "a", "bank_a"))
    assert r.status_code == 201


async def test_unregistered_source_bank_rejected(client):
    r = await client.post("/antibodies", json=body() | {"source_bank": "evil"}, headers=ADMIN)
    assert r.status_code == 422


# ---------------------------------------------------------------- create / dedupe
async def test_create_publishes_event_keyed_by_hash(client, app, bus, clock):
    r = await client.post("/antibodies", json=body(), headers=ANALYST)
    assert r.status_code == 201
    ab = r.json()
    assert ab["revoked"] is False and ab["kind"] == "mule_account"
    assert ab["expires_at"].startswith((clock.now + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M"))
    ev = events(bus)
    assert len(ev) == 1 and ev[0]["antibody_id"] == ab["antibody_id"] and not ev[0]["revoked"]
    assert bus.messages(Topics.ANTIBODIES)[0][0] == ab["key_hash"]
    assert len(events(bus, Topics.LEDGER)) == 1


async def test_hash_differs_by_kind_distinct_antibodies(client):
    ids = set()
    for kind in ("mule_account", "script", "device"):
        h = keyed_hash("same-value", kind, FED_KEY)
        r = await client.post("/antibodies", json=body(h, kind=kind), headers=ANALYST)
        assert r.status_code == 201
        ids.add(r.json()["antibody_id"])
    assert len(ids) == 3


async def test_resubmit_is_idempotent_no_event_no_extension(client, app, bus, clock):
    first = (await client.post("/antibodies", json=body(), headers=ANALYST)).json()
    clock.advance(days=3)
    r = await client.post("/antibodies", json=body(), headers=hdr("analyst", "analyst-2"))
    assert r.status_code == 200
    assert r.json()["antibody_id"] == first["antibody_id"]
    assert r.json()["expires_at"] == first["expires_at"]
    assert r.json()["confirmed_by"] == "analyst-1"
    assert len(events(bus)) == 1 and len(events(bus, Topics.LEDGER)) == 1


async def test_extend_requires_flag_and_audits_once(client, app, bus, clock):
    first = (await client.post("/antibodies", json=body(), headers=ANALYST)).json()
    clock.advance(days=3)
    r = await client.post("/antibodies", json=body(extend=True), headers=ANALYST)
    assert r.status_code == 200 and r.json()["expires_at"] > first["expires_at"]
    again = await client.post("/antibodies", json=body(extend=True), headers=ANALYST)
    assert again.json()["expires_at"] == r.json()["expires_at"]  # same instant: no new audit
    assert len(events(bus, Topics.LEDGER)) == 2  # create + one extend
    assert len(events(bus)) == 2  # banks learn the new expiry


async def test_ttl_env_override(db_url, bus, clock, monkeypatch):
    monkeypatch.setenv("ANTIBODY_TTL_DAYS", "2")
    app = create_app(
        database_url=db_url, bus=bus, clock=clock, banks=["bank_a"], drain_interval_s=0
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/antibodies", json=body(), headers=ANALYST)
    assert r.json()["expires_at"].startswith(
        (clock.now + timedelta(days=2)).strftime("%Y-%m-%dT%H")
    )


async def test_concurrent_duplicate_posts_create_one_antibody_one_event(client, app, bus):
    rs = await asyncio.gather(
        *[client.post("/antibodies", json=body(), headers=ANALYST) for _ in range(8)]
    )
    assert sorted(r.status_code for r in rs) == [200] * 7 + [201]
    assert len({r.json()["antibody_id"] for r in rs}) == 1
    await app.state.hub.drain()
    assert len(events(bus)) == 1 and len(events(bus, Topics.LEDGER)) == 1
    listing = (await client.get("/antibodies?state=all", headers=ANALYST)).json()
    assert len(listing) == 1


# ---------------------------------------------------------------- revoke / tombstone
async def test_revoked_antibody_removed_and_tombstone_published(client, app, bus):
    ab = (await client.post("/antibodies", json=body(), headers=ANALYST)).json()
    assert BloomFilter.from_snapshot((await bloom(client)).json()).contains(ab["key_hash"])
    r = await client.delete(
        f"/antibodies/{ab['antibody_id']}?reason=false+positive", headers=ANALYST
    )
    assert r.status_code == 200 and r.json()["revoked"] is True
    snap = (await bloom(client)).json()
    assert not BloomFilter.from_snapshot(snap).contains(ab["key_hash"]) and snap["count"] == 0
    ev = events(bus)
    assert [e["revoked"] for e in ev] == [False, True]
    assert ev[1]["antibody_id"] == ab["antibody_id"]
    exact = (await client.get("/antibodies/exact?bank_id=bank_a", headers=BANK_A)).json()
    assert exact["items"] == []


async def test_revoke_idempotent_requires_reason_and_404(client, bus):
    ab = (await client.post("/antibodies", json=body(), headers=ANALYST)).json()
    url = f"/antibodies/{ab['antibody_id']}"
    assert (await client.delete(url, headers=ANALYST)).status_code == 422  # reason required
    assert (await client.delete(url + "?reason=", headers=ANALYST)).status_code == 422
    assert (await client.delete(url + "?reason=r", headers=ANALYST)).status_code == 200
    assert (await client.delete(url + "?reason=r", headers=ANALYST)).status_code == 200
    assert len(events(bus)) == 2  # create + exactly one tombstone
    assert (
        await client.delete("/antibodies/" + "0" * 64 + "?reason=r", headers=ANALYST)
    ).status_code == 404


async def test_resubmission_after_revoke_new_generation(client, bus):
    a1 = (await client.post("/antibodies", json=body(), headers=ANALYST)).json()
    await client.delete(f"/antibodies/{a1['antibody_id']}?reason=r", headers=ANALYST)
    r = await client.post("/antibodies", json=body(), headers=ANALYST)
    assert r.status_code == 201
    a2 = r.json()
    assert a2["antibody_id"] != a1["antibody_id"] and a2["generation"] == 2
    assert [e["revoked"] for e in events(bus)] == [False, True, False]
    assert (await client.get(f"/antibodies/{a1['antibody_id']}", headers=ANALYST)).json()["revoked"]


# ---------------------------------------------------------------- expiry
async def test_expired_antibody_not_in_bloom_and_one_tombstone(client, app, bus, clock):
    ab = (await client.post("/antibodies", json=body(), headers=ANALYST)).json()
    clock.advance(days=14, seconds=1)
    # excluded even before the sweep runs
    snap = (await bloom(client)).json()
    assert snap["count"] == 0 and not BloomFilter.from_snapshot(snap).contains(ab["key_hash"])
    assert (await client.get("/antibodies/exact?bank_id=bank_a", headers=BANK_A)).json()[
        "items"
    ] == []
    assert await app.state.hub.sweep() == 1
    assert await app.state.hub.sweep() == 0
    ev = events(bus)
    assert [e["revoked"] for e in ev] == [False, True]
    got = (await client.get(f"/antibodies/{ab['antibody_id']}", headers=ANALYST)).json()
    assert got["revoked"] and got["revoked_by"] == "system:expiry"
    # a later revoke does not publish a second tombstone
    await client.delete(f"/antibodies/{ab['antibody_id']}?reason=r", headers=ANALYST)
    assert len(events(bus)) == 2


async def test_resubmit_after_expiry_creates_new_generation(client, bus, clock):
    a1 = (await client.post("/antibodies", json=body(), headers=ANALYST)).json()
    clock.advance(days=15)
    r = await client.post("/antibodies", json=body(), headers=ANALYST)
    assert r.status_code == 201 and r.json()["generation"] == 2
    assert [e["revoked"] for e in events(bus)] == [False, True, False]
    assert events(bus)[1]["antibody_id"] == a1["antibody_id"]


# ---------------------------------------------------------------- protected
async def test_protected_hash_cannot_become_antibody(client, bus):
    h = keyed_hash("merchant-acct", "mule_account", FED_KEY)
    r = await client.post("/protected", json={"key_hash": h, "note": "bigbazaar"}, headers=ADMIN)
    assert r.status_code == 201
    assert (await client.post("/protected", json={"key_hash": h}, headers=ADMIN)).status_code == 200
    r = await client.post("/antibodies", json=body(h), headers=ADMIN)
    assert r.status_code == 409 and r.json()["detail"] == "PROTECTED"
    assert events(bus) == []


async def test_protected_only_admin(client):
    r = await client.post("/protected", json={"key_hash": mule()}, headers=ANALYST)
    assert r.status_code == 403


async def test_protecting_active_antibody_auto_revokes_with_tombstone(client, bus):
    ab = (await client.post("/antibodies", json=body(), headers=ANALYST)).json()
    r = await client.post("/protected", json={"key_hash": ab["key_hash"]}, headers=ADMIN)
    assert r.status_code == 201 and r.json()["revoked_antibodies"] == [ab["antibody_id"]]
    assert [e["revoked"] for e in events(bus)] == [False, True]
    assert (await bloom(client)).json()["count"] == 0
    got = (await client.get(f"/antibodies/{ab['antibody_id']}", headers=ANALYST)).json()
    assert got["revoked_by"] == "admin-1"


# ---------------------------------------------------------------- outbox
async def test_outbox_failure_once_then_drain_delivers_exactly_once(client, app, bus):
    bus.fail_next = 1
    r = await client.post("/antibodies", json=body(), headers=ANALYST)
    assert r.status_code == 201  # state change is not failed by the bus
    assert events(bus) == []
    await app.state.hub.drain()
    await app.state.hub.drain()
    assert len(events(bus)) == 1 and len(events(bus, Topics.LEDGER)) == 1


async def test_idempotent_reentry_drains_outbox(client, app, bus):
    bus.fail_next = 1
    await client.post("/antibodies", json=body(), headers=ANALYST)
    assert events(bus) == []
    r = await client.post("/antibodies", json=body(), headers=ANALYST)
    assert r.status_code == 200
    assert len(events(bus)) == 1 and len(events(bus, Topics.LEDGER)) == 1


async def test_ledger_payload_hash_deterministic_and_deduped(client, app, bus):
    await client.post("/antibodies", json=body(), headers=ANALYST)
    await client.post("/antibodies", json=body(), headers=ANALYST)
    led = events(bus, Topics.LEDGER)
    assert len(led) == 1
    assert led[0]["service"] == "antibody-hub" and led[0]["actor"] == "analyst-1"
    assert led[0]["event_type"] == "antibody.created" and len(led[0]["payload_hash"]) == 64


async def test_state_survives_restart(db_url, bus, clock):
    a = create_app(database_url=db_url, bus=bus, clock=clock, banks=["bank_a"], drain_interval_s=0)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=a), base_url="http://t") as c:
        first = (await c.post("/antibodies", json=body(), headers=ANALYST)).json()
    b = create_app(database_url=db_url, bus=bus, clock=clock, banks=["bank_a"], drain_interval_s=0)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=b), base_url="http://t") as c:
        r = await c.post("/antibodies", json=body(), headers=ANALYST)
    assert r.status_code == 200 and r.json()["antibody_id"] == first["antibody_id"]


# ---------------------------------------------------------------- reads
async def test_list_get_roles_and_bounds(client, clock):
    ids = []
    for i in range(5):
        clock.advance(hours=1)
        h = mule(f"acct{i}")
        ids.append((await client.post("/antibodies", json=body(h), headers=ANALYST)).json())
    await client.delete(f"/antibodies/{ids[0]['antibody_id']}?reason=r", headers=ANALYST)
    officer = hdr("officer", "o1")
    active = (await client.get("/antibodies", headers=officer)).json()
    assert [a["antibody_id"] for a in active] == [
        a["antibody_id"] for a in ids[1:]
    ]  # soonest expiry
    assert len((await client.get("/antibodies?state=all", headers=officer)).json()) == 5
    assert len((await client.get("/antibodies?limit=2", headers=officer)).json()) == 2
    assert (await client.get("/antibodies?limit=100000", headers=officer)).status_code == 422
    assert (await client.get("/antibodies", headers=hdr("citizen", "c"))).status_code == 403
    assert (await client.get("/antibodies", headers=BANK_A)).status_code == 403
    assert (await client.get("/antibodies/" + "0" * 64, headers=officer)).status_code == 404
    assert (
        await client.get(f"/antibodies/{ids[1]['antibody_id']}", headers=officer)
    ).status_code == 200


async def test_bloom_snapshot_contents_etag_and_304(client):
    hs = [mule(f"a{i}") for i in range(20)]
    for h in hs:
        await client.post("/antibodies", json=body(h), headers=ANALYST)
    r = await bloom(client)
    assert r.status_code == 200
    snap = r.json()
    assert snap["count"] == 20 and snap["n"] >= 40 and snap["fp_rate"] == 1e-6
    assert {"version", "m", "k", "bits", "generated_at"} <= snap.keys()
    assert len(base64.b64decode(snap["bits"])) * 8 == snap["m"]
    bf = BloomFilter.from_snapshot(snap)
    assert all(bf.contains(h) for h in hs) and not bf.contains(mule("other"))
    etag = r.headers["etag"]
    r2 = await bloom(client, **{"If-None-Match": etag})
    assert r2.status_code == 304
    await client.post("/antibodies", json=body(mule("new")), headers=ANALYST)
    r3 = await bloom(client, **{"If-None-Match": etag})
    assert r3.status_code == 200 and r3.headers["etag"] != etag


async def test_bloom_access_rules(client):
    assert (await bloom(client, bank="bank_zzz", headers=ADMIN)).status_code == 403
    assert (await bloom(client, bank="bank_b")).status_code == 403  # bank A asking as bank B
    assert (await bloom(client, headers=ANALYST)).status_code == 403
    assert (await bloom(client, headers=hdr("citizen", "c"))).status_code == 403
    assert (await client.get("/antibodies/bloom?bank_id=bank_a")).status_code == 401
    assert (await bloom(client, bank="bank_b", headers=ADMIN)).status_code == 200


async def test_exact_pagination(client, clock):
    hs = []
    for i in range(7):
        clock.advance(minutes=1)
        hs.append(mule(f"p{i}"))
        await client.post("/antibodies", json=body(hs[-1]), headers=ANALYST)
    got, cursor = [], None
    for _ in range(10):
        q = "/antibodies/exact?bank_id=bank_a&limit=3" + (f"&since={cursor}" if cursor else "")
        page = (await client.get(q, headers=BANK_A)).json()
        got += [i["key_hash"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert got == hs
    assert (await client.get("/antibodies/exact?bank_id=nope", headers=ADMIN)).status_code == 403
    assert (
        await client.get("/antibodies/exact?bank_id=bank_a&since=zzz", headers=BANK_A)
    ).status_code == 422


async def test_health_and_gated_metrics(client, monkeypatch):
    assert (await client.get("/healthz")).status_code == 200
    assert (await client.get("/readyz")).status_code == 200
    assert (await client.get("/metrics", headers=hdr("citizen", "c"))).status_code == 403
    await client.post("/antibodies", json=body(), headers=ANALYST)
    r = await client.get("/metrics", headers=ANALYST)
    assert r.status_code == 200 and "antibody_hub_active 1" in r.text
    assert mule() not in r.text
    monkeypatch.setenv("HUB_METRICS_TOKEN", "tok")
    monkeypatch.delenv("TRUST_GATEWAY_HEADERS")
    assert (await client.get("/metrics")).status_code == 401
    assert (await client.get("/metrics", headers={"X-Metrics-Token": "tok"})).status_code == 200
    assert (await client.get("/metrics", headers={"X-Metrics-Token": "bad"})).status_code == 401


async def test_two_threads_create_exactly_one(db_url, clock):
    import threading

    from antibody_hub.store import AntibodyStore

    store = AntibodyStore(db_url, clock=clock)
    barrier = threading.Barrier(2)
    out = []

    def go():
        barrier.wait()
        out.append(store.submit("mule_account", mule(), "bank_a", "analyst-1"))

    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(r.created for r in out) == [False, True]
    assert len(store.pending()) == 2  # one antibody event + one ledger entry


async def test_lifespan_runs_expiry_sweep(db_url, bus, clock):
    app = create_app(
        database_url=db_url, bus=bus, clock=clock, banks=["bank_a"], drain_interval_s=0.05
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            await c.post("/antibodies", json=body(), headers=ANALYST)
            clock.advance(days=15)
            for _ in range(100):
                if len(events(bus)) == 2:
                    break
                await asyncio.sleep(0.05)
    assert [e["revoked"] for e in events(bus)] == [False, True]
