"""Opt-in Postgres tests: set LEDGER_TEST_POSTGRES_URL (see scripts/with_postgres.sh)."""

import os
import threading
import time

import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError

from evidence_ledger import package as pkg
from evidence_ledger.keys import KeyRing
from evidence_ledger.store import (
    ADVISORY_LOCK_ID,
    LedgerStore,
    checkpoints,
    ledger_entries,
    metadata,
)
from evidence_ledger.verify import verify_chain

from .conftest import entry_in, make_signer

URL = os.getenv("LEDGER_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="LEDGER_TEST_POSTGRES_URL not set")


@pytest.fixture
def store():
    s = LedgerStore(URL, signer=make_signer(), checkpoint_every=50)
    metadata.drop_all(s.engine)
    s.create_schema()
    yield s
    metadata.drop_all(s.engine)
    s.close()


def run_threads(fns):
    errs = []
    barrier = threading.Barrier(len(fns))

    def wrap(fn):
        def go():
            barrier.wait()
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                errs.append(e)

        return go

    ts = [threading.Thread(target=wrap(f)) for f in fns]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs, errs


def test_concurrent_appends_are_gap_free_and_verify(store):
    def worker(w):
        return lambda: [store.append(entry_in(w * 1000 + i)) for i in range(50)]

    t0 = time.perf_counter()
    run_threads([worker(w) for w in range(8)])
    dt = time.perf_counter() - t0
    es = store.page(1, 1000)
    assert [e.seq for e in es] == list(range(1, 401))
    cps = store.checkpoints(0, 100)
    assert [c["seq"] for c in cps] == [50 * i for i in range(1, 9)]
    ring = KeyRing(store.signer)
    assert verify_chain(es, checkpoints=cps, pubkeys=ring.public_keys()).ok
    print(f"\npostgres: 400 appends (8 threads) in {dt:.2f}s = {400 / dt:.0f}/s")


def test_duplicate_race_yields_exactly_one_row(store):
    results = []
    run_threads([lambda: results.append(store.append(entry_in(1))) for _ in range(12)])
    assert sum(r.created for r in results) == 1 and store.head().seq == 1
    with store.engine.begin() as c:
        assert c.execute(text("select count(*) from ledger_entries")).scalar() == 1


def test_triggers_block_update_delete_truncate(store):
    for i in range(1, 51):
        store.append(entry_in(i))
    for table in (ledger_entries, checkpoints):
        with pytest.raises(DBAPIError), store.engine.begin() as c:
            c.execute(
                update(table).values({list(table.c.keys())[1]: table.c[list(table.c.keys())[1]]})
            )
        with pytest.raises(DBAPIError), store.engine.begin() as c:
            c.execute(delete(table))
        with pytest.raises(DBAPIError), store.engine.begin() as c:
            c.execute(text(f"truncate {table.name}"))
    assert store.head().seq == 50


def test_schema_creation_twice_and_concurrently(store):
    run_threads([store.create_schema for _ in range(4)])
    store.create_schema()
    assert store.append(entry_in(1)).created


def test_advisory_lock_serialises_appends(store):
    holder = store.engine.connect()
    tx = holder.begin()
    holder.execute(text("SELECT pg_advisory_xact_lock(:i)"), {"i": ADVISORY_LOCK_ID})
    done = threading.Event()

    def go():
        store.append(entry_in(1))
        done.set()

    t = threading.Thread(target=go)
    t.start()
    assert not done.wait(0.5)  # blocked behind the lock holder
    tx.commit()
    holder.close()
    assert done.wait(5)
    t.join()
    assert store.head().seq == 1


def test_package_on_postgres(store, tmp_path):
    for i in range(1, 61):
        store.append(entry_in(i, refs=["c"] if i % 20 == 0 else []))
    ring = KeyRing(store.signer)
    p = pkg.build(store, ring, "c", case_refs=["c"])
    z = tmp_path / "p.zip"
    z.write_bytes(p.data)
    from evidence_ledger.verify import verify_package

    assert verify_package(z)[0].ok


def test_on_conflict_do_update_cannot_rewrite_rows(store):
    store.append(entry_in(1))
    with pytest.raises(DBAPIError), store.engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO ledger_entries (seq, ts, service, actor, event_type, payload_hash, "
                "prev_hash, entry_hash, case_refs, idem_key) VALUES (1, 't', 's', 'evil', 'e', "
                ":h, :h, :h2, '[]', :h3) ON CONFLICT (seq) DO UPDATE SET actor = 'evil'"
            ),
            {"h": "f" * 64, "h2": "e" * 64, "h3": "d" * 64},
        )
    assert store.get(1).actor != "evil"
    with pytest.raises(DBAPIError), store.engine.begin() as c:  # duplicate seq without ON CONFLICT
        c.execute(
            text(
                "INSERT INTO ledger_entries (seq, ts, service, actor, event_type, payload_hash, "
                "prev_hash, entry_hash, case_refs, idem_key) VALUES (1, 't', 's', 'evil', 'e', "
                ":h, :h, :h2, '[]', :h3)"
            ),
            {"h": "f" * 64, "h2": "c" * 64, "h3": "b" * 64},
        )
