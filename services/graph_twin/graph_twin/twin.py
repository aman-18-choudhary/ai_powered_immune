"""``GraphTwin``: the synchronous, thread-safe core (ingestion, detection runs, maintenance).

The async service wraps every call in ``asyncio.to_thread``. Lock order: ``twin.lock`` then the
store's own lock. Persistence and memory move together: the transaction is committed first-class
(rolled back on error) and the in-memory change is undone if the transaction fails.
"""

import logging
import re
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from scam_contracts.models import Antibody, Transaction, TxnDecision

from . import audit
from .config import Settings
from .graph import (
    ACCOUNT,
    DEVICE,
    TOKEN,
    EdgeState,
    NodeState,
    PaymentRec,
    RelayIndex,
    TwinGraph,
    account_key,
    es,
    from_us,
    is_active_antibody,
    ns,
    pay_out,
    to_paise,
    to_us,
)
from .model import AuditRow, Ring, RunResult
from .registry import RingEvent, RingRegistry
from .rings import candidates_from, region_starts, seeds_of
from .store import GraphStore, aware

log = logging.getLogger("graph_twin")

LEVEL = {"allow": 0, "step_up": 1, "hold_verify": 2}
_IDENT = re.compile(r"^[\x21-\x7e]{1,128}$")


class EventRejected(ValueError):
    """A structurally valid event the twin refuses (never echoes the offending value)."""


def _ident(value: str | None, what: str) -> str:
    if value is None or not _IDENT.match(value):
        raise EventRejected(f"invalid {what}")
    return value


