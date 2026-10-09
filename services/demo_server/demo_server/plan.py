"""Scenario plan: the ordered timeline (in simulated seconds) a run plays.

Pure and deterministic in (world, seed, mode); CPU-bound, so callers run it in a thread.

* ``hero``: the sim-engine hero campaign only (calls, victim transfers, mule forwards).
* ``hero+noise``: additionally ``N_NOISE_TXNS`` benign transactions and ``N_NOISE_CALLS`` benign
  calls from the simulator, re-timed (deterministically) across the scenario window so the console
  looks alive; the benign history before the window is recorded into the banks' history stores
  first so the guards are not scoring cold payers.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from scam_contracts.models import CallEvent, Transaction
from sim_engine.benign import gen_benign_txns
from sim_engine.calls import gen_benign_calls
from sim_engine.replay import HeroMetadata, Scenario, build_hero_scenario
from sim_engine.world import World

N_NOISE_TXNS = 300
N_NOISE_CALLS = 12
MODES = ("hero", "hero+noise")
ORDER_CALL, ORDER_TXN, ORDER_OBSERVE = 0, 1, 2


@dataclass(frozen=True)
class Item:
    t: float
    order: int
    idx: int
    kind: str  # call | txn | observe
    obj: Any
    noise: bool = False
    role: str | None = None  # campaign role of a txn (victim_transfer / mule_forward)

    @property
    def key(self) -> tuple[float, int, int]:
        return (self.t, self.order, self.idx)


@dataclass
class Plan:
    seed: int
    mode: str
    scenario: Scenario
    meta: HeroMetadata
    t0: datetime
    items: list[Item]
    duration: float
    history: list[Transaction] = field(default_factory=list)  # benign, before the window


def bank_slot(bank_id: str, meta: HeroMetadata) -> str:
    """Which of the two demo bank instances scores a transaction of ``bank_id``."""
    if bank_id == meta.victim_a_bank:
        return "bank_a"
    if bank_id == meta.victim_b_bank:
        return "bank_b"
    return "bank_a" if hashlib.sha256(bank_id.encode()).digest()[0] & 1 == 0 else "bank_b"


def build_plan(world: World, seed: int, mode: str) -> Plan:
    if mode not in MODES:
        raise ValueError("unknown mode")
    sc = build_hero_scenario(world, seed=seed)
    meta = sc.metadata
    assert meta is not None
    t0 = min(e.ts for e in sc.calls)
    raw: list[tuple[datetime, int, str, Any, bool, str | None]] = []
    roles = sc.campaign.txn_roles
    for e in sc.calls:
        raw.append((e.ts, ORDER_CALL, "call", e, False, None))
    for t in sc.txns:
        role = roles[t.txn_id]
        scored = role == "victim_transfer"
        raw.append(
            (t.ts, ORDER_TXN if scored else ORDER_OBSERVE, "txn" if scored else "observe", t,
             False, role)
        )  # fmt: skip
    history: list[Transaction] = []
    if mode == "hero+noise":
        end = max(r[0] for r in raw)
        raw.extend(_noise(world, seed, meta, sc, t0, end, history))
    raw.sort(key=lambda r: (r[0], r[1], r[3].call_id if r[2] == "call" else r[3].txn_id))
    items = [
        Item((ts - t0).total_seconds(), order, i, kind, obj, noise, role)
        for i, (ts, order, kind, obj, noise, role) in enumerate(raw)
    ]
    duration = max(it.t for it in items)
    return Plan(seed, mode, sc, meta, t0, items, duration, history)


def _noise(
    world: World,
    seed: int,
    meta: HeroMetadata,
    sc: Scenario,
    t0: datetime,
    end: datetime,
    history: list[Transaction],
) -> list[tuple[datetime, int, str, Any, bool, str | None]]:
    rng = np.random.default_rng([seed, 4242])
    skip = {meta.victim_a_token, meta.victim_b_token, *sc.campaign.mule_payer_tokens}
    # benign traffic for the scenario's calendar day and the day before
    day0 = world.start
    days = max(2, (t0.date() - day0.date()).days + 1)
    txns = [t for t in gen_benign_txns(world, days, seed) if t.payer_token not in skip]
    before = [t for t in txns if t.ts < t0]
    after = [t for t in txns if t.ts >= t0]
    history.extend(before)
    out: list[tuple[datetime, int, str, Any, bool, str | None]] = []
    span = (end - t0).total_seconds()
    if after and span > 0:
        pick = np.linspace(0, len(after) - 1, num=min(N_NOISE_TXNS, len(after)))
        for slot, j in enumerate(pick):
            frac = (slot + float(rng.uniform(0.1, 0.9))) / len(pick)
            t = after[int(j)]
            ts = t0 + timedelta(seconds=min(span, frac * span))
            out.append((ts, ORDER_TXN, "txn", t.model_copy(update={"ts": ts}), True, None))
    calls = [c for c in gen_benign_calls(world, days, seed) if c.victim_token not in skip]
    by_call: dict[str, list[CallEvent]] = {}
    for c in calls:
        by_call.setdefault(c.call_id, []).append(c)
    ids = sorted(by_call, key=lambda cid: hashlib.sha256(f"{seed}:{cid}".encode()).hexdigest())
    for n, cid in enumerate(ids[:N_NOISE_CALLS]):
        evs = sorted(by_call[cid], key=lambda e: e.ts)
        start = t0 + timedelta(seconds=float(rng.uniform(0, max(1.0, span * 0.9))) + n * 0.001)
        base = evs[0].ts
        for e in evs:
            ts = start + (e.ts - base)
            out.append((ts, ORDER_CALL, "call", e.model_copy(update={"ts": ts}), True, None))
    return out
