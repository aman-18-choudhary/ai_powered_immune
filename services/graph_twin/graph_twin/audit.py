"""Ledger audit entries for ring events (PII-free, deterministic, built with the svckit helpers).

Entries are written to the ``gt_audit_outbox`` table in the SAME transaction as the ring change
they describe and delivered by ``Drainer`` (fire-and-forget, capped, with a breaker) plus a
periodic sweeper. Nothing on the ingestion or API path awaits the ledger.
"""

import hashlib
import json
from decimal import Decimal
from typing import Any

from svckit.ledger import build_or_placeholder, safe_actor, utc_ts

from .graph import from_us
from .model import MODEL_VERSION, AuditRow, Ring

SERVICE = "graph-twin"
SYSTEM_ACTOR = "system:graph-twin"
BUCKETS = (
    (Decimal(10_000), "<10k"), (Decimal(100_000), "10k-100k"), (Decimal(1_000_000), "100k-1m"),
    (Decimal(10_000_000), "1m-10m"),
)  # fmt: skip


def inflow_bucket(inr: Decimal) -> str:
    for limit, label in BUCKETS:
        if inr < limit:
            return label
    return ">=10m"


def ring_ref(ring_id: str) -> str:
    return f"ring:{ring_id}"


def _entry(
    actor: str, event_type: str, payload: dict[str, Any], refs: list[str],
    placeholder: dict[str, Any], dedupe: str,
) -> AuditRow:  # fmt: skip
    entry, _ = build_or_placeholder(
        SERVICE, safe_actor(actor), event_type, payload, model_version=MODEL_VERSION,
        case_refs=refs, placeholder=placeholder, placeholder_refs=refs,
    )  # fmt: skip
    return AuditRow(
        dedupe=hashlib.sha256(dedupe.encode()).hexdigest()[:40] + ":" + event_type[:40],
        msg_key=refs[0] if refs else SERVICE,
        body=entry.model_dump_json(),
    )


def _base(ring: Ring, event: str, now_us: int) -> dict[str, Any]:
    roles = ring.roles_count()
    return {
        "ring_id": ring.ring_id, "event": event, "ring_version": ring.version,
        "members_by_role": roles, "victims": ring.victims,
        "signalled_victims": ring.victims_signalled,
        "inflow_bucket": inflow_bucket(Decimal(ring.inflow_paise) / 100),
        "score": round(ring.score, 2), "status": ring.status, "antibody": ring.antibody,
        "at": utc_ts(from_us(now_us)),
    }  # fmt: skip


def ring_event_row(kind: str, ring: Ring, info: dict[str, Any], now_us: int) -> AuditRow:
    refs = [ring_ref(ring.ring_id)]
    body = _base(ring, kind, now_us)
    ph = {"ring_id": ring.ring_id, "event": kind, "ring_version": ring.version}
    if kind == "detected":
        dedupe = f"detected:{ring.ring_id}"
    elif kind == "merged":
        body["absorbed_ring_id"] = info["absorbed"]
        refs.append(ring_ref(info["absorbed"]))
        ph["absorbed_ring_id"] = info["absorbed"]
        dedupe = f"merged:{ring.ring_id}:{info['absorbed']}"
    else:
        body["from_status"], body["to_status"] = info["from"], info["to"]
        dedupe = f"status:{ring.ring_id}:{info['from']}:{info['to']}:{ring.version}"
    return _entry(SYSTEM_ACTOR, f"ring.{kind}", body, refs, ph, dedupe)


def takedown_row(
    actor: str, ring: Ring, mode: str, removed_count: int, reduction_pct: float,
    harm_before: Decimal, now_us: int, remove_ids: list[str],
) -> AuditRow:  # fmt: skip
    body = _base(ring, "simulated", now_us)
    body.update(
        mode=mode,
        removed_count=removed_count,
        reduction_pct=round(reduction_pct, 1),
        harm_bucket=inflow_bucket(harm_before),
    )
    refs = [ring_ref(ring.ring_id)]
    ph = {"ring_id": ring.ring_id, "event": "simulated", "ring_version": ring.version}
    key = json.dumps([actor, ring.ring_id, ring.version, sorted(remove_ids), mode])
    return _entry(actor, "takedown.simulated", body, refs, ph, "sim:" + key)
