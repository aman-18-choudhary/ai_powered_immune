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

    Two short critical sections copy plain tuples out of the graph (members and their in-edges;
    then the shown payers and the edges among the shown nodes); sorting, truncation and building
    the response happen outside ``twin.lock``. The view is consistent per section, not across
    both; ``ring.version`` / the ETag identify the ring snapshot it describes."""
    g = twin.tg.g
    cfg = twin.cfg
    now_us = to_us(twin.clock())
    # ---- section 1: the members and their in-edges
    mem_state: dict[str, tuple[Any, ...]] = {}
    mem_in: dict[str, list[tuple[str, tuple[int, ...]]]] = {}
    chain: set[str] = set()
    truncated_scan = False
    with twin.lock:
        members = {n for n in ring.members if n in g}
        for n in members:
            mem_state[n] = _node_tuple(g, n, ring, now_us)
            pin = pay_in(g, n, cfg.fanin_scan_cap)
            truncated_scan = truncated_scan or len(pin) >= cfg.fanin_scan_cap
            mem_in[n] = [(p, _edge_tuple(e)) for p, e in pin]
            if any(p in members for p in twin.tg.idx.relay_into.get(n, ())):
                chain.add(n)
    ext: dict[str, int] = {}
    for n in members:
        for p, e in mem_in[n]:
            if p not in members:
                ext[p] = ext.get(p, 0) + e[1]
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
    shown = {n for n, _, _ in member_rows} | set(shown_victims)
    # ---- section 2: the shown payers and the edges among the shown nodes
    vic_state: dict[str, tuple[Any, ...]] = {}
    shown_in: dict[str, list[tuple[str, tuple[int, ...]]]] = {
        n: mem_in[n] for n, _, _ in member_rows
    }
    with twin.lock:
        for p in shown_victims:
            if p in g:
                vic_state[p] = _node_tuple(g, p, ring, now_us)
                shown_in[p] = [
                    (q, _edge_tuple(e)) for q, e in pay_in(g, p, cfg.fanin_scan_cap) if q in shown
                ]
    keys: dict[str, str] = {}
    nodes: list[dict[str, Any]] = []
    for n, role, t in member_rows:
        nodes.append(_node_json(n, mem_state[n], role, t))
        keys[n] = api_id(n)
    for p in shown_victims:
        if p in vic_state:
            nodes.append(_node_json(p, vic_state[p], VICTIM, 0))
            keys[p] = api_id(p)
    edges = []
    for n in keys:
        for p, e in shown_in.get(n, ()):
            if p in keys:
                edges.append(
                    {
                        "src": keys[p], "dst": keys[n], "count": e[0],
                        "total_inr": paise_str(e[1]), "first_ts": iso(e[2]),
                        "last_ts": iso(e[3]),
                        "rails": sorted(RAIL_NAME[b] for b in RAIL_NAME if e[4] & b),
                        "flagged": e[5],
                    }
                )  # fmt: skip
    edges.sort(key=lambda e: (e["src"], e["dst"]))
    return {
        "ring_id": ring.ring_id, "version": ring.version, "model_version": MODEL_VERSION,
        "truncated": truncated, "node_count": len(nodes), "nodes": nodes, "edges": edges,
    }  # fmt: skip


def _edge_tuple(e: Any) -> tuple[int, ...]:
    return (e.count, e.total, e.first, e.last, e.rails, e.susp)


def _node_tuple(g: Any, key: str, ring: Ring, now_us: int) -> tuple[Any, ...]:
    st = ns(g, key)
    return (
        st.bank, is_active_antibody(st, now_us), st.susp_in > 0, key in ring.core,
        st.first, st.last, st.in_tot, st.out_tot,
    )  # fmt: skip


def _node_json(key: str, t: tuple[Any, ...], role: str, tier: int) -> dict[str, Any]:
    bank, antibody, flagged, core, first, last, in_tot, out_tot = t
    flags = []
    if antibody:
        flags.append("antibody")
    if flagged:
        flags.append("flagged_inflow")
    if core:
        flags.append("core")
    return {
        "id": api_id(key), "role": role, "tier": tier, "bank": bank, "flags": flags,
        "first_seen": iso(first), "last_seen": iso(last),
        "in_inr": paise_str(in_tot), "out_inr": paise_str(out_tot),
    }  # fmt: skip
