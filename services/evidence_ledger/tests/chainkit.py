"""Test helpers: build a valid in-memory chain and sign checkpoints with `cryptography`."""

import base64
import copy
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from scam_contracts.canonical import canonical_json, payload_hash

from evidence_ledger.verify import GENESIS, compute_entry_hash, key_id_of


def make_entries(n: int, *, start_hash: str = GENESIS, start_seq: int = 1) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    prev = start_hash
    for i in range(n):
        seq = start_seq + i
        payload = {"txn_id": f"t{seq}", "decision": "hold_verify", "reasons": ["URGENCY"]}
        e: dict[str, Any] = {
            "seq": seq, "ts": f"2026-03-10T12:00:{seq % 60:02d}.000000Z", "service": "txn-guard",
            "actor": "system:txn-guard", "event_type": "hold.created",
            "payload_hash": payload_hash(payload), "prev_hash": prev, "model_version": "m1",
            "payload": payload, "payload_present": True, "case_refs": [f"txn:t{seq}"],
        }  # fmt: skip
        e["entry_hash"] = compute_entry_hash(prev, e)
        out.append(e)
        prev = e["entry_hash"]
    return out


class Keys:
    def __init__(self, seed: bytes = b"\x01" * 32) -> None:
        self.sk = Ed25519PrivateKey.from_private_bytes(seed)
        self.pub = self.sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.key_id = key_id_of(self.pub)
        self.pubkeys = {self.key_id: self.pub.hex()}

    def checkpoint(self, entry: dict[str, Any]) -> dict[str, Any]:
        cp = {
            "checkpoint_id": f"cp-{entry['seq']}", "seq": entry["seq"],
            "entry_hash": entry["entry_hash"], "count": entry["seq"],
            "ts": "2026-03-10T12:30:00.000000Z", "key_id": self.key_id,
        }  # fmt: skip
        cp["signature_b64"] = base64.b64encode(self.sk.sign(canonical_json(cp))).decode()
        return cp


def clone(x: Any) -> Any:
    return copy.deepcopy(x)


def rehash_from(entries: list[dict[str, Any]], i: int) -> None:
    """Attacker helper: recompute hashes (and linkage) from index i to the end."""
    for j in range(i, len(entries)):
        if j > 0:
            entries[j]["prev_hash"] = entries[j - 1]["entry_hash"]
        entries[j]["entry_hash"] = compute_entry_hash(entries[j]["prev_hash"], entries[j])


def redact(entry: dict[str, Any]) -> dict[str, Any]:
    """The exported hash-only form: payload withheld, hash and presence flag kept."""
    out = clone(entry)
    out["payload"] = None
    out["payload_present"] = True
    return out
