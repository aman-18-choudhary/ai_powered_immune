"""Task 13: structured, PII-free ledger payloads from the hold audit outbox, case refs and the
call_ref link between a CallRisk and the holds it influenced."""

import hashlib
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from scam_contracts.canonical import payload_hash
from scam_contracts.models import LedgerEntryIn
from scam_contracts.topics import Topics
from svckit.bus import InMemoryBus
from svckit.ledger import build_ledger_entry, call_ref, payee_ref

from txn_guard.features import active_call_id
from txn_guard.history import Context, InMemoryHistoryStore
from txn_guard.holds import BusAuditSink, InMemoryHoldStore, amount_bucket

from .conftest import T0
from .test_hardening import Clock, FlakyAudit, R, risk, scorer, svc_for  # noqa: F401

PAYEE = "3a9f0c12d45b7e68" + hashlib.sha256(b"mule").hexdigest()[:48]


def entries(bus):
    return [LedgerEntryIn.model_validate_json(r) for _, r in bus.messages(Topics.LEDGER)]


def bus_holds(bus):
    return InMemoryHoldStore(audit=BusAuditSink(bus), clock=Clock())


@pytest.mark.parametrize(
    ("amount", "label"),
    [(1, "<1k"), (999.99, "<1k"), (1000, "1k-10k"), (9999, "1k-10k"), (10000, "10k-100k"),
     (99999, "10k-100k"), (100000, "100k-1m"), (999999, "100k-1m"), (1000000, ">=1m"),
     (Decimal("5000000"), ">=1m")],
)  # fmt: skip
def test_amount_bucket(amount, label):
    assert amount_bucket(amount) == label


async def test_created_payload_shape_refs_hash_and_no_pii(scorer, make_txn):  # noqa: F811
    bus = InMemoryBus()
    svc, _, holds, _ = svc_for(scorer, make_txn, holds=bus_holds(bus), bus=InMemoryBus())
    t = make_txn(amount="95000", age=3, payee=PAYEE)
    d = await svc.handle_txn(t)
    assert d.decision != "allow"
    (e,) = entries(bus)
    assert e.event_type == "hold.created" and e.actor == "system:txn-guard"
    p = e.payload
    assert p is not None and e.payload_hash == payload_hash(p)
    assert p["txn_id"] == t.txn_id and p["decision"] == d.decision and p["decision_seq"] == 1
    assert p["score"] == round(d.score, 4) and p["model_version"] == d.model_version
    assert p["reason_codes"] == sorted({r.code for r in d.reasons})
    assert p["rail"] == "UPI" and p["amount_bucket"] == "10k-100k"
    assert p["deadline_ts"].endswith("Z") and p["payee_ref"] == PAYEE[:16]
    assert e.case_refs == [t.txn_id, f"payee_ref:{PAYEE[:16]}"]
    blob = json.dumps(p) + json.dumps(e.case_refs)
    assert "95000" not in blob and "payer_1" not in blob and t.payer_token not in blob
    assert "First payment" not in blob  # reason text is not emitted, codes only
    build_ledger_entry(
        "txn-guard", e.actor, e.event_type, p, model_version=e.model_version, case_refs=e.case_refs
    )  # fmt: skip  (passes the emitter's own guard)


async def test_late_call_risk_links_call_ref_and_trigger(scorer, make_txn):  # noqa: F811
    bus = InMemoryBus()
    svc, _, holds, _ = svc_for(scorer, make_txn, holds=bus_holds(bus), bus=InMemoryBus())
    t = make_txn(amount="40000", payee=PAYEE)
    assert (await svc.handle_txn(t)).decision == "allow"
    assert entries(bus) == []
    await svc.handle_call_risk(risk(0.9, cid="call-777"))
    (e,) = entries(bus)
    assert e.event_type == "hold.created" and e.payload["trigger"] == "late_call_risk"
    assert e.payload["from_decision"] == "allow"
    cref = call_ref("call-777")
    assert e.case_refs == [t.txn_id, f"payee_ref:{PAYEE[:16]}", cref]
    assert e.payload["call_ref"] == cref.split(":")[1] and "call-777" not in json.dumps(e.payload)


