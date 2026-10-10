"""Takedown simulation: harm as a max-flow, greedy and exhaustive best-k removal, timeline.

HARM MODEL (a model, not a prediction). Harm(ring) = the INR that would flow from victims into the
ring's terminal accounts (cash-outs, plus members that have not forwarded anywhere yet) over
the next ``H`` days
(``GRAPH_HARM_HORIZON_DAYS``, default 7), if the observed behaviour simply continued:

* every edge (u, v) of the ring (victim -> member, member -> member) gets the capacity
  ``total_paise(u, v) * H / W`` in integer paise, where ``total`` is the aggregated amount seen on
  the edge and ``W = max(1 day, last_seen - first_seen)`` is the ring's observed window, i.e. the
  average rate over the window, projected forward;
* a super-source feeds every external payer, every terminal feeds a super-sink;
* harm = the max flow (``networkx`` Dinic, integer capacities: exact), so a mule that received
  less than it could sweep, or a sweep smaller than the inflow, caps what reaches the cash-out.
Removing nodes deletes them and their edges; ``reduction_pct = 100 * (before - after) / before``.
Assumptions that matter: stationarity (the observed rate continues), the 1-day floor on W (a burst
is treated as at least a day of activity), no re-routing (victims and mules do not find new
accounts: this overstates the effect of a takedown), and aggregated edges (no per-hour shape).
Per-ring size cap ``GRAPH_MAX_RING_NODES``; victims beyond ``GRAPH_FLOW_VICTIM_CAP`` are folded into
one non-removable "other victims" source per member.
"""

import itertools
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import networkx as nx
from networkx.algorithms.flow import dinitz

from .config import DAY_US, Settings
from .graph import pay_in, to_us
from .model import CASH_OUT, Ring, TakedownResult
from .store import GraphStore

S, T = "\x00source", "\x00sink"
BIG = 10**18
EXHAUSTIVE_MAX_MEMBERS = 12
EXHAUSTIVE_MAX_K = 3


class Cancelled(Exception):
    """The deadline passed or the caller gave up: the search stops at the next check."""


class CancelToken:
    """Cooperative cancellation: ``check()`` raises ``Cancelled`` once ``cancel()`` was called or
    ``deadline_s`` seconds have passed since creation. A thread cannot be killed, so the flow loops
    poll it between max-flow computations (one computation is the longest uninterruptible step)."""

    def __init__(self, deadline_s: float | None = None) -> None:
        self._stop = threading.Event()
        self._deadline = None if deadline_s is None else time.monotonic() + deadline_s

    def cancel(self) -> None:
        self._stop.set()

    @property
    def cancelled(self) -> bool:
        return self._stop.is_set() or (
            self._deadline is not None and time.monotonic() >= self._deadline
        )

    def check(self) -> None:
        if self.cancelled:
            raise Cancelled


def _check(token: CancelToken | None) -> None:
    if token is not None:
        token.check()


class TooLarge(ValueError):
    """The ring's flow network exceeds ``GRAPH_MAX_RING_NODES``."""


@dataclass(frozen=True)
class FlowInput:
    edges: tuple[tuple[str, str, int], ...]  # (src, dst, total paise), external srcs included
    roles: tuple[tuple[str, str], ...]  # member -> role
    window_us: int
    folded: tuple[tuple[str, int], ...] = ()  # member -> paise from folded (other) victims

    @property
    def members(self) -> list[str]:
        return [m for m, _ in self.roles]


def window_of(ring: Ring) -> int:
    return max(DAY_US, ring.last_seen_us - ring.first_seen_us)


def extract_flow_input(g: nx.DiGraph, ring: Ring, cfg: Settings) -> FlowInput:
    # only core accounts are takedown candidates; non-core ("unknown") accounts that paid into
    # the ring are modelled as external payers, as in the ring statistics
    members = {n: r for n, r in ring.members.items() if n in ring.core}
    ext: dict[tuple[str, str], int] = {}
    internal: list[tuple[str, str, int]] = []
    for n in sorted(members):
        if n not in g:
            continue
        for p, e in pay_in(g, n, cfg.fanin_scan_cap):
            if p in members:
                internal.append((p, n, e.total))
            else:
                ext[(p, n)] = e.total
    by_victim: dict[str, int] = {}
    for (p, _), t in ext.items():
        by_victim[p] = by_victim.get(p, 0) + t
    keep = {
        p
        for p, _ in sorted(by_victim.items(), key=lambda kv: (-kv[1], kv[0]))[: cfg.flow_victim_cap]
    }
    edges = list(internal)
    folded: dict[str, int] = {}
    for (p, n), t in sorted(ext.items()):
        if p in keep:
            edges.append((p, n, t))
        else:
            folded[n] = folded.get(n, 0) + t
    if len(members) + len(keep) + 2 > cfg.max_ring_nodes:
        raise TooLarge("ring flow network too large")
    return FlowInput(
        tuple(sorted(edges)), tuple(sorted(members.items())), window_of(ring),
        tuple(sorted(folded.items())),
    )  # fmt: skip


