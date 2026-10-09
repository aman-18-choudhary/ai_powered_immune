"""Fix round 1: an audit payload problem never fails or rolls back an analyst action."""

import json
import sqlite3

import pytest
import svckit.ledger as ledger_mod
from scam_contracts.topics import Topics
from svckit.ledger import LedgerPayloadError, build_ledger_entry

from antibody_hub.api import create_app

from .conftest import events, hdr
from .test_lifecycle import body

PHONEISH = "919876543210"


def led(bus):
    return events(bus, Topics.LEDGER)


async def test_numeric_principal_sub_succeeds_and_is_redacted(client, app, bus, tmp_path):
    r = await client.post("/antibodies", json=body(), headers=hdr("analyst", PHONEISH, "bank_a"))
    assert r.status_code == 201, r.text
    (e,) = led(bus)
    assert e["actor"].startswith("redacted_") and e["payload"]["event"] == "created"
    assert "audit" not in e["payload"]
    build_ledger_entry("antibody-hub", e["actor"], e["event_type"], e["payload"],
                       case_refs=e["case_refs"])  # fmt: skip
    con = sqlite3.connect(tmp_path / "hub.db")
    rows = con.execute("select body from audit_outbox where topic = ?", (Topics.LEDGER,)).fetchall()
    con.close()
    assert rows and PHONEISH not in json.dumps(rows) and PHONEISH not in json.dumps(led(bus))


@pytest.mark.parametrize("bank", ["bank123456789", "1bank", "b", "Bank_A", "bank.a"])
async def test_bank_slug_is_validated_with_422(client, bank):
    r = await client.post("/antibodies", json=body(source_bank=bank),
                          headers=hdr("analyst", "a1"))  # fmt: skip
    assert r.status_code == 422


async def test_hub_banks_are_validated_at_startup(db_url, bus, clock):
    with pytest.raises(ValueError):
        create_app(database_url=db_url, bus=bus, clock=clock, banks=["bank_a", "x123456789"])


async def test_numeric_bank_in_principal_header_does_not_fail_revoke(client, app, bus):
    ab = (await client.post("/antibodies", json=body(), headers=hdr("analyst", "a1"))).json()
    r = await client.post(f"/antibodies/{ab['antibody_id']}/revoke", json={"reason": "r"},
                          headers=hdr("analyst", "a1", "123456789012"))  # fmt: skip
    assert r.status_code == 200
    rev = led(bus)[-1]
    assert rev["event_type"].startswith("antibody.revoked") and "123456789012" not in json.dumps(
        rev
    )


async def test_forced_refusal_writes_a_placeholder_row_in_the_same_transaction(
    client, app, bus, monkeypatch
):
    def boom(*a, **k):
        raise LedgerPayloadError("forced")

    real = ledger_mod.build_ledger_entry
    calls = {"n": 0}

    def once(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            boom()
        return real(*a, **k)

    monkeypatch.setattr(ledger_mod, "build_ledger_entry", once)
    r = await client.post("/antibodies", json=body(), headers=hdr("analyst", "a1"))
    assert r.status_code == 201
    (e,) = led(bus)
    assert e["payload"]["audit"] == "payload_refused" and e["event_type"] == "antibody.created"
    assert set(e["payload"]) == {"antibody_id", "event", "generation", "kind", "audit"}
    assert e["case_refs"][0] == r.json()["antibody_id"]


async def test_non_utc_injected_clock_cannot_skip_an_extend(db_url, bus):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from antibody_hub.hub import Hub
    from antibody_hub.store import AntibodyStore

    ist = ZoneInfo("Asia/Kolkata")
    t = {"now": datetime(2026, 1, 6, 15, 0, tzinfo=ist)}
    store = AntibodyStore(db_url, clock=lambda: t["now"])
    hub = Hub(store, bus)
    from .conftest import mule

    h = mule("ist")
    first = await hub.submit("mule_account", h, "bank_a", "analyst-1", None, False, role="analyst")
    t["now"] += timedelta(hours=1)
    ext = await hub.submit("mule_account", h, "bank_a", "analyst-1", None, True, role="analyst")
    assert first.created and ext.extended
    store.close()
