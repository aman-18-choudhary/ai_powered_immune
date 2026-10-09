import threading

import pytest
from scam_contracts.canonical import payload_hash
from scam_contracts.models import LedgerEntryIn
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError

from evidence_ledger.chain import EntryRejected
from evidence_ledger.store import checkpoints, entry_refs, ledger_entries
from evidence_ledger.verify import GENESIS, verify_chain

from .conftest import entry_in


def all_entries(store):
    return store.page(1, 1000)


def test_append_builds_a_verifiable_chain(store):
    for i in range(1, 8):
        r = store.append(entry_in(i))
        assert r.created and r.entry.seq == i
    es = all_entries(store)
    assert es[0].prev_hash == GENESIS
    assert verify_chain(es).ok
    assert store.head().seq == 7 and store.head().entry_hash == es[-1].entry_hash


def test_duplicate_event_written_once(store):
    a = store.append(entry_in(1))
    b = store.append(entry_in(1))
    c = store.append(entry_in(1))  # replay of the exact same entry 3x -> one row
    assert not b.created and not c.created and b.entry == a.entry
    assert store.head().seq == 1
    assert store.counters.snapshot()["duplicates"] == 2
    # a different event_type or service is a different event
    assert store.append(entry_in(1, event_type="hold.resolved")).created
    assert store.append(entry_in(1, service="antibody-hub")).created


def test_entries_differing_in_actor_model_or_refs_are_distinct_events(store):
    """Review probe: the old (service, event_type, payload_hash) key silently dropped these."""
    a = store.append(entry_in(1, refs=["caseA"]))
    other_actor = store.append(entry_in(1, refs=["caseA"], actor="analyst-9"))
    other_ref = store.append(entry_in(1, refs=["caseB"]))
    assert other_actor.created and other_ref.created
    assert len({a.entry.seq, other_actor.entry.seq, other_ref.entry.seq}) == 3
    assert store.select_seqs(["caseB"], [], limit=10) == [other_ref.entry.seq]
    mv = entry_in(1, refs=["caseA"]).model_copy(update={"model_version": "m2"})
    assert store.append(mv).created
    # and each of those is itself idempotent
    assert not store.append(entry_in(1, refs=["caseB"])).created
    assert not store.append(entry_in(1, refs=["caseA"], actor="analyst-9")).created
    assert store.head().seq == 4


def test_case_refs_order_does_not_make_a_new_event(store):
    assert store.append(entry_in(1, refs=["a", "b"])).created
    assert not store.append(entry_in(1, refs=["b", "a"])).created


def test_hash_only_then_same_entry_with_payload_is_a_distinct_entry(store):
    """Documented: the payload-less form and the payload-carrying form are different records."""
    full = entry_in(1)
    hash_only = full.model_copy(update={"payload": None})
    a = store.append(hash_only)
    b = store.append(full)
    assert a.created and b.created and a.entry.payload is None and b.entry.payload is not None


def test_concurrent_duplicate_race_yields_one_row(store):
    results = []
    barrier = threading.Barrier(8)
    e = entry_in(1, actor="same")

    def go():
        barrier.wait()
        results.append(store.append(e))

    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(r.created for r in results) == 1 and store.head().seq == 1


