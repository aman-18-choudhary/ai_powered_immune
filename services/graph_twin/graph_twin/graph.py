"""The in-memory twin: one ``networkx.DiGraph`` of accounts, tokens and devices.

Nodes (key = namespace + identifier; identifiers are already keyed hashes / tokens):
  ``a:<hash>``  payment account (``payee_hash`` of the account, or ``payer_account_ref`` of the
                same account when it pays: the same keyed hash, see the README "account identity")
  ``t:<token>`` a payer whose account is unknown (producer did not send ``payer_account_ref``)
  ``d:<token>`` a device token
Edges: ``pay`` (src pays dst; aggregated: count, total paise, first/last ts, rails, flagged count)
and ``uses`` (account -> device it paid from: the shared-device link).

State lives in ``__slots__`` objects stored under one attribute key (``"s"``), timestamps are
integer microseconds since the epoch and money is integer paise (no floats anywhere in totals).
Not thread safe: ``GraphTwin`` serialises access.
"""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import networkx as nx

from .config import Settings

ACCOUNT, TOKEN, DEVICE = "account", "token", "device"
RAIL_BIT = {"UPI": 1, "IMPS": 2, "NEFT": 4}
RAIL_NAME = {v: k for k, v in RAIL_BIT.items()}
SIG_CAP = 64  # distinct flagged payers remembered per node (the count above it is lost)


def to_us(dt: datetime) -> int:
    d = dt.astimezone(UTC)
    return (
        (d - datetime(1970, 1, 1, tzinfo=UTC)).days * 86_400 * 1_000_000
        + (d.hour * 3600 + d.minute * 60 + d.second) * 1_000_000
        + d.microsecond
    )


def from_us(us: int) -> datetime:
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=us)


def to_paise(amount: Decimal) -> int:
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def paise_str(p: int) -> str:
    """Decimal string with two places, exact."""
    sign = "-" if p < 0 else ""
    q, r = divmod(abs(p), 100)
    return f"{sign}{q}.{r:02d}"


def account_key(h: str) -> str:
    return "a:" + h


def api_id(key: str) -> str:
    """Opaque 12-hex node id for responses: the first 12 chars of an account hash (the same
    prefix family as ledger ``payee_ref``), a hash of the key for tokens and devices."""
    if key.startswith("a:"):
        return key[2:14]
    return hashlib.sha256(key.encode()).hexdigest()[:12]


class NodeState:
    __slots__ = (
        "kind", "bank", "first", "last", "in_tot", "out_tot", "in_cnt", "out_cnt",
        "in_first", "in_last", "out_first", "out_last", "susp_in", "sig", "ab_until", "age",
    )  # fmt: skip

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.bank: str | None = None
        self.first = 0
        self.last = 0
        self.in_tot = 0
        self.out_tot = 0
        self.in_cnt = 0
        self.out_cnt = 0
        self.in_first = 0
        self.in_last = 0
        self.out_first = 0
        self.out_last = 0
        self.susp_in = 0
        self.sig: set[str] = set()  # payers with a flagged payment to this node (capped)
        self.ab_until = 0  # active-antibody expiry (us); 0 = none
        self.age: int | None = None  # latest payee_account_age_days seen

    def clone(self) -> "NodeState":
        c = NodeState(self.kind)
        for k in self.__slots__:
            setattr(c, k, getattr(self, k))
        c.sig = set(self.sig)
        return c


class EdgeState:
    __slots__ = ("kind", "count", "total", "first", "last", "rails", "susp")

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.count = 0
        self.total = 0
        self.first = 0
        self.last = 0
        self.rails = 0
        self.susp = 0

    def clone(self) -> "EdgeState":
        c = EdgeState(self.kind)
        for k in self.__slots__:
            setattr(c, k, getattr(self, k))
        return c


@dataclass(frozen=True)
class PaymentRec:
    src: str
    dst: str
    device: str | None
    amount: int  # paise
    ts: int  # us
    rail: str
    bank: str
    age: int
    level: int  # 0 none, 1 step_up, 2 hold_verify


def ns(g: nx.DiGraph, key: str) -> NodeState:
    return g.nodes[key]["s"]


def es(g: nx.DiGraph, a: str, b: str) -> EdgeState:
    return g.edges[a, b]["s"]


