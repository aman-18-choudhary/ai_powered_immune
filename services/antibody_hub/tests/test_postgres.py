"""Opt-in Postgres concurrency tests: set HUB_TEST_POSTGRES_URL (see scripts/with_postgres.sh)."""

import os
import threading
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from antibody_hub.store import AntibodyStore, Protected, antibodies, metadata, protected_hashes

from .conftest import Clock, mule

URL = os.getenv("HUB_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="HUB_TEST_POSTGRES_URL not set")


@pytest.fixture
def store():
    clock = Clock()
    s = AntibodyStore(URL, clock=clock)  # type: ignore[arg-type]
    metadata.drop_all(s.engine)
    s.create_schema()
    s.test_clock = clock  # type: ignore[attr-defined]
    yield s
    metadata.drop_all(s.engine)
    s.close()


def run_threads(fns):
    out, errs = [], []
    barrier = threading.Barrier(len(fns))

    def wrap(fn):
        def go():
            barrier.wait()
            try:
                out.append(fn())
            except Exception as e:  # noqa: BLE001
                errs.append(e)

        return go

    ts = [threading.Thread(target=wrap(f)) for f in fns]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return out, errs


def test_concurrent_submits_create_one(store):
    out, errs = run_threads(
        [lambda: store.submit("mule_account", mule(), "bank_a", "a") for _ in range(12)]
    )
    assert not errs and sum(r.created for r in out) == 1
    assert len(store.pending()) == 2


def test_concurrent_expiry_sweeps_single_tombstone_each(store):
    for i in range(20):
        store.submit("mule_account", mule(f"e{i}"), "bank_a", "a")
    store.test_clock.advance(days=15)
    out, errs = run_threads([store.expire_due for _ in range(4)])
    assert not errs and sum(out) == 20
    assert len(store.pending(1000)) == 20 * 2 * 2  # created+expired, antibody+ledger


def test_concurrent_add_protected_same_hash_idempotent(store):
    store.submit("mule_account", mule(), "bank_a", "a")
    out, errs = run_threads([lambda: store.add_protected(mule(), "admin", None) for _ in range(8)])
    assert not errs
    assert sorted(c for c, _ in out) == [False] * 7 + [True]
    assert sum(len(r) for _, r in out) == 1


def test_protected_invariant_under_submit_race(store):
    hashes = [mule(f"p{i}") for i in range(40)]
    fns = []
    for h in hashes:

        def submit(h=h):
            try:
                store.submit("mule_account", h, "bank_a", "a")
            except Protected:
                pass

        fns += [submit, lambda h=h: store.add_protected(h, "admin", None)]
    _, errs = run_threads(fns)
    assert not errs
    with store.engine.connect() as c:
        bad = c.execute(
            select(func.count())
            .select_from(
                antibodies.join(
                    protected_hashes, antibodies.c.key_hash == protected_hashes.c.key_hash
                )
            )
            .where(antibodies.c.revoked.is_(False))
        ).scalar()
    assert bad == 0


def test_expired_then_resubmitted_concurrently_single_new_generation(store):
    store.submit("mule_account", mule(), "bank_a", "a")
    store.test_clock.advance(days=14, seconds=1)
    out, errs = run_threads(
        [lambda: store.submit("mule_account", mule(), "bank_a", "a") for _ in range(6)]
    )
    assert not errs and sum(r.created for r in out) == 1
    assert {r.record["generation"] for r in out} == {2}
    _ = timedelta
