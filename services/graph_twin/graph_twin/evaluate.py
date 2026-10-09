"""Evaluation harness on simulator campaigns (needs ``sim-engine``; run ``python -m
graph_twin.evaluate``). Used by the tests and for the README numbers.

Signal model. txn-guard decisions are not needed to evaluate the GRAPH: this harness feeds a
label-free stand-in with the same shape (``hold_verify`` for a payment to an account younger than
30 days of at least INR 20,000; ``step_up`` from INR 10,000). It also produces realistic
false-positive holds on benign traffic (the simulator has 6% young citizen accounts and 20% new
merchants), which the benign-immunity checks rely on.
"""

import json
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from scam_contracts.models import Transaction, TxnDecision

from .graph import account_key
from .twin import GraphTwin

# Disjoint from every training / calibration seed of the other services (txn_guard 101-106,
# 201-203, 9001-9004; call_guard 1000/2000/3000), checked in tests/test_evaluation.py.
EVAL_SEEDS = (7101, 7102, 7103)
SIGNAL_SERVICE_VERSION = "stand-in-rule-v1"


def signal_level(t: Transaction) -> str:
    if t.payee_account_age_days < 30 and t.amount_inr >= Decimal(20_000):
        return "hold_verify"
    if t.payee_account_age_days < 30 and t.amount_inr >= Decimal(10_000):
        return "step_up"
    return "allow"


def make_decision(t: Transaction, level: str, seq: int = 1) -> TxnDecision:
    return TxnDecision(
        txn_id=t.txn_id, decision=level, score=0.9 if level != "allow" else 0.1, reasons=[],
        model_version=SIGNAL_SERVICE_VERSION, ts=t.ts, decision_seq=seq,
    )  # fmt: skip


@dataclass
class Replay:
    detect_ts: dict[str, datetime] = field(default_factory=dict)  # ring_id -> first seen (event ts)
    events: int = 0


def replay(
    twin: GraphTwin,
    clock,  # tests.conftest.Clock-like: has .set(datetime)
    txns: Iterable[Transaction],
    *,
    signal: Callable[[Transaction], str] = signal_level,
    detect_every: int = 1,
) -> Replay:
    """Feed transactions in time order; the clock follows event time; detection runs every
    ``detect_every`` events (1 = after each event, the latency-bound mode)."""
    out = Replay()
    for t in sorted(txns, key=lambda x: (x.ts, x.txn_id)):
        clock.set(t.ts)
        twin.apply_txn(t)
        lvl = signal(t)
        if lvl != "allow":
            twin.apply_decision(make_decision(t, lvl))
        out.events += 1
        if out.events % detect_every == 0:
            twin.detect_dirty()
            for rid in twin.registry.rings:
                out.detect_ts.setdefault(rid, t.ts)
    twin.detect_dirty()
    for rid in twin.registry.rings:
        out.detect_ts.setdefault(rid, clock.now)
    return out


def campaign_truth(camp) -> tuple[set[str], str]:
    """(mule account node keys, cash-out node key) of a simulator campaign."""
    cash = account_key(camp.mule_chain[-1])
    return {account_key(h) for h in camp.mule_chain[:-1]}, cash


def evaluate(seed: int, *, n_citizens: int = 600, days: int = 7, n_campaigns: int = 5,
             victims: int = 25) -> dict:  # fmt: skip
    import numpy as np
    from sim_engine.benign import gen_benign_txns
    from sim_engine.labels import GroundTruth
    from sim_engine.scam import gen_scam_campaign
    from sim_engine.world import build_world

    from .config import Settings
    from .store import GraphStore

    world = build_world(seed, n_citizens)
    benign = list(gen_benign_txns(world, days, seed))
    rng = np.random.default_rng([seed, 14014])
    camps = []
    for k in range(n_campaigns):
        start = world.start + timedelta(
            days=int(rng.integers(2, days - 1)), hours=float(rng.uniform(8, 18))
        )
        camps.append(gen_scam_campaign(world, f"gt{k}", victims, seed, start_ts=start))
    truth = GroundTruth(camps)
    allt = benign + [t for c in camps for t in c.txns]

    class _Clock:
        now = world.start

        def __call__(self):
            return self.now

        def set(self, v):
            self.now = v

    clock = _Clock()
    store = GraphStore("sqlite://")
    twin = GraphTwin(store, Settings(), clock)
    rep = replay(twin, clock, allt)
    gt_members: dict[str, set[str]] = {}
    for c in camps:
        mules, cash = campaign_truth(c)
        gt_members[c.campaign_id] = mules | {cash}
    all_gt = set().union(*gt_members.values())
    rings = list(twin.registry.rings.values())
    detected = set()
    tp = fp = 0
    per_campaign = {}
    for r in rings:
        mem = {n for n, role in r.members.items() if role in ("mule", "cash_out")}
        tp += len(mem & all_gt)
        fp += len(mem - all_gt)
        detected |= mem
    for c in camps:
        gt = gt_members[c.campaign_id]
        hit = [r for r in rings if r.core & gt]
        found = set().union(*[set(r.members) for r in hit]) if hit else set()
        det_ts = min((rep.detect_ts[r.ring_id] for r in hit), default=None)
        vfirst = sorted(c.victim_first_txn_ts)
        before = sum(1 for v in vfirst if det_ts is not None and v <= det_ts)
        vt = [t for t in c.txns if c.txn_roles[t.txn_id] == "victim_transfer"]
        total_inr = sum((t.amount_inr for t in vt), Decimal(0))
        after_inr = sum(
            (t.amount_inr for t in vt if det_ts is not None and t.ts > det_ts), Decimal(0)
        )
        per_campaign[c.campaign_id] = {
            "rings": len(hit), "recall": round(len(found & gt) / len(gt), 3),
            "victims_before_detection": before if det_ts is not None else None,
            "victims_total": len(vfirst),
            "inr_after_detection_pct": (
                round(float(100 * after_inr / total_inr), 1) if det_ts is not None else None
            ),
            "detected_at_victim_no": before,
        }  # fmt: skip
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = len(detected & all_gt) / len(all_gt)
    benign_rings = sum(1 for r in rings if not (r.core & all_gt))
    store.close()
    return {
        "seed": seed, "events": rep.events, "campaigns": n_campaigns, "rings": len(rings),
        "benign_rings": benign_rings, "member_precision": round(precision, 4),
        "member_recall": round(recall, 4), "per_campaign": per_campaign,
        "txn_ground_truth_check": all(truth.campaign_of(t.txn_id) for c in camps for t in c.txns),
    }  # fmt: skip


if __name__ == "__main__":
    for s in EVAL_SEEDS if len(sys.argv) < 2 else [int(a) for a in sys.argv[1:]]:
        print(json.dumps(evaluate(s), indent=1, sort_keys=True))
