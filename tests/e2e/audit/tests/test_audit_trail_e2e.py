"""Hero scenario -> every service's ledger entries -> case -> evidence package -> offline verify.

Seeds 1..3 of the constructed hero case (a templated digital-arrest call that call-guard catches
for victim A, an off-template call it misses for victim B, one shared seasoned mule account, two
banks). Asserts the audit trail of B's blocked transfer can be rebuilt from the package alone.
"""

import json
import re
from pathlib import Path

import pytest
from scam_contracts.canonical import payload_hash
from svckit.ledger import call_ref, payee_ref

from scam_audit.harness import (
    CASE_ID,
    OTHER_CASE_SENTINEL,
    AuditRun,
    raw_identifiers,
    run_hero_audit,
)

SEEDS = [1, 2, 3]
PHONE_LIKE = re.compile(rb"(?<![0-9A-Za-z])(?:\+?91[- ]?)?[6-9]\d{9}(?![0-9A-Za-z])")


@pytest.fixture(scope="module")
def world_and_scorer():
    from sim_engine.world import build_world
    from txn_guard.model import Scorer

    return build_world(5, 600), Scorer()


@pytest.fixture(params=SEEDS, ids=[f"seed{s}" for s in SEEDS])
async def run(request, tmp_path, world_and_scorer) -> AuditRun:
    world, scorer = world_and_scorer
    return await run_hero_audit(request.param, tmp_path, world=world, scorer=scorer)


def package_blobs(run: AuditRun) -> dict[str, bytes]:
    out = {"<zip>": run.package}
    for p in sorted(Path(run.package_dir).iterdir()):
        out[p.name] = p.read_bytes()
    return out


def timeline(run: AuditRun) -> list[tuple[str, str]]:
    """[(event_type, body-of-section)] in the order explanations.md lists the entries."""
    text = (run.package_dir / "explanations.md").read_text()
    parts = re.split(r"^## Entry \d+: ", text, flags=re.M)[1:]
    return [(p.split("\n", 1)[0].strip(), p) for p in parts]


def test_package_verifies_offline_with_the_pinned_key(run):
    assert run.verify_returncode == 0, run.verify_stdout + run.verify_stderr
    assert "PASS" in run.verify_stdout and "Traceback" not in run.verify_stderr
    assert run.public_key_hex  # the key the verifier was pinned to


def test_chain_intact_nothing_quarantined_nothing_lost(run):
    assert run.chain_ok and run.quarantined == 0
    assert run.bus_ledger_distinct == sum(run.counts.values())  # every distinct message stored
    assert run.counts["antibody.created"] == 1 and run.counts["hold.resolved"] == 1
    assert run.counts["antibody.extended"] == 1 and run.counts["antibody.revoked"] == 1
    assert run.counts["hold.upgraded"] >= 1 and run.counts["callrisk.alert"] >= 1
    assert run.counts["hold.created"] >= 3
    assert not any(k.startswith("ledger.") for k in run.counts)


def test_exactly_one_ledger_entry_per_state_change(run):
    """Count the state changes from the services' own outputs and compare with the chain."""
    held: set[str] = set()  # txns that have an open hold
    seen: set[str] = set()
    created = upgraded = 0
    for d in run.extras["decisions"]:  # TxnDecisions in publish order
        if d.decision != "allow" and d.txn_id not in held:
            held.add(d.txn_id)  # first non-allow verdict opens the hold (also a late upgrade)
            created += 1
        elif d.txn_id in seen and d.txn_id in held:
            upgraded += 1  # a stronger verdict on an existing hold
        seen.add(d.txn_id)
    expected = {
        "callrisk.alert": run.extras["call_risks"],
        "hold.created": created + 1,  # + the unrelated "case-other" sentinel entry
        "hold.upgraded": upgraded,
        "hold.resolved": 1,
        "antibody.created": 1,
        "antibody.extended": 1,
        "antibody.revoked": 1,
    }
    assert dict(run.counts) == {k: v for k, v in expected.items() if v}, (run.counts, expected)


def test_timeline_order_alert_hold_antibody_resolve_then_b_hold(run):
    tl = timeline(run)
    kinds = [k for k, _ in tl]
    a_alert = next(i for i, (k, b) in enumerate(tl) if k == "callrisk.alert"
                   and call_ref(run.a_call_id).split(":")[1] in b)  # fmt: skip
    a_hold = next(i for i, (k, b) in enumerate(tl) if k == "hold.created"
                  and run.shared_hold_txn_id in b)  # fmt: skip
    ab = kinds.index("antibody.created")
    a_res = next(i for i, (k, b) in enumerate(tl) if k == "hold.resolved"
                 and run.shared_hold_txn_id in b)  # fmt: skip
    b_hold = next(i for i, (k, b) in enumerate(tl) if k == "hold.created"
                  and run.b_first.txn_id in b)  # fmt: skip
    assert a_alert < a_hold < ab < a_res < b_hold
    assert "ANTIBODY_MATCH" in tl[b_hold][1] and "hold_verify" in tl[b_hold][1]
    assert "ANTIBODY_MATCH" not in tl[a_hold][1]
    assert run.b_decision.decision == "hold_verify"
    assert run.extras["b_alerts"] == 0  # B's own call was not detected: only the shared memory


