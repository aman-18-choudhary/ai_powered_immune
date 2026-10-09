"""Signal-seeded fraud-ring detection over the twin graph.

Why seeded: structure alone (fan-in, fan-out, pass-through) also describes legitimate merchants,
payroll and settlement chains. A candidate account therefore needs a SIGNAL: it received at least
one ``step_up`` / ``hold_verify`` transaction, or it carries an active ``mule_account`` antibody.

Pipeline (``detect_rings``)
1. seeds = account nodes with a signal.
2. expansion (bounded by ``max_component``): from a member, follow its outflow when the member is a
   RELAY (pass-through: it forwarded >= ``passthrough_ratio`` of what it received, its first and
   last outflows within ``passthrough_max_s`` of its first and last inflows, and >= 2 distinct
   payers); include relay in-neighbours (other mules feeding the same sink) and relay accounts that
   share a device (a device used by more than ``device_max_accounts`` accounts is ignored).
3. components of the member subgraph (payment edges + shared devices); a component larger than
   ``louvain_min_nodes`` is split with seeded Louvain (``seed=42``, edge weight log1p(INR)).
4. roles: mule = relay; cash_out = non-relay fed by relay members; unknown = the rest. Victims are
   the external payers of the members (reported, never members).
5. gates: >= ``min_size`` accounts (mule + cash-out, plus antibody-flagged unknown) or an antibody
   with >= 2 accounts; >= ``min_signalled_payers`` distinct flagged payers or an antibody; score >=
   ``min_score``.
   A ring needs >= ``min_mules`` pass-through accounts unless an antibody confirms it, and a
   cash-out must receive >= ``sink_min_share`` of its inflow from the ring's relays (a popular
   merchant fed once by a relay is not a cash-out).
All iteration is over sorted keys, so the output is deterministic.
"""

import hashlib
from collections import deque
from dataclasses import replace
from decimal import Decimal

import networkx as nx
from svckit.ledger import opaque_hex16

from .config import Settings
from .graph import (
    ACCOUNT,
    DEVICE,
    RelayIndex,
    device_accounts,
    devices_of,
    is_active_antibody,
    ns,
    pay_in,
    pay_out,
)
from .model import CASH_OUT, MULE, UNKNOWN, Ring

LOUVAIN_SEED = 42
YOUNG_DAYS = 30


def make_ring_id(core: frozenset[str] | set[str]) -> str:
    h = hashlib.sha256("|".join(sorted(core)).encode()).hexdigest()
    return opaque_hex16(h)


def is_seed(g: nx.DiGraph, n: str, now_us: int) -> bool:
    st = ns(g, n)
    return st.kind == ACCOUNT and (st.susp_in > 0 or is_active_antibody(st, now_us))


def _tenth(part: int, whole: int) -> bool:
    return part * 10 >= whole


def _device_peers(g: nx.DiGraph, n: str, cfg: Settings) -> list[str]:
    out: list[str] = []
    for d in sorted(devices_of(g, n)):
        if g.in_degree(d) > cfg.device_max_accounts:
            continue
        out.extend(a for a in sorted(device_accounts(g, d)) if a != n)
    return out


def expand(
    g: nx.DiGraph, idx: RelayIndex, starts: list[str], cfg: Settings, now_us: int
) -> set[str]:  # fmt: skip
    members: set[str] = set()
    q: deque[str] = deque(sorted(starts))
    while q and len(members) < cfg.max_component:
        n = q.popleft()
        if n in members or n not in g:
            continue
        members.add(n)
        st = ns(g, n)
        if idx.is_relay(n):
            for m, e in sorted(pay_out(g, n), key=lambda x: x[0]):
                if m.startswith("a:") and m not in members and _tenth(e.total, st.out_tot):
                    q.append(m)
        for p in sorted(idx.relay_into.get(n, ())):
            if p not in members and _tenth(g.edges[p, n]["s"].total, ns(g, p).out_tot):
                q.append(p)
        for a in _device_peers(g, n, cfg):
            if a not in members and (idx.is_relay(a) or is_seed(g, a, now_us)):
                q.append(a)
    return members


