import base64
import random

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from scam_contracts.canonical import canonical_json

from evidence_ledger.verify import canonical_json as vcanon
from evidence_ledger.verify import ed25519_verify

# RFC 8032 section 7.1 (sk, pk, msg, sig)
RFC = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]  # fmt: skip


@pytest.mark.parametrize(("sk", "pk", "msg", "sig"), RFC)
def test_rfc8032_vectors(sk, pk, msg, sig):
    m = bytes.fromhex(msg)
    assert ed25519_verify(bytes.fromhex(pk), m, bytes.fromhex(sig))
    # and the remembered vector agrees with the reference implementation
    priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(sk))
    assert priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex() == pk
    assert priv.sign(m).hex() == sig
    assert not ed25519_verify(bytes.fromhex(pk), m + b"x", bytes.fromhex(sig))


def test_agrees_with_cryptography_on_random_signatures():
    rnd = random.Random(7)
    for _ in range(12):
        priv = Ed25519PrivateKey.from_private_bytes(rnd.randbytes(32))
        pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        msg = rnd.randbytes(rnd.randrange(0, 300))
        sig = priv.sign(msg)
        assert ed25519_verify(pub, msg, sig)
        bad = bytearray(sig)
        bad[rnd.randrange(64)] ^= 1 << rnd.randrange(8)
        assert not ed25519_verify(pub, msg, bytes(bad))
        assert not ed25519_verify(pub, msg + b"\x00", sig)


def test_rejects_malformed_inputs():
    pk = bytes.fromhex(RFC[0][1])
    sig = bytes.fromhex(RFC[0][3])
    assert not ed25519_verify(pk, b"", sig[:63])
    assert not ed25519_verify(pk[:31], b"", sig)
    assert not ed25519_verify(b"\xff" * 32, b"", sig)  # y >= p / not on curve
    s_ge_l = sig[:32] + (2**253).to_bytes(32, "little")
    assert not ed25519_verify(pk, b"", s_ge_l)  # non-canonical scalar (malleability)


def test_canonical_json_parity_between_contract_and_standalone_verifier():
    import inspect
    import re

    samples = [{}, {"b": [1, 2.5, None, True], "a": {"z": "é₹", "y": ""}}, [1, "x"], "s", 3]
    for s in samples:
        assert canonical_json(s) == vcanon(s)
    body = lambda f: re.sub(r"\s+", "", inspect.getsource(f).split(":", 1)[1].split('"""')[-1])  # noqa: E731
    assert body(canonical_json) == body(vcanon)
    with pytest.raises(ValueError):
        vcanon({"x": float("inf")})
    assert base64  # silence unused
