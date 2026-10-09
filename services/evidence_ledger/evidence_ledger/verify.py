#!/usr/bin/env python3
"""Standalone, offline verifier for evidence-ledger hash chains and evidence packages.

STANDARD LIBRARY ONLY. This single file is (a) imported by the ledger service for chain/entry
hashing and (b) shipped verbatim inside every evidence package as ``verify.py``. It needs no
network, no third-party package and no ledger code: read it, it is the whole algorithm.

Usage:  python verify.py [PATH] [--trusted-key-id KEY_ID] [--json]
        PATH is an extracted package directory (default: the directory of this file) or the
        package .zip. Exit status 0 = PASS, 1 = FAIL. Prints the reasons.

Definitions
* canonical JSON: UTF-8, keys sorted, separators (",", ":"), ensure_ascii=False, no NaN.
* chain format 2: entry_hash = sha256( prev_hash_ascii || canonical_json({seq, ts, service,
  actor, event_type, payload_hash, payload_present, model_version, case_refs}) ) as hex; the first
  entry's prev_hash is 64 zeros. ``ts`` is the ledger's receipt time (UTC). The payload BODY is
  not hashed; it is checked separately: sha256(canonical_json(payload)) must equal payload_hash.
  An entry whose payload_present is true but whose body is omitted is REDACTED (payload
  withheld): its hash chain still verifies, its content is unknown beyond payload_hash.
* checkpoint signature = Ed25519 over canonical_json({checkpoint_id, seq, entry_hash, count, ts,
  key_id}); key_id = first 16 hex of sha256(raw 32-byte public key).
* first_bad_seq: the seq of the first entry at which a check fails, scanning in order. A
  recomputed-hash mismatch is reported AT the altered entry; if an attacker also re-hashes the
  altered entry, the break surfaces at the NEXT entry (prev_hash linkage). A missing entry is
  reported as the missing seq; a checkpoint mismatch is reported as the checkpoint's seq (the
  tamper lies at or before it); tail truncation as last_seen_seq + 1.
* The public keys inside a package are self-asserted. Pin the signer with --trusted-key-id taken
  from a source you trust independently of the package.
"""

import argparse
import base64
import hashlib
import hmac
import json
import re
import sys
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GENESIS = "0" * 64
PACKAGE_FORMAT_VERSION = 2
CHAIN_FORMAT_VERSION = 2
PACKAGE_MEMBERS = (
    "entries.json",
    "chain_proof.json",
    "explanations.md",
    "certificate_section63_template.md",
    "verify.py",
)
MANIFEST = "manifest.json"
CHECKPOINT_FIELDS = ("checkpoint_id", "seq", "entry_hash", "count", "ts", "key_id")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def canonical_json(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def payload_hash(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj)).hexdigest()


# --------------------------------------------------------------------------- Ed25519 (RFC 8032)
_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _I % _P
    if (x * x - x2) % _P != 0:
        return None
    return _P - x if (x & 1) != sign else x


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0) or 0
_G = (_GX, _GY, 1, _GX * _GY % _P)
_Point = tuple[int, int, int, int]