def test_concurrent_appends_are_gap_free(store):
    def worker(w):
        for i in range(10):
            store.append(entry_in(w * 100 + i))

    ts = [threading.Thread(target=worker, args=(w,)) for w in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    es = all_entries(store)
    assert [e.seq for e in es] == list(range(1, 41))
    assert verify_chain(es).ok


def test_payload_hash_mismatch_rejected(store):
    bad = LedgerEntryIn(
        service="s", actor="a", event_type="e", payload_hash="0" * 64, payload={"k": "v"}
    )
    with pytest.raises(EntryRejected) as ei:
        store.append(bad)
    assert ei.value.code == "payload_hash_mismatch"
    assert store.head().seq == 0


@pytest.mark.parametrize(
    "payload",
    [{"note": "call 9876543210"}, {"a": {"b": ["x", "mail me bob@okaxis"]}}, {"9876543210": 1}],
)
def test_pii_in_payload_rejected_without_echo(store, payload):
    e = LedgerEntryIn(
        service="s", actor="a", event_type="e", payload_hash=payload_hash(payload), payload=payload
    )
    with pytest.raises(EntryRejected) as ei:
        store.append(e)
    assert ei.value.code == "pii" and "9876543210" not in str(ei.value)


@pytest.mark.parametrize(
    "kw",
    [dict(actor="bob@gmail.com"), dict(service="a b"), dict(event_type="x" * 200),
     dict(case_refs=["acct-123456789012"]), dict(payload_hash="xyz"), dict(actor="a" * 200)],
)  # fmt: skip
def test_bad_fields_rejected(store, kw):
    base = dict(service="s", actor="a", event_type="e", payload_hash="a" * 64)
    with pytest.raises(EntryRejected):
        store.append(LedgerEntryIn(**{**base, **kw}))


def test_corroboration_actor_with_bank_suffix_is_accepted(store):
    assert store.append(entry_in(1, actor="analyst-1@bank_a")).created


def test_hash_only_entries_are_valid(store):
    r = store.append(entry_in(1, payload=None, refs=[]))
    assert r.entry.payload is None and verify_chain(all_entries(store)).ok


def test_receipt_time_is_utc_aware_and_monotonic_with_clock(store):
    store.append(entry_in(1))
    store.append(entry_in(2))
    a, b = all_entries(store)
    assert a.ts.utcoffset().total_seconds() == 0 and a.ts < b.ts


# ------------------------------------------------------------------ immutability
@pytest.mark.parametrize("table", [ledger_entries, entry_refs, checkpoints])
def test_update_and_delete_blocked_by_triggers(store, table):
    for i in range(1, 6):  # 5 entries -> a checkpoint exists
        store.append(entry_in(i))
    with pytest.raises(DBAPIError), store.engine.begin() as c:
        c.execute(update(table).values({list(table.c.keys())[1]: "x"}))
    with pytest.raises(DBAPIError), store.engine.begin() as c:
        c.execute(delete(table))
    with store.engine.begin() as c:
        assert c.execute(text(f"select count(*) from {table.name}")).scalar() > 0


def test_tampering_after_dropping_triggers_is_still_detected(store):
    """A DBA bypassing the triggers is exactly what the hash chain and checkpoints are for."""
    for i in range(1, 11):
        store.append(entry_in(i))
    with store.engine.begin() as c:
        c.execute(text("drop trigger trg_ledger_entries_no_update"))
        c.execute(text("update ledger_entries set actor='mallory' where seq=3"))
    r = verify_chain(all_entries(store))
    assert not r.ok and r.first_bad_seq == 3


# ------------------------------------------------------------------ checkpoints
def test_checkpoint_every_n_entries(store, keyring):
    for i in range(1, 13):
        store.append(entry_in(i))
    cps = store.checkpoints(0, 100)
    assert [c["seq"] for c in cps] == [5, 10]
    es = all_entries(store)
    assert verify_chain(es, checkpoints=cps, pubkeys=keyring.public_keys()).ok
    assert store.latest_checkpoint()["seq"] == 10
    assert store.counters.snapshot()["checkpoints"] == 2


def test_checkpoint_interval_only_when_new_entries(store, clock):
    assert store.checkpoint_if_due(60) is False  # empty ledger
    store.append(entry_in(1))
    assert store.checkpoint_if_due(60) is False  # not yet due
    clock.advance(seconds=120)
    assert store.checkpoint_if_due(60) is True
    assert store.checkpoint_if_due(60) is False  # nothing new
    store.append(entry_in(2))
    clock.advance(seconds=120)
    assert store.checkpoint_if_due(60) is True
    assert [c["seq"] for c in store.checkpoints(0, 10)] == [1, 2]


def test_checkpoint_covering_creates_on_demand_and_is_stable(store):
    for i in range(1, 8):
        store.append(entry_in(i))
    cp = store.checkpoint_covering(6)  # head is 7, cps exist at 5
    assert cp["seq"] == 7
    assert store.checkpoint_covering(6)["checkpoint_id"] == cp["checkpoint_id"]
    assert store.checkpoint_covering(5)["seq"] == 5
    assert store.checkpoint_covering(99) is None


def test_checkpoint_writes_are_idempotent_per_seq(store):
    for i in range(1, 4):
        store.append(entry_in(i))
    a = store.checkpoint_covering(3)
    b = store.checkpoint_covering(3)
    assert a == b and len(store.checkpoints(0, 10)) == 1


# ------------------------------------------------------------------ reads and cases
def test_pagination_bounds(store):
    for i in range(1, 8):
        store.append(entry_in(i))
    assert [e.seq for e in store.page(3, 2)] == [3, 4]
    assert len(store.page(1, 10_000)) == 7  # capped, never unbounded
    assert store.get(99) is None and store.get(2).seq == 2


def test_case_selection_by_refs_and_seqs(store):
    store.append(entry_in(1, refs=["case-a", "txn_1"]))
    store.append(entry_in(2, refs=["case-b"]))
    store.append(entry_in(3, refs=["case-a"]))
    assert store.select_seqs(["case-a"], [], limit=10) == [1, 3]
    assert store.select_seqs(["case-a"], [2], limit=10) == [1, 2, 3]
    assert store.select_seqs(["nope"], [], limit=10) == []
    with pytest.raises(LookupError):
        store.select_seqs([], [99], limit=10)
    assert len(store.select_seqs(["case-a"], [], limit=1)) == 2  # limit+1 means "too many"


# ---------------------------------------------------------------- fix round 1 minors
def test_receipt_timestamps_never_run_backwards(signer, tmp_path):
    from datetime import UTC, datetime, timedelta

    from evidence_ledger.store import LedgerStore

    times = iter(
        [
            datetime(2026, 3, 10, 12, 0, tzinfo=UTC) + timedelta(seconds=s)
            for s in (10, 5, 20, 1, 30)
        ]
    )
    s = LedgerStore(f"sqlite:///{tmp_path}/c.db", signer=signer, clock=lambda: next(times))
    for i in range(1, 5):
        s.append(entry_in(i))
    ts = [e.ts for e in s.page(1, 10)]
    assert ts == sorted(ts) and ts[1] == ts[0]  # clamped to the previous receipt time
    s.close()


def test_sqlite_insert_or_replace_cannot_overwrite_rows(store):
    from sqlalchemy.exc import DBAPIError

    for i in range(1, 6):
        store.append(entry_in(i))
    before = [e.entry_hash for e in store.page(1, 10)]
    row = store.get(2)
    with pytest.raises(DBAPIError), store.engine.begin() as c:
        c.execute(
            text(
                "INSERT OR REPLACE INTO ledger_entries (seq, ts, service, actor, event_type, "
                "payload_hash, prev_hash, entry_hash, case_refs, idem_key) VALUES "
                "(2, :ts, 's', 'a', 'e', :h, :h, :h, '[]', 'x')"
            ),
            {"ts": row.ts.isoformat(), "h": "f" * 64},
        )
    cp = store.latest_checkpoint()
    with pytest.raises(DBAPIError), store.engine.begin() as c:
        c.execute(
            text(
                "REPLACE INTO checkpoints (checkpoint_id, seq, entry_hash, count, ts, key_id, "
                "signature_b64) VALUES (:i, :s, :h, 1, 't', 'k', 'sig')"
            ),
            {"i": cp["checkpoint_id"], "s": cp["seq"], "h": "f" * 64},
        )
    assert [e.entry_hash for e in store.page(1, 10)] == before
    assert store.latest_checkpoint() == cp


def test_export_history_is_queryable_but_not_part_of_the_case(store):
    store.append(entry_in(1, refs=["case-x"]))
    audit = entry_in(2, event_type="package.exported", refs=["case-x"])
    store.append(audit)
    assert store.select_seqs(["case-x"], [], limit=10, exclude_event="package.exported") == [1]
    assert store.select_seqs(["case-x"], [], limit=10) == [1, 2]
    assert [e.seq for e in store.refs_page("case-x", "package.exported", 10)] == [2]