async def test_txn_after_call_risk_carries_the_call_ref(scorer, make_txn):  # noqa: F811
    bus = InMemoryBus()
    svc, _, holds, _ = svc_for(scorer, make_txn, holds=bus_holds(bus), bus=InMemoryBus())
    await svc.handle_call_risk(risk(0.9, ts=T0 - timedelta(minutes=2), cid="call-42"))
    t = make_txn(amount="40000", payee=PAYEE)
    d = await svc.handle_txn(t)
    assert d.decision != "allow"
    (e,) = entries(bus)
    assert call_ref("call-42") in e.case_refs


async def test_upgrade_resolve_overdue_payloads(scorer, make_txn):  # noqa: F811
    bus = InMemoryBus()
    clock = Clock()
    holds = InMemoryHoldStore(audit=BusAuditSink(bus), clock=clock)
    svc, *_ = svc_for(scorer, make_txn, holds=holds, bus=InMemoryBus(), clock=clock)
    t = make_txn(amount="6000", age=20, payee=PAYEE, ts=T0 + timedelta(hours=2))
    d = await svc.handle_txn(t)
    assert d.decision == "step_up"
    await svc.handle_call_risk(risk(0.95, ts=t.ts + timedelta(minutes=1), cid="c9"))
    clock.t += timedelta(minutes=10)
    assert await holds.sweep() == 1
    await holds.resolve(t.txn_id, "confirm_block", "analyst-7", role="analyst")
    es = {e.event_type: e for e in entries(bus)}
    assert list(es) == ["hold.created", "hold.upgraded", "hold.overdue", "hold.resolved"]
    up = es["hold.upgraded"].payload
    assert (up["from_decision"], up["to_decision"], up["trigger"]) == (
        "step_up", "hold_verify", "call_risk")  # fmt: skip
    assert up["decision_seq"] == 2 and up["call_ref"] == call_ref("c9").split(":")[1]
    rs = es["hold.resolved"]
    assert rs.actor == "analyst-7"
    assert rs.payload["action"] == "confirm_block" and rs.payload["resolver_role"] == "analyst"
    assert rs.payload["resolver_ref"] == "analyst-7" and rs.payload["decision_seq"] == 2
    assert rs.payload["resolved_ts"].endswith("Z")
    assert es["hold.overdue"].payload["decision"] == "hold_verify"
    for e in es.values():  # every later event keeps the same join keys
        assert e.case_refs[:2] == [t.txn_id, f"payee_ref:{PAYEE[:16]}"]
        assert call_ref("c9") in e.case_refs or e.event_type == "hold.created"
        assert e.payload_hash == payload_hash(e.payload)


async def test_retries_are_byte_identical_and_deterministic():
    """A sink that fails twice, then works: the ledger gets the same bytes every time, and an
    independent run with the same clock produces the same hashes."""

    async def run(fail):
        bus = InMemoryBus()
        inner = BusAuditSink(bus)
        flaky = FlakyAudit()
        flaky.fail = fail

        class Sink:
            async def emit(self, entry):
                if flaky.fail > 0:
                    flaky.fail -= 1
                    raise ConnectionError("down")
                await inner.emit(entry)

        store = InMemoryHoldStore(audit=Sink(), clock=Clock())
        await store.create("t1", "hold_verify", R, Clock()() + timedelta(seconds=60),
                           payer_token="p", score=0.71234, model_version="gbm-v1",
                           rail="UPI", amount_bucket="1k-10k", payee_ref=payee_ref(PAYEE))  # fmt: skip
        for _ in range(3):
            try:
                await store.drain_audit("t1")
            except ConnectionError:
                pass
        return [raw for _, raw in bus.messages(Topics.LEDGER)]

    clean, flaky = await run(0), await run(2)
    assert len(clean) == 1 and clean == flaky


def test_active_call_id_picks_highest_in_window_risk(make_txn):
    t = make_txn()
    ctx = Context(
        now=T0,
        call_risks=((T0 - timedelta(minutes=3), 0.8), (T0 - timedelta(minutes=2), 0.95),
                    (T0 - timedelta(minutes=40), 0.99), (T0 - timedelta(minutes=1), 0.95)),
        call_ids=("a", "b", "far", "c"),
    )  # fmt: skip
    assert active_call_id(t, ctx) == "b"  # 0.99 is outside the window; tie -> earlier ts
    assert active_call_id(t, Context(now=T0)) is None
    assert active_call_id(t, Context(now=T0, call_risks=((T0, 0.9),))) is None  # no ids
    h = InMemoryHistoryStore()
    h.record_call_risk(risk(0.9, cid="zzz"))
    assert active_call_id(t, h.context_for(t, T0)) == "zzz"
