"""Deterministic, signed evidence packages.

A package is a ZIP (stored, fixed member order, zeroed timestamps, no randomness) so the same
inputs always give identical bytes:

* ``entries.json``      the case and the contiguous chain SEGMENT [min selected seq .. covering
                        checkpoint seq] (every entry in full, so the hash chain can be recomputed);
                        ``selected_seqs`` says which of them belong to the case.
* ``chain_proof.json``  segment bounds, the anchor (``prev_hash`` of the first entry), the signed
                        checkpoint(s) covering the end of the segment, and all public keys.
* ``explanations.md``   human-readable timeline rendered ONLY from stored fields and payloads.
* ``certificate_section63_template.md``  a template for counsel (not legal advice).
* ``verify.py``         the standalone stdlib-only verifier (byte copy of ``verify.py`` here).
* ``manifest.json``     sha256 of every other member + the entry list, Ed25519-signed.

Design note: the end of the segment is a signed checkpoint, so the checkpoint signature
authenticates the whole segment (any change to any entry changes every later hash). The segment's
start is bound by the anchor only through that chain.
"""

import base64
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scam_contracts.canonical import canonical_json

from . import verify as verify_module
from .chain import to_dict
from .keys import KeyRing
from .store import LedgerStore
from .verify import CHAIN_FORMAT_VERSION, MANIFEST, PACKAGE_FORMAT_VERSION, PACKAGE_MEMBERS

AUDIT_EVENT = "package.exported"
DEFAULT_MAX_SPAN = 5000

REASON_MEANINGS: dict[str, str] = {
    "DIGITAL_ARREST_PHRASE": "caller says the victim is under 'digital arrest'",
    "SAFE_ACCOUNT_TRANSFER": "caller demands moving funds to a 'safe'/RBI/verification account",
    "ISOLATION_DEMAND": "caller demands the victim stay on the call, stay silent or stay in view",
    "PAYMENT_DEMAND": "caller demands money in connection with an arrest or case",
    "AUTHORITY_IMPERSONATION": "caller claims to be a law-enforcement or regulatory body",
    "LEGAL_THREAT": "caller alleges a criminal case, warrant, seized parcel or similar",
    "ACCOUNT_VERIFICATION_ASK": "caller asks to 'verify' the victim's account, balance or ID",
    "CREDENTIAL_ASK": "caller asks for an OTP, PIN or password",
    "URGENCY": "caller applies time pressure",
    "NEW_PAYEE": "the payee is new to this payer",
    "YOUNG_PAYEE_ACCOUNT": "the payee account is only a few days old",
    "YOUNG_PAYEE_LARGE_AMOUNT_FLOOR": "large amount to a very young payee account (policy floor)",
    "NEW_PAYEE_EXTREME_AMOUNT": "extreme amount to a payee new to this payer (policy floor)",
    "ACTIVE_SCAM_CALL": "a scam-call risk signal exists for this payer in the recent window",
    "AMOUNT_ANOMALY": "the amount is far above this payer's typical transfer",
    "VELOCITY_SPIKE": "unusually many or large transfers in a short window",
    "NIGHT_TRANSFER": "the transfer happened at night (IST)",
    "NEW_DEVICE": "the device has not been seen for this payer",
    "PAYEE_AMOUNT_ESCALATION": "amounts to the same payee are escalating",
    "ANTIBODY_MATCH": "the payee matches a confirmed shared threat marker (antibody)",
    "ANTIBODY_MATCH_KNOWN_PAYEE": "the payee matches an antibody although it is a known payee",
    "SCRIPT_CLASSIFIER_MATCH": "the call text matches a known scam-script pattern",
    "NO_RISK_INDICATORS": "no risk indicator fired",
    "RAIL_TYPICAL_AMOUNT_DAMPER": "the amount is typical for the rail, which lowered the score",
    "LATE_CALL_RISK_POST_SETTLEMENT": "call risk arrived after the transfer had settled",
    "LATE_ANTIBODY_POST_SETTLEMENT": "an antibody arrived after the transfer had settled",
}


class PackageTooLarge(Exception):
    """The case spans more than the permitted number of chain entries; narrow the case."""

    def __init__(self, span: int, limit: int) -> None:
        super().__init__(f"case spans {span} entries, limit is {limit}; narrow the case")
        self.span, self.limit = span, limit


class EmptyCase(Exception):
    """No ledger entry matches the case."""


class NotCovered(Exception):
    """No signed checkpoint can cover the case yet."""


@dataclass(frozen=True)
class Package:
    data: bytes
    sha256: str
    from_seq: int
    to_seq: int
    segment_to_seq: int