def is_active_antibody(st: NodeState, now_us: int) -> bool:
    return st.ab_until > now_us


def pay_out(g: nx.DiGraph, n: str) -> list[tuple[str, EdgeState]]:
    return [(m, d["s"]) for m, d in g.succ[n].items() if d["s"].kind == "pay"]


def pay_in(g: nx.DiGraph, n: str, cap: int | None = None) -> list[tuple[str, EdgeState]]:
    """In-edges (always ``pay``: ``uses`` edges end at devices). ``cap`` bounds the scan."""
    out: list[tuple[str, EdgeState]] = []
    for m, d in g.pred[n].items():
        out.append((m, d["s"]))
        if cap is not None and len(out) >= cap:
            break
    return out


def devices_of(g: nx.DiGraph, n: str) -> list[str]:
    return [m for m, d in g.succ[n].items() if d["s"].kind == "uses"]


def device_accounts(g: nx.DiGraph, d: str) -> list[str]:
    return list(g.pred[d])


def relay_status(g: nx.DiGraph, n: str, cfg: Settings, now_us: int = 0) -> bool:
    """Pass-through account: it received at least ``relay_min_inr``, forwarded >=
    ``passthrough_ratio`` of it, its first and last outflows lie within ``passthrough_max_s`` of
    its first and last inflows, and it has >= ``relay_min_payers`` distinct payers (an active
    antibody waives the payer count)."""
    st = ns(g, n)
    if st.kind != ACCOUNT or st.in_tot < cfg.relay_min_inr * 100:
        return False
    if st.out_tot < cfg.passthrough_ratio * st.in_tot:
        return False
    t = cfg.passthrough_max_us
    if abs(st.out_first - st.in_first) > t or abs(st.out_last - st.in_last) > t:
        return False
    return g.in_degree(n) >= cfg.relay_min_payers or (now_us > 0 and is_active_antibody(st, now_us))


class RelayIndex:
    """Incremental index of pass-through accounts: ``relay_of[r]`` = the accounts r forwards to,
    ``relay_into[s]`` = the relays that forward into s. Updated for the touched nodes of every
    event (O(out-degree)), so detection never scans a fan-in."""

    def __init__(self) -> None:
        self.relay_of: dict[str, frozenset[str]] = {}
        self.relay_into: dict[str, set[str]] = {}

    def _drop(self, r: str) -> None:
        for s in self.relay_of.pop(r, frozenset()):
            peers = self.relay_into.get(s)
            if peers is not None:
                peers.discard(r)
                if not peers:
                    del self.relay_into[s]

    def refresh(self, g: nx.DiGraph, n: str, cfg: Settings, now_us: int) -> None:
        if n not in g:
            self._drop(n)
            return
        if not relay_status(g, n, cfg, now_us):
            self._drop(n)
            return
        sinks = frozenset(m for m, _ in pay_out(g, n))
        old = self.relay_of.get(n, frozenset())
        if sinks == old:
            return
        self._drop(n)
        self.relay_of[n] = sinks
        for s in sinks:
            self.relay_into.setdefault(s, set()).add(n)

    def refresh_many(self, g: nx.DiGraph, nodes: Iterable[str], cfg: Settings, now_us: int) -> None:
        for n in nodes:
            self.refresh(g, n, cfg, now_us)

    @classmethod
    def build(cls, g: nx.DiGraph, cfg: Settings, now_us: int) -> "RelayIndex":
        idx = cls()
        idx.refresh_many(g, [n for n in g.nodes if ns(g, n).kind == ACCOUNT], cfg, now_us)
        return idx

    def is_relay(self, n: str) -> bool:
        return n in self.relay_of