def test_no_raw_identifiers_or_amounts_in_any_package_byte(run):
    blobs = package_blobs(run)
    for label, values in raw_identifiers(run).items():
        for v in values:
            for name, data in blobs.items():
                assert v.encode() not in data, (label, name)
    for name, data in blobs.items():
        assert not PHONE_LIKE.search(data), name
        if name.endswith((".json", ".md")):  # data members (verify.py is code: decorators etc.)
            assert b"+91" not in data and b"@" not in data.replace(b"@bank_", b""), name
    # no amount: no payload key but the bucket, no value equal to any victim transfer amount
    amounts = {str(t.amount_inr) for t in [*run.a_txns, run.b_first]} | {
        str(float(t.amount_inr)) for t in [*run.a_txns, run.b_first]
    }

    def walk(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                assert "amount" not in k or k == "amount_bucket", path + k
                walk(v, path + k + ".")
        elif isinstance(o, list):
            for v in o:
                walk(v, path)
        else:
            assert str(o) not in amounts, path

    for e in json.loads((run.package_dir / "entries.json").read_text())["entries"]:
        if e["payload"] is not None:
            walk(e["payload"])


def test_other_cases_are_redacted_but_the_chain_still_binds_them(run):
    assert any(
        (e["payload"] or {}).get("event_code") == OTHER_CASE_SENTINEL for e in run.ledger_entries
    )  # the ledger has it...
    for name, data in package_blobs(run).items():
        assert OTHER_CASE_SENTINEL.encode() not in data, name  # ...the package does not
    manifest = json.loads((run.package_dir / "manifest.json").read_text())
    assert manifest["redacted_seqs"]
    ents = json.loads((run.package_dir / "entries.json").read_text())["entries"]
    other = [e for e in ents if e["case_refs"] == ["case-other"]]
    assert len(other) == 1 and other[0]["payload"] is None and other[0]["payload_present"]
    assert len(other[0]["payload_hash"]) == 64


def test_bs_blocked_transfer_is_reconstructable_from_the_package_alone(run):
    ents = json.loads((run.package_dir / "entries.json").read_text())["entries"]
    mule = payee_ref(run.meta.shared_mule_payee_hash)
    b_hold = next(
        e for e in ents
        if e["event_type"] == "hold.created" and (e["payload"] or {}).get("txn_id") == run.b_first.txn_id
    )  # fmt: skip
    p = b_hold["payload"]
    assert p["decision"] == "hold_verify" and "ANTIBODY_MATCH" in p["reason_codes"]
    assert p["model_version"] and b_hold["model_version"] == p["model_version"]
    assert b_hold["service"] == "txn-guard" and mule in b_hold["case_refs"]
    assert payload_hash(p) == b_hold["payload_hash"]
    # why it happened: an antibody for the same mule (same payee_ref), created earlier by bank A
    ab = next(e for e in ents if e["event_type"] == "antibody.created")
    assert ab["seq"] < b_hold["seq"] and mule in ab["case_refs"]
    assert ab["payload"]["key_hash_prefix"] == run.meta.shared_mule_payee_hash[:8]
    assert ab["payload"]["actor_bank"] == "bank_a" and ab["payload"]["kind"] == "mule_account"
    # and what led to the antibody: A's held transfer to that mule, driven by A's scam call
    a_hold = next(e for e in ents if (e["payload"] or {}).get("txn_id") == run.shared_hold_txn_id
                  and e["event_type"] == "hold.created")  # fmt: skip
    assert a_hold["seq"] < ab["seq"] and mule in a_hold["case_refs"]
    alert = next(e for e in ents if e["event_type"] == "callrisk.alert"
                 and e["case_refs"] == [call_ref(run.a_call_id)])  # fmt: skip
    assert call_ref(run.a_call_id) in a_hold["case_refs"] and alert["seq"] < a_hold["seq"]
    assert alert["payload"]["score"] >= alert["payload"]["threshold"]
    # the analyst's resolution of A's hold is on the record with a pseudonymous principal
    res = next(e for e in ents if e["event_type"] == "hold.resolved")
    assert res["payload"]["resolver_ref"] == res["actor"] == "analyst-1"
    assert (
        res["payload"]["action"] == "confirm_block" and res["payload"]["resolver_role"] == "analyst"
    )


def test_case_selected_by_refs_only(run):
    assert run.case["case_id"] == CASE_ID
    assert set(run.case["case_refs"]) == {
        run.a_txns[0].txn_id,
        payee_ref(run.meta.shared_mule_payee_hash),
        call_ref(run.a_call_id),
    }


def test_report_counts_and_package_size(run, capsys):
    with capsys.disabled():
        by_type = ", ".join(f"{k}={v}" for k, v in sorted(run.counts.items()))
        print(
            f"\naudit e2e seed {run.seed}: {sum(run.counts.values())} ledger entries ({by_type}); "
            f"messages on ledger topic {run.bus_ledger_messages} (distinct {run.bus_ledger_distinct}); "
            f"quarantined {run.quarantined}; package {len(run.package)} bytes"
        )