def _j(obj: Any) -> bytes:
    """Pretty JSON for humans, still deterministic."""
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _scalar(v: Any) -> str:
    return v if isinstance(v, str) else canonical_json(v).decode()


def render_explanations(
    case: dict[str, Any], segment: list[dict[str, Any]], selected: set[int]
) -> str:
    """Only stored data is rendered. Nothing is inferred, summarised or invented."""
    lines = [
        f"# Evidence timeline for case {case['case_id']}",
        "",
        f"Case title: {case.get('title') or '(none)'}",
        "",
        "This document is rendered mechanically from the ledger entries in `entries.json`. "
        "Times are the ledger's RECEIPT times (UTC); chain order is receipt order, not the order "
        "in which events occurred. Entries without a retained payload show only their hash.",
        "",
    ]
    for e in segment:
        if e["seq"] not in selected:
            continue
        lines += [
            f"## Entry {e['seq']}: {e['event_type']}",
            "",
            f"- Receipt time (UTC): {e['ts']}",
            f"- Producing service: {e['service']}",
            f"- Actor (role/token): {e['actor']}",
            f"- Model version: {e['model_version'] or 'none recorded'}",
            f"- Payload hash: {e['payload_hash']}",
            f"- Entry hash: {e['entry_hash']}",
        ]
        p = e["payload"]
        if e["event_type"].startswith("ledger.entry_quarantined."):
            reason = e["event_type"].rsplit(".", 1)[-1]
            lines.append(
                f"- Status: entry withheld by the ledger ({reason}); only its original payload "
                "hash is kept."
            )
        elif p is None:
            lines.append("- No payload retained (hash only).")
        else:
            rest = dict(p)
            if "decision" in rest:
                lines.append(f"- Decision: {_scalar(rest.pop('decision'))}")
            reasons = rest.pop("reasons", rest.pop("reason_codes", None))
            if reasons is not None:
                lines.append("- Reason codes:")
                for r in reasons if isinstance(reasons, list) else [reasons]:
                    code = r.get("code") if isinstance(r, dict) else r
                    meaning = REASON_MEANINGS.get(str(code), "no description in the dictionary")
                    lines.append(f"  - {_scalar(code)}: {meaning}")
            for k in sorted(rest):
                lines.append(f"- {k}: {_scalar(rest[k])}")
        lines.append("")
    context = [e for e in segment if e["seq"] not in selected]
    if context:
        lines += [
            "## Context entries",
            "",
            "These entries are included only so the hash chain can be verified end to end; "
            "they are not part of the case and are not described here. See `entries.json`.",
            "",
            "| seq | receipt time (UTC) | event type |",
            "|---|---|---|",
        ]
        lines += [f"| {e['seq']} | {e['ts']} | {e['event_type']} |" for e in context]
        lines.append("")
    return "\n".join(lines)


def render_certificate(case_id: str, hashes: dict[str, str]) -> str:
    rows = "\n".join(f"| {name} | `{h}` |" for name, h in sorted(hashes.items()))
    return f"""# Electronic-record certificate TEMPLATE

(Bharatiya Sakshya Adhiniyam, 2023, Section 63)

> DISCLAIMER. This is a TEMPLATE for counsel to complete and conform to the form prescribed in
> the Schedule to the Act. It is not legal advice and it is not a statement that any record is
> admissible. Whether and how the Section 63 requirements are met depends on the facts, on the
> operational controls of the producing organisation, and on counsel's assessment.

Case: `{case_id}`

## Part A: to be completed by the producing party (person in charge of the system)

1. Producing party (organisation, address): ______________________________
2. Name, designation and signature of the person making this certificate: ______________________
3. The electronic record is the evidence package identified below, produced by the evidence-ledger
   service from entries recorded by the platform's services (txn-guard, antibody-hub, ...).
4. Description of the system and process (to be completed and verified by the producing party):
   - How entries are recorded: ______________________________
   - Retention, access control and database role separation: ______________________________
   - How signing keys are held and rotated: ______________________________
   - Clock source and its reliability (the ledger records RECEIPT time): ____________________
5. Statement on regular operation of the system during the relevant period: ____________________
6. Date and place: ______________________

## Hash values of the package members (SHA-256)

| member | sha256 |
|---|---|
{rows}

The sha256 of `manifest.json` and of the whole `.zip` are not printed here (they would be circular);
record them when this certificate is completed: manifest.json: ______  package .zip: ______

## Part B: to be completed by an expert, if counsel so decides

1. Name and qualification: ______________________________
2. Verification performed (for example `python verify.py <package> --trusted-key-id <key id>`),
   date, verifier copy used and how it was obtained: ______________________________
3. Signing key id relied on and how it was confirmed independently of this package: ____________
4. Result (PASS/FAIL) and observations: ______________________________
5. Signature and date: ______________________________
"""