def _terminals(fi: FlowInput) -> set[str]:
    """Cash-outs, plus any member that has not (yet) forwarded to another member: money parked in
    a dead-end collector, or in a mule before its first observed sweep, counts as harm."""
    roles = dict(fi.roles)
    has_onward = {s for s, d, _ in fi.edges if s in roles and d in roles}
    return {m for m, r in roles.items() if r == CASH_OUT or m not in has_onward}


def _net(fi: FlowInput, remove: frozenset[str], horizon_us: int) -> nx.DiGraph:
    net: nx.DiGraph = nx.DiGraph()
    members = set(fi.members)

    def cap(total: int) -> int:
        return total * horizon_us // fi.window_us

    # external payers are not removable and the source edge to them is unbounded, so a payer's
    # edges can be replaced by source -> member edges of the same capacities (same max flow, a
    # network with no payer nodes)
    ingest: dict[str, int] = {}
    for s, d, total in fi.edges:
        if s in remove or d in remove:
            continue
        c = cap(total)
        if c <= 0:
            continue
        if s in members:
            net.add_edge(s, d, capacity=c)
        else:
            ingest[d] = ingest.get(d, 0) + c
    for m, total in fi.folded:
        if m in remove:
            continue
        c = cap(total)
        if c > 0:
            ingest[m] = ingest.get(m, 0) + c
    for m, c in ingest.items():
        net.add_edge(S, m, capacity=c)
    for t in _terminals(fi):
        if t in net and t not in remove:
            net.add_edge(t, T, capacity=BIG)
    return net


def harm_paise(
    fi: FlowInput, remove: frozenset[str], horizon_us: int, token: CancelToken | None = None,
    base: nx.DiGraph | None = None,
) -> int:  # fmt: skip
    """Max flow with ``remove`` deleted. ``base`` (the full network from ``_net``) lets a search
    evaluate many removal sets on a view instead of rebuilding the network each time."""
    _check(token)
    members = set(fi.members)
    if base is None or any(n not in members for n in remove):  # a payer was named: rebuild
        net = _net(fi, remove, horizon_us)
    else:
        net = nx.restricted_view(base, [n for n in remove if n in base], [])
    if S not in net or T not in net:
        return 0
    return int(nx.maximum_flow_value(net, S, T, flow_func=dinitz))


def _result(
    fi: FlowInput, remove: list[str], horizon_us: int, before: int | None = None,
    observed: Decimal | None = None, token: CancelToken | None = None,
) -> TakedownResult:  # fmt: skip
    rm = frozenset(remove)
    base = _net(fi, frozenset(), horizon_us)
    b = harm_paise(fi, frozenset(), horizon_us, token, base) if before is None else before
    a = harm_paise(fi, rm, horizon_us, token, base)
    present = {x for e in fi.edges for x in e[:2]} | set(fi.members)
    cut = sorted({(s, d) for s, d, _ in fi.edges if (s in rm or d in rm)})
    pct = round(100.0 * (b - a) / b, 2) if b > 0 else 0.0
    return TakedownResult(
        harm_before_inr=Decimal(b) / 100, harm_after_inr=Decimal(a) / 100, reduction_pct=pct,
        removed=tuple(sorted(x for x in rm if x in present)), edges_cut=tuple(cut),
        observed_after_ts_inr=observed,
    )  # fmt: skip


def horizon_us(cfg: Settings) -> int:
    return int(cfg.harm_horizon_days * DAY_US)


def simulate_takedown(
    g: nx.DiGraph, ring: Ring, remove: list[str], cfg: Settings | None = None
) -> TakedownResult:  # fmt: skip
    cfg = cfg or Settings()
    return simulate_flow(extract_flow_input(g, ring, cfg), remove, cfg)


def simulate_flow(
    fi: FlowInput, remove: list[str], cfg: Settings, token: CancelToken | None = None
) -> TakedownResult:  # fmt: skip
    return _result(fi, remove, horizon_us(cfg), token=token)


def candidates(fi: FlowInput) -> list[str]:
    """Removable accounts: the ring's members (victims are not takedown targets)."""
    return sorted(fi.members)


def restricted_pool(fi: FlowInput, base: nx.DiGraph, hz: int, size: int) -> list[str]:
    """Greedy candidates on a big ring: members that carry flow in one maximum flow, terminals
    (cash-outs and dead-end collectors) and articulation points of the flow network first, then by
    throughput, at most ``size``. A member that carries no flow in a maximum flow cannot lower the
    first greedy step, so dropping it loses nothing there; later steps are heuristic."""
    members = set(fi.members)
    if S not in base or T not in base:
        return sorted(members)[:size]
    _, flow = nx.maximum_flow(base, S, T, flow_func=dinitz)
    through: dict[str, int] = {m: 0 for m in members}
    for outs in flow.values():
        for v, f in outs.items():
            if v in through and f > 0:
                through[v] += f
    terminals = _terminals(fi)
    artic = set(nx.articulation_points(base.to_undirected())) & members
    pri = {m: 0 if (m in terminals or m in artic) else 1 for m in members}
    live = [m for m in members if through[m] > 0]
    live.sort(key=lambda m: (pri[m], -through[m], m))
    return sorted(live[:size])