def _add(p: _Point, q: _Point) -> _Point:
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s: int, p: _Point) -> _Point:
    q: _Point = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _compress(p: _Point) -> bytes:
    zi = pow(p[2], _P - 2, _P)
    x, y = p[0] * zi % _P, p[1] * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decompress(b: bytes) -> _Point | None:
    if len(b) != 32:
        return None
    n = int.from_bytes(b, "little")
    y, sign = n & ((1 << 255) - 1), n >> 255
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """RFC 8032 section 5.1.7 (cofactorless), rejecting non-canonical S and bad encodings."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a_pt = _decompress(public_key)
    r_pt = _decompress(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if a_pt is None or r_pt is None or s >= _L:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public_key + message).digest(), "little")
    left = _compress(_mul(s, _G))
    right = _compress(_add(r_pt, _mul(h % _L, a_pt)))
    return hmac.compare_digest(left, right)


def key_id_of(public_key: bytes) -> str:
    return hashlib.sha256(public_key).hexdigest()[:16]


# --------------------------------------------------------------------------- chain
@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    first_bad_seq: int | None
    reason: str
    redacted: tuple[int, ...] = ()


def _ok() -> VerifyResult:
    return VerifyResult(True, None, "ok")


def _bad(seq: int | None, reason: str) -> VerifyResult:
    return VerifyResult(False, seq, reason)


def ts_str(value: Any) -> str:
    """Receipt time as ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` (UTC). Accepts that string, any ISO-8601
    string, or an aware datetime."""
    from datetime import UTC, datetime

    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        raise ValueError("naive timestamp")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


ENTRY_FIELDS = frozenset(
    {
        "seq", "ts", "service", "actor", "event_type", "payload_hash", "prev_hash", "entry_hash",
        "model_version", "payload", "payload_present", "case_refs",
    }
)  # fmt: skip
HASHED_FIELDS = (
    "seq", "ts", "service", "actor", "event_type", "payload_hash", "payload_present",
    "model_version", "case_refs",
)  # fmt: skip


def entry_dict(e: Any) -> dict[str, Any]:
    """Normalise a dict or a pydantic-like model to the canonical entry dict. ``payload_present``
    defaults to ``payload is not None`` (a stored entry); an exported REDACTED entry sets it true
    with ``payload`` None."""
    d = e.model_dump() if hasattr(e, "model_dump") else dict(e)
    payload = d.get("payload")
    out = {
        "seq": d["seq"], "ts": ts_str(d["ts"]), "service": d["service"], "actor": d["actor"],
        "event_type": d["event_type"], "payload_hash": d["payload_hash"],
        "prev_hash": d["prev_hash"], "model_version": d.get("model_version"),
        "payload": payload,
        "payload_present": d.get("payload_present", payload is not None),
        "case_refs": list(d.get("case_refs") or []),
    }  # fmt: skip
    if "entry_hash" in d:
        out["entry_hash"] = d["entry_hash"]
    return out


def compute_entry_hash(prev_hash: str, entry: Any) -> str:
    d = entry_dict(entry if isinstance(entry, Mapping) else entry.model_dump())
    core = {k: d[k] for k in HASHED_FIELDS}
    return hashlib.sha256(prev_hash.encode("ascii") + canonical_json(core)).hexdigest()


def _normalise_keys(pubkeys: Mapping[str, Any]) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for kid, v in pubkeys.items():
        raw = bytes.fromhex(v) if isinstance(v, str) else bytes(v)
        if key_id_of(raw) == kid:  # a key listed under the wrong id is simply not usable
            out[kid] = raw
    return out


def checkpoint_message(cp: Mapping[str, Any]) -> bytes:
    return canonical_json({k: cp[k] for k in CHECKPOINT_FIELDS})


def check_checkpoint_signature(cp: Mapping[str, Any], keys: Mapping[str, bytes]) -> str | None:
    """None if the signature verifies, else a reason."""
    try:
        raw = keys.get(cp["key_id"])
        if raw is None:
            return f"checkpoint {cp['seq']} signed by unknown key {cp['key_id']}"
        sig = base64.b64decode(cp["signature_b64"], validate=True)
        if not ed25519_verify(raw, checkpoint_message(cp), sig):
            return f"checkpoint {cp['seq']} signature invalid"
    except (KeyError, ValueError, TypeError):
        return "malformed checkpoint"
    return None


def verify_chain(
    entries: Sequence[Any],
    *,
    checkpoints: Sequence[Mapping[str, Any]] | None = None,
    pubkeys: Mapping[str, Any] | None = None,
    anchor_prev_hash: str | None = None,
) -> VerifyResult:
    """Verify a chain (or a contiguous segment of it).

    Checks, in order per entry: seq contiguity, prev_hash linkage, payload_hash vs payload (when a
    payload is present), entry_hash recomputation. The first entry must link to
    ``anchor_prev_hash`` if given, else to GENESIS when its seq is 1 (an unanchored segment is
    checked for internal consistency only). If ``checkpoints`` are given, ``pubkeys`` are required
    (fail closed); each checkpoint's signature is verified and, when its seq lies inside the
    entries, its entry_hash must match; a checkpoint beyond the last entry means truncation.
    """
    try:
        ents = [entry_dict(e) for e in entries]
        stored = [dict(e) if isinstance(e, Mapping) else e.model_dump() for e in entries]
    except (KeyError, ValueError, TypeError, AttributeError):
        return _bad(None, "malformed entry")
    prev_seq: int | None = None
    prev_hash: str | None = None
    redacted: list[int] = []
    for e, raw in zip(ents, stored, strict=True):
        seq = e["seq"]
        if not isinstance(raw, Mapping) or not set(raw) <= ENTRY_FIELDS:
            return _bad(seq if isinstance(seq, int) else None, f"entry {seq} has an unknown field")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
            return _bad(seq if isinstance(seq, int) else None, "invalid seq")
        if prev_seq is not None and seq != prev_seq + 1:
            if seq > prev_seq + 1:
                return _bad(prev_seq + 1, f"entry {prev_seq + 1} missing (next is {seq})")
            return _bad(prev_seq + 1, f"entries out of order or duplicated at {seq}")
        if prev_hash is None:
            expect = anchor_prev_hash if anchor_prev_hash is not None else (
                GENESIS if seq == 1 else None
            )  # fmt: skip
        else:
            expect = prev_hash
        if expect is not None and e["prev_hash"] != expect:
            return _bad(seq, f"entry {seq} prev_hash does not link to its predecessor")
        if not isinstance(e["payload_present"], bool):
            return _bad(seq, f"entry {seq} payload_present must be true or false")
        if e["payload"] is not None:
            if not e["payload_present"]:
                return _bad(seq, f"entry {seq} carries a payload but payload_present is false")
            try:
                ph = payload_hash(e["payload"])
            except (TypeError, ValueError):
                return _bad(seq, f"entry {seq} payload is not canonical JSON")
            if ph != e["payload_hash"]:
                return _bad(seq, f"entry {seq} payload does not match payload_hash")
        elif e["payload_present"]:
            redacted.append(seq)  # payload withheld: the hash chain still binds payload_hash
        if compute_entry_hash(e["prev_hash"], e) != raw.get("entry_hash"):
            return _bad(seq, f"entry {seq} entry_hash mismatch (entry altered)")
        prev_seq, prev_hash = seq, e["entry_hash"]
    if checkpoints:
        if not pubkeys:
            return _bad(None, "checkpoints given but no public keys: signatures cannot be verified")
        keys = _normalise_keys(pubkeys)
        by_seq = {e["seq"]: e for e in ents}
        for cp in sorted(checkpoints, key=lambda c: c.get("seq", 0)):
            problem = check_checkpoint_signature(cp, keys)
            if problem:
                return _bad(cp.get("seq"), problem)
            seq = cp["seq"]
            if prev_seq is not None and seq > prev_seq:
                return _bad(
                    prev_seq + 1,
                    f"chain truncated: checkpoint covers seq {seq}, last entry {prev_seq}",
                )
            hit = by_seq.get(seq)
            if hit is not None and hit["entry_hash"] != cp["entry_hash"]:
                return _bad(seq, f"entry {seq} does not match signed checkpoint (chain rewritten)")
    return VerifyResult(True, None, "ok", tuple(redacted))


# --------------------------------------------------------------------------- package
def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class _Source:
    """Read-only view of an extracted directory or a zip file."""

    def __init__(self, path: Path) -> None:
        self.zip = zipfile.ZipFile(path) if path.is_file() else None
        self.dir = None if self.zip else path

    def names(self) -> list[str]:
        if self.zip:
            return [n for n in self.zip.namelist() if not n.endswith("/")]
        assert self.dir is not None
        return [p.name for p in self.dir.iterdir() if p.is_file()]

    def read(self, name: str) -> bytes:
        if self.zip:
            return self.zip.read(name)
        assert self.dir is not None
        return (self.dir / name).read_bytes()


def verify_package(path: Path, trusted_key_id: str | None = None) -> tuple[VerifyResult, list[str]]:
    """Return (result, notes). Notes are informational lines (signer, segment, caveats)."""
    notes: list[str] = []
    try:
        src = _Source(path)
        names = set(src.names())
        if MANIFEST not in names:
            return _bad(None, "manifest.json missing"), notes
        manifest = json.loads(src.read(MANIFEST))
        if manifest.get("package_format_version") != PACKAGE_FORMAT_VERSION:
            return _bad(None, "unsupported package_format_version"), notes
        if manifest.get("chain_format_version") != CHAIN_FORMAT_VERSION:
            return _bad(None, "unsupported chain_format_version"), notes
        members = manifest["members"]
        if set(members) != set(PACKAGE_MEMBERS):
            return _bad(None, "manifest member list is not the expected set"), notes
        extra = names - set(PACKAGE_MEMBERS) - {MANIFEST}
        extra = {n for n in extra if not n.startswith("__pycache__")}
        if extra:
            return _bad(None, f"unexpected files in package: {sorted(extra)}"), notes
        blobs: dict[str, bytes] = {}
        for name in PACKAGE_MEMBERS:
            if name not in names:
                return _bad(None, f"member {name} missing"), notes
            blobs[name] = src.read(name)
            if not hmac.compare_digest(_sha(blobs[name]), str(members[name])):
                return _bad(None, f"member {name} does not match the manifest hash"), notes
        proof = json.loads(blobs["chain_proof.json"])
        keys = _normalise_keys(proof["public_keys"])
        kid = manifest["key_id"]
        if trusted_key_id is not None and kid != trusted_key_id:
            return _bad(None, f"signer key_id {kid} is not the trusted key {trusted_key_id}"), notes
        if kid not in keys:
            return _bad(None, "manifest signer key is not among the package public keys"), notes
        unsigned = {k: v for k, v in manifest.items() if k != "signature_b64"}
        try:
            sig = base64.b64decode(manifest["signature_b64"], validate=True)
        except (KeyError, ValueError):
            return _bad(None, "manifest signature missing or malformed"), notes
        if not ed25519_verify(keys[kid], canonical_json(unsigned), sig):
            return _bad(None, "manifest signature invalid"), notes
        notes.append(f"manifest signed by key_id {kid}")
        if trusted_key_id is None:
            notes.append(
                "WARNING: signer key not pinned; compare this key_id with one obtained "
                "independently (--trusted-key-id)"
            )
        data = json.loads(blobs["entries.json"])
        entries = data["entries"]
        if data.get("case_id") != manifest["case_id"]:
            return _bad(None, "entries.json case_id differs from manifest"), notes
        seg = proof["segment"]
        if (
            not entries
            or entries[0]["seq"] != seg["from_seq"]
            or entries[-1]["seq"] != seg["to_seq"]
        ):
            return _bad(None, "segment bounds do not match entries"), notes
        cps = proof["checkpoints"]
        if not any(c.get("seq") == seg["to_seq"] for c in cps):
            return _bad(None, "no signed checkpoint covers the end of the segment"), notes
        res = verify_chain(
            entries, checkpoints=cps, pubkeys=proof["public_keys"],
            anchor_prev_hash=proof["anchor_prev_hash"],
        )  # fmt: skip
        if not res.ok:
            return res, notes
        selected = set(data["selected_seqs"])
        withheld = selected & set(res.redacted)
        if withheld:
            return _bad(
                min(withheld), f"selected entry {min(withheld)} has its payload withheld"
            ), notes
        if list(res.redacted) != manifest.get("redacted_seqs"):
            return _bad(None, "manifest redacted_seqs does not match the redacted entries"), notes
        by_seq = {e["seq"]: e["entry_hash"] for e in entries}
        listed = manifest["entries"]
        if [x["seq"] for x in listed] != sorted(set(data["selected_seqs"])):
            return _bad(None, "manifest entry list differs from entries.json selection"), notes
        for x in listed:
            if by_seq.get(x["seq"]) != x["entry_hash"]:
                return _bad(x["seq"], f"manifest entry {x['seq']} not in the verified chain"), notes
        if res.redacted:
            notes.append(
                f"redacted (payload withheld): {len(res.redacted)} context entries, seqs "
                f"{list(res.redacted)}; the chain binds only their payload_hash"
            )
        notes.append(
            f"chain segment {seg['from_seq']}..{seg['to_seq']} verified ({len(entries)} entries, "
            f"{len(listed)} selected) against signed checkpoint(s)"
        )
        return _ok(), notes
    except (KeyError, ValueError, TypeError, OSError, zipfile.BadZipFile, AttributeError) as exc:
        return _bad(None, f"package unreadable or malformed ({type(exc).__name__})"), notes


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Offline evidence-package verifier")
    ap.add_argument("path", nargs="?", default=str(Path(__file__).resolve().parent))
    ap.add_argument("--trusted-key-id", default=None)
    ap.add_argument("--allow-unpinned", action="store_true")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)
    res, notes = verify_package(Path(args.path), args.trusted_key_id)
    if args.json:
        print(json.dumps({"ok": res.ok, "first_bad_seq": res.first_bad_seq, "reason": res.reason,
                          "notes": notes}))  # fmt: skip
    else:
        for n in notes:
            print(f"  {n}")
        if res.ok:
            print("PASS: package is internally consistent and signed")
        else:
            at = f" (first bad seq {res.first_bad_seq})" if res.first_bad_seq is not None else ""
            print(f"FAIL: {res.reason}{at}")
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