def _zip(members: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as z:
        for name, data in members:
            zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_STORED
            zi.create_system = 3
            zi.external_attr = 0o644 << 16
            z.writestr(zi, data)
    return buf.getvalue()


def verifier_source() -> bytes:
    return Path(verify_module.__file__).read_bytes()


def build(
    store: LedgerStore,
    keyring: KeyRing,
    case_id: str,
    *,
    title: str = "",
    seqs: list[int] | tuple[int, ...] = (),
    case_refs: list[str] | tuple[str, ...] = (),
    max_span: int = DEFAULT_MAX_SPAN,
) -> Package:
    try:
        selected = store.select_seqs(
            list(case_refs), list(seqs), limit=max_span, exclude_event=AUDIT_EVENT
        )
    except LookupError:
        raise EmptyCase(case_id) from None
    if not selected:
        raise EmptyCase(case_id)
    if len(selected) > max_span:
        raise PackageTooLarge(len(selected), max_span)
    lo, hi = selected[0], selected[-1]
    cp = store.checkpoint_covering(hi)
    if cp is None:
        raise NotCovered(case_id)
    span = cp["seq"] - lo + 1
    if span > max_span:
        raise PackageTooLarge(span, max_span)
    sel = set(selected)
    entries = []
    redacted_seqs: list[int] = []
    for e in store.segment(lo, cp["seq"]):
        d = to_dict(e)
        if e.seq not in sel and d["payload_present"]:
            d["payload"] = None  # withheld: the chain still binds payload_hash (chain format 2)
            redacted_seqs.append(e.seq)
        entries.append(d)
    if len(entries) != span:
        raise NotCovered(case_id)  # the ledger changed shape underneath us; refuse
    cps = [c for c in store.checkpoints(hi, max_span) if c["seq"] <= cp["seq"]]
    case = {"case_id": case_id, "title": title, "case_refs": sorted(set(case_refs))}
    entries_doc = {
        "package_format_version": PACKAGE_FORMAT_VERSION,
        "chain_format_version": CHAIN_FORMAT_VERSION, "case_id": case_id, "case": case,
        "selected_seqs": selected, "entries": entries,
    }  # fmt: skip
    proof = {
        "package_format_version": PACKAGE_FORMAT_VERSION,
        "chain_format_version": CHAIN_FORMAT_VERSION,
        "segment": {"from_seq": lo, "to_seq": cp["seq"], "count": span},
        "anchor_prev_hash": entries[0]["prev_hash"],
        "checkpoints": cps,
        "public_keys": keyring.public_keys(),
    }  # fmt: skip
    blobs: dict[str, bytes] = {
        "entries.json": _j(entries_doc),
        "chain_proof.json": _j(proof),
        "explanations.md": render_explanations(case, entries, sel).encode(),
    }
    blobs["verify.py"] = verifier_source()
    pre = {n: _sha(b) for n, b in blobs.items()}
    blobs["certificate_section63_template.md"] = render_certificate(case_id, pre).encode()
    members = {n: _sha(blobs[n]) for n in PACKAGE_MEMBERS}
    manifest: dict[str, Any] = {
        "package_format_version": PACKAGE_FORMAT_VERSION,
        "chain_format_version": CHAIN_FORMAT_VERSION,
        "case_id": case_id,
        "generated_from_head_seq": store.last_non_audit_seq(AUDIT_EVENT),
        "entries": [
            {"seq": e["seq"], "entry_hash": e["entry_hash"]} for e in entries if e["seq"] in sel
        ],
        "redacted_seqs": redacted_seqs,
        "members": members,
        "key_id": keyring.signer.key_id,
    }  # fmt: skip
    sig = keyring.signer.sign(canonical_json(manifest))
    manifest["signature_b64"] = base64.b64encode(sig).decode()
    ordered = [(n, blobs[n]) for n in PACKAGE_MEMBERS] + [(MANIFEST, _j(manifest))]
    data = _zip(ordered)
    return Package(data, _sha(data), lo, selected[-1], cp["seq"])


def build_package(store: LedgerStore, keyring: KeyRing, case_id: str, **kw: Any) -> bytes:
    """Convenience wrapper returning only the zip bytes (see ``build``)."""
    return build(store, keyring, case_id, **kw).data
