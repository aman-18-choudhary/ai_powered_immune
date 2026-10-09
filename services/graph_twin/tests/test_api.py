import json
import logging
import re
from datetime import timedelta

import httpx
import pytest
from scam_contracts.models import LedgerEntryIn
from scam_contracts.topics import Topics
from svckit.bus import InMemoryBus

from graph_twin.api import create_app
from graph_twin.config import Settings
from graph_twin.graph import api_id

from .conftest import T0, Clock, h, make_campaign

OFFICER = {"X-Principal-Role": "officer", "X-Principal-Sub": "officer-1"}
ANALYST = {"X-Principal-Role": "analyst", "X-Principal-Sub": "analyst-1"}
ADMIN = {"X-Principal-Role": "admin", "X-Principal-Sub": "admin-1"}
CITIZEN = {"X-Principal-Role": "citizen", "X-Principal-Sub": "c-1"}


@pytest.fixture(autouse=True)
def _trust(monkeypatch):
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "1")
    for v in ("GRAPH_GATEWAY_SECRET", "GRAPH_METRICS_TOKEN"):
        monkeypatch.delenv(v, raising=False)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def bus() -> InMemoryBus:
    return InMemoryBus()


@pytest.fixture
def app(db_url, bus, clock):
    return create_app(db_url, bus=bus, clock=clock, settings=Settings(), background=False)


@pytest.fixture
async def seeded(app, clock):
    """An app with one detected ring (the lifespan is not run: tasks are driven by the test)."""
    twin, svc = app.state.twin, app.state.service
    camp = make_campaign(twin, clock, victims=9, mules=3)
    await svc.flush()
    (rid,) = twin.published
    yield camp, rid
    await svc.stop()
    twin.store.close()


@pytest.fixture
def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_untrusted_requests_are_401(app, client, monkeypatch, seeded):
    _, rid = seeded
    monkeypatch.delenv("TRUST_GATEWAY_HEADERS")
    for path in ("/rings", f"/rings/{rid}", "/stats", "/metrics"):
        assert (await client.get(path, headers=OFFICER)).status_code == 401
    r = await client.post(f"/rings/{rid}/simulate", json={"k": 1}, headers=OFFICER)
    assert r.status_code == 401
    assert (await client.get("/healthz")).status_code == 200


async def test_gateway_secret_constant_time_and_non_ascii_safe(client, monkeypatch, seeded):
    monkeypatch.delenv("TRUST_GATEWAY_HEADERS")
    monkeypatch.setenv("GRAPH_GATEWAY_SECRET", "s3cret")
    ok = await client.get("/rings", headers={**OFFICER, "X-Gateway-Secret": "s3cret"})
    assert ok.status_code == 200
    for bad in ("wrong", "", "s3creté", "中文"):
        r = await client.get("/rings", headers={**OFFICER, "X-Gateway-Secret": bad.encode("utf-8")})
        assert r.status_code == 401
    # TRUST_GATEWAY_HEADERS does not override a configured secret
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "1")
    assert (await client.get("/rings", headers=OFFICER)).status_code == 401


async def test_roles(client, seeded):
    _, rid = seeded
    for h_ in (OFFICER, ANALYST, ADMIN):
        assert (await client.get("/rings", headers=h_)).status_code == 200
    assert (await client.get("/rings", headers=CITIZEN)).status_code == 403
    assert (
        await client.post(f"/rings/{rid}/simulate", json={"k": 1}, headers=ANALYST)
    ).status_code == 403
    assert (
        await client.post(f"/rings/{rid}/simulate", json={"k": 1}, headers=OFFICER)
    ).status_code == 200
    assert (
        await client.post(f"/rings/{rid}/simulate", json={"k": 1}, headers=ADMIN)
    ).status_code == 200
    assert (await client.get("/rings", headers={"X-Principal-Role": "officer"})).status_code == 401