class GraphTwin:
    def __init__(
        self,
        store: GraphStore,
        cfg: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.cfg = cfg or Settings()
        raw = clock or (lambda: datetime.now(UTC))
        self.clock: Callable[[], datetime] = lambda: raw().astimezone(UTC)
        self.lock = threading.RLock()
        self.tg = TwinGraph()
        self.registry = RingRegistry(self.cfg)
        self.dirty: set[str] = set()
        self.counters: Counter[str] = Counter()
        self.pending_decisions = 0
        self._carry: tuple[list[RingEvent], set[str], set[str]] = ([], set(), set())
        # immutable copies for readers on the event loop (replaced atomically, never mutated)
        self.published: dict[str, Ring] = {}
        self.published_alias: dict[str, str] = {}
        self.load()

    # ------------------------------------------------------------------ restart
    def load(self) -> None:
        data = self.store.load_all()
        g = self.tg.g
        with self.lock:
            for r in data["nodes"]:
                st = NodeState(r["kind"])
                st.bank = r["bank"]
                st.first = to_us(aware(r["first_ts"]))  # type: ignore[arg-type]
                st.last = to_us(aware(r["last_ts"]))  # type: ignore[arg-type]
                st.age = r["age"]
                ab = aware(r["ab_until"])
                st.ab_until = to_us(ab) if ab else 0
                g.add_node(r["key"], s=st)
            for r in data["edges"]:
                for k in (r["src"], r["dst"]):
                    if k not in g:
                        kind = (
                            ACCOUNT
                            if k.startswith("a:")
                            else DEVICE
                            if k.startswith("d:")
                            else TOKEN
                        )
                        g.add_node(k, s=NodeState(kind))
                e = EdgeState(r["kind"])
                e.count, e.total, e.rails, e.susp = r["cnt"], r["total"], r["rails"], r["susp"]
                e.first = to_us(aware(r["first_ts"]))  # type: ignore[arg-type]
                e.last = to_us(aware(r["last_ts"]))  # type: ignore[arg-type]
                g.add_edge(r["src"], r["dst"], s=e)
            for n in list(g.nodes):
                self.tg.recompute_node(n)
            self.tg.idx = RelayIndex.build(g, self.cfg, to_us(self.clock()))
            for r in data["rings"]:
                snap = __import__("json").loads(r["body"])
                ring = Ring.from_snapshot(snap, r["version"], to_us(aware(r["updated_at"])))  # type: ignore[arg-type]
                self.registry.load(ring)
            self._publish(set(self.registry.rings), set())
            self.pending_decisions = data["pending"]
            now_us = to_us(self.clock())
            self.dirty = set(seeds_of(g, now_us))
            for r in self.registry.rings.values():
                self.dirty |= {n for n in r.core if n in g}

    # --------------------------------------------------------------- snapshots
    def _snap(self, nodes: list[str], edges: list[tuple[str, str]]) -> Callable[[], None]:
        g = self.tg.g
        old_nodes = {n: (ns(g, n).clone() if n in g else None) for n in nodes}
        old_edges = {(a, b): (es(g, a, b).clone() if g.has_edge(a, b) else None) for a, b in edges}

        def restore() -> None:
            for (a, b), e in old_edges.items():
                if e is None:
                    if g.has_edge(a, b):
                        g.remove_edge(a, b)
                else:
                    g.add_edge(a, b, s=e)
            for n, st in old_nodes.items():
                if st is None:
                    if n in g:
                        g.remove_node(n)
                else:
                    g.add_node(n, s=st)
            self.tg.idx.refresh_many(g, old_nodes, self.cfg, to_us(self.clock()))

        return restore

    # --------------------------------------------------------------- ingestion
    def apply_txn(self, t: Transaction) -> bool:
        """Apply one transaction exactly once (idempotent on ``txn_id``). Returns True when new."""
        cfg = self.cfg
        txn_id = _ident(t.txn_id, "txn id")
        dst = account_key(_ident(t.payee_hash, "payee"))
        src = (
            account_key(_ident(t.payer_account_ref, "payer ref"))
            if t.payer_account_ref
            else "t:" + _ident(t.payer_token, "payer token")
        )
        dev = "d:" + _ident(t.device_id_token, "device") if t.device_id_token else None
        bank = _ident(t.bank_id, "bank")
        ts = t.ts.astimezone(UTC)
        now = self.clock()
        if ts > now + timedelta(seconds=cfg.future_skew_s):
            self.counters["txns_rejected_future"] += 1
            raise EventRejected("timestamp too far in the future")
        if ts < now - timedelta(days=cfg.retention_days):
            self.counters["txns_stale"] += 1
            return False
        paise = to_paise(t.amount_inr)
        if paise <= 0:
            raise EventRejected("invalid amount")
        if src == dst:
            self.counters["txns_self"] += 1
            return False
        ts_us = to_us(ts)
        with self.lock:
            keys = [k for k in (src, dst, dev) if k]
            edge_keys = [(src, dst)] + ([(src, dev)] if dev else [])
            restore = self._snap(keys, edge_keys)
            try:
                with self.store.tx() as conn:
                    dec = self.store.get_decision(conn, txn_id)
                    level = dec["level"] if dec else 0
                    seq = dec["seq"] if dec else 0
                    if not self.store.insert_txn(
                        conn, txn_id=txn_id, src=src, dst=dst, device=dev, amount=paise, ts=ts,
                        rail=t.rail, bank=bank, level=level, seq=seq,
                    ):  # fmt: skip
                        self.counters["txns_duplicate"] += 1
                        return False
                    if dec and not dec["applied"]:
                        self.store.mark_decision_applied(conn, txn_id)
                        self.pending_decisions = max(0, self.pending_decisions - 1)
                    self.tg.add_payment(
                        PaymentRec(
                            src,
                            dst,
                            dev,
                            paise,
                            ts_us,
                            t.rail,
                            bank,
                            t.payee_account_age_days,
                            level,
                        )  # fmt: skip
                    )
                    self.tg.idx.refresh_many(self.tg.g, (src, dst), cfg, to_us(now))
                    self._persist(conn, keys, edge_keys)
            except BaseException:
                restore()
                raise
            self.counters["txns_applied"] += 1
            self._mark_dirty(dst, src)
            if self.tg.node_count > cfg.max_nodes:
                self.evict(frozenset(k for k in (src, dst, dev) if k))
            return True

    def _persist(self, conn, keys: list[str], edge_keys: list[tuple[str, str]]) -> None:  # type: ignore[no-untyped-def]
        g = self.tg.g
        for k in keys:
            if k in g:
                self.store.upsert_node(conn, k, ns(g, k))
        for a, b in edge_keys:
            if g.has_edge(a, b):
                self.store.upsert_edge(conn, a, b, es(g, a, b))

    def _mark_dirty(self, *keys: str) -> None:
        for k in keys:
            if len(self.dirty) >= self.cfg.max_dirty:
                self.counters["dirty_dropped"] += 1
                return
            self.dirty.add(k)

    def apply_decision(self, d: TxnDecision) -> bool:
        """Latest ``decision_seq`` wins; replays and older seqs are no-ops. A decision that
        arrives before its transaction waits in the bounded pending buffer."""
        txn_id = _ident(d.txn_id, "txn id")
        level = LEVEL[d.decision]
        seq = d.decision_seq
        if seq < 1:
            raise EventRejected("invalid decision_seq")
        ts = d.ts.astimezone(UTC)
        with self.lock:
            snapshot: Callable[[], None] | None = None
            try:
                with self.store.tx() as conn:
                    cur = self.store.get_decision(conn, txn_id)
                    if cur and cur["seq"] >= seq:
                        self.counters["decisions_duplicate"] += 1
                        return False
                    t = self.store.get_txn(conn, txn_id)
                    self.store.put_decision(conn, txn_id, seq, level, ts, applied=t is not None)
                    if t is None:
                        self.counters["decisions_pending"] += 1
                        if cur is None:
                            self.pending_decisions += 1
                        if self.pending_decisions > self.cfg.max_pending_decisions:
                            dropped = self.store.drop_oldest_pending(
                                conn, self.cfg.max_pending_decisions
                            )
                            self.pending_decisions -= dropped
                            self.counters["decisions_dropped"] += dropped
                        return True
                    old = t["level"]
                    self.store.set_txn_level(conn, txn_id, level, seq)
                    src, dst = t["src"], t["dst"]
                    if (old >= 1) != (level >= 1) and self.tg.g.has_edge(src, dst):
                        snapshot = self._snap([src, dst], [(src, dst)])
                        self.tg.change_level(src, dst, old, level)
                        self._persist(conn, [src, dst], [(src, dst)])
                        self._mark_dirty(dst, src)
            except BaseException:
                if snapshot:
                    snapshot()
                raise
            self.counters["decisions_applied"] += 1
            return True

    def apply_antibody(self, ab: Antibody) -> bool:
        """Idempotent on (antibody_id, revoked, expires_at): revocation is sticky, expiry only
        extends. Only ``mule_account`` antibodies flag a node."""
        if ab.kind != "mule_account":
            self.counters["antibodies_ignored"] += 1
            return False
        ab_id = _ident(ab.antibody_id, "antibody id")
        key = account_key(_ident(ab.key_hash, "antibody key"))
        exp = ab.expires_at.astimezone(UTC)
        with self.lock:
            restore: Callable[[], None] | None = None
            try:
                with self.store.tx() as conn:
                    cur = self.store.get_antibody(conn, ab_id)
                    if cur:
                        revoked = bool(cur["revoked"]) or ab.revoked
                        new_exp = cur["expires_at"] if revoked else max(cur["expires_at"], exp)
                        if revoked == bool(cur["revoked"]) and new_exp == cur["expires_at"]:
                            self.counters["antibodies_noop"] += 1
                            return False
                    else:
                        revoked, new_exp = ab.revoked, exp
                    self.store.put_antibody(conn, ab_id, ab.kind, key[2:], new_exp, revoked)
                    until = self.store.active_antibody_until(conn, key[2:])
                    now = self.clock()
                    active = until if until is not None and until > now else None
                    restore = self._snap([key], [])
                    if active is not None:
                        st = self.tg.ensure_node(key, ACCOUNT, to_us(now))
                        st.ab_until = to_us(active)
                        self.store.upsert_node(conn, key, st)
                    elif key in self.tg.g:
                        st = ns(self.tg.g, key)
                        st.ab_until = 0
                        self.store.upsert_node(conn, key, st)
                    self.tg.idx.refresh(self.tg.g, key, self.cfg, to_us(now))
            except BaseException:
                if restore:
                    restore()
                raise
            self.counters["antibodies_applied"] += 1
            self._mark_dirty(key)
            return True

    # --------------------------------------------------------------- detection
    def detect_dirty(self) -> RunResult:
        """One incremental detection pass over the components touched since the last pass."""
        res = RunResult()
        cfg = self.cfg
        with self.lock:
            batch = self.dirty
            self.dirty = set()
            events, changed, deleted = self._carry
            self._carry = ([], set(), set())
            if batch:
                self.counters["detect_runs"] += 1
                g = self.tg.g
                now_us = to_us(self.clock())
                idx = self.tg.idx
                starts, region = region_starts(g, idx, batch, cfg, now_us)
                touched = self.registry.rings_of(batch | region)
                for rid in sorted(touched):
                    starts += [n for n in sorted(self.registry.rings[rid].core) if n in g]
                if starts:
                    for cand in candidates_from(g, idx, sorted(set(starts)), cfg, now_us):
                        ev, ch, de = self.registry.reconcile(cand, g, idx, now_us)
                        events += ev
                        changed |= ch
                        deleted |= de
                for rid in sorted(touched - changed - deleted):
                    ev, did = self.registry.refresh(rid, g, idx, now_us)
                    events += ev
                    if did:
                        changed.add(rid)
            else:
                now_us = to_us(self.clock())
            changed -= deleted
            if events or changed or deleted:
                try:
                    res.audit = self._flush_rings(events, changed, deleted, now_us)
                    self._publish(changed, deleted)
                except BaseException:
                    self._carry = (events, changed, deleted)
                    self.dirty |= batch
                    raise
            res.touched_rings = changed
            self.counters["rings_detected"] = len(self.registry.rings)
            return res

    def _publish(self, changed: set[str], deleted: set[str]) -> None:
        pub = {k: v for k, v in self.published.items() if k not in deleted}
        for rid in changed:
            r = self.registry.rings.get(rid)
            if r is not None:
                pub[rid] = replace(r, members=dict(r.members))
        self.published = pub
        self.published_alias = dict(self.registry.alias)

    def resolve_published(self, ring_id: str) -> Ring | None:
        r = self.published.get(ring_id)
        if r is None:
            r = self.published.get(self.published_alias.get(ring_id, ""))
        return r

    def _flush_rings(
        self, events: list[RingEvent], changed: set[str], deleted: set[str], now_us: int
    ) -> list[AuditRow]:  # fmt: skip
        rows: list[AuditRow] = []
        for ev in events:
            ring = self.registry.rings.get(ev.ring_id)
            if ring is not None:
                rows.append(audit.ring_event_row(ev.kind, ring, ev.info, now_us))
        now = from_us(now_us)
        with self.store.tx() as conn:
            for rid in sorted(changed):
                r = self.registry.rings.get(rid)
                if r:
                    self.store.save_ring(
                        conn, rid, r.version, from_us(r.first_detected_us), from_us(r.updated_us),
                        from_us(r.last_seen_us or r.updated_us), r.snapshot(),
                    )  # fmt: skip
            self.store.delete_rings(conn, sorted(deleted))
            fresh = [
                row
                for row in rows
                if self.store.enqueue_audit(conn, row.dedupe, row.msg_key, row.body, now)
            ]
        return fresh

    def enqueue_audit_row(self, row: AuditRow) -> bool:
        """Persist one audit row (e.g. ``takedown.simulated``); True when new."""
        with self.store.tx() as conn:
            return self.store.enqueue_audit(conn, row.dedupe, row.msg_key, row.body, self.clock())

    # ------------------------------------------------------------- maintenance
    def _protected(self, now_us: int) -> set[str]:
        g = self.tg.g
        prot: set[str] = set()
        for r in self.registry.rings.values():
            for n in r.members:
                if n in g:
                    prot.add(n)
                    prot.update(g.pred[n])
                    prot.update(g.succ[n])
        for n in g.nodes:
            st = ns(g, n)
            if st.susp_in > 0 or is_active_antibody(st, now_us):
                prot.add(n)
                continue
            if st.kind == ACCOUNT or st.kind == TOKEN:
                if any(e.susp for _, e in pay_out(g, n)):
                    prot.add(n)
        return prot

    def evict(self, exclude: frozenset[str] = frozenset()) -> int:
        """Bring the node count to 95% of ``GRAPH_MAX_NODES``: oldest-last-seen first, never a
        ring member or its neighbours, a node with a signal, or the payer of a flagged payment."""
        cfg = self.cfg
        with self.lock:
            g = self.tg.g
            target = int(cfg.max_nodes * 0.95)
            over = g.number_of_nodes() - target
            if over <= 0:
                return 0
            now_us = to_us(self.clock())
            prot = self._protected(now_us)
            cand = sorted((n for n in g.nodes if n not in prot), key=lambda n: (ns(g, n).last, n))[
                :over
            ]
            self._remove_nodes(cand)
            self.counters["evicted_nodes"] += len(cand)
            if len(cand) < over:
                self.counters["evict_blocked"] += 1
            return len(cand)

    def _remove_nodes(self, keys: list[str]) -> None:
        g = self.tg.g
        neigh: set[str] = set()
        for n in keys:
            neigh.update(g.pred[n])
            neigh.update(g.succ[n])
        for n in keys:
            g.remove_node(n)
            self.tg.idx.refresh(g, n, self.cfg, 0)  # n is gone: drops it from the index
        for n in neigh - set(keys):
            if n in g:
                self.tg.recompute_node(n)
        self.tg.idx.refresh_many(g, neigh - set(keys), self.cfg, to_us(self.clock()))
        with self.store.tx() as conn:
            self.store.delete_nodes(conn, keys)

    def sweep(self) -> dict[str, int]:
        """Retention + compaction + ring lifecycle (rings first, so an expired ring's nodes are no
        longer protected). Holds the twin lock for O(nodes) work, off the event loop."""
        cfg = self.cfg
        with self.lock:
            now = self.clock()
            now_us = to_us(now)
            cutoff_us = now_us - cfg.retention_us
            events, changed, deleted = self.registry.tick(now_us)
            ce, cc, cd = self._carry
            events, changed, deleted = events + ce, changed | cc, deleted | cd
            self._carry = ([], set(), set())
            audit_rows: list[AuditRow] = []
            if events or changed or deleted:
                try:
                    audit_rows = self._flush_rings(events, changed - deleted, deleted, now_us)
                    self._publish(changed - deleted, deleted)
                except BaseException:
                    self._carry = (events, changed, deleted)
                    raise
            touched = self.tg.expire_edges(cutoff_us)
            self.tg.idx.refresh_many(self.tg.g, touched, cfg, now_us)
            g = self.tg.g
            prot = self._protected(now_us)
            dead: set[str] = set()
            for n in g.nodes:
                if g.degree(n) != 0 or n in prot:
                    continue
                st = ns(g, n)
                if (
                    st.kind == DEVICE
                    or st.last < cutoff_us
                    or (st.ab_until and st.ab_until <= now_us)
                ):
                    dead.add(n)
            if dead:
                self._remove_nodes(sorted(dead))
            with self.store.tx() as conn:
                cut = from_us(cutoff_us)
                self.store.delete_edges_before(conn, cut)
                purged = self.store.purge_old(conn, cut, now - timedelta(seconds=cfg.pending_ttl_s))
                self.store.purge_antibodies(conn, now - timedelta(days=1))
                self.store.purge_audit(conn, now - timedelta(days=cfg.audit_retention_days))
                self.pending_decisions = self.store.count_pending(conn)
            evicted = self.evict() if g.number_of_nodes() > cfg.max_nodes else 0
            for r in self.registry.rings.values():  # anchor refresh for rings (dirty overflow)
                self._mark_dirty(*[n for n in r.core if n in g])
            self.pending_audit_rows = audit_rows
            return {"removed_nodes": len(dead), "evicted": evicted, **purged}

    pending_audit_rows: list[AuditRow] = []

    # -------------------------------------------------------------------- stats
    def stats(self) -> dict[str, int]:
        with self.lock:
            g = self.tg.g
            return {
                "nodes": g.number_of_nodes(),
                "edges": g.number_of_edges(),
                "rings": len(self.registry.rings),
                "pending_decisions": self.pending_decisions,
                "dirty": len(self.dirty),
                "rings_dropped": self.registry.dropped_total,
                **dict(sorted(self.counters.items())),
            }
