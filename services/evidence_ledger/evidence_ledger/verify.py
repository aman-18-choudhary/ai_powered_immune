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
import stat
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
    if _compress(a_pt) != public_key or _compress(r_pt) != signature[:32]:
        return False  # non-canonical point encodings
    if _compress(_mul(8, a_pt)) == _compress((0, 1, 1, 0)):
        return False  # small-order public key
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
    pinned: bool = False


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


MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_RATIO = 100
MAX_JSON_DEPTH = 64
_ALLOWED = frozenset((*PACKAGE_MEMBERS, MANIFEST))


class PackageError(ValueError):
    """Structural problem with the package container (message is safe to print)."""


def load_json(raw: bytes) -> Any:
    """json.loads with a nesting cap (checked iteratively, so hostile input cannot trigger
    RecursionError) and strict UTF-8."""
    depth = 0
    in_str = esc = False
    for b in raw:
        if in_str:
            if esc:
                esc = False
            elif b == 0x5C:
                esc = True
            elif b == 0x22:
                in_str = False
        elif b == 0x22:
            in_str = True
        elif b in (0x5B, 0x7B):
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise PackageError(f"JSON nested deeper than {MAX_JSON_DEPTH} levels")
        elif b in (0x5D, 0x7D):
            depth -= 1
    return json.loads(raw.decode("utf-8"))


class _Source:
    """Read-only, validated view of an extracted directory or a zip file. Construction fails
    (PackageError) on duplicate names, nested/odd names, non-regular files, oversized members and
    compression bombs; only the expected flat set of member names is accepted."""

    def __init__(self, path: Path) -> None:
        self.zip: zipfile.ZipFile | None = None
        self.dir: Path | None = None
        sizes: dict[str, int] = {}
        if path.is_file():
            self.zip = zipfile.ZipFile(path)
            seen: set[str] = set()
            for info in self.zip.infolist():
                name = info.filename
                if name in seen:
                    raise PackageError(f"duplicate member name {name!r} in zip")
                seen.add(name)
                if name not in _ALLOWED:
                    raise PackageError(f"unexpected member {name!r} in package")
                mode = (info.external_attr >> 16) & 0o170000
                if info.is_dir() or mode not in (0, 0o100000):
                    raise PackageError(f"member {name} is not a regular file")
                if info.file_size > MAX_MEMBER_BYTES:
                    raise PackageError(f"member {name} too large")
                if info.file_size > 1024 and info.file_size > MAX_RATIO * max(
                    info.compress_size, 1
                ):
                    raise PackageError(f"member {name} exceeds the allowed compression ratio")
                sizes[name] = info.file_size
        elif path.is_dir():
            self.dir = path
            for entry in sorted(path.iterdir()):
                name = entry.name
                if name == "__pycache__" and entry.is_dir() and not entry.is_symlink():
                    continue
                if name not in _ALLOWED:
                    raise PackageError(f"unexpected file {name!r} in package")
                st = entry.lstat()
                if not stat.S_ISREG(st.st_mode):
                    raise PackageError(f"member {name} is not a regular file")
                if st.st_size > MAX_MEMBER_BYTES:
                    raise PackageError(f"member {name} too large")
                sizes[name] = st.st_size
        else:
            raise PackageError("package path is neither a directory nor a zip file")
        if sum(sizes.values()) > MAX_TOTAL_BYTES:
            raise PackageError("package too large")
        self.sizes = sizes

    def names(self) -> list[str]:
        return list(self.sizes)

    def read(self, name: str) -> bytes:
        if self.zip:
            with self.zip.open(name) as f:
                data = f.read(MAX_MEMBER_BYTES + 1)
        else:
            assert self.dir is not None
            with open(self.dir / name, "rb") as f:
                data = f.read(MAX_MEMBER_BYTES + 1)
        if len(data) > MAX_MEMBER_BYTES:
            raise PackageError(f"member {name} too large")
        return data


_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")


def parse_pubkey(token: str) -> bytes:
    """A trusted Ed25519 public key: 64 hex chars, base64 of the raw 32 bytes, base64/PEM of an
    X.509 SubjectPublicKeyInfo, or a path to a file holding any of those."""
    t = token.strip()
    if len(t) < 4096 and Path(t).is_file():
        t = Path(t).read_text().strip()
    body = "".join(ln for ln in t.splitlines() if not ln.startswith("-----"))
    try:
        raw = (
            bytes.fromhex(body)
            if re.fullmatch(r"[0-9a-fA-F]{64}", body)
            else base64.b64decode(body, validate=True)
        )
    except ValueError:
        raise ValueError("trusted public key is not hex, base64 or PEM") from None
    if len(raw) == 44 and raw.startswith(_SPKI_PREFIX):
        raw = raw[len(_SPKI_PREFIX) :]
    if len(raw) != 32:
        raise ValueError("trusted public key must be a 32-byte Ed25519 key")
    return raw


