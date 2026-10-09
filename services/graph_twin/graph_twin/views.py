"""Response shapes for the API: deterministic, opaque 12-hex node ids, decimal-string amounts."""

import hashlib
from typing import Any

from .graph import (
    RAIL_NAME,
    api_id,
    from_us,
    is_active_antibody,
    ns,
    paise_str,
    pay_in,
    to_us,
)
from .model import CASH_OUT, MODEL_VERSION, MULE, UNKNOWN, VICTIM, Ring
from .twin import GraphTwin


def iso(us: int) -> str:
    return from_us(us).strftime("%Y-%m-%dT%H:%M:%S.%fZ") if us else ""


def ring_summary(r: Ring) -> dict[str, Any]:
    return {
        "ring_id": r.ring_id,
        "status": r.status,
        "version": r.version,
        "model_version": MODEL_VERSION,
        "score": round(r.score, 4),
        "confidence": round(r.confidence, 4),
        "first_detected": iso(r.first_detected_us),
        "first_seen": iso(r.first_seen_us),
        "last_seen": iso(r.last_seen_us),
        "members_by_role": r.roles_count(),
        "victims": r.victims,
        "signalled_victims": r.victims_signalled,
        "victims_truncated": r.victims_truncated,
        "inflow_inr": paise_str(r.inflow_paise),
        "banks": list(r.banks),
        "antibody": r.antibody,
        "shared_device": r.shared_device,
        "passthrough_share": round(r.passthrough_share, 4),
        "merged_from": list(r.merged_from),
    }


def list_etag(rings: list[Ring], status: str | None, limit: int) -> str:
    h = hashlib.sha256(f"{status}|{limit}|".encode())
    for r in rings:
        h.update(f"{r.ring_id}:{r.version};".encode())
    return f'W/"rings-{h.hexdigest()[:20]}"'


def ring_etag(r: Ring, suffix: str = "") -> str:
    return f'W/"{r.ring_id}-{r.version}{suffix}"'


def graph_view(twin: GraphTwin, ring: Ring, max_nodes: int) -> dict[str, Any]:
    """Nodes (ring members + their payers, truncated) and the aggregated edges among them.
    Called with the twin lock held by the caller."""
    g = twin.tg.g
    idx = twin.tg.idx
    cfg = twin.cfg
    now_us = to_us(twin.clock())
    members = {n for n in ring.members if n in g}
    ext: dict[str, int] = {}
    truncated_scan = False
    for n in members:
        pin = pay_in(g, n, cfg.fanin_scan_cap)
        truncated_scan = truncated_scan or len(pin) >= cfg.fanin_scan_cap
        for p, e in pin:
            if p not in members:
                ext[p] = ext.get(p, 0) + e.total
    chain = {n for n in members if any(p in members for p in idx.relay_into.get(n, ()))}
    nodes: list[dict[str, Any]] = []

    def node(key: str, role: str, tier: int) -> dict[str, Any]:
        st = ns(g, key)
        flags = []
        if is_active_antibody(st, now_us):
            flags.append("antibody")
        if st.susp_in > 0:
            flags.append("flagged_inflow")
        if key in ring.core:
            flags.append("core")
        return {
            "id": api_id(key), "role": role, "tier": tier, "bank": st.bank,
            "flags": flags, "first_seen": iso(st.first), "last_seen": iso(st.last),
            "in_inr": paise_str(st.in_tot), "out_inr": paise_str(st.out_tot),
        }  # fmt: skip

    tier = {CASH_OUT: 3, MULE: 1, UNKNOWN: 1}
    member_rows = []
    for n in sorted(members, key=lambda k: (tier[ring.members[k]], k)):
        role = ring.members[n]
        t = tier[role] + (1 if role == MULE and n in chain else 0)
        member_rows.append((n, role, t))
    victim_keys = [p for p, _ in sorted(ext.items(), key=lambda kv: (-kv[1], kv[0]))]
    room = max(0, max_nodes - len(member_rows))
    shown_victims = victim_keys[:room]
    truncated = len(victim_keys) > room or len(member_rows) > max_nodes or truncated_scan
    member_rows = member_rows[:max_nodes]
    keys: dict[str, str] = {}
    for n, role, t in member_rows:
        nodes.append(node(n, role, t))
        keys[n] = api_id(n)
    for p in shown_victims:
        nodes.append(node(p, VICTIM, 0))
        keys[p] = api_id(p)
    edges = []
    for n in keys:
        for p, e in pay_in(g, n, cfg.fanin_scan_cap):
            if p in keys:
                edges.append(
                    {
                        "src": keys[p], "dst": keys[n], "count": e.count,
                        "total_inr": paise_str(e.total), "first_ts": iso(e.first),
                        "last_ts": iso(e.last),
                        "rails": sorted(RAIL_NAME[b] for b in RAIL_NAME if e.rails & b),
                        "flagged": e.susp,
                    }
                )  # fmt: skip
    edges.sort(key=lambda e: (e["src"], e["dst"]))
    return {
        "ring_id": ring.ring_id, "version": ring.version, "model_version": MODEL_VERSION,
        "truncated": truncated, "node_count": len(nodes), "nodes": nodes, "edges": edges,
    }  # fmt: skip
