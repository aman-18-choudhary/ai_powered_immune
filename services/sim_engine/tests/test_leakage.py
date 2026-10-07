"""Guard: no single cheap side feature may separate scam from benign (inflated benchmarks)."""

from collections import Counter
from decimal import Decimal

import numpy as np
import pytest

from sim_engine.benign import gen_benign_txns
from sim_engine.calls import gen_benign_calls
from sim_engine.scam import gen_scam_campaign


def balanced_accuracy(scam: list, benign: list) -> float:
    """Best single-feature classifier. Ordered numeric/boolean features: best threshold in
    either direction. Categorical features: majority-class mapping. Returns balanced
    accuracy (0.5 = useless, 1.0 = perfect separator)."""
    cats = sorted(set(scam) | set(benign), key=str)
    cs, cb = Counter(scam), Counter(benign)
    ns, nb = len(scam), len(benign)
    cat_ba = (
        sum(max(cs[c] / ns, 0) for c in cats if cs[c] / ns > cb[c] / nb) / 2
        + sum(cb[c] / nb for c in cats if cb[c] / nb >= cs[c] / ns) / 2
    )
    best = cat_ba
    if all(isinstance(v, int | float | bool | np.integer | np.floating) for v in cats):
        for t in cats:
            tpr = sum(v >= t for v in scam) / ns
            tnr = sum(v < t for v in benign) / nb
            best = max(best, (tpr + tnr) / 2, ((1 - tpr) + (1 - tnr)) / 2)
    return best


@pytest.fixture(scope="module")
def call_sets(world):
    scam_calls: dict[str, list] = {}
    for i in range(4):
        for e in gen_scam_campaign(world, f"leak-{i}", 15, 60 + i).calls:
            scam_calls.setdefault(e.call_id, []).append(e)
    ben_calls: dict[str, list] = {}
    for e in gen_benign_calls(world, days=4, seed=9):
        ben_calls.setdefault(e.call_id, []).append(e)
    return scam_calls, ben_calls


def test_call_channel_not_a_shortcut(call_sets):
    s, b = call_sets
    ba = balanced_accuracy([v[0].channel for v in s.values()], [v[0].channel for v in b.values()])
    assert ba < 0.80, ba


def test_chunk_count_not_a_shortcut(call_sets):
    s, b = call_sets
    ba = balanced_accuracy([len(v) for v in s.values()], [len(v) for v in b.values()])
    assert ba < 0.80, ba
    assert set(len(v) for v in s.values()) & set(len(v) for v in b.values())


@pytest.fixture(scope="module")
def txn_sets(world, benign):
    scam = [t for i in range(4) for t in gen_scam_campaign(world, f"tx-{i}", 15, 70 + i).txns]
    big = list(gen_benign_txns(world, days=7, seed=12))
    return scam, big + benign


@pytest.mark.parametrize(
    "name,fn",
    [
        ("whole_rupee", lambda t: t.amount_inr == t.amount_inr.to_integral_value()),
        ("multiple_of_100", lambda t: t.amount_inr % Decimal(100) == 0),
        ("multiple_of_1000", lambda t: t.amount_inr % Decimal(1000) == 0),
    ],
)
def test_amount_features_not_shortcuts(txn_sets, name, fn):
    scam, benign = txn_sets
    ba = balanced_accuracy([fn(t) for t in scam], [fn(t) for t in benign])
    assert ba < 0.80, (name, ba)


def test_payee_age_informative_but_not_decisive(txn_sets):
    scam, benign = txn_sets
    ba = balanced_accuracy(
        [t.payee_account_age_days < 30 for t in scam],
        [t.payee_account_age_days < 30 for t in benign],
    )
    assert 0.6 < ba < 0.95, ba
    young = np.mean([t.payee_account_age_days < 30 for t in benign])
    assert 0.08 < young < 0.30  # legitimately new payees exist


def test_benign_repeat_payee_affinity(benign):
    seen: set = set()
    repeat = 0
    for t in benign:
        key = (t.payer_token, t.payee_hash)
        repeat += key in seen
        seen.add(key)
    first_time = 1 - repeat / len(benign)
    assert 0.25 < first_time < 0.90  # first-time payee is real but non-decisive


def test_benign_amounts_mostly_whole_rupees(benign):
    upi = [t.amount_inr for t in benign if t.rail == "UPI"]
    whole = np.mean([a == a.to_integral_value() for a in upi])
    assert whole > 0.8
    counts = Counter(upi)
    assert all(counts[Decimal(r)] > 0 for r in (100, 500, 1000))  # round-number spikes
