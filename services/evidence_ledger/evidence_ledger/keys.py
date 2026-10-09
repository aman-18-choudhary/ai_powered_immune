"""Ed25519 signing keys: loading, key ids, rotation. The private key never leaves ``Signer``:
no ``__repr__``, no serialisation, nothing is logged."""

import base64
import os
import stat
from collections.abc import Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)

from .verify import key_id_of


class KeyError_(ValueError):
    """Configuration problem with a signing key (message never contains key material)."""


class Signer:
    def __init__(self, key: Ed25519PrivateKey) -> None:
        self.__key = key
        self.public_raw: bytes = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.key_id: str = key_id_of(self.public_raw)

    @classmethod
    def from_seed(cls, seed: bytes) -> "Signer":
        return cls(Ed25519PrivateKey.from_private_bytes(seed))

    @classmethod
    def generate(cls) -> "Signer":
        return cls(Ed25519PrivateKey.generate())

    def sign(self, message: bytes) -> bytes:
        return self.__key.sign(message)

    def private_pem(self) -> bytes:
        """Only for the keygen helper."""
        return self.__key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())

    def __repr__(self) -> str:
        return f"Signer(key_id={self.key_id})"

    __str__ = __repr__

    def __reduce__(self) -> str:
        raise TypeError("Signer cannot be serialised")


class KeyRing:
    """The current signer plus retired public keys (so old checkpoints and packages stay
    verifiable after rotation)."""

    def __init__(self, signer: Signer, retired: Mapping[str, bytes] | None = None) -> None:
        self.signer = signer
        self.retired: dict[str, bytes] = dict(retired or {})

    def public_keys(self) -> dict[str, str]:
        out = {k: v.hex() for k, v in self.retired.items()}
        out[self.signer.key_id] = self.signer.public_raw.hex()
        return dict(sorted(out.items()))

    def describe(self) -> list[dict[str, object]]:
        cur = self.signer.key_id
        return [
            {"key_id": k, "public_key_hex": v, "status": "current" if k == cur else "retired"}
            for k, v in self.public_keys().items()
        ]


def parse_public_key(token: str) -> bytes:
    """A raw 32-byte Ed25519 public key as hex (64 chars) or base64."""
    token = token.strip()
    try:
        raw = bytes.fromhex(token) if len(token) == 64 else base64.b64decode(token, validate=True)
        Ed25519PublicKey.from_public_bytes(raw)
    except ValueError as exc:
        raise KeyError_(
            "retired public key is neither 64 hex chars nor base64 of 32 bytes"
        ) from exc
    if len(raw) != 32:
        raise KeyError_("retired public key must be 32 bytes")
    return raw


def load_keyring(env: Mapping[str, str] | None = None) -> KeyRing:
    """LEDGER_SIGNING_KEY_FILE (PEM, mode must have no group/other bits) or, for dev only,
    LEDGER_SIGNING_KEY_B64 (base64 of the 32-byte seed). LEDGER_RETIRED_PUBKEYS: comma-separated
    public keys of rotated-out signers."""
    env = os.environ if env is None else env
    path = env.get("LEDGER_SIGNING_KEY_FILE")
    b64 = env.get("LEDGER_SIGNING_KEY_B64")
    if path:
        mode = stat.S_IMODE(os.stat(path).st_mode)
        if mode & 0o077:
            raise KeyError_(f"signing key file {path} must not be accessible to group/other (0600)")
        with open(path, "rb") as f:
            key = load_pem_private_key(f.read(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise KeyError_("signing key file is not an Ed25519 key")
        signer = Signer(key)
    elif b64:
        try:
            signer = Signer.from_seed(base64.b64decode(b64, validate=True))
        except ValueError as exc:
            raise KeyError_("LEDGER_SIGNING_KEY_B64 must be base64 of a 32-byte seed") from exc
    else:
        raise KeyError_("set LEDGER_SIGNING_KEY_FILE (or LEDGER_SIGNING_KEY_B64 for dev)")
    retired = {}
    for tok in (env.get("LEDGER_RETIRED_PUBKEYS") or "").split(","):
        if tok.strip():
            raw = parse_public_key(tok)
            retired[key_id_of(raw)] = raw
    retired.pop(signer.key_id, None)
    return KeyRing(signer, retired)
