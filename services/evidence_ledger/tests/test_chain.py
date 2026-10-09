import pytest

from evidence_ledger.verify import GENESIS, verify_chain

from .chainkit import Keys, clone, make_entries, rehash_from


def test_chain_verifies():
    r = verify_chain(make_entries(10))
    assert r.ok and r.first_bad_seq is None and r.reason == "ok"


def test_empty_chain_is_ok():
    assert verify_chain([]).ok


def test_genesis_prev_hash_required():
    es = make_entries(3)
    es[0]["prev_hash"] = "1" * 64
    r = verify_chain(es)
    assert not r.ok and r.first_bad_seq == 1


def test_tamper_detected():
    es = make_entries(6)
    es[2]["payload"]["decision"] = "allow"  # entry 3
    r = verify_chain(es)
    assert not r.ok and r.first_bad_seq == 3 and "payload" in r.reason


@pytest.mark.parametrize(
    ("field", "value", "seq"),
    [("actor", "mallory", 3), ("event_type", "hold.resolved", 3), ("ts", "2020-01-01T00:00:00.000000Z", 3),
     ("model_version", "m9", 3), ("case_refs", ["txn:other"], 3), ("service", "x", 3),
     ("prev_hash", "f" * 64, 3), ("entry_hash", "f" * 64, 3), ("payload_hash", "f" * 64, 3)],
)  # fmt: skip
def test_field_mutation_detected_at_exact_seq(field, value, seq):
    es = make_entries(6)
    es[2][field] = value
    r = verify_chain(es)
    assert not r.ok and r.first_bad_seq == seq


def test_recomputed_hash_breaks_next_link():
    es = make_entries(6)
    es[2]["actor"] = "mallory"
    es[2]["entry_hash"] = __import__("evidence_ledger.verify", fromlist=["x"]).compute_entry_hash(
        es[2]["prev_hash"], es[2]
    )
    r = verify_chain(es)
    assert not r.ok and r.first_bad_seq == 4  # entry 4 no longer links to the rewritten entry 3


def test_deleted_middle_entry():
    es = make_entries(6)
    del es[2]
    r = verify_chain(es)
    assert not r.ok and r.first_bad_seq == 3 and "missing" in r.reason


def test_swapped_entries():
    es = make_entries(6)
    es[2], es[3] = es[3], es[2]
    r = verify_chain(es)
    assert not r.ok and r.first_bad_seq == 3


def test_appended_forged_entry():
    es = make_entries(5)
    forged = clone(es[-1])
    forged.update(seq=6, actor="mallory")
    es.append(forged)
    r = verify_chain(es)
    assert not r.ok and r.first_bad_seq == 6


def test_well_formed_forged_extension_is_not_detectable_without_a_later_checkpoint():
    """Documented limit: an attacker who can write (and re-hash) past the last checkpoint is
    only caught by a checkpoint that covers the forged range."""
    k = Keys()
    es = make_entries(5)
    cp = k.checkpoint(es[4])
    more = make_entries(2, start_hash=es[-1]["entry_hash"], start_seq=6)
    assert verify_chain(es + more, checkpoints=[cp], pubkeys=k.pubkeys).ok


def test_truncated_tail_detected_only_with_checkpoints():
    k = Keys()
    es = make_entries(10)
    cp = k.checkpoint(es[9])
    truncated = es[:6]
    assert verify_chain(truncated).ok  # nothing to compare against
    r = verify_chain(truncated, checkpoints=[cp], pubkeys=k.pubkeys)
    assert not r.ok and r.first_bad_seq == 7 and "truncat" in r.reason


def test_whole_chain_rewrite_detected_by_checkpoint():
    k = Keys()
    es = make_entries(10)
    cp = k.checkpoint(es[6])
    forged = clone(es)
    forged[1]["actor"] = "mallory"
    rehash_from(forged, 1)
    assert verify_chain(forged).ok  # internally consistent
    r = verify_chain(forged, checkpoints=[cp], pubkeys=k.pubkeys)
    assert not r.ok and r.first_bad_seq == 7 and "checkpoint" in r.reason


def test_checkpoint_signature_checked():
    k = Keys()
    es = make_entries(5)
    cp = k.checkpoint(es[4])
    assert verify_chain(es, checkpoints=[cp], pubkeys=k.pubkeys).ok
    bad = dict(cp, count=99)
    r = verify_chain(es, checkpoints=[bad], pubkeys=k.pubkeys)
    assert not r.ok and r.first_bad_seq == 5 and "signature" in r.reason
    other = Keys(b"\x02" * 32)
    assert not verify_chain(es, checkpoints=[cp], pubkeys=other.pubkeys).ok
    assert not verify_chain(es, checkpoints=[cp]).ok  # fail closed: no keys, no trust
    unknown = dict(cp, key_id="0" * 16)
    assert "unknown key" in verify_chain(es, checkpoints=[unknown], pubkeys=k.pubkeys).reason


def test_segment_anchor():
    full = make_entries(8)
    seg = full[3:6]
    assert verify_chain(seg, anchor_prev_hash=full[2]["entry_hash"]).ok
    r = verify_chain(seg, anchor_prev_hash="a" * 64)
    assert not r.ok and r.first_bad_seq == 4
    assert verify_chain(seg).ok  # unanchored segment: internal linkage only


def test_genesis_constant():
    assert GENESIS == "0" * 64


def test_accepts_pydantic_models():
    from scam_contracts.models import LedgerEntry

    es = [LedgerEntry.model_validate(e) for e in make_entries(4)]
    assert verify_chain(es).ok
