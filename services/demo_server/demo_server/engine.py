"""Scenario engine: plays the hero scenario through the real services in simulated time.

Time model. ``t`` is simulated seconds since the scenario's first call. The engine advances ``t``
in slices of at most ``SLICE_S``; between slices it waits ``slice / speed`` wall seconds through
an injectable ``sleep`` (tests pass an instant one). Every ``TICK_S`` of simulated time a
``metric.tick`` is emitted, so the event sequence depends on the seed and mode only, never on
speed or wall time.

Determinism (what is and is not guaranteed). For a given (seed, mode): event kinds, order, ids,
``t`` and scores are identical across runs and speeds. Excluded: ``wall`` timestamps,
``antibody.applied.latency_ms`` (a real measurement), ``run_id``. ``ledger.entry`` events are
emitted at deterministic synchronisation points after each step, waiting at most
``LEDGER_WAIT_S`` of wall time for the ledger consumer; if the ledger lags beyond that, entries
simply appear at a later point. Decisions and stream events never wait for the ledger.

Seek. ``seek(t)`` restarts the run from scratch (same seed, new resources) and replays it without
pacing until ``t``; the replayed events are rebuilt into the history (ids restart from 1 and are
identical to the original run's), subscribers get one ``scenario.state`` snapshot and then live
events. Manual analyst actions taken earlier are re-applied at their recorded simulated time.
"""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import logging
import shutil
import threading
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from call_guard.consumer import drain_ledger_pending, handle_event
from call_guard.model import Scorer as CallScorer
from call_guard.model import load_classifier
from evidence_ledger.keys import Signer
from scam_contracts.models import CallRisk, Transaction, TxnDecision
from scam_contracts.topics import Topics
from sim_engine.world import World, build_world
from svckit.ledger import call_ref, txn_ref
from txn_guard.holds import Hold, HoldConflict, HoldNotFound, amount_bucket
from txn_guard.model import Scorer as TxnScorer

from . import geo as geomod
from .config import Settings
from .events import EventLog, Subscriber
from .harness import Harness, new_gateway_secret
from .pacing import Timeline
from .plan import Item, Plan, bank_slot, build_plan
from .ring import RingGraph
from .sanitise import sanitise_detail

log = logging.getLogger("demo_server.engine")

CONFIRM_DELAY_S = 60.0  # simulated seconds from the first hold on the shared mule to the analyst
TICK_S = 30.0
SLICE_S = 5.0
LEDGER_WAIT_S = 1.0
ANTIBODY_WAIT_S = 5.0
HARNESS_BUILD_WAIT_S = 30.0
CALL_ALERT_THRESHOLD = 0.7
ANALYST_ACTOR = "analyst-1"
EVIDENCE_REF = "case-77"
WORLD_SEED, WORLD_CITIZENS = 5, 600
SPEED_MIN, SPEED_MAX = 0.25, 20.0
SCENARIO_KIND = "hero_hard_case"
SCENARIO_NOTE = (
    "Constructed hard case: seasoned mule account and a call-guard miss; only the shared threat "
    "memory saves victim B; simulator data"
)
IDLE_STATUSES = ("idle", "finished", "stopped", "error")

COUNTERS = (
    "chunks", "call_alerts", "txns_scored", "holds_created", "holds_open", "holds_resolved",
    "antibodies_published", "antibodies_applied", "antibody_matches", "ledger_entries",
    "geo_points", "ring_members",
)  # fmt: skip

STEPS: dict[str, tuple[str, str]] = {
    "call_begins": (
        "A scam call begins",
        "A caller posing as an investigator phones Victim A (simulated call).",
    ),
    "call_flagged": (
        "Call-guard flags the call",
        "The session risk crosses the alert threshold; the paying bank is told.",
    ),
    "transfer_held": (
        "Bank A holds the transfer",
        "Victim A's transfer to a mule account is held for verification.",
    ),
    "analyst_confirms": (
        "Analyst confirms the mule",
        "The analyst step (automatic in this demo unless a person acts) confirms the payee is a "
        "mule account and blocks the transfer.",
    ),
    "antibody_shared": (
        "Antibody shared with other banks",
        "A privacy-preserving threat marker for the mule account is published to the hub, which "
        "broadcasts it to every subscribed bank; in this single-process demo Bank B consumes it.",
    ),
    "bank_b_blocks": (
        "Bank B blocks the same mule",
        "Victim B, at another bank, pays the same account: nothing local looks wrong, "
        "the shared antibody holds it.",
    ),
    "bank_b_misses": (
        "Bank B did not hold the transfer",
        "Without a shared antibody in time, Bank B's guard did not hold this transfer.",
    ),
    "analyst_released": (
        "Analyst released the hold",
        "The analyst judged the transfer legitimate: no antibody is published for this payee.",
    ),
    "cash_out": (
        "Funds reach the cash-out account",
        "Mule accounts forward the money onward; the ring graph links them.",
    ),
    "evidence_sealed": (
        "Evidence sealed",
        "The decisions of this run are recorded in the hash-chained ledger; an offline verifier "
        "can check the exported package.",
    ),
}


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _reasons(rs: list[Any], bucket: str | None = None) -> list[dict[str, Any]]:
    """Reasons for events and views; exact amounts in the detail text become the bucket label."""
    return [
        {
            "code": r.code, "weight": round(float(r.weight), 4),
            "detail": sanitise_detail(r.detail, bucket),
        }
        for r in rs
    ]  # fmt: skip