def best_flow(
    fi: FlowInput, k: int, cfg: Settings, token: CancelToken | None = None
) -> list[str]:  # fmt: skip
    """Greedy: repeatedly remove the member whose removal lowers the harm most; ties go to the
    smallest key. Stops early once nothing improves the harm any further. Rings of at most
    ``GRAPH_TAKEDOWN_POOL`` members search every member; bigger rings search the restricted pool
    (``restricted_pool``). Each step checks ``token``."""
    hz = horizon_us(cfg)
    base = _net(fi, frozenset(), hz)
    chosen: list[str] = []
    cur = harm_paise(fi, frozenset(), hz, token, base)
    pool = candidates(fi)
    if len(pool) > cfg.takedown_pool:
        pool = restricted_pool(fi, base, hz, cfg.takedown_pool)
    for _ in range(max(0, k)):
        best: tuple[int, str] | None = None
        for c in pool:
            if c in chosen:
                continue
            h = harm_paise(fi, frozenset([*chosen, c]), hz, token, base)
            if best is None or (h, c) < best:
                best = (h, c)
        if best is None or (best[0] >= cur and chosen):
            break
        chosen.append(best[1])
        cur = best[0]
    return chosen


def best_takedown(
    g: nx.DiGraph, ring: Ring, k: int, cfg: Settings | None = None, token: CancelToken | None = None
) -> list[str]:  # fmt: skip
    cfg = cfg or Settings()
    return best_flow(extract_flow_input(g, ring, cfg), k, cfg, token)


def exhaustive_flow(
    fi: FlowInput, k: int, cfg: Settings, token: CancelToken | None = None
) -> list[str]:  # fmt: skip
    """Optimal k-subset by enumeration (small rings only: <= 12 members, k <= 3)."""
    pool = candidates(fi)
    if len(pool) > EXHAUSTIVE_MAX_MEMBERS or k > EXHAUSTIVE_MAX_K:
        raise ValueError("exhaustive search is limited to <= 12 members and k <= 3")
    hz = horizon_us(cfg)
    best: tuple[int, tuple[str, ...]] | None = None
    base = _net(fi, frozenset(), hz)
    for combo in itertools.combinations(pool, min(k, len(pool))):
        h = harm_paise(fi, frozenset(combo), hz, token, base)
        if best is None or (h, combo) < best:
            best = (h, combo)
    return list(best[1]) if best else []


def best_takedown_exhaustive(
    g: nx.DiGraph, ring: Ring, k: int, cfg: Settings | None = None
) -> list[str]:  # fmt: skip
    cfg = cfg or Settings()
    return exhaustive_flow(extract_flow_input(g, ring, cfg), k, cfg)


def harm_of(g: nx.DiGraph, ring: Ring, remove: list[str], cfg: Settings | None = None) -> Decimal:
    return simulate_takedown(g, ring, remove, cfg).harm_after_inr


def takedown_timeline(
    store: GraphStore, ring: Ring, removal_ts: datetime, remove: list[str],
    cfg: Settings | None = None, limit: int = 200_000,
) -> TakedownResult:  # fmt: skip
    """'Freeze these accounts at T': harm before/after using ONLY transactions seen before
    ``removal_ts`` (the projection a responder had at that moment), plus the hindsight total of
    INR that actually reached the terminals from T on (``observed_after_ts_inr``)."""
    cfg = cfg or Settings()
    members = sorted(ring.members)
    rows = store.edge_txns(members, removal_ts, limit)
    roles = tuple(sorted(ring.members.items()))
    if not rows:
        fi = FlowInput((), roles, DAY_US)
        return _result(fi, remove, horizon_us(cfg), observed=Decimal(0))
    agg: dict[tuple[str, str], int] = {}
    for r in rows:
        k = (r["src"], r["dst"])
        agg[k] = agg.get(k, 0) + r["amount"]
    first = min(to_us(r["ts"]) for r in rows)
    window = max(DAY_US, to_us(removal_ts) - first)
    fi = FlowInput(tuple(sorted((s, d, t) for (s, d), t in agg.items())), roles, window)
    terminals = sorted(_terminals(fi))
    after = store.terminal_txns_after(terminals, removal_ts, limit) if terminals else []
    observed = Decimal(sum(r["amount"] for r in after if r["src"] in ring.members)) / 100
    return _result(fi, remove, horizon_us(cfg), observed=observed)


def node_flow_ids(fi: FlowInput) -> set[str]:
    """Every node that can be named in a takedown request: members and modelled victims."""
    return set(fi.members) | {s for s, _, _ in fi.edges}


__all__ = [
    "CancelToken", "Cancelled", "FlowInput", "TakedownResult", "TooLarge", "best_takedown",
    "best_takedown_exhaustive", "extract_flow_input", "harm_of", "simulate_takedown",
    "takedown_timeline",
]  # fmt: skip
