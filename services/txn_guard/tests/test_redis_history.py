from datetime import timedelta

import fakeredis
import pytest
from scam_contracts.models import CallRisk

from txn_guard.features import extract_features
from txn_guard.history import Context, InMemoryHistoryStore
from txn_guard.redis_history import RedisHistoryStore

from .conftest import T0


@pytest.fixture
def stores():
    return RedisHistoryStore(fakeredis.FakeRedis()), InMemoryHistoryStore()


def _feed(stores, make_txn):
    for i in range(30):
        t = make_txn(amount=str(300 + (i * 37) % 400), payee=f"p{i % 4}", device=f"d{i % 2}",
                     ts=T0 - timedelta(days=9) + timedelta(hours=i * 6))  # fmt: skip
        for s in stores:
            s.record_txn(t)


def test_redis_context_matches_in_memory(stores, make_txn):
    _feed(stores, make_txn)
    risk = CallRisk(call_id="c", victim_token="payer_1", score=0.9, reasons=[],
                    model_version="x", ts=T0 - timedelta(minutes=3))  # fmt: skip
    for s in stores:
        s.record_call_risk(risk)
    for kw in ({}, {"payee": "p1"}, {"payee": "brand_new", "device": "d_new", "amount": "50000"}):
        t = make_txn(**kw)
        a = extract_features(t, stores[0].context_for(t, T0))
        b = extract_features(t, stores[1].context_for(t, T0))
        assert a.keys() == b.keys()
        for k in a:
            assert a[k] == pytest.approx(b[k], rel=1e-9, abs=1e-9), k


def test_unknown_payer_and_payee_fields_are_zero_not_none(stores, make_txn):
    ctx = stores[0].context_for(make_txn(payer="nobody", payee="nobody_payee"), T0)
    assert isinstance(ctx, Context)
    assert ctx.payee_n == 0 and ctx.payee_max_amount == 0.0 and ctx.payee_first_seen_ts is None
    assert ctx.payer_n == 0 and ctx.recent == () and ctx.call_risks == ()
    extract_features(make_txn(payer="nobody"), ctx)  # no TypeError


def test_none_or_missing_stored_fields_coerced(make_txn):
    r = fakeredis.FakeRedis()
    s = RedisHistoryStore(r)
    t = make_txn()
    s.record_txn(t)
    for key in r.keys("*pair*"):
        if r.type(key) == b"hash":
            r.hdel(key, "max")  # simulate a missing field
            r.hset(key, "n", "")
    ctx = s.context_for(make_txn(ts=T0 + timedelta(hours=1)), T0)
    assert ctx.payee_n == 0 and ctx.payee_max_amount == 0.0
    extract_features(make_txn(), ctx)


def test_record_txn_is_idempotent_per_txn_id(stores, make_txn):
    t = make_txn(amount="1000")
    for _ in range(3):
        stores[0].record_txn(t)
    stores[1].record_txn(t)
    t2 = make_txn(amount="1000", ts=T0 + timedelta(minutes=5))
    c = stores[0].context_for(t2, T0)
    assert c.payer_n == 1 and c.payee_n == 1 and len(c.recent) == 1
