import hashlib
import math

import pytest

from antibody_hub.bloom import BloomFilter


def _items(prefix: str, n: int) -> list[str]:
    return [hashlib.sha256(f"{prefix}{i}".encode()).hexdigest() for i in range(n)]


def test_no_false_negatives_10k():
    members = _items("m", 10_000)
    bf = BloomFilter.for_items(members, fp_rate=1e-6)
    assert all(bf.contains(x) for x in members)
    assert bf.count == 10_000


def test_empirical_fp_rate_within_5x_configured():
    members = _items("m", 10_000)
    p = 1e-2
    bf = BloomFilter.create(capacity=len(members), fp_rate=p)  # no headroom: worst case
    for x in members:
        bf.add(x)
    outsiders = _items("o", 100_000)
    fp = sum(bf.contains(x) for x in outsiders) / len(outsiders)
    print(f"measured FP rate {fp:.5f} at configured {p}")
    assert fp < 5 * p


def test_snapshot_grade_fp_rate_essentially_zero():
    members = _items("m", 10_000)
    bf = BloomFilter.for_items(members, fp_rate=1e-6)
    fp = sum(bf.contains(x) for x in _items("o", 100_000))
    print(f"snapshot-grade FP hits in 100k outsiders: {fp}")
    assert fp / 100_000 < 5e-6  # i.e. zero hits


def test_headroom_and_sizing():
    bf = BloomFilter.for_items(_items("m", 1000), fp_rate=1e-6)
    assert bf.capacity >= 2000
    assert bf.m >= math.ceil(-bf.capacity * math.log(1e-6) / math.log(2) ** 2)
    assert bf.k == round(bf.m / bf.capacity * math.log(2))


def test_serialisation_roundtrip():
    members = _items("m", 500)
    bf = BloomFilter.for_items(members, fp_rate=1e-4)
    back = BloomFilter.from_snapshot(bf.to_snapshot())
    assert (back.m, back.k, back.count) == (bf.m, bf.k, bf.count)
    assert all(back.contains(x) for x in members)
    assert back.to_snapshot()["bits"] == bf.to_snapshot()["bits"]


def test_empty_filter_contains_nothing():
    bf = BloomFilter.for_items([], fp_rate=1e-6)
    assert not bf.contains("a" * 64)


def test_invalid_params():
    with pytest.raises(ValueError):
        BloomFilter.create(capacity=0, fp_rate=0.01)
    with pytest.raises(ValueError):
        BloomFilter.create(capacity=10, fp_rate=1.5)
