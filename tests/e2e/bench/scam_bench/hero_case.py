"""Hero scenario evaluation: the constructed HARD case (seasoned shared mule, undetected call).

Victim A (bank A) is robbed through a templated digital-arrest call that call-guard catches; A's
largest transfer, to a seasoned mule account (60-400 days old), is held on A's own strong signals
(call risk + anomalous amount). Victim B (bank B, another state) is called with an off-template
script the shipped call-guard does not alert on and, 90 s after A's last transfer, pays the SAME
seasoned mule. Nothing local flags B: no call risk, no young-payee signal. Only the shared threat
memory can.

This is a constructed case, not a prevalence estimate. The analyst confirmation model is the
benchmark's (label-free here: the first hold of the transfer to the mule is confirmed 60 s later).
"""

import hashlib
from datetime import timedelta
from decimal import Decimal
from typing import Any

from scam_contracts.models import Antibody, Transaction, TxnDecision
from svckit.bus import InMemoryBus
from svckit.idempotency import InMemoryIdempotencyStore
from txn_guard.antibody_cache import AntibodyLookup, InMemoryAntibodyCache
from txn_guard.history import InMemoryHistoryStore
from txn_guard.holds import InMemoryAuditSink, InMemoryHoldStore
from txn_guard.model import Scorer
from txn_guard.service import TxnGuardService

CONFIRM_DELAY = timedelta(seconds=60)


class _Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _warm(history, payer: str, device: str, first_ts, n: int = 40) -> None:
    for i in range(n):
        history.record_txn(
            Transaction(
                txn_id=f"warm-{payer}-{i}", idempotency_key=f"warm-{payer}-{i}", bank_id="b",
                payer_token=payer, payee_hash=f"{i % 4:064x}", rail="UPI",
                amount_inr=Decimal(300 + (i * 37) % 400),
                ts=first_ts - timedelta(days=10) + timedelta(hours=i * 5),
                payee_account_age_days=900, device_id_token=device,
            )
        )  # fmt: skip


async def _call_guard(sc) -> list:
    from call_guard.model import Scorer as CallScorer
    from call_guard.model import load_classifier
    from call_guard.session import InMemorySessionStore, SessionScorer

    sessions = SessionScorer(InMemorySessionStore(), CallScorer(load_classifier()))
    out = []
    for e in sorted(sc.calls, key=lambda e: e.ts):
        _, pending = await sessions.update_with_crossing(e)
        if pending:
            r = await sessions.crossing_risk(e)
            if r is not None:
                await sessions.mark_published(e.call_id, await sessions.crossing_no(e.call_id))
                out.append(r)
    return out


async def _one(world, seed: int, scorer: Scorer, antibody: bool) -> dict[str, Any]:
    from sim_engine.replay import build_hero_scenario

    sc = build_hero_scenario(world, seed=seed)
    meta = sc.metadata
    risks = await _call_guard(sc)
    vt = sorted((t for t in sc.txns if sc.campaign.txn_roles[t.txn_id] == "victim_transfer"),
                key=lambda t: t.ts)  # fmt: skip
    a_txns = [t for t in vt if t.payer_token == meta.victim_a_token]
    b_first = next(t for t in vt if t.payer_token == meta.victim_b_token)
    clock = _Clock(a_txns[0].ts)
    svc_a = _service(scorer, clock)
    svc_b = _service(scorer, clock)
    _warm(svc_a.history, meta.victim_a_token, a_txns[0].device_id_token, a_txns[0].ts)
    _warm(svc_b.history, meta.victim_b_token, b_first.device_id_token, b_first.ts)
    a_risks = [r for r in risks if r.victim_token == meta.victim_a_token]
    events = sorted([(r.ts, 0, r) for r in a_risks] + [(t.ts, 1, t) for t in a_txns],
                    key=lambda x: (x[0], x[1]))  # fmt: skip
    shared_hold_ts = None
    for ts, kind, obj in events:
        clock.t = ts
        if kind == 0:
            await svc_a.handle_call_risk(obj)
        else:
            d = await svc_a.handle_txn(obj)
            if d.decision == "hold_verify" and obj.payee_hash == meta.shared_mule_payee_hash:
                shared_hold_ts = shared_hold_ts or obj.ts
    due = shared_hold_ts + CONFIRM_DELAY if shared_hold_ts else None
    if antibody and due is not None and due < b_first.ts:
        clock.t = due
        await svc_b.handle_antibody(
            Antibody(
                antibody_id=hashlib.sha256(meta.shared_mule_payee_hash.encode()).hexdigest(),
                kind="mule_account", key_hash=meta.shared_mule_payee_hash, source_bank="bank_a",
                confirmed_by="analyst", created_at=due, expires_at=due + timedelta(days=14),
            )
        )  # fmt: skip
    clock.t = b_first.ts
    d_b: TxnDecision = await svc_b.handle_txn(b_first)
    return {
        "decision": d_b.decision,
        "match": "ANTIBODY_MATCH" in {r.code for r in d_b.reasons},
        "b_alerts": sum(r.victim_token == meta.victim_b_token for r in risks),
        "a_alerts": len(a_risks),
        "shared_held": shared_hold_ts is not None,
        "due_before_b": due is not None and due < b_first.ts,
        "gap_s": (b_first.ts - max(t.ts for t in a_txns)).total_seconds(),
        "age_days": meta.shared_mule_age_days,
    }


def _service(scorer: Scorer, clock) -> TxnGuardService:
    return TxnGuardService(
        scorer, InMemoryHistoryStore(),
        InMemoryHoldStore(audit=InMemoryAuditSink(), clock=clock), InMemoryBus(),
        InMemoryIdempotencyStore(), clock=clock,
        antibodies=AntibodyLookup(InMemoryAntibodyCache(clock=clock)),
    )  # fmt: skip


async def run_hero_cases(n_seeds: int = 40, world_seed: int = 5) -> dict[str, Any]:
    from sim_engine.world import build_world

    world, scorer = build_world(world_seed, 600), Scorer()
    model_only = {"allow": 0, "step_up": 0, "hold_verify": 0}
    with_ab = {"allow": 0, "step_up": 0, "hold_verify": 0}
    n = {"b_alerts": 0, "a_alerts": 0, "shared_held": 0, "due_before_b": 0, "match": 0, "gap90": 0}
    ages: list[int] = []
    for seed in range(1, n_seeds + 1):
        c = await _one(world, seed, scorer, False)
        a = await _one(world, seed, scorer, True)
        model_only[c["decision"]] += 1
        with_ab[a["decision"]] += 1
        n["b_alerts"] += c["b_alerts"]
        n["a_alerts"] += c["a_alerts"] >= 1
        n["shared_held"] += c["shared_held"]
        n["due_before_b"] += c["due_before_b"]
        n["match"] += a["match"]
        n["gap90"] += c["gap_s"] == 90.0
        ages.append(c["age_days"])
    return {"seeds": n_seeds, "model_only": model_only, "with_antibody": with_ab, "counts": n,
            "age_range": (min(ages), max(ages)) if ages else (0, 0)}  # fmt: skip
