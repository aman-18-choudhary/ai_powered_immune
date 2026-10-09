import base64
import os
import subprocess
import sys

import pytest

from evidence_ledger.keys import KeyError_, Signer, load_keyring
from evidence_ledger.verify import ed25519_verify, key_id_of


def test_key_id_is_first_16_hex_of_sha256_of_public_key():
    s = Signer.from_seed(b"\x03" * 32)
    import hashlib

    assert s.key_id == hashlib.sha256(s.public_raw).hexdigest()[:16] == key_id_of(s.public_raw)


def test_signer_never_exposes_the_private_key():
    s = Signer.from_seed(b"\x03" * 32)
    seed_hex = (b"\x03" * 32).hex()
    assert seed_hex not in repr(s) and seed_hex not in str(s)
    assert "key" in repr(s).lower() and s.key_id in repr(s)
    import pickle

    with pytest.raises(TypeError):
        pickle.dumps(s)
    assert ed25519_verify(s.public_raw, b"m", s.sign(b"m"))


def test_keygen_writes_0600_and_does_not_print_private_key(tmp_path):
    path = tmp_path / "k.pem"
    out = subprocess.run(
        [sys.executable, "-m", "evidence_ledger.keygen", str(path)], capture_output=True, text=True
    )
    assert out.returncode == 0 and "PRIVATE" not in out.stdout and "PRIVATE" not in out.stderr
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    ring = load_keyring({"LEDGER_SIGNING_KEY_FILE": str(path)})
    assert ring.signer.key_id in out.stdout
    again = subprocess.run(
        [sys.executable, "-m", "evidence_ledger.keygen", str(path)], capture_output=True, text=True
    )
    assert again.returncode != 0  # never overwrites a key


def test_key_file_with_loose_mode_is_refused(tmp_path):
    path = tmp_path / "k.pem"
    path.write_bytes(Signer.generate().private_pem())
    os.chmod(path, 0o644)
    with pytest.raises(KeyError_, match="0600"):
        load_keyring({"LEDGER_SIGNING_KEY_FILE": str(path)})
    os.chmod(path, 0o600)
    assert load_keyring({"LEDGER_SIGNING_KEY_FILE": str(path)}).signer.key_id


def test_dev_b64_key_and_missing_key():
    seed = base64.b64encode(b"\x05" * 32).decode()
    assert (
        load_keyring({"LEDGER_SIGNING_KEY_B64": seed}).signer.key_id
        == Signer.from_seed(b"\x05" * 32).key_id
    )
    with pytest.raises(KeyError_):
        load_keyring({})
    with pytest.raises(KeyError_) as ei:
        load_keyring({"LEDGER_SIGNING_KEY_B64": "!!notbase64"})
    assert "notbase64" not in str(ei.value)


def test_retired_public_keys_listed_after_rotation():
    old = Signer.from_seed(b"\x01" * 32)
    new = Signer.from_seed(b"\x02" * 32)
    env = {
        "LEDGER_SIGNING_KEY_B64": base64.b64encode(b"\x02" * 32).decode(),
        "LEDGER_RETIRED_PUBKEYS": f"{old.public_raw.hex()}, {base64.b64encode(new.public_raw).decode()}",
    }
    ring = load_keyring(env)
    assert set(ring.public_keys()) == {old.key_id, new.key_id}
    status = {k["key_id"]: k["status"] for k in ring.describe()}
    assert status == {old.key_id: "retired", new.key_id: "current"}
    with pytest.raises(KeyError_):
        load_keyring(env | {"LEDGER_RETIRED_PUBKEYS": "zz"})


def test_pem_is_pkcs8_ed25519():
    pem = Signer.generate().private_pem()
    assert pem.startswith(b"-----BEGIN PRIVATE KEY-----")
