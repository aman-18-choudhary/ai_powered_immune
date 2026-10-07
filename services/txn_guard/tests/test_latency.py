import statistics
import time
from datetime import timedelta

import numpy as np

from txn_guard.features import extract_features
from txn_guard.history import InMemoryHistoryStore
from txn_guard.model import Scorer

from .conftest import T0


def test_p99_latency_under_300ms(make_txn, capsys):
    scorer = Scorer()
    assert not scorer.fallback_mode
    store = InMemoryHistoryStore()
    rng = np.random.default_rng(0)
    for p in range(50):
        for i in range(30):
            store.record_txn(
                make_txn(
                    payer=f"p{p}",
                    amount=str(int(rng.lognormal(6, 1.2)) + 1),
                    ts=T0 - timedelta(hours=40 - i),
                )
            )
    txns = [
        make_txn(
            payer=f"p{i % 50}",
            amount=str(int(rng.lognormal(6, 1.5)) + 1),
            age=int(rng.integers(1, 2000)),
            payee=f"x{i % 90}",
            ts=T0,
        )
        for i in range(1000)
    ]
    scorer.score(extract_features(txns[0], store.context_for(txns[0], T0)))  # warm
    lat = []
    for t in txns:
        t0 = time.perf_counter()
        scorer.score(extract_features(t, store.context_for(t, T0)))
        lat.append(time.perf_counter() - t0)
    p50, p99 = statistics.median(lat), float(np.percentile(lat, 99))
    with capsys.disabled():
        print(
            f"\nlatency p50={p50 * 1000:.2f}ms p99={p99 * 1000:.2f}ms (n=1000, incl. ctx+features+model+reasons)"
        )
    assert p99 < 0.3
