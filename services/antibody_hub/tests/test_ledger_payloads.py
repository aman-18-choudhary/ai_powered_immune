"""Task 13: structured, PII-free ledger payloads and case refs from the audit outbox."""

import json
import sqlite3
from datetime import timedelta

from scam_contracts.canonical import payload_hash
from scam_contracts.hashing import keyed_hash
from scam_contracts.topics import Topics
from svckit.ledger import build_ledger_entry

from .conftest import ADMIN, ANALYST, FED_KEY, events, hdr, mule
from .test_lifecycle import RAW_ACCOUNT, body


def ledger(bus):
    return events(bus, Topics.LEDGER)


async def test_created_payload_refs_and_guard(client, app, bus, clock):
    h = keyed_hash(RAW_ACCOUNT, "mule_account", FED_KEY)
    r = await client.post(
        "/antibodies", json=body(h, evidence_ref="case-77"),
        headers=hdr("analyst", "analyst-1", "bank_a"),
    )  # fmt: skip
    ab = r.json()
    (e,) = ledger(bus)
    p = e["payload"]
    assert e["event_type"] == "antibody.created" and e["actor"] == "analyst-1"
    assert p == {
        "antibody_id": ab["antibody_id"], "event": "created", "generation": 1,
        "kind": "mule_account", "key_hash_prefix": h[:8],
        "expires_at": (clock() + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "actor_role": "analyst", "actor_bank": "bank_a",
    }  # fmt: skip
    assert e["payload_hash"] == payload_hash(p)
    assert e["case_refs"] == [ab["antibody_id"], f"payee_ref:{h[:16]}"]
    blob = json.dumps(e)
    assert h not in blob and RAW_ACCOUNT not in blob and "case-77" not in blob
    build_ledger_entry("antibody-hub", e["actor"], e["event_type"], p, case_refs=e["case_refs"])


async def test_every_lifecycle_event_has_payload_and_shared_payee_ref(client, app, bus, clock):
    h = mule()
    ab = (await client.post("/antibodies", json=body(h), headers=ANALYST)).json()
    clock.advance(seconds=3600)
    await client.post("/antibodies", json=body(h, extend=True), headers=ANALYST)
    b = hdr("analyst", "b1", "bank_b")
    await client.post("/antibodies", json=body(h, source_bank="bank_b"), headers=b)
    await client.post(
        f"/antibodies/{ab['antibody_id']}/revoke", json={"reason": "r"}, headers=ADMIN
    )
    await client.post("/protected", json={"key_hash": mule("9"), "note": "bank"}, headers=ADMIN)
    await client.delete(f"/protected/{mule('9')}", headers=ADMIN)
    led = ledger(bus)
    kinds = [e["event_type"] for e in led]
    assert kinds == ["antibody.created", "antibody.extended", "antibody.corroborated",
                     "antibody.revoked", "protected.added", "protected.removed"]  # fmt: skip
    for e in led:
        assert e["payload_hash"] == payload_hash(e["payload"]) and e["case_refs"]
        build_ledger_entry("antibody-hub", e["actor"], e["event_type"], e["payload"],
                           case_refs=e["case_refs"])  # fmt: skip
    ab_events = led[:4]
    assert {tuple(e["case_refs"]) for e in ab_events} == {
        (ab["antibody_id"], f"payee_ref:{h[:16]}")
    }
    assert led[2]["actor"] == "b1@bank_b" and led[2]["payload"]["actor_bank"] == "bank_b"
    assert led[3]["payload"]["actor_role"] == "admin"
    assert led[4]["case_refs"] == [f"payee_ref:{mule('9')[:16]}"]
    assert led[4]["payload"]["event"] == "protected.added" and "note" not in json.dumps(led[4])


async def test_expiry_is_system_and_deterministic(client, app, bus, clock):
    await client.post("/antibodies", json=body(), headers=ANALYST)
    clock.advance(days=15)
    assert await app.state.hub.sweep() == 1
    exp = ledger(bus)[-1]
    assert exp["event_type"] == "antibody.expired" and exp["actor"] == "system:expiry"
    assert exp["payload"]["actor_role"] == "system" and "actor_bank" not in exp["payload"]


async def test_replay_after_failed_publish_is_byte_identical(client, app, bus, clock):
    bus.fail_next = 2
    await client.post("/antibodies", json=body(), headers=ANALYST)
    assert ledger(bus) == []
    clock.advance(seconds=30)  # a later drain must not change the entry
    await app.state.hub.drain()
    await app.state.hub.drain()
    assert len(ledger(bus)) == 1
    again = await client.post("/antibodies", json=body(), headers=ANALYST)
    assert again.status_code == 200 and len(ledger(bus)) == 1


async def test_two_actors_same_bank_corroborations_are_distinct(client, app, bus):
    await client.post("/antibodies", json=body(), headers=ANALYST)
    for who in ("b1", "b2"):
        await client.post("/antibodies", json=body(source_bank="bank_b"),
                          headers=hdr("analyst", who, "bank_b"))  # fmt: skip
    cor = [e for e in ledger(bus) if e["event_type"] == "antibody.corroborated"]
    assert sorted(e["actor"] for e in cor) == ["b1@bank_b", "b2@bank_b"]
    assert cor[0]["payload_hash"] == cor[1]["payload_hash"]  # same payload, distinct by actor


async def test_outbox_has_no_raw_identifiers(client, app, db_url, bus, tmp_path):
    await client.post("/antibodies", json=body(keyed_hash(RAW_ACCOUNT, "mule_account", FED_KEY)),
                      headers=ANALYST)  # fmt: skip
    con = sqlite3.connect(tmp_path / "hub.db")
    dump = "\n".join(con.iterdump())
    con.close()
    assert RAW_ACCOUNT not in dump
