import pytest
from scam_contracts import hashing as contract_hashing

from antibody_hub import hashing

KEY = b"test-federation-key"


def test_keyed_hash_is_reexported_not_copied():
    assert hashing.keyed_hash is contract_hashing.keyed_hash


def test_hash_differs_by_kind():
    h = {
        k: hashing.keyed_hash("123456789012", k, KEY) for k in ("mule_account", "script", "device")
    }
    assert len(set(h.values())) == 3


def test_hash_depends_on_key_and_is_deterministic():
    a = hashing.keyed_hash("123456789012", "mule_account", KEY)
    assert a == hashing.keyed_hash("123456789012", "mule_account", KEY)
    assert a != hashing.keyed_hash("123456789012", "mule_account", b"other-key")
    assert len(a) == 64


def test_empty_key_rejected():
    with pytest.raises(ValueError):
        hashing.keyed_hash("x", "device", b"")