def verify_package(
    path: Path,
    trusted_key_ids: Sequence[str] = (),
    trusted_pubkeys: Sequence[bytes] = (),
) -> tuple[VerifyResult, list[str]]:
    """Return (result, notes). ``result.pinned`` is true only if the signer (and every key that
    signed a checkpoint) was pinned by the caller: ``trusted_pubkeys`` (full keys) or
    ``trusted_key_ids`` (64-bit labels, weaker). Unpinned verification proves internal
    consistency only: anyone can build a self-consistent package with their own key."""
    notes: list[str] = []
    pin_raw = {key_id_of(k): k for k in trusted_pubkeys}
    pin_ids = set(trusted_key_ids)
    pinned = bool(pin_raw or pin_ids)

    def is_pinned(kid: str, raw: bytes) -> bool:
        if kid in pin_raw:
            return pin_raw[kid] == raw
        return kid in pin_ids

    try:
        src = _Source(path)
        names = set(src.names())
        if MANIFEST not in names:
            return _bad(None, "manifest.json missing"), notes
        manifest = load_json(src.read(MANIFEST))
        if manifest.get("package_format_version") != PACKAGE_FORMAT_VERSION:
            return _bad(None, "unsupported package_format_version"), notes
        if manifest.get("chain_format_version") != CHAIN_FORMAT_VERSION:
            return _bad(None, "unsupported chain_format_version"), notes
        members = manifest["members"]
        if set(members) != set(PACKAGE_MEMBERS):
            return _bad(None, "manifest member list is not the expected set"), notes
        blobs: dict[str, bytes] = {}
        for name in PACKAGE_MEMBERS:
            if name not in names:
                return _bad(None, f"member {name} missing"), notes
            blobs[name] = src.read(name)
            if not hmac.compare_digest(_sha(blobs[name]), str(members[name])):
                return _bad(None, f"member {name} does not match the manifest hash"), notes
        proof = load_json(blobs["chain_proof.json"])
        keys = _normalise_keys(proof["public_keys"])
        kid = manifest["key_id"]
        if kid not in keys:
            return _bad(None, "manifest signer key is not among the package public keys"), notes
        if pinned and not is_pinned(kid, keys[kid]):
            return _bad(None, f"signer key_id {kid} is not the trusted key"), notes
        unsigned = {k: v for k, v in manifest.items() if k != "signature_b64"}
        try:
            sig = base64.b64decode(manifest["signature_b64"], validate=True)
        except (KeyError, ValueError):
            return _bad(None, "manifest signature missing or malformed"), notes
        if not ed25519_verify(keys[kid], canonical_json(unsigned), sig):
            return _bad(None, "manifest signature invalid"), notes
        notes.append(f"manifest signed by key_id {kid}")
        data = load_json(blobs["entries.json"])
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
        if pinned:
            for c in cps:
                ck = keys.get(c["key_id"])
                if ck is None or not is_pinned(c["key_id"], ck):
                    return _bad(
                        c["seq"], f"checkpoint {c['seq']} signed by a key that is not pinned"
                    ), notes
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
        return VerifyResult(True, None, "ok", res.redacted, pinned), notes
    except PackageError as exc:
        return _bad(None, str(exc)), notes
    except (KeyError, ValueError, TypeError, OSError, zipfile.BadZipFile, AttributeError) as exc:
        return _bad(None, f"package unreadable or malformed ({type(exc).__name__})"), notes
    except (RecursionError, MemoryError) as exc:
        return _bad(None, f"package too complex to verify ({type(exc).__name__})"), notes


UNPINNED_LINE = (
    "INTEGRITY OK - UNPINNED: AUTHENTICITY NOT ESTABLISHED; obtain the signer public key "
    "out-of-band and re-run with --trusted-pubkey"
)


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0: integrity OK and the signer is pinned (or --allow-unpinned, still labelled).
    Exit 2: integrity OK but UNPINNED (authenticity not established). Exit 1: failure."""
    ap = argparse.ArgumentParser(description="Offline evidence-package verifier")
    ap.add_argument("path", nargs="?", default=str(Path(__file__).resolve().parent))
    ap.add_argument("--trusted-key-id", action="append", default=[], help="16-hex key label")
    ap.add_argument("--trusted-pubkey", action="append", default=[],
                    help="full Ed25519 public key: hex, base64, PEM text or file path")  # fmt: skip
    ap.add_argument("--allow-unpinned", action="store_true",
                    help="exit 0 even if unpinned (label still printed)")  # fmt: skip
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)
    try:
        pubs = [parse_pubkey(t) for t in args.trusted_pubkey]
        res, notes = verify_package(Path(args.path), args.trusted_key_id, pubs)
    except ValueError as exc:
        res, notes = _bad(None, str(exc)), []
    except Exception as exc:  # never a traceback: this runs on hostile input
        res, notes = _bad(None, f"verification aborted ({type(exc).__name__})"), []
    code = 1 if not res.ok else (0 if res.pinned or args.allow_unpinned else 2)
    if args.json:
        print(json.dumps({"ok": res.ok, "pinned": res.pinned, "exit": code,
                          "first_bad_seq": res.first_bad_seq, "reason": res.reason,
                          "notes": notes}))  # fmt: skip
    else:
        for n in notes:
            print(f"  {n}")
        if not res.ok:
            at = f" (first bad seq {res.first_bad_seq})" if res.first_bad_seq is not None else ""
            print(f"FAIL: {res.reason}{at}")
        elif res.pinned:
            print("PASS: package is internally consistent and signed by the pinned key")
        else:
            print(UNPINNED_LINE)
    return code


if __name__ == "__main__":
    sys.exit(main())