async def test_list_summary_and_etag_304(client, seeded):
    _, rid = seeded
    r = await client.get("/rings", headers=OFFICER)
    body = r.json()
    assert body["count"] == 1 and body["items"][0]["ring_id"] == rid
    item = body["items"][0]
    assert item["inflow_inr"] == "450000.00" and item["members_by_role"]["mule"] == 3
    assert item["model_version"] == "graph-v1" and item["victims"] == 9
    etag = r.headers["etag"]
    again = await client.get("/rings", headers={**OFFICER, "If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""
    star = await client.get("/rings", headers={**OFFICER, "If-None-Match": "*"})
    assert star.status_code == 304
    one = await client.get(f"/rings/{rid}", headers=OFFICER)
    assert (
        one.status_code == 200
        and (
            await client.get(
                f"/rings/{rid}", headers={**OFFICER, "If-None-Match": one.headers["etag"]}
            )
        ).status_code
        == 304
    )
    assert (await client.get("/rings?status=stable", headers=OFFICER)).json()["count"] == 0
    assert (await client.get("/rings?status=new", headers=OFFICER)).json()["count"] == 1


async def test_etag_changes_with_the_ring_version(app, client, seeded, clock):
    camp, rid = seeded
    twin, svc = app.state.twin, app.state.service
    e1 = (await client.get(f"/rings/{rid}/graph", headers=OFFICER)).headers["etag"]
    from .conftest import txn

    t = txn(h("fresh-victim"), camp["mules"][0], T0 + timedelta(minutes=40), "5000")
    twin.apply_txn(t)
    await svc.flush()
    e2 = (await client.get(f"/rings/{rid}/graph", headers=OFFICER)).headers["etag"]
    assert e1 != e2
    assert (
        await client.get(f"/rings/{rid}/graph", headers={**OFFICER, "If-None-Match": e1})
    ).status_code == 200


async def test_responses_are_deterministic_bytes(client, seeded):
    _, rid = seeded
    a = await client.get(f"/rings/{rid}/graph", headers=OFFICER)
    b = await client.get(f"/rings/{rid}/graph", headers=OFFICER)
    assert a.content == b.content


async def test_graph_has_only_opaque_prefixes_and_layout_tiers(client, seeded):
    camp, rid = seeded
    g = (await client.get(f"/rings/{rid}/graph", headers=OFFICER)).json()
    text = json.dumps(g)
    assert not re.search(r"[0-9a-f]{20,}", text.replace(rid, ""))  # no raw hashes / tokens
    for raw in (*camp["victims"], *camp["mules"], camp["cash"]):
        assert raw not in text and raw[:16] not in text
    ids = {n["id"] for n in g["nodes"]}
    assert all(re.fullmatch(r"[0-9a-f]{12}", i) for i in ids)
    by_role = {}
    for n in g["nodes"]:
        by_role.setdefault(n["role"], []).append(n)
    assert {n["tier"] for n in by_role["victim_payer"]} == {0}
    assert {n["tier"] for n in by_role["mule"]} == {1}
    assert {n["tier"] for n in by_role["cash_out"]} == {3}
    assert len(by_role["victim_payer"]) == 9 and not g["truncated"]
    e = g["edges"][0]
    assert isinstance(e["total_inr"], str) and re.fullmatch(r"\d+\.\d\d", e["total_inr"])
    assert {e["src"] for e in g["edges"]} | {e["dst"] for e in g["edges"]} <= ids
    assert api_id("a:" + camp["cash"]) in ids


async def test_graph_truncates_at_max_response_nodes(db_url, bus, clock):
    app = create_app(
        db_url, bus=bus, clock=clock, settings=Settings(max_response_nodes=8), background=False
    )
    twin, svc = app.state.twin, app.state.service
    make_campaign(twin, clock, victims=18, mules=3)
    await svc.flush()
    (rid,) = twin.published
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    g = (await client.get(f"/rings/{rid}/graph", headers=OFFICER)).json()
    assert g["truncated"] is True and len(g["nodes"]) == 8
    await svc.stop()
    twin.store.close()


async def test_validation_errors_never_echo_input(client, seeded):
    _, rid = seeded
    secret = "TOPSECRETVALUE0123"
    bodies = [
        {"remove": [secret]},
        {"remove": ["a" * 12], "k": 2},
        {"k": 99},
        {"remove": [f"{i:012x}" for i in range(51)]},
        {},
        {"k": secret},
    ]
    for b in bodies:
        r = await client.post(f"/rings/{rid}/simulate", json=b, headers=OFFICER)
        assert r.status_code == 422, b
        assert secret not in r.text and "input" not in r.text
    for path in (
        f"/rings/{secret}",
        f"/rings/{secret}/graph",
        "/rings?status=" + secret,
        "/rings?limit=" + secret,
    ):
        r = await client.get(path, headers=OFFICER)
        assert r.status_code == 422 and secret not in r.text


async def test_simulate_cash_out_by_id_and_by_k(client, seeded):
    camp, rid = seeded
    cash = api_id("a:" + camp["cash"])
    r = await client.post(f"/rings/{rid}/simulate", json={"remove": [cash]}, headers=OFFICER)
    j = r.json()
    assert r.status_code == 200 and j["reduction_pct"] == 100.0 and j["harm_after_inr"] == "0.00"
    assert j["harm_before_inr"] == "3150000.00" and j["removed"] == [cash]
    assert j["edges_cut"] and all(cash in (e["src"], e["dst"]) for e in j["edges_cut"])
    k = (await client.post(f"/rings/{rid}/simulate", json={"k": 1}, headers=OFFICER)).json()
    assert k["selected"] == [cash] and k["reduction_pct"] == 100.0
    b = (await client.get(f"/rings/{rid}/best-takedown?k=2", headers=ANALYST)).json()
    assert cash in b["selected"] and b["k"] == 1 + 1
    victim = api_id("a:" + camp["victims"][0])
    s = (
        await client.post(f"/rings/{rid}/simulate", json={"remove": [victim]}, headers=OFFICER)
    ).json()
    assert 0 < s["reduction_pct"] < 20
    unknown = await client.post(
        f"/rings/{rid}/simulate", json={"remove": ["0" * 12]}, headers=OFFICER
    )
    assert unknown.status_code == 422 and "0" * 12 not in unknown.text


async def test_unknown_ring_is_404_and_body_cap_413(client, seeded):
    assert (await client.get("/rings/" + "0" * 16, headers=OFFICER)).status_code == 404
    big = await client.post(
        "/rings/" + "0" * 16 + "/simulate", content=b"x" * 40_000, headers=OFFICER
    )
    assert big.status_code == 413


async def test_request_id_is_sanitised(client):
    r = await client.get("/healthz", headers={"X-Request-Id": "abc\r\nSet-Cookie: x"})
    assert re.fullmatch(r"[0-9a-f]{12}", r.headers["x-request-id"])
    r = await client.get("/healthz", headers={"X-Request-Id": "req-12345678"})
    assert r.headers["x-request-id"] == "req-12345678"


async def test_logs_have_no_query_string_body_or_identifiers(client, seeded, caplog):
    camp, rid = seeded
    caplog.set_level(logging.DEBUG)
    await client.get("/rings?status=new&limit=5", headers=OFFICER)
    await client.post(
        f"/rings/{rid}/simulate", json={"remove": [api_id("a:" + camp["cash"])]}, headers=OFFICER
    )
    mine = [r for r in caplog.records if r.name.startswith("graph_twin")]
    text = "\n".join(r.getMessage() for r in mine)
    assert "status=new" not in text and "limit=5" not in text
    assert api_id("a:" + camp["cash"]) not in text and camp["cash"][:12] not in text
    assert any("route=/rings/{ring_id}/simulate" in r.getMessage() for r in mine)


async def test_stats_and_metrics_gated(client, seeded, monkeypatch):
    s = (await client.get("/stats", headers=ANALYST)).json()
    assert s["rings"] == 1 and s["nodes"] > 10 and s["model_version"] == "graph-v1"
    m = await client.get("/metrics", headers=ANALYST)
    assert m.status_code == 200 and "graph_twin_rings 1" in m.text
    assert (await client.get("/metrics", headers=CITIZEN)).status_code == 403
    monkeypatch.delenv("TRUST_GATEWAY_HEADERS")
    assert (await client.get("/metrics")).status_code == 401
    monkeypatch.setenv("GRAPH_METRICS_TOKEN", "tok")
    assert (await client.get("/metrics", headers={"X-Metrics-Token": "tok"})).status_code == 200
    assert (
        await client.get("/metrics", headers={"X-Metrics-Token": "tök".encode()})
    ).status_code == 401


# ------------------------------------------------------------------------------------- audit
def ledger_entries(bus: InMemoryBus) -> list[LedgerEntryIn]:
    return [LedgerEntryIn.model_validate_json(raw) for _, raw in bus.messages(Topics.LEDGER)]


async def test_ring_and_takedown_events_reach_the_ledger_without_pii(app, client, bus, seeded):
    camp, rid = seeded
    svc = app.state.service
    await svc.settle()
    detected = [e for e in ledger_entries(bus) if e.event_type == "ring.detected"]
    assert len(detected) == 1
    d = detected[0]
    assert d.case_refs == [f"ring:{rid}"] and d.service == "graph-twin"
    assert d.model_version == "graph-v1" and d.actor == "system:graph-twin"
    assert d.payload["members_by_role"] == {"mule": 3, "cash_out": 1, "unknown": 0}
    assert d.payload["inflow_bucket"] == "100k-1m" and d.payload["ring_id"] == rid
    await client.post(
        f"/rings/{rid}/simulate", json={"remove": [api_id("a:" + camp["cash"])]}, headers=OFFICER
    )
    await svc.settle()
    sim = [e for e in ledger_entries(bus) if e.event_type == "takedown.simulated"]
    assert len(sim) == 1 and sim[0].actor == "officer-1"
    assert sim[0].payload["reduction_pct"] == 100.0 and sim[0].payload["removed_count"] == 1
    blob = json.dumps([e.model_dump(mode="json") for e in ledger_entries(bus)])
    for raw in (*camp["victims"], *camp["mules"], camp["cash"]):
        assert raw not in blob and raw[:16] not in blob
    # a repeat of the identical simulation is the same entry (idempotent audit)
    await client.post(
        f"/rings/{rid}/simulate", json={"remove": [api_id("a:" + camp["cash"])]}, headers=OFFICER
    )
    await svc.settle()
    assert len([e for e in ledger_entries(bus) if e.event_type == "takedown.simulated"]) == 1


async def test_a_phone_like_principal_is_redacted_in_the_audit_actor(app, client, bus, seeded):
    _, rid = seeded
    h_ = {"X-Principal-Role": "officer", "X-Principal-Sub": "9876543210"}
    assert (
        await client.post(f"/rings/{rid}/simulate", json={"k": 1}, headers=h_)
    ).status_code == 200
    await app.state.service.settle()
    sim = [e for e in ledger_entries(bus) if e.event_type == "takedown.simulated"]
    assert sim and sim[0].actor.startswith("redacted_") and "9876543210" not in sim[0].actor
