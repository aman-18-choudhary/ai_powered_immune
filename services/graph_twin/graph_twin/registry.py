"""Ring registry: stable ids, the merge rule, status lifecycle and the version counters.

Identity. ``ring_id`` = ``opaque_hex16(sha256(sorted core member keys))`` at FIRST detection and
never recomputed: a ring that grows keeps its id.
Merge rule. A detection candidate whose core shares ANY member with an existing ring joins that
ring; if it touches several rings they merge into the OLDEST (``first_detected``, then id). The
absorbed ids stay resolvable as aliases (``merged_from``).
Status. ``new`` at creation; ``growing`` when core members, victims, inflow or edge aggregates
increase; ``stable`` after ``stable_after_s`` of stream time without growth.
Version. Increments whenever the persisted snapshot (members, roles, statistics, status,
``edge_sig``) changes; it keys the ETags.
"""

from dataclasses import dataclass
from typing import Any

import networkx as nx

from .config import Settings
from .graph import RelayIndex
from .model import Ring
from .rings import classify, make_ring_id, ring_stats


@dataclass
class RingEvent:
    kind: str  # detected | merged | status_changed
    ring_id: str
    info: dict[str, Any]


class RingRegistry:
    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self.rings: dict[str, Ring] = {}
        self.alias: dict[str, str] = {}
        self.core_index: dict[str, str] = {}
        self.member_index: dict[str, str] = {}
        self.dropped_total = 0  # candidates refused because GRAPH_MAX_RINGS was reached

    # --------------------------------------------------------------------- index
    def _index(self, r: Ring) -> None:
        for n in r.core:
            self.core_index[n] = r.ring_id
        for n in r.members:
            self.member_index[n] = r.ring_id
        for a in r.merged_from:
            self.alias[a] = r.ring_id

    def _unindex(self, r: Ring) -> None:
        for n in r.core:
            if self.core_index.get(n) == r.ring_id:
                del self.core_index[n]
        for n in r.members:
            if self.member_index.get(n) == r.ring_id:
                del self.member_index[n]

    def load(self, ring: Ring) -> None:
        self.rings[ring.ring_id] = ring
        self._index(ring)

    def resolve(self, ring_id: str) -> Ring | None:
        r = self.rings.get(ring_id)
        if r is None and ring_id in self.alias:
            r = self.rings.get(self.alias[ring_id])
        return r

    def remove(self, ring_id: str) -> None:
        r = self.rings.pop(ring_id, None)
        if r:
            self._unindex(r)
            for a in r.merged_from:
                self.alias.pop(a, None)

    def rings_of(self, nodes: set[str]) -> set[str]:
        return {self.member_index[n] for n in nodes if n in self.member_index}

    # ----------------------------------------------------------------- reconcile
    def reconcile(
        self, cand: Ring, g: nx.DiGraph, idx: RelayIndex, now_us: int
    ) -> tuple[list[RingEvent], set[str], set[str]]:  # fmt: skip
        """Fold one candidate in. Returns (events, changed ring ids, deleted ring ids)."""
        events: list[RingEvent] = []
        hits = sorted(
            {self.core_index[n] for n in cand.core if n in self.core_index},
            key=lambda rid: (self.rings[rid].first_detected_us, rid),
        )
        if not hits:
            if len(self.rings) >= self.cfg.max_rings:
                self.dropped_total += 1
                return [], set(), set()
            cand.ring_id = make_ring_id(cand.core)
            if cand.ring_id in self.rings:  # same core already tracked: treat as a hit
                hits = [cand.ring_id]
            else:
                cand.first_detected_us = now_us
                cand.growth_us = now_us
                cand.updated_us = now_us
                cand.status = "new"
                cand.version = 1
                self.rings[cand.ring_id] = cand
                self._index(cand)
                events.append(RingEvent("detected", cand.ring_id, {}))
                return events, {cand.ring_id}, set()
        surv = self.rings[hits[0]]
        before = self._growth_key(surv)
        before_view = surv.stats_view()
        before_status = surv.status
        deleted: set[str] = set()
        self._unindex(surv)
        for aid in hits[1:]:
            a = self.rings[aid]
            surv.members.update(a.members)
            surv.core = surv.core | a.core
            surv.merged_from = tuple(sorted({*surv.merged_from, aid, *a.merged_from}))
            self.remove(aid)
            self.alias[aid] = surv.ring_id
            deleted.add(aid)
            events.append(RingEvent("merged", surv.ring_id, {"absorbed": aid}))
        surv.members.update(cand.members)
        surv.core = surv.core | cand.core
        surv.members = classify(g, idx, set(surv.members), self.cfg, now_us)
        surv.core = frozenset(
            n for n, r in surv.members.items() if r in ("mule", "cash_out") or n in surv.core
        )
        ring_stats(g, surv, self.cfg, now_us)
        self._index(surv)
        self._finish(surv, before, before_view, before_status, now_us, events)
        return events, {surv.ring_id}, deleted

    @staticmethod
    def _growth_key(r: Ring) -> tuple[int, int, int, int, str]:
        return (len(r.core), r.victims, r.inflow_paise, len(r.members), r.edge_sig)

    def _finish(
        self, r: Ring, before: tuple[int, int, int, int, str], before_view: tuple[Any, ...],
        before_status: str, now_us: int, events: list[RingEvent],
    ) -> None:  # fmt: skip
        after = self._growth_key(r)
        grew = any(a > b for a, b in zip(after[:4], before[:4], strict=True))
        if grew:
            r.growth_us = now_us
            if r.status != "growing":
                r.status = "growing"
        if r.status != before_status:
            events.append(
                RingEvent("status_changed", r.ring_id, {"from": before_status, "to": r.status})
            )
        if r.stats_view() != before_view:
            r.version += 1
            r.updated_us = now_us

    def refresh(
        self, ring_id: str, g: nx.DiGraph, idx: RelayIndex, now_us: int
    ) -> tuple[list[RingEvent], bool]:  # fmt: skip
        """Recompute one ring's roles and statistics after its members' neighbourhood changed."""
        r = self.rings.get(ring_id)
        if r is None:
            return [], False
        events: list[RingEvent] = []
        before, view, status = self._growth_key(r), r.stats_view(), r.status
        alive = {n for n in r.members if n in g}
        if not alive:
            return [], False
        self._unindex(r)
        r.members = classify(g, idx, alive, self.cfg, now_us)
        r.core = frozenset(n for n in r.core if n in alive) | frozenset(
            n for n, role in r.members.items() if role in ("mule", "cash_out")
        )
        ring_stats(g, r, self.cfg, now_us)
        self._index(r)
        self._finish(r, before, view, status, now_us, events)
        return events, r.stats_view() != view or r.status != status

    def tick(self, now_us: int) -> tuple[list[RingEvent], set[str], set[str]]:
        """Stream-time lifecycle: new/growing -> stable; expired rings leave the registry."""
        events: list[RingEvent] = []
        changed: set[str] = set()
        deleted: set[str] = set()
        stable_us = int(self.cfg.stable_after_s * 1_000_000)
        for rid in sorted(self.rings):
            r = self.rings[rid]
            if r.last_seen_us and r.last_seen_us < now_us - self.cfg.retention_us:
                self.remove(rid)
                deleted.add(rid)
                continue
            if r.status != "stable" and now_us - r.growth_us >= stable_us:
                events.append(RingEvent("status_changed", rid, {"from": r.status, "to": "stable"}))
                r.status = "stable"
                r.version += 1
                r.updated_us = now_us
                changed.add(rid)
        return events, changed, deleted
