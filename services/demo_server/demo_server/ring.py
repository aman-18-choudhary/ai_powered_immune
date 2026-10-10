"""Deterministic ring graph and takedown simulation computed from a run's own transactions.

Follows the graph-twin model (``services/graph_twin``) so it can be swapped for the service:

* nodes are accounts, keyed by the keyed account hash (``payer_account_ref`` when the paying bank
  supplies it, else the payee hash); a payer without an account reference becomes a token node.
  Node ids in responses are opaque 12-hex prefixes (SHA-256 of the key for token nodes);
* edges aggregate payments per (src, dst): count, total in integer paise, first/last time, rails,
  number of flagged payments;
* a *relay* (mule) forwards >= 70% of what it received; a *cash-out* is a non-relay receiving
  >= 30% of its inflow from relays; victims are the external payers into members. Tiers: 0 victim,
  1 mule fed by victims, 2 mule fed by relays, 3 cash-out;
* seeds are payees of flagged (step_up / hold_verify) payments and antibody keys; a ring grows
  from a seed by following relay outflows and adding relays that feed the same sink;
* harm = max flow (Dinic, exact integers) of the projected victim inflow to the ring's terminals
  over ``horizon_days``: edge capacity = total_paise * horizon_s / max(1 day, observed span).

This is a model, not a prediction: the observed rate is assumed to continue and nobody re-routes
after a freeze (which overstates a takedown). Money is ``Decimal`` / integer paise and leaves this
module only as decimal strings.
"""

from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import networkx as nx
from scam_contracts.models import Transaction

RELAY_RATIO = 0.7
SINK_SHARE = 0.3
MIN_DAY_S = 86_400
DEFAULT_HORIZON_DAYS = 7
EXHAUSTIVE_MAX_NODES = 12
EXHAUSTIVE_MAX_K = 3
MAX_NODES = 500
_BUCKETS = ((10_000, "<10k"), (100_000, "10k-100k"), (1_000_000, "100k-1m"), (10_000_000, "1m-10m"))


def _paise(amount: Decimal) -> int:
    return int((amount * 100).to_integral_value())


def inr(paise: int) -> str:
    """Decimal string with two places (no float anywhere)."""
    sign = "-" if paise < 0 else ""
    p = abs(paise)
    return f"{sign}{p // 100}.{p % 100:02d}"


def inflow_bucket(paise: int) -> str:
    rupees = paise // 100
    for limit, label in _BUCKETS:
        if rupees < limit:
            return label
    return ">=10m"