class _Stopped(Exception):
    pass


@dataclass
class RunState:
    """Everything the stream and REST views read; survives seek epochs of the same run."""

    run_id: str | None = None
    seed: int = 0
    mode: str = "hero"
    speed: float = 10.0
    status: str = "idle"  # idle|starting|running|paused|seeking|finished|stopped|error
    t: float = 0.0
    duration: float = 0.0
    step: str | None = None
    epoch: int = 0
    next_id: int = 1
    counters: dict[str, int] = field(default_factory=lambda: dict.fromkeys(COUNTERS, 0))
    geo_points: deque[geomod.GeoPoint] = field(default_factory=lambda: deque(maxlen=2000))
    manual_log: list[dict[str, Any]] = field(default_factory=list)
    started_wall: str | None = None
    banks: dict[str, str] = field(default_factory=dict)
    pacing: str = "real"
    wall_duration: float = 0.0

    def reset_epoch(self) -> None:
        self.t = 0.0
        self.step = None
        self.next_id = 1
        self.counters = dict.fromkeys(COUNTERS, 0)
        self.geo_points.clear()

    def view(self, last_id: int) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "status": self.status, "t": round(self.t, 3),
            "duration": round(self.duration, 3), "speed": self.speed, "step": self.step,
            "counters": dict(self.counters), "simulated": True, "mode": self.mode,
            "seed": self.seed, "epoch": self.epoch, "last_event_id": last_id,
            "banks": dict(self.banks), "pacing": self.pacing,
            "scenario_kind": SCENARIO_KIND, "scenario_note": SCENARIO_NOTE,
            "wall_duration": round(self.wall_duration, 3),
        }  # fmt: skip