def _component_graph(g: nx.DiGraph, members: set[str], cfg: Settings) -> nx.Graph:
    import math

    h: nx.Graph = nx.Graph()
    for n in sorted(members):
        h.add_node(n)
    dev_seen: dict[str, list[str]] = {}
    for n in sorted(members):
        for m, e in pay_out(g, n):
            if m in members:
                w = math.log1p(e.total / 100)
                if h.has_edge(n, m):
                    h[n][m]["w"] += w
                else:
                    h.add_edge(n, m, w=w)
        for d in devices_of(g, n):
            dev_seen.setdefault(d, []).append(n)
    for d in sorted(dev_seen):
        accs = dev_seen[d]
        if len(device_accounts(g, d)) > cfg.device_max_accounts:
            continue
        for i in range(1, len(accs)):  # a path, not a clique: linear in the device's accounts
            a, b = accs[i - 1], accs[i]
            if h.has_edge(a, b):
                h[a][b]["w"] += 1.0
            else:
                h.add_edge(a, b, w=1.0)
    return h


def classify(
    g: nx.DiGraph, idx: RelayIndex, members: set[str], cfg: Settings, now_us: int
) -> dict[str, str]:
    roles: dict[str, str] = {}
    for n in sorted(members):
        if idx.is_relay(n):
            roles[n] = MULE
            continue
        fed = sum(g.edges[p, n]["s"].total for p in idx.relay_into.get(n, ()) if p in members)
        sink = fed > 0 and fed >= cfg.sink_min_share * ns(g, n).in_tot
        roles[n] = CASH_OUT if sink else UNKNOWN
    return roles


def ring_stats(
    g: nx.DiGraph, ring: Ring, cfg: Settings, now_us: int
) -> None:  # fmt: skip
    """Recompute the statistics, score and confidence of ``ring`` from the graph."""
    members = ring.members
    ext: dict[str, int] = {}
    truncated = False
    banks: set[str] = set()
    first = last = 0
    inflow = 0
    digest = hashlib.sha256()
    for n in sorted(members):
        if n not in g:
            continue
        st = ns(g, n)
        if st.bank:
            banks.add(st.bank)
        first = st.first if not first else min(first, st.first)
        last = max(last, st.last)
        pin = pay_in(g, n, cfg.fanin_scan_cap)
        truncated = truncated or len(pin) >= cfg.fanin_scan_cap
        for p, e in sorted(pin, key=lambda x: x[0]):
            digest.update(f"{p}>{n}:{e.count}:{e.total}:{e.susp};".encode())
            if p not in members:
                ext[p] = ext.get(p, 0) + e.total
                inflow += e.total
                pst = ns(g, p)
                if pst.bank:
                    banks.add(pst.bank)
    signalled: set[str] = set()
    for n in members:
        if n in g:
            signalled |= ns(g, n).sig
    to_cash = 0
    for n in members:
        if n in g and members[n] == CASH_OUT:
            for p, e in pay_in(g, n, cfg.fanin_scan_cap):
                if p in members:
                    to_cash += e.total
    dev_use: dict[str, int] = {}
    for n in members:
        if n in g:
            for d in devices_of(g, n):
                dev_use[d] = dev_use.get(d, 0) + 1
    ring.edge_sig = digest.hexdigest()[:16]
    ring.inflow_paise = inflow
    ring.victims = len(ext)
    ring.victims_signalled = len(signalled & set(ext))
    ring.victims_truncated = truncated
    ring.first_seen_us = first
    ring.last_seen_us = last
    ring.banks = tuple(sorted(banks))[:20]
    ring.antibody = any(n in g and is_active_antibody(ns(g, n), now_us) for n in members)
    ring.shared_device = any(c >= 2 for c in dev_use.values())
    ring.passthrough_share = min(1.0, to_cash / inflow) if inflow > 0 else 0.0
    core_nodes = [n for n in members if members[n] in (MULE, CASH_OUT)]
    young = [n for n in core_nodes if n in g and (ns(g, n).age or 10**6) < YOUNG_DAYS]
    f_young = len(young) / len(core_nodes) if core_nodes else 0.0
    ring.score = round(
        0.25 * min(1.0, ring.victims / 10)
        + 0.20 * min(1.0, ring.victims_signalled / 3)
        + 0.20 * ring.passthrough_share
        + 0.15 * (1.0 if ring.antibody else 0.0)
        + 0.10 * f_young
        + 0.05 * (1.0 if ring.shared_device else 0.0)
        + 0.05 * min(1.0, len(core_nodes) / 5),
        4,
    )
    evidence = [
        ring.victims_signalled >= cfg.min_signalled_payers,
        ring.antibody,
        any(r == CASH_OUT for r in members.values()),
        ring.passthrough_share >= 0.5,
        ring.shared_device,
    ]
    ring.confidence = round(sum(evidence) / len(evidence), 4)