class TwinGraph:
    def __init__(self) -> None:
        self.g: nx.DiGraph = nx.DiGraph()
        self.idx = RelayIndex()

    # -------------------------------------------------------------------- nodes
    def ensure_node(self, key: str, kind: str, ts: int) -> NodeState:
        if key not in self.g:
            st = NodeState(kind)
            st.first = st.last = ts
            self.g.add_node(key, s=st)
            return st
        st = ns(self.g, key)
        if ts:
            st.first = min(st.first, ts) if st.first else ts
            st.last = max(st.last, ts)
        return st

    @property
    def node_count(self) -> int:
        return self.g.number_of_nodes()

    # ----------------------------------------------------------------- payments
    def add_payment(self, p: PaymentRec) -> None:
        sk = ACCOUNT if p.src.startswith("a:") else TOKEN
        s = self.ensure_node(p.src, sk, p.ts)
        d = self.ensure_node(p.dst, ACCOUNT, p.ts)
        s.bank = p.bank
        d.age = p.age
        s.out_tot += p.amount
        s.out_cnt += 1
        s.out_first = p.ts if not s.out_first else min(s.out_first, p.ts)
        s.out_last = max(s.out_last, p.ts)
        d.in_tot += p.amount
        d.in_cnt += 1
        d.in_first = p.ts if not d.in_first else min(d.in_first, p.ts)
        d.in_last = max(d.in_last, p.ts)
        e = self._edge(p.src, p.dst, "pay")
        e.count += 1
        e.total += p.amount
        e.first = p.ts if not e.first else min(e.first, p.ts)
        e.last = max(e.last, p.ts)
        e.rails |= RAIL_BIT.get(p.rail, 0)
        if p.level >= 1:
            self._flag(p.src, p.dst, +1)
        if p.device:
            dv = self.ensure_node(p.device, DEVICE, p.ts)
            dv.bank = p.bank
            u = self._edge(p.src, p.device, "uses")
            u.count += 1
            u.first = p.ts if not u.first else min(u.first, p.ts)
            u.last = max(u.last, p.ts)

    def _edge(self, a: str, b: str, kind: str) -> EdgeState:
        if self.g.has_edge(a, b):
            return es(self.g, a, b)
        e = EdgeState(kind)
        self.g.add_edge(a, b, s=e)
        return e

    def _flag(self, src: str, dst: str, delta: int) -> None:
        e = es(self.g, src, dst)
        d = ns(self.g, dst)
        before = e.susp
        e.susp = max(0, e.susp + delta)
        d.susp_in = max(0, d.susp_in + delta)
        if before == 0 and e.susp > 0 and len(d.sig) < SIG_CAP:
            d.sig.add(src)
        elif e.susp == 0:
            d.sig.discard(src)

    def change_level(self, src: str, dst: str, old: int, new: int) -> None:
        """A decision upgrade/downgrade of an applied payment (flagged = level >= 1)."""
        if not self.g.has_edge(src, dst):
            return
        if old < 1 <= new:
            self._flag(src, dst, +1)
        elif new < 1 <= old:
            self._flag(src, dst, -1)

    def set_antibody(self, key: str, until_us: int, ts: int) -> None:
        st = self.ensure_node(key, ACCOUNT, ts)
        st.ab_until = until_us

    # -------------------------------------------------------------- maintenance
    def recompute_node(self, key: str) -> None:
        """Rebuild a node's in/out aggregates from its pay edges (after edge expiry)."""
        if key not in self.g:
            return
        st = ns(self.g, key)
        if st.kind == DEVICE:
            return
        st.in_tot = st.out_tot = st.in_cnt = st.out_cnt = 0
        st.in_first = st.in_last = st.out_first = st.out_last = 0
        st.susp_in = 0
        st.sig = set()
        for m, e in pay_in(self.g, key):
            st.in_tot += e.total
            st.in_cnt += e.count
            st.in_first = e.first if not st.in_first else min(st.in_first, e.first)
            st.in_last = max(st.in_last, e.last)
            st.susp_in += e.susp
            if e.susp and len(st.sig) < SIG_CAP:
                st.sig.add(m)
        for _, e in pay_out(self.g, key):
            st.out_tot += e.total
            st.out_cnt += e.count
            st.out_first = e.first if not st.out_first else min(st.out_first, e.first)
            st.out_last = max(st.out_last, e.last)

    def expire_edges(self, cutoff_us: int) -> set[str]:
        """Drop edges whose last activity is before ``cutoff``; returns the touched nodes."""
        dead = [(a, b) for a, b, d in self.g.edges(data=True) if d["s"].last < cutoff_us]
        touched: set[str] = set()
        for a, b in dead:
            self.g.remove_edge(a, b)
            touched.update((a, b))
        for n in touched:
            if n in self.g:
                self.recompute_node(n)
        return touched