def _utc(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _node_id(key: str) -> str:
    if key.startswith("a:") and len(key) >= 14:
        return key[2:14]
    return hashlib.sha256(key.encode()).hexdigest()[:12]


@dataclass
class _Edge:
    count: int = 0
    paise: int = 0
    first: datetime | None = None
    last: datetime | None = None
    rails: set[str] = field(default_factory=set)
    flagged: int = 0


@dataclass
class _Node:
    in_paise: int = 0
    out_paise: int = 0
    first_in: datetime | None = None
    last_in: datetime | None = None
    first_out: datetime | None = None
    last_out: datetime | None = None
    bank: str | None = None
    flagged_in: int = 0
    antibody: bool = False


@dataclass(frozen=True)
class TakedownResult:
    harm_before_paise: int
    harm_after_paise: int
    removed: tuple[str, ...]
    cut_edges: tuple[tuple[str, str], ...]

    @property
    def reduction_pct(self) -> float:
        if self.harm_before_paise <= 0:
            return 0.0
        return round(
            100.0 * (self.harm_before_paise - self.harm_after_paise) / self.harm_before_paise, 1
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "harm_before": inr(self.harm_before_paise),
            "harm_after": inr(self.harm_after_paise),
            "reduction_pct": self.reduction_pct,
            "removed": list(self.removed),
            "cut_edges": len(self.cut_edges),
            "cut_edge_list": [{"src": a, "dst": b} for a, b in self.cut_edges],
        }


class RingGraph:
    """Incremental account graph plus ring detection and takedown simulation."""

    def __init__(self, horizon_days: int = DEFAULT_HORIZON_DAYS) -> None:
        if not 1 <= horizon_days <= 90:
            raise ValueError("horizon_days must be between 1 and 90")
        self.horizon_days = horizon_days
        self._nodes: dict[str, _Node] = {}
        self._edges: dict[tuple[str, str], _Edge] = {}
        self._seen: set[str] = set()
        self._seed_order: dict[str, int] = {}  # first time an account became a seed
        self.version = 0

    def frozen(self) -> RingGraph:
        """An independent copy for computing off the event loop: call it ON the loop (where all
        mutation happens), hand the copy to a worker thread. Cost is linear in the graph size."""
        g = RingGraph(self.horizon_days)
        g._nodes = {k: replace(n) for k, n in self._nodes.items()}
        g._edges = {k: replace(e, rails=set(e.rails)) for k, e in self._edges.items()}
        g._seen = set(self._seen)
        g._seed_order = dict(self._seed_order)
        g.version = self.version
        return g

    # ------------------------------------------------------------------------------ ingest
    @staticmethod
    def payer_key(txn: Transaction) -> str:
        return f"a:{txn.payer_account_ref}" if txn.payer_account_ref else f"t:{txn.payer_token}"

    def add_txn(self, txn: Transaction, flagged: bool = False) -> bool:
        """Fold one payment in (idempotent per txn_id). True when the graph changed."""
        if txn.txn_id in self._seen:
            if flagged:
                self._flag(txn)
            return False
        self._seen.add(txn.txn_id)
        src, dst = self.payer_key(txn), f"a:{txn.payee_hash}"
        p = _paise(txn.amount_inr)
        s, d = self._nodes.setdefault(src, _Node()), self._nodes.setdefault(dst, _Node())
        s.bank = s.bank or txn.bank_id
        s.out_paise += p
        s.first_out = min(s.first_out or txn.ts, txn.ts)
        s.last_out = max(s.last_out or txn.ts, txn.ts)
        d.in_paise += p
        d.first_in = min(d.first_in or txn.ts, txn.ts)
        d.last_in = max(d.last_in or txn.ts, txn.ts)
        e = self._edges.setdefault((src, dst), _Edge())
        e.count += 1
        e.paise += p
        e.first = min(e.first or txn.ts, txn.ts)
        e.last = max(e.last or txn.ts, txn.ts)
        e.rails.add(txn.rail)
        if flagged:
            e.flagged += 1
            d.flagged_in += 1
            self._seed(dst)
        self.version += 1
        return True

    def _flag(self, txn: Transaction) -> None:
        dst = self._nodes.get(f"a:{txn.payee_hash}")
        e = self._edges.get((self.payer_key(txn), f"a:{txn.payee_hash}"))
        if dst is not None and e is not None:
            e.flagged += 1
            dst.flagged_in += 1
            self._seed(f"a:{txn.payee_hash}")
            self.version += 1

    def mark_antibody(self, key_hash: str) -> None:
        n = self._nodes.setdefault(f"a:{key_hash}", _Node())
        if not n.antibody:
            n.antibody = True
            self._seed(f"a:{key_hash}")
            self.version += 1

    def _seed(self, key: str) -> None:
        self._seed_order.setdefault(key, len(self._seed_order))

    # --------------------------------------------------------------------------- detection
    def _relay(self, key: str) -> bool:
        n = self._nodes[key]
        if n.in_paise <= 0 or n.out_paise < RELAY_RATIO * n.in_paise:
            return False
        return len({s for (s, d) in self._edges if d == key}) >= 1

    def _in_edges(self, key: str) -> list[tuple[str, _Edge]]:
        return [(s, e) for (s, d), e in self._edges.items() if d == key]

    def _out_edges(self, key: str) -> list[tuple[str, _Edge]]:
        return [(d, e) for (s, d), e in self._edges.items() if s == key]

    def members(self) -> dict[str, set[str]]:
        """Ring members per seed-connected group: ``{ring_key: set(node keys)}``; the ring key is
        the smallest member key of the seed set at detection (stable id comes from ``ring_id``)."""
        seeds = sorted(
            k
            for k, n in self._nodes.items()
            if (n.flagged_in > 0 or n.antibody) and k.startswith("a:")
        )
        relay = {k for k in self._nodes if self._relay(k)}
        groups: list[set[str]] = []
        for s in seeds:
            grp = {s}
            frontier = [s]
            while frontier:
                cur = frontier.pop()
                if cur in relay or cur == s:
                    for dst, _ in self._out_edges(cur):
                        if dst not in grp and dst.startswith("a:"):
                            grp.add(dst)
                            frontier.append(dst)
                # relays that feed a member sink are siblings of the same ring
                for src, _ in self._in_edges(cur):
                    if src in relay and src not in grp and self._feeds_sink(src):
                        grp.add(src)
                        frontier.append(src)
            groups.append(grp)
        merged: list[set[str]] = []
        for g in groups:  # rings sharing any member merge
            hit = [m for m in merged if m & g]
            for m in hit:
                merged.remove(m)
                g = g | m
            merged.append(g)
        return {min(g): g for g in sorted(merged, key=lambda g: min(g))}

    def _feeds_sink(self, relay_key: str) -> bool:
        for dst, _ in self._out_edges(relay_key):
            if dst not in self._nodes:
                continue
            ins = self._in_edges(dst)
            tot = sum(e.paise for _, e in ins)
            from_relays = sum(e.paise for s, e in ins if self._relay_cached(s))
            if tot and from_relays >= SINK_SHARE * tot and not self._relay_cached(dst):
                return True
        return False

    def _relay_cached(self, key: str) -> bool:
        return key in self._nodes and self._relay(key)

    # ---------------------------------------------------------------------------- views
    def ring_ids(self) -> list[str]:
        return [self.ring_id(m) for m in self.members().values()]

    def ring_id(self, members: set[str]) -> str:
        """Stable id: derived from the member that was seeded first (merges keep the oldest)."""
        seeded = [k for k in members if k in self._seed_order]
        oldest = min(seeded, key=lambda k: (self._seed_order[k], k)) if seeded else min(members)
        return hashlib.sha256(("ring:" + oldest).encode()).hexdigest()[:16]

    def ring_for(self, ring_id: str | None = None) -> set[str] | None:
        rings = list(self.members().values())
        if not rings:
            return None
        if ring_id in (None, "current"):  # the ring holding the oldest seed
            return min(rings, key=lambda m: self._oldest_order(m))
        for m in rings:
            if self.ring_id(m) == ring_id:
                return m
        return None

    def _oldest_order(self, members: set[str]) -> tuple[int, str]:
        seeded = [(self._seed_order[k], k) for k in members if k in self._seed_order]
        return min(seeded) if seeded else (10**9, min(members))

    def _roles(self, members: set[str]) -> dict[str, tuple[str, int]]:
        relay = {k for k in members if self._relay(k)}
        out: dict[str, tuple[str, int]] = {}
        for k in members:
            ins = self._in_edges(k)
            tot = sum(e.paise for _, e in ins)
            from_relays = sum(e.paise for s, e in ins if s in relay)
            if k not in relay and tot and from_relays >= SINK_SHARE * tot:
                out[k] = ("cash_out", 3)
            else:
                fed_by_relay = any(s in relay for s, _ in ins)
                out[k] = ("mule", 2 if fed_by_relay else 1)
        return out

    def _victims(self, members: set[str]) -> set[str]:
        return {s for m in members for s, _ in self._in_edges(m) if s not in members}

    def snapshot(self, ring_id: str | None = None) -> dict[str, Any] | None:
        members = self.ring_for(ring_id)
        if members is None:
            return None
        roles = self._roles(members)
        victims = self._victims(members)
        keys = sorted(members | victims, key=_node_id)
        truncated = len(keys) > MAX_NODES
        keys = keys[:MAX_NODES]
        shown = set(keys)
        nodes: list[dict[str, Any]] = []
        for k in keys:
            n = self._nodes[k]
            role, tier = roles.get(k, ("victim", 0))
            flags = []
            if n.flagged_in:
                flags.append("held_payee")
            if n.antibody:
                flags.append("antibody")
            nodes.append(
                {
                    "id": _node_id(k), "role": role, "tier": tier, "bank": n.bank, "flags": flags,
                    "first_seen": _utc(min(t for t in (n.first_in, n.first_out) if t)),
                    "last_seen": _utc(max(t for t in (n.last_in, n.last_out) if t)),
                    "in_inr": inr(n.in_paise), "out_inr": inr(n.out_paise),
                }
            )  # fmt: skip
        edges = []
        for (s, d), e in sorted(
            self._edges.items(), key=lambda kv: (_node_id(kv[0][0]), _node_id(kv[0][1]))
        ):
            if (s in members or d in members) and s in shown and d in shown:
                assert e.first and e.last
                edges.append(
                    {
                        "src": _node_id(s), "dst": _node_id(d), "count": e.count,
                        "total_inr": inr(e.paise), "first_ts": _utc(e.first),
                        "last_ts": _utc(e.last), "rails": sorted(e.rails), "flagged": e.flagged,
                    }
                )  # fmt: skip
        inflow = sum(
            self._edges[(s, m)].paise for m in members for s in victims if (s, m) in self._edges
        )
        harm = self._harm(members, victims, frozenset())
        return {
            "ring_id": self.ring_id(members),
            "computed_from": "this run's transactions (simulated)",
            "horizon_days": self.horizon_days,
            "members": sorted(_node_id(k) for k in members),
            "victims": len(victims),
            "inflow_inr": inr(inflow),
            "inflow_bucket": inflow_bucket(inflow),
            "harm_inr": inr(harm),
            "nodes": nodes,
            "edges": edges,
            "truncated": truncated,
            "version": self.version,
        }

    # ------------------------------------------------------------------------- takedown
    def _span_s(self, members: set[str], victims: set[str]) -> int:
        ts = [
            t
            for (s, d), e in self._edges.items()
            if d in members or s in members
            for t in (e.first, e.last)
            if t is not None
        ]
        span = int((max(ts) - min(ts)).total_seconds()) if ts else 0
        return max(MIN_DAY_S, span)

    def _harm(self, members: set[str], victims: set[str], removed: frozenset[str]) -> int:
        """Projected victim -> terminal flow in paise with ``removed`` members deleted."""
        live = members - removed
        span = self._span_s(members, victims)
        horizon = self.horizon_days * MIN_DAY_S
        g = nx.DiGraph()
        src, sink = "__source__", "__sink__"
        g.add_node(src)
        g.add_node(sink)
        nodes = live | victims
        for (s, d), e in self._edges.items():
            if s in nodes and d in live and (s in live or s in victims):
                cap = e.paise * horizon // span
                if cap > 0:
                    g.add_edge(s, d, capacity=cap)
        for v in victims:
            if v in g:
                out_cap = sum(dd["capacity"] for _, _, dd in g.out_edges(v, data=True))
                if out_cap:
                    g.add_edge(src, v, capacity=out_cap)
        for m in live:
            if m not in g:
                continue
            has_out = any(u == m and w in members for (u, w) in self._edges)
            if not has_out:  # terminal: cash-out, or a mule that has not forwarded yet
                g.add_edge(m, sink)
        if not g[src]:
            return 0
        value, _ = nx.maximum_flow(g, src, sink, flow_func=nx.algorithms.flow.dinitz)
        return int(value)

    def _resolve(
        self, ring_id: str | None, remove: list[str]
    ) -> tuple[set[str], set[str], frozenset[str]]:
        members = self.ring_for(ring_id)
        if members is None:
            raise LookupError("no ring")
        by_id = {_node_id(k): k for k in members}
        if len(remove) != len(set(remove)):
            raise ValueError("duplicate node ids")
        unknown = [r for r in remove if r not in by_id]
        if unknown:
            raise ValueError("unknown or non-removable node id")
        return members, self._victims(members), frozenset(by_id[r] for r in remove)

    def simulate_takedown(self, remove: list[str], ring_id: str | None = None) -> TakedownResult:
        members, victims, rem = self._resolve(ring_id, remove)
        before = self._harm(members, victims, frozenset())
        after = self._harm(members, victims, rem)
        cut = sorted(
            (_node_id(s), _node_id(d))
            for (s, d) in self._edges
            if (s in rem or d in rem) and (s in members or d in members)
        )
        return TakedownResult(before, after, tuple(sorted(remove)), tuple(cut))

    def best_takedown(self, k: int, ring_id: str | None = None) -> TakedownResult:
        """Greedy by marginal harm reduction; ties go to the smallest node id; stops early when
        nothing improves."""
        if k < 1:
            raise ValueError("k must be >= 1")
        members, victims, _ = self._resolve(ring_id, [])
        by_id = {_node_id(m): m for m in members}
        chosen: list[str] = []
        cur = self._harm(members, victims, frozenset())
        for _ in range(min(k, len(by_id))):
            best: tuple[int, str] | None = None
            for nid in sorted(by_id):
                if nid in chosen:
                    continue
                h = self._harm(members, victims, frozenset(by_id[c] for c in [*chosen, nid]))
                if best is None or h < best[0]:
                    best = (h, nid)
            if best is None or best[0] >= cur:
                break
            cur = best[0]
            chosen.append(best[1])
        return self.simulate_takedown(chosen, ring_id)

    def best_takedown_exhaustive(self, k: int, ring_id: str | None = None) -> TakedownResult:
        """Optimal k-subset by enumeration; refused above 12 members or k > 3."""
        members, victims, _ = self._resolve(ring_id, [])
        if len(members) > EXHAUSTIVE_MAX_NODES or not 1 <= k <= EXHAUSTIVE_MAX_K:
            raise ValueError("exhaustive search is limited to <= 12 members and k <= 3")
        by_id = {_node_id(m): m for m in members}
        ids = sorted(by_id)
        best: tuple[int, tuple[str, ...]] | None = None
        for r in range(1, min(k, len(ids)) + 1):
            for combo in itertools.combinations(ids, r):
                h = self._harm(members, victims, frozenset(by_id[c] for c in combo))
                if best is None or (h, len(combo), combo) < (best[0], len(best[1]), best[1]):
                    best = (h, combo)
        return self.simulate_takedown(list(best[1]) if best else [], ring_id)