def _gate(ring: Ring, cfg: Settings, g: nx.DiGraph, now_us: int) -> bool:
    core_n = sum(1 for r in ring.members.values() if r in (MULE, CASH_OUT))
    ab_unknown = sum(
        1 for n, r in ring.members.items()
        if r == UNKNOWN and n in g and is_active_antibody(ns(g, n), now_us)
    )  # fmt: skip
    size = core_n + ab_unknown
    accounts = len(ring.members)
    mules = sum(1 for r in ring.members.values() if r == MULE)
    big_enough = (size >= cfg.min_size and mules >= cfg.min_mules) or (
        ring.antibody and accounts >= 2
    )
    if not big_enough or not ring.core:
        return False
    if not ring.antibody and ring.victims_signalled < cfg.min_signalled_payers:
        return False
    return ring.victims >= 1 and ring.score >= cfg.min_score


def seeds_of(g: nx.DiGraph, now_us: int) -> list[str]:
    return sorted(n for n in g.nodes if is_seed(g, n, now_us))


def candidates_from(
    g: nx.DiGraph, idx: RelayIndex, starts: list[str], cfg: Settings, now_us: int
) -> list[Ring]:  # fmt: skip
    """Expand from ``starts`` and return the rings that pass the gates (fresh ids)."""
    members = expand(g, idx, starts, cfg, now_us)
    if not members:
        return []
    h = _component_graph(g, members, cfg)
    out: list[Ring] = []
    comps = sorted((sorted(c) for c in nx.connected_components(h)), key=lambda c: c[0])
    for comp in comps:
        parts: list[list[str]]
        if len(comp) > cfg.louvain_min_nodes:
            sub = h.subgraph(comp)
            comms = nx.community.louvain_communities(sub, weight="w", seed=LOUVAIN_SEED)
            parts = sorted((sorted(c) for c in comms), key=lambda c: c[0])
        else:
            parts = [comp]
        for part in parts:
            pset = set(part)
            roles = classify(g, idx, pset, cfg, now_us)
            core = frozenset(
                n for n, r in roles.items()
                if r in (MULE, CASH_OUT) or (r == UNKNOWN and is_active_antibody(ns(g, n), now_us))
            )  # fmt: skip
            if not core:
                continue
            ring = Ring(ring_id=make_ring_id(core), members=roles, core=core)
            ring_stats(g, ring, cfg, now_us)
            if _gate(ring, cfg, g, now_us):
                out.append(ring)
    return sorted(out, key=lambda r: r.ring_id)


def detect_rings(
    g: nx.DiGraph,
    min_size: int = 3,
    *,
    cfg: Settings | None = None,
    now_us: int | None = None,
    starts: list[str] | None = None,
    idx: RelayIndex | None = None,
) -> list[Ring]:
    """Full (non-incremental) detection over ``g``. ``min_size`` counts accounts excluding
    victims. ``now_us`` defaults to the latest activity in the graph (stream time)."""
    base = cfg or Settings()
    cfg = replace(base, min_size=min_size)
    if now_us is None:
        now_us = max((ns(g, n).last for n in g.nodes), default=0)
    idx = idx or RelayIndex.build(g, cfg, now_us)
    st = starts if starts is not None else seeds_of(g, now_us)
    return candidates_from(g, idx, st, cfg, now_us)


def region_starts(
    g: nx.DiGraph, idx: RelayIndex, dirty: set[str], cfg: Settings, now_us: int
) -> tuple[list[str], set[str]]:  # fmt: skip
    """Seeds near the touched nodes, found through the relay index: a touched node matters if it
    is a seed, a relay, or fed by relays. Everything else (a merchant's customers, a citizen's
    shops) costs O(1), so ingestion of unrelated traffic does not trigger expansions."""
    seeds: set[str] = set()
    seen: set[str] = set()
    for n in sorted(dirty):
        if n not in g or ns(g, n).kind == DEVICE:
            continue
        if is_seed(g, n, now_us):
            seeds.add(n)
        sinks = set(idx.relay_of.get(n, ()))
        if n in idx.relay_into:
            sinks.add(n)
        if idx.is_relay(n):
            seen.add(n)
        for s in sorted(sinks):
            seen.add(s)
            if is_seed(g, s, now_us):
                seeds.add(s)
            for r in sorted(idx.relay_into.get(s, ())):
                seen.add(r)
                if is_seed(g, r, now_us):
                    seeds.add(r)
        if idx.is_relay(n) or n in idx.relay_into:
            for a in _device_peers(g, n, cfg):
                seen.add(a)
                if is_seed(g, a, now_us):
                    seeds.add(a)
        if len(seen) >= cfg.max_component:
            break
    return sorted(seeds), seen


def inr(paise: int) -> Decimal:
    return Decimal(paise) / 100