class Run:
    """One scenario execution (one seek epoch). Owns a ``Harness``; see module docstring."""

    def __init__(
        self, engine: Engine, plan: Plan, *, fast_until: float | None, start_paused: bool,
        workdir: Path,
    ) -> None:  # fmt: skip
        self.e = engine
        self.st = engine.state
        self.plan = plan
        self.meta = plan.meta
        self.fast_until = fast_until
        self.fast = fast_until is not None
        self.workdir = workdir
        self.timeline = Timeline(
            engine.state.pacing, [it.t for it in plan.items], plan.duration, TICK_S
        )
        self.harness: Harness | None = None
        self.harness_future: asyncio.Future[Harness] | None = None
        self.ring = RingGraph()
        self.running = asyncio.Event()
        if not start_paused:
            self.running.set()
        self.wake = asyncio.Event()
        self.stop_flag = False
        self.done = asyncio.Event()
        self.ready = asyncio.Event()  # set once a seek replay reached its target (or ended)
        self.hold_meta: dict[str, dict[str, Any]] = {}
        self._hold_buf: list[tuple[str, str, Hold]] = []
        self._hold_last: dict[str, tuple[str, int]] = {}
        self._ledger_seq = 0
        self._ring_sig: tuple[Any, ...] | None = None
        self._bus_ledger_seen = 0
        self.shared_hold: tuple[float, str, str] | None = None  # (t, txn_id, slot)
        self.confirmed = False
        self._confirm_lock = asyncio.Lock()
        self._sync_lock = asyncio.Lock()
        self._heap: list[tuple[tuple[float, int, int], int, Item]] = []
        self._seq = 0
        self._ledger_slow = False
        self._step_seen: set[str] = set()
        self.finished_summary: dict[str, Any] = {}
        self._b_first_txn: str | None = None
        self.cases: set[str] = set()
        self.first_alert_call_id: str | None = None

    # ------------------------------------------------------------------ emission
    def emit(self, kind: str, /, **fields: Any) -> dict[str, Any]:
        st = self.st
        ev: dict[str, Any] = {
            "id": st.next_id, "t": round(st.t, 3), "wall": _utc_iso(), "kind": kind,
            "run_id": st.run_id, "wall_offset": round(self.timeline.wall_of(st.t), 3), **fields,
        }  # fmt: skip
        st.next_id += 1
        self._reduce(ev)
        if kind == "metric.tick":
            ev["counters"] = dict(st.counters)
        self.e.log.append(ev, broadcast=not self.fast)
        return ev

    def _reduce(self, ev: dict[str, Any]) -> None:
        c, k = self.st.counters, ev["kind"]
        if k == "call.chunk":
            c["chunks"] += 1
        elif k == "call.alert":
            c["call_alerts"] += 1
        elif k == "txn.scored":
            c["txns_scored"] += 1
            if any(r["code"] == "ANTIBODY_MATCH" for r in ev.get("reasons", [])):
                c["antibody_matches"] += 1
        elif k == "hold.created":
            c["holds_created"] += 1
            c["holds_open"] += 1
        elif k == "hold.resolved":
            c["holds_resolved"] += 1
            c["holds_open"] = max(0, c["holds_open"] - 1)
        elif k == "antibody.published":
            c["antibodies_published"] += 1
        elif k == "antibody.applied":
            c["antibodies_applied"] += 1
        elif k == "ledger.entry":
            c["ledger_entries"] = max(c["ledger_entries"], int(ev["seq"]))
        elif k == "geo.point":
            c["geo_points"] += 1
        elif k == "ring.updated":
            c["ring_members"] = int(ev["members"])

    def step(self, key: str) -> None:
        if key in self._step_seen:
            return
        self._step_seen.add(key)
        title, caption = STEPS[key]
        self.st.step = key
        self.emit("scenario.step", key=key, title=title, caption=caption)

    def _geo(self, kind: str, row: tuple[str, str, float, float, float]) -> None:
        p = geomod.point(self.st.t, kind, row)
        self.st.geo_points.append(p)
        self.emit("geo.point", lat=p.lat, lon=p.lon, point_kind=kind, city=p.city, state=p.state)

    # ---------------------------------------------------------------- hold observer
    def on_hold(self, slot: str, kind: str, hold: Hold) -> None:
        self._hold_buf.append((slot, kind, hold))

    def flush_holds(self) -> None:
        buf, self._hold_buf = self._hold_buf, []
        for slot, kind, h in buf:
            ref = txn_ref(h.txn_id)
            meta = self.hold_meta.setdefault(ref, {"txn_id": h.txn_id, "bank": slot})
            deadline_t = (h.deadline - self.plan.t0).total_seconds()
            base = {
                "txn_ref": ref, "bank": slot, "decision": h.decision,
                "decision_seq": h.decision_seq, "score": round(h.score, 4),
                "reasons": _reasons(h.reasons, h.amount_bucket), "rail": h.rail,
                "amount_bucket": h.amount_bucket, "deadline_t": round(deadline_t, 3),
            }  # fmt: skip
            if kind == "created":
                meta["created_t"] = round(self.st.t, 3)
                self.emit("hold.created", **base, state="open")
                self._hold_last[ref] = (h.decision, h.decision_seq)
            elif kind == "upgraded":
                prev = self._hold_last.get(ref, (None, 0))
                self._hold_last[ref] = (h.decision, h.decision_seq)
                self.emit("hold.upgraded", **base, from_decision=prev[0], state="open")
            else:
                self.emit(
                    "hold.resolved", **base, state=h.state,
                    action="release" if h.state == "released" else "confirm_block",
                    resolved_by=h.resolved_by, source=meta.get("source", "auto"),
                )  # fmt: skip

    # ----------------------------------------------------------------- pacing
    async def _sleep(self, wall_s: float) -> None:
        """Injectable sleep, interruptible by pause/stop/speed changes."""
        if wall_s <= 0:
            return
        s = asyncio.ensure_future(self.e.sleep(wall_s))
        w = asyncio.ensure_future(self.wake.wait())
        try:
            await asyncio.wait({s, w}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for f in (s, w):
                if not f.done():
                    f.cancel()
            await asyncio.wait({s, w})
        self.wake.clear()

    async def _gate(self) -> None:
        while not self.running.is_set():
            if self.stop_flag:
                raise _Stopped
            await self.running.wait()
        if self.stop_flag:
            raise _Stopped

    async def advance_to(self, target: float) -> None:
        st = self.st
        while st.t < target:
            if self.fast and self.fast_until is not None and st.t >= self.fast_until:
                await self._leave_fast()
            boundary = (int(st.t // TICK_S) + 1) * TICK_S
            if self.fast:
                nxt = min(target, boundary)
                if self.fast_until is not None and st.t < self.fast_until:
                    nxt = min(nxt, self.fast_until)
            else:
                await self._gate()
                nxt = min(target, boundary, st.t + SLICE_S)
                tl = self.timeline
                dwall = tl.wall_of(nxt) - tl.wall_of(st.t)
                await self._sleep(dwall / max(SPEED_MIN, st.speed))
                if self.stop_flag:
                    raise _Stopped
                if not self.running.is_set():
                    continue  # paused mid-slice: the slice did not elapse
            st.t = nxt
            if nxt == boundary:
                self.emit("metric.tick")
            await self._apply_manual()

    async def _leave_fast(self) -> None:
        """Replay reached the seek target: publish one snapshot, then continue live."""
        self.fast = False
        self.fast_until = None
        self.st.status = "running" if self.running.is_set() else "paused"
        self.e.broadcast_snapshot()
        self.ready.set()

    # --------------------------------------------------------------- manual input
    async def _apply_manual(self, inclusive: bool = False) -> None:
        """Re-apply recorded manual actions whose simulated time has been reached (replays).

        Between items only actions strictly before ``t`` are due (an item at ``t`` has not run
        yet); after an item ran, actions at ``t`` are due too. An action whose hold does not exist
        yet is retried at the next boundary."""
        key = f"done_{self.st.epoch}"
        for rec in self.st.manual_log:
            if rec.get("epoch") == self.st.epoch or rec.get(key):
                continue
            due = rec["t"] <= self.st.t if inclusive else rec["t"] < self.st.t
            if not due:
                continue
            try:
                await self.resolve_hold(
                    rec["txn_ref"], rec["action"], rec["actor"], rec["role"], record=False
                )
                rec[key] = True
            except HoldNotFound:
                continue
            except Exception as e:
                rec[key] = True
                log.warning("manual replay failed (%s)", type(e).__name__)

    async def resolve_hold(
        self, ref: str, action: str, actor: str, role: str, *, expect_seq: int | None = None,
        record: bool = True,
    ) -> Hold:  # fmt: skip
        """Resolve a hold as a human analyst (idempotent; same action twice is a no-op)."""
        meta = self.hold_meta.get(ref)
        if meta is None or self.harness is None:
            raise HoldNotFound(ref)
        h = self.harness
        bank = h.banks[meta["bank"]]
        meta["source"] = "manual"
        try:
            hold = await bank.holds.resolve(
                meta["txn_id"],
                action,
                actor,
                expect_seq=expect_seq,
                role=role,  # type: ignore[arg-type]
            )
        except (HoldNotFound, HoldConflict):
            raise
        if record:
            self.st.manual_log.append(
                {"t": self.st.t, "txn_ref": ref, "action": action, "actor": actor, "role": role,
                 "epoch": self.st.epoch}
            )  # fmt: skip
            del self.st.manual_log[:-1000]
        self.flush_holds()
        meta_t = self.shared_hold
        if (
            action == "confirm_block"
            and meta_t is not None
            and meta_t[1] == meta["txn_id"]
            and not self.confirmed
        ):
            await self._confirm(actor, role, resolve_hold=False)
        await self.sync_ledger()
        return hold

    # ------------------------------------------------------------------ ledger
    async def sync_ledger(self) -> None:
        """Deliver pending audit entries, wait (bounded) for the ledger, emit new entries."""
        async with self._sync_lock:
            await self._sync_ledger()

    async def _sync_ledger(self) -> None:
        h = self.harness
        assert h is not None
        try:
            await drain_ledger_pending(h.bus, h.sessions)
            for b in h.banks.values():
                await b.holds.sweep()
        except Exception as e:
            log.warning("audit drain failed (%s)", type(e).__name__)
        want = len({raw for _, raw in h.bus.messages(Topics.LEDGER)})
        if not self._ledger_slow:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + LEDGER_WAIT_S
            while loop.time() < deadline:
                head = await asyncio.to_thread(h.ledger_store.head)
                if head.seq >= want:
                    break
                await asyncio.sleep(0.002)
            else:
                self._ledger_slow = True  # breaker: stop waiting, keep emitting what arrived
                log.warning("ledger lagging; no longer waiting at sync points")
        while True:
            rows = await asyncio.to_thread(h.ledger_store.page, self._ledger_seq + 1, 200)
            if not rows:
                break
            for e in rows:
                self._ledger_seq = e.seq
                self.emit(
                    "ledger.entry", seq=e.seq, event_type=e.event_type, service=e.service,
                    actor_ref=e.actor, entry_hash_prefix=e.entry_hash[:12],
                )  # fmt: skip
            if len(rows) < 200:
                break

    # ----------------------------------------------------------------- items
    def _city_of(self, token: str) -> tuple[str, str, float, float, float] | None:
        c = self.e.token_city.get(token)
        return c

    def _victim_label(self, token: str) -> str | None:
        if token == self.meta.victim_a_token:
            return "A"
        if token == self.meta.victim_b_token:
            return "B"
        return None

    async def process_call(self, ev: Any) -> None:
        h = self.harness
        assert h is not None
        await h.pre.prescore(ev)
        before = len(h.bus.messages(Topics.CALL_RISK))
        await handle_event(ev, h.sessions, h.bus, h.call_idem)  # the real consumer path
        risk, _ = await h.sessions.update_with_crossing(ev)  # replay-safe: current session state
        h.pre.forget(ev)
        who = self._victim_label(ev.victim_token)
        cref = call_ref(ev.call_id)
        if who == "A" and "call_begins" not in self._step_seen:
            self.step("call_begins")
        self.emit(
            "call.chunk", call_ref=cref, victim=who, speaker="caller", text=ev.transcript_chunk,
            score=round(risk.score, 4), reasons=_reasons(risk.reasons), lang=ev.lang,
            channel=ev.channel, model_version=risk.model_version,
        )  # fmt: skip
        new = h.bus.messages(Topics.CALL_RISK)[before:]
        for _, raw in new:
            cr = CallRisk.model_validate_json(raw)
            self.emit(
                "call.alert", call_ref=call_ref(cr.call_id),
                victim=self._victim_label(cr.victim_token),
                score=round(cr.score, 4), reasons=_reasons(cr.reasons),
                threshold=CALL_ALERT_THRESHOLD, model_version=cr.model_version,
            )  # fmt: skip
            if who == "A":
                self.step("call_flagged")
                self.first_alert_call_id = self.first_alert_call_id or cr.call_id
            row = self._city_of(cr.victim_token)
            if row is not None:
                self._geo("scam_call", row)
            slot = self._slot_for_token(cr.victim_token)
            h.clock.t = max(h.clock.t, cr.ts)
            await h.banks[slot].handle_call_risk(cr)
            self.flush_holds()

    def _slot_for_token(self, token: str) -> str:
        if token == self.meta.victim_a_token:
            return "bank_a"
        if token == self.meta.victim_b_token:
            return "bank_b"
        return "bank_a" if hashlib.sha256(token.encode()).digest()[0] & 1 == 0 else "bank_b"

    async def process_txn(self, it: Item) -> None:
        h = self.harness
        assert h is not None
        txn: Transaction = it.obj
        slot = bank_slot(txn.bank_id, self.meta)
        h.clock.t = txn.ts
        d: TxnDecision = await h.banks[slot].handle_txn(txn)
        hero = not it.noise
        who = self._victim_label(txn.payer_token)
        ref = txn_ref(txn.txn_id)
        self.emit(
            "txn.scored", txn_ref=ref, bank=slot, bank_name=self.st.banks.get(slot),
            rail=txn.rail, amount_bucket=amount_bucket(txn.amount_inr), decision=d.decision,
            score=round(d.score, 4),
            reasons=_reasons(d.reasons, amount_bucket(txn.amount_inr)), seq=d.decision_seq,
            victim=who, hero=hero,
        )  # fmt: skip
        meta = self.hold_meta.setdefault(ref, {"txn_id": txn.txn_id, "bank": slot})
        meta.update(
            amount_inr=f"{txn.amount_inr:.2f}", payee_ref=txn.payee_hash[:12], rail=txn.rail,
            bank_name=self.st.banks.get(slot), hero=hero,
        )  # fmt: skip
        self.flush_holds()
        flagged = d.decision != "allow"
        if hero:
            self.ring.add_txn(txn, flagged=flagged)
            self._emit_ring()
        row = self._city_of(txn.payer_token)
        if hero and flagged and row is not None:
            self._geo("held_transfer", row)
        codes = {r.code for r in d.reasons}
        if hero and flagged and txn.payee_hash == self.meta.shared_mule_payee_hash:
            if slot == "bank_a" and self.shared_hold is None and d.decision == "hold_verify":
                self.shared_hold = (self.st.t, txn.txn_id, slot)
                self.step("transfer_held")
                self._push(Item(self.st.t + CONFIRM_DELAY_S, 1, 10**6, "confirm", None))
        if hero and who == "B" and txn.payee_hash == self.meta.shared_mule_payee_hash:
            if "bank_b_blocks" not in self._step_seen and "bank_b_misses" not in self._step_seen:
                if "ANTIBODY_MATCH" in codes and d.decision == "hold_verify":
                    self.step("bank_b_blocks")
                    if row is not None:
                        self._geo("protected", row)
                else:
                    self.step("bank_b_misses")
            self._b_first_txn = self._b_first_txn or ref

    async def process_observe(self, it: Item) -> None:
        txn: Transaction = it.obj
        self.ring.add_txn(txn)
        self.step("cash_out")
        pts = self.plan.scenario.campaign.cashout_points
        if pts:
            idx = int(hashlib.sha256(txn.txn_id.encode()).hexdigest()[:8], 16) % len(pts)
            self._geo("cash_out", geomod.nearest_city(*pts[idx]))
        self._emit_ring()

    def _emit_ring(self) -> None:
        snap = self.ring.snapshot()
        if snap is None:
            return
        sig = (snap["ring_id"], len(snap["members"]), snap["victims"], snap["inflow_bucket"])
        if sig == self._ring_sig:
            return
        self._ring_sig = sig
        self.emit(
            "ring.updated", ring_id=snap["ring_id"], members=len(snap["members"]),
            victims=snap["victims"], inflow_bucket=snap["inflow_bucket"],
        )  # fmt: skip

    # ---------------------------------------------------------- analyst + antibody
    async def _confirm(self, actor: str, role: str, *, resolve_hold: bool) -> None:
        async with self._confirm_lock:
            if self.confirmed or self.shared_hold is None or self.harness is None:
                return
            h = self.harness
            _, txn_id, slot = self.shared_hold
            h.clock.t = max(h.clock.t, self.plan.t0 + timedelta(seconds=self.st.t))
            hold = await h.banks[slot].holds.get(txn_id)
            if hold is not None and hold.state == "released":
                self.confirmed = True
                self.step("analyst_released")
                return
            self.confirmed = True
            if resolve_hold:
                self.step("analyst_confirms")
            t_pub = time.perf_counter()
            res = await h.hub.submit(
                "mule_account", self.meta.shared_mule_payee_hash, "bank_a", actor, EVIDENCE_REF,
                False, role=role,
            )  # fmt: skip
            ab_id = res.record["antibody_id"]
            self.ring.mark_antibody(self.meta.shared_mule_payee_hash)
            self.emit(
                "antibody.published", antibody_ref=ab_id[:16], antibody_kind=res.record["kind"],
                bank="bank_a",
            )  # fmt: skip
            self.step("antibody_shared")
            bank_b = h.banks["bank_b"]
            try:
                await asyncio.wait_for(bank_b.applied_event(ab_id).wait(), ANTIBODY_WAIT_S)
                lat = (bank_b.applied_at[ab_id] - t_pub) * 1000.0
                self.emit(
                    "antibody.applied", antibody_ref=ab_id[:16], bank="bank_b",
                    latency_ms=round(lat, 1),
                )  # fmt: skip
            except TimeoutError:
                log.warning("antibody not applied at bank_b within %.0fs", ANTIBODY_WAIT_S)
            self.flush_holds()  # late-antibody upgrades of pending transfers
            self._emit_ring()
            if resolve_hold:
                try:
                    await h.banks[slot].holds.resolve(txn_id, "confirm_block", actor, role=role)
                except (HoldConflict, HoldNotFound):
                    pass
                self.flush_holds()

    # -------------------------------------------------------------------- main
    def _push(self, it: Item) -> None:
        self._seq += 1
        heapq.heappush(self._heap, (it.key, self._seq, it))

    async def setup(self) -> None:
        e = self.e

        def build() -> Harness:  # SQLite schema creation: off the event loop
            self.workdir.mkdir(parents=True, exist_ok=True)
            return Harness(
                self.workdir, e.txn_scorer, e.call_scorer, e.signer, e.gateway_secret,
                self.plan.t0, self.on_hold,
            )  # fmt: skip

        # keep the future on the run: a cancelled setup must still be awaited by teardown, or
        # the orphaned thread recreates the work dir and stores after they were removed
        self.harness_future = asyncio.ensure_future(asyncio.to_thread(build))
        self.harness = await asyncio.shield(self.harness_future)
        await self.harness.open()
        h = self.harness
        sc, meta = self.plan.scenario, self.meta
        vt = sorted(
            (t for t in sc.txns if sc.campaign.txn_roles[t.txn_id] == "victim_transfer"),
            key=lambda t: t.ts,
        )
        a_first = next(t for t in vt if t.payer_token == meta.victim_a_token)
        b_first = next(t for t in vt if t.payer_token == meta.victim_b_token)
        await asyncio.to_thread(self._warm_all, h, a_first, b_first)
        for it in self.plan.items:
            self._push(it)

    def _warm_all(self, h: Harness, a_first: Transaction, b_first: Transaction) -> None:
        from datetime import timedelta as td
        from decimal import Decimal

        def warm(history: Any, payer: str, device: str, first_ts: datetime) -> None:
            for i in range(40):
                history.record_txn(
                    Transaction(
                        txn_id=f"warm-{payer}-{i}", idempotency_key=f"warm-{payer}-{i}",
                        bank_id="b", payer_token=payer, payee_hash=f"{i % 4:064x}", rail="UPI",
                        amount_inr=Decimal(300 + (i * 37) % 400),
                        ts=first_ts - td(days=10) + td(hours=i * 5),
                        payee_account_age_days=900, device_id_token=device,
                    )
                )  # fmt: skip

        warm(
            h.banks["bank_a"].history, self.meta.victim_a_token, a_first.device_id_token, a_first.ts
        )
        warm(
            h.banks["bank_b"].history, self.meta.victim_b_token, b_first.device_id_token, b_first.ts
        )
        for t in self.plan.history:  # benign history for the noise payers
            h.banks[bank_slot(t.bank_id, self.meta)].history.record_txn(t)

    async def execute(self) -> None:
        st = self.st
        try:
            try:
                await self.setup()
                self.emit(
                    "scenario.started", seed=st.seed, mode=st.mode,
                    duration=round(st.duration, 3), simulated=True,
                    banks=dict(st.banks), scenario_kind=SCENARIO_KIND,
                    scenario_note=SCENARIO_NOTE,
                )  # fmt: skip
                if not self.fast:
                    st.status = "running" if self.running.is_set() else "paused"
                while self._heap:
                    _, _, it = heapq.heappop(self._heap)
                    await self.advance_to(it.t)
                    if not self.fast:
                        await self._gate()
                    if it.kind == "call":
                        await self.process_call(it.obj)
                    elif it.kind == "txn":
                        await self.process_txn(it)
                    elif it.kind == "observe":
                        await self.process_observe(it)
                    elif it.kind == "confirm":
                        await self._confirm(ANALYST_ACTOR, "analyst", resolve_hold=True)
                    await self._apply_manual(inclusive=True)
                    await self.sync_ledger()
                if self.fast:
                    await self._leave_fast()
                self.step("evidence_sealed")
                await self.sync_ledger()
                # what the ledger's maintenance task would do: sign a checkpoint at the head
                await asyncio.to_thread(self.harness.ledger_store.checkpoint_if_due, 0.0)  # type: ignore[union-attr]
                head = await asyncio.to_thread(self.harness.ledger_store.head)  # type: ignore[union-attr]
                self.finished_summary = {
                    "reason": "completed", "ledger_entries": head.seq,
                    "antibody_published": self.st.counters["antibodies_published"] > 0,
                    "victim_b_protected": "bank_b_blocks" in self._step_seen,
                    "latency_ms": self._latency_ms(),
                }  # fmt: skip
                self.emit("scenario.finished", **self.finished_summary)
                st.status = "finished"
            except _Stopped:
                self.emit("scenario.finished", reason="stopped")
                st.status = "stopped"
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error("scenario run failed (%s)", type(e).__name__, exc_info=True)
                self.emit("scenario.finished", reason="error")
                st.status = "error"
        finally:
            self.done.set()
            self.ready.set()
            if self.fast:  # cancelled while replaying: leave subscribers with a coherent state
                self.fast = False

    # ------------------------------------------------------------- REST views
    async def hold_view(self, ref: str) -> dict[str, Any] | None:
        meta = self.hold_meta.get(ref)
        h = self.harness
        if meta is None or h is None:
            return None
        bank = h.banks[meta["bank"]]
        hold = await bank.holds.get(meta["txn_id"])
        if hold is None:
            return None
        t0 = self.plan.t0
        return {
            "txn_ref": ref, "bank": meta["bank"], "bank_name": meta.get("bank_name"),
            "state": hold.state, "decision": hold.decision, "decision_seq": hold.decision_seq,
            "score": round(hold.score, 4), "rail": hold.rail,
            "reasons": _reasons(hold.reasons, hold.amount_bucket),
            "amount_bucket": hold.amount_bucket, "amount_inr": meta.get("amount_inr"),
            "payee_ref": meta.get("payee_ref"), "hero": bool(meta.get("hero")),
            "created_t": meta.get("created_t"),
            "deadline_t": round((hold.deadline - t0).total_seconds(), 3),
            "deadline": _iso(hold.deadline),
            "overdue": bank.holds.is_overdue(hold),
            "resolved_by": hold.resolved_by,
            "resolved_at": (
                _iso(hold.resolved_at)
                if hold.resolved_at
                else None
            ),
            "call_ref": hold.call_ref or None,
        }  # fmt: skip

    async def hold_views(self, state: str = "open") -> list[dict[str, Any]]:
        views = [v for ref in list(self.hold_meta) if (v := await self.hold_view(ref)) is not None]
        if state == "open":
            views = [v for v in views if v["state"] == "open"]
        return sorted(views, key=lambda v: (v["deadline_t"], v["txn_ref"]))

    async def antibody_views(self, state: str = "active") -> list[dict[str, Any]]:
        h = self.harness
        if h is None:
            return []
        rows = await asyncio.to_thread(h.hub_store.listing, state, 200)
        now = h.clock()
        out = []
        for r in rows:
            expired = r["expires_at"] <= now
            out.append(
                {
                    "antibody_ref": r["antibody_id"][:16], "kind": r["kind"],
                    "source_bank": r["source_bank"],
                    "state": "revoked" if r["revoked"] else ("expired" if expired else "active"),
                    "key_hash_prefix": r["key_hash"][:8],
                    "created_at": _iso(r["created_at"]),
                    "expires_at": _iso(r["expires_at"]),
                }
            )  # fmt: skip
        return out

    def default_case_refs(self) -> list[str]:
        """Case refs for the hero case: victim A's first transfer, the shared mule payee, and
        A's alerted call (the same refs the e2e audit uses)."""
        from svckit.ledger import payee_ref

        sc, meta = self.plan.scenario, self.meta
        a_txns = sorted(
            (
                t
                for t in sc.txns
                if sc.campaign.txn_roles[t.txn_id] == "victim_transfer"
                and t.payer_token == meta.victim_a_token
            ),
            key=lambda t: t.ts,
        )
        call_id = (
            self.first_alert_call_id
            or min(
                (e for e in sc.calls if e.victim_token == meta.victim_a_token), key=lambda e: e.ts
            ).call_id
        )
        return [
            txn_ref(a_txns[0].txn_id),
            payee_ref(meta.shared_mule_payee_hash),
            call_ref(call_id),
        ]

    def _latency_ms(self) -> float | None:
        h = self.harness
        if h is None:
            return None
        for ev in reversed(self.e.log.history):
            if ev["kind"] == "antibody.applied":
                return float(ev["latency_ms"])
        return None


_MODELS: tuple[Any, ...] | None = None
_MODELS_LOCK = threading.Lock()


def _shared_models() -> tuple[World, TxnScorer, CallScorer, dict[str, tuple[Any, ...]]]:
    """World, scorers and token->city table, built once per process (immutable afterwards)."""
    global _MODELS
    with _MODELS_LOCK:
        if _MODELS is None:
            world = build_world(WORLD_SEED, WORLD_CITIZENS)
            cities = {
                world.payer_token(c.citizen_id): geomod.city_for(c.state, c.district, c.city)
                for c in world.citizens
            }
            _MODELS = (world, TxnScorer(), CallScorer(load_classifier()), cities)
        return _MODELS  # type: ignore[return-value]


class Engine:
    """Process-wide owner of the current run, the event log and the shared models."""

    def __init__(
        self, settings: Settings, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    ) -> None:  # fmt: skip
        self.settings = settings
        self.sleep = sleep
        self.log = EventLog(
            settings.history_size, settings.client_queue_size, settings.max_ws_clients
        )
        self.state = RunState()
        self.run: Run | None = None
        self.task: asyncio.Task[None] | None = None
        self.plan: Plan | None = None
        self.runs: deque[dict[str, Any]] = deque(maxlen=settings.runs_kept)
        self._lock = asyncio.Lock()
        self.world: World | None = None
        self.txn_scorer: TxnScorer | None = None
        self.call_scorer: CallScorer | None = None
        self.token_city: dict[str, tuple[str, str, float, float, float]] = {}
        self.signer = Signer.generate()
        self.gateway_secret = new_gateway_secret()
        self.work_dir = settings.ensure_work_dir()
        self._closed = False
        self._run_dir_n = 0

    @property
    def public_key_hex(self) -> str:
        return self.signer.public_raw.hex()

    async def start(self) -> None:
        """Load the models and the simulated world (once per process) off the event loop."""
        world, txn, call, cities = await asyncio.to_thread(_shared_models)
        self.world, self.txn_scorer, self.call_scorer = world, txn, call
        self.token_city = cities

    # ---------------------------------------------------------------- views
    def last_id(self) -> int:
        return self.state.next_id - 1

    def state_view(self) -> dict[str, Any]:
        v = self.state.view(self.last_id())
        run = self.run
        v["wall_offset"] = round(run.timeline.wall_of(self.state.t), 3) if run else 0.0
        return v

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.last_id(), "t": round(self.state.t, 3), "wall": _utc_iso(),
            "kind": "scenario.state", **self.state_view(),
            "events": self.log.last(self.settings.snapshot_events),
        }  # fmt: skip

    def broadcast_snapshot(self) -> None:
        self.log.broadcast(self.snapshot())

    def subscribe(self) -> Subscriber | None:
        return self.log.subscribe(self.snapshot())

    # ------------------------------------------------------------ run control
    async def _teardown(self) -> None:
        run, task = self.run, self.task
        self.run = self.task = None
        if run is not None:
            run.stop_flag = True
            run.running.set()
            run.wake.set()
        if task is not None:
            task.cancel()
            await asyncio.wait([task])
        if run is not None and run.harness is None and run.harness_future is not None:
            try:  # the build thread cannot be cancelled: wait for it, then close what it made
                run.harness = await asyncio.wait_for(
                    asyncio.shield(run.harness_future), HARNESS_BUILD_WAIT_S
                )
            except Exception as e:
                log.warning("harness build did not complete (%s)", type(e).__name__)
        if run is not None and run.harness is not None:
            await run.harness.close()
        if run is not None:
            await asyncio.to_thread(shutil.rmtree, run.workdir, True)

    def _new_dir(self) -> Path:
        self._run_dir_n += 1
        return self.work_dir / f"run-{self._run_dir_n}"

    def _remember(self) -> None:
        st = self.state
        if st.run_id is None:
            return
        rec = {
            "run_id": st.run_id,
            "seed": st.seed,
            "mode": st.mode,
            "status": st.status,
            "started": st.started_wall,
            "events": st.next_id - 1,
            "pacing": st.pacing,
        }
        for i, r in enumerate(self.runs):
            if r["run_id"] == st.run_id:
                self.runs[i] = rec
                return
        self.runs.append(rec)

    async def start_run(
        self, seed: int = 1, speed: float = 10.0, mode: str = "hero", pacing: str = "real"
    ) -> str:
        """(Re)start: any previous run is fully torn down first; concurrent starts serialise and
        the last one wins, so exactly one run (and one task) exists afterwards."""
        if self.world is None:
            raise RuntimeError("engine not started")
        async with self._lock:
            self._remember()
            await self._teardown()
            plan = await asyncio.to_thread(build_plan, self.world, seed, mode)
            st = self.state
            st.run_id = uuid.uuid4().hex[:12]
            st.seed, st.mode, st.speed = seed, mode, float(speed)
            st.pacing = pacing if pacing in ("real", "cinematic") else "real"
            st.status, st.step, st.epoch = "starting", None, 0
            st.duration = plan.duration
            st.started_wall = _utc_iso()
            st.manual_log = []
            st.banks = {
                "bank_a": plan.meta.victim_a_bank.removeprefix("bank_"),
                "bank_b": plan.meta.victim_b_bank.removeprefix("bank_"),
            }
            st.reset_epoch()
            self.log.reset()
            self._launch(plan, fast_until=None, start_paused=False)
            self._remember()
            return st.run_id

    def _launch(self, plan: Plan, *, fast_until: float | None, start_paused: bool) -> None:
        run = Run(
            self, plan, fast_until=fast_until, start_paused=start_paused, workdir=self._new_dir()
        )
        self.run = run
        self.state.wall_duration = (
            run.timeline.end_wall if run.timeline.pacing == "cinematic" else plan.duration
        )
        self.task = asyncio.create_task(run.execute())
        self.plan = plan

    async def control(
        self, action: str, t: float | None = None, speed: float | None = None,
        wall: float | None = None,
    ) -> dict[str, Any]:  # fmt: skip
        async with self._lock:
            st, run = self.state, self.run
            if speed is not None:
                st.speed = float(min(SPEED_MAX, max(SPEED_MIN, speed)))
                if run is not None:
                    run.wake.set()
            if run is None or st.run_id is None:
                if action in ("pause", "resume", "stop", "seek"):
                    raise LookupError("no run")
            if action == "pause":
                if st.status in ("running", "starting"):
                    run.running.clear()  # type: ignore[union-attr]
                    run.wake.set()  # type: ignore[union-attr]
                    st.status = "paused"
                    self.broadcast_snapshot()
            elif action == "resume":
                if st.status == "paused":
                    run.running.set()  # type: ignore[union-attr]
                    st.status = "running"
                    self.broadcast_snapshot()
            elif action == "stop":
                if st.status in ("running", "paused", "starting", "seeking"):
                    run.stop_flag = True  # type: ignore[union-attr]
                    run.running.set()  # type: ignore[union-attr]
                    run.wake.set()  # type: ignore[union-attr]
                    done = run.done  # type: ignore[union-attr]
                    # release the lock-free wait: the run emits scenario.finished itself
                    await done.wait()
                    self._remember()
                    self.broadcast_snapshot()
            elif action == "seek":
                if (t is None) == (wall is None):
                    raise ValueError("seek needs exactly one of t and wall")
                if t is None:
                    assert wall is not None
                    tl = run.timeline  # type: ignore[union-attr]
                    t = tl.t_of(float(wall))
                await self._seek(float(t))
            else:
                raise ValueError("unknown action")
            return self.state_view()

    async def _seek(self, t: float) -> None:
        st = self.state
        resume = st.status in ("running", "starting")
        t = min(max(0.0, t), st.duration)
        manual = st.manual_log
        await self._teardown()
        plan = await asyncio.to_thread(build_plan, self.world, st.seed, st.mode)  # type: ignore[arg-type]
        st.epoch += 1
        st.reset_epoch()
        st.status = "seeking"
        st.manual_log = manual
        self.log.reset()
        self._launch(plan, fast_until=t, start_paused=not resume)
        assert self.run is not None
        await asyncio.wait_for(self.run.ready.wait(), 60.0)

    async def join(self, timeout: float | None = None) -> None:
        """Wait until the current run finished, was stopped or failed (tests, graceful close)."""
        run = self.run
        if run is not None:
            await asyncio.wait_for(run.done.wait(), timeout)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        async with self._lock:
            self._remember()
            await self._teardown()
        for s in tuple(self.log.subscribers):
            s.dropped = True
        await asyncio.to_thread(shutil.rmtree, self.work_dir, True)
