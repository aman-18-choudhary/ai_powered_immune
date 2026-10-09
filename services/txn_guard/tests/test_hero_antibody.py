"""Hero scenario end-to-end across two txn-guard instances (bank_a, bank_b) and the antibody
topic: victim B's first transfer goes to a mule victim A already paid."""

import asyncio
import hashlib
import time
from datetime import timedelta
from decimal import Decimal

import pytest
from scam_contracts.models import Antibody, Transaction, TxnDecision
from scam_contracts.topics import Topics
from sim_engine.replay import build_hero_scenario
from sim_engine.world import build_world
from svckit.bus import InMemoryBus
from svckit.idempotency import InMemoryIdempotencyStore

from txn_guard.antibody_cache import AntibodyLookup, InMemoryAntibodyCache
from txn_guard.consumer import run_antibody_consumer
from txn_guard.history import InMemoryHistoryStore
from txn_guard.holds import InMemoryAuditSink, InMemoryHoldStore
from txn_guard.model import Scorer
from txn_guard.service import TxnGuardService

CONFIRM_DELAY = timedelta(seconds=60)


class Clock:
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
                amount_inr=Decimal(300 + (i * 37) % 400), ts=first_ts - timedelta(days=10) + timedelta(hours=i * 5),
                payee_account_age_days=900, device_id_token=device,
            )
        )  # fmt: skip


def _service(scorer, bus, clock):
    cache = InMemoryAntibodyCache(clock=clock)
    return TxnGuardService(
        scorer, InMemoryHistoryStore(), InMemoryHoldStore(audit=InMemoryAuditSink(), clock=clock),
        bus, InMemoryIdempotencyStore(), clock=clock, antibodies=AntibodyLookup(cache),
    )  # fmt: skip


def _mule_antibody(payee_hash: str, ts) -> Antibody:
    return Antibody(antibody_id=hashlib.sha256(payee_hash.encode()).hexdigest(), kind="mule_account",
                    key_hash=payee_hash, source_bank="bank_a", confirmed_by="analyst-1",
                    created_at=ts, expires_at=ts + timedelta(days=14))  # fmt: skip


async def _call_guard_risks(sc):
    """Run the REAL call-guard over the scenario's call events; returns (alerts, per-call peak)."""
    from call_guard.model import Scorer as CallScorer
    from call_guard.model import load_classifier
    from call_guard.session import InMemorySessionStore, SessionScorer

    sessions = SessionScorer(InMemorySessionStore(), CallScorer(load_classifier()))
    risks, peak = [], {}
    for e in sorted(sc.calls, key=lambda e: e.ts):
        risk, pending = await sessions.update_with_crossing(e)
        peak[e.victim_token] = max(peak.get(e.victim_token, 0.0), risk.score)
        if pending:
            published = await sessions.crossing_risk(e)
            if published is not None:
                await sessions.mark_published(e.call_id, await sessions.crossing_no(e.call_id))
                risks.append(published)
    return risks, peak


async def _run_seed(world, seed, scorer, with_antibody: bool):
    sc = build_hero_scenario(world, seed=seed)
    meta = sc.metadata
    risks, peak = await _call_guard_risks(sc)
    vt = sorted((t for t in sc.txns if sc.campaign.txn_roles[t.txn_id] == "victim_transfer"),
                key=lambda t: t.ts)  # fmt: skip
    a_txns = [t for t in vt if t.payer_token == meta.victim_a_token]
    b_first = next(t for t in vt if t.payer_token == meta.victim_b_token)
    clock = Clock(a_txns[0].ts)
    bus = InMemoryBus()
    bank_a, bank_b = _service(scorer, bus, clock), _service(scorer, bus, clock)
    _warm(bank_a.history, meta.victim_a_token, a_txns[0].device_id_token, a_txns[0].ts)
    _warm(bank_b.history, meta.victim_b_token, b_first.device_id_token, b_first.ts)
    a_risks = [r for r in risks if r.victim_token == meta.victim_a_token]
    b_risks = [r for r in risks if r.victim_token == meta.victim_b_token]
    events = sorted([(r.ts, 0, r) for r in a_risks] + [(t.ts, 1, t) for t in a_txns],
                    key=lambda x: (x[0], x[1]))  # fmt: skip
    task = run_antibody_consumer(bus, bank_b, InMemoryIdempotencyStore(), "bank_b")
    info = {
        "b_alerts": len(b_risks),
        "a_alerts": len(a_risks),
        "b_peak": peak.get(meta.victim_b_token, 0.0),
    }
    try:
        held: list[tuple] = []  # A's held victim transfers: (ts, payee)
        for ts, kind, obj in events:
            clock.t = ts
            if kind == 0:
                await bank_a.handle_call_risk(obj)
            else:
                d = await bank_a.handle_txn(obj)
                if d.decision == "hold_verify":
                    held.append((obj.ts, obj.payee_hash))
        # the analyst confirms the payee of the held transfer to the shared mule 60 s after the hold
        shared_hold = next((h for h in held if h[1] == meta.shared_mule_payee_hash), None)
        info["shared_held"] = shared_hold is not None
        due = shared_hold[0] + CONFIRM_DELAY if shared_hold else None
        info["lead_ok"] = due is not None and due < b_first.ts  # antibody due before B's transfer
        info["gap_s"] = (b_first.ts - max(t.ts for t in a_txns)).total_seconds()
        applied_s = None
        if with_antibody and due is not None:
            clock.t = due
            t0 = time.perf_counter()
            await bus.publish(Topics.ANTIBODIES, meta.shared_mule_payee_hash,
                              _mule_antibody(meta.shared_mule_payee_hash, due))  # fmt: skip
            while bank_b.antibodies.lookup(meta.shared_mule_payee_hash) is None:
                assert time.perf_counter() - t0 < 5.0
                await asyncio.sleep(0.002)
            applied_s = time.perf_counter() - t0
        clock.t = b_first.ts
        d_b: TxnDecision = await bank_b.handle_txn(b_first)
    finally:
        task.cancel()
    info["applied_s"] = applied_s
    return info, d_b


async def test_hero_hard_case_only_the_shared_threat_memory_saves_victim_b(capsys):
    """Seasoned shared mule (>= 60 days) + B's call undetected by the shipped call-guard: the model
    plus policy alone does not hold B's first transfer; with the antibody (A's hold confirmed 60 s
    later, applied across two instances) it is hold_verify with ANTIBODY_MATCH in 40/40 seeds."""
    world, scorer = build_world(5, 600), Scorer()
    dist = {"allow": 0, "step_up": 0, "hold_verify": 0}
    blocked = shared_held = lead_ok = 0
    max_apply = 0.0
    for seed in range(1, 41):
        info_c, control = await _run_seed(world, seed, scorer, False)
        info, with_ab = await _run_seed(world, seed, scorer, True)
        assert info["b_alerts"] == 0 and info["b_peak"] < 0.7, (seed, info)  # call-guard misses B
        assert info["a_alerts"] >= 1, (seed, info)  # but catches A's templated call
        assert info["gap_s"] == 90.0
        assert info["shared_held"] and info["lead_ok"], (seed, info)
        shared_held += 1
        lead_ok += 1
        dist[control.decision] += 1
        assert "ANTIBODY_MATCH" not in {r.code for r in control.reasons}
        assert with_ab.decision == "hold_verify", (seed, control.decision)
        assert "ANTIBODY_MATCH" in {r.code for r in with_ab.reasons} and with_ab.score >= 0.9
        blocked += 1
        max_apply = max(max_apply, info["applied_s"])
        del info_c
    assert max_apply < 5.0
    not_held = dist["allow"] + dist["step_up"]
    assert not_held >= 36, dist  # >= 90% of seeds: the model alone does not hold B
    with capsys.disabled():
        print(f"\nhero hard case seeds 1..40: model-only B first transfer {dist}; with antibody "
              f"hold_verify+ANTIBODY_MATCH {blocked}/40; A's shared-mule transfer held {shared_held}/40; "
              f"antibody due before B {lead_ok}/40; max apply {max_apply * 1000:.0f} ms")  # fmt: skip


@pytest.mark.parametrize("seed", [1, 14])
def test_hero_payee_is_shared_with_victim_a(seed):
    sc = build_hero_scenario(build_world(5, 600), seed=seed)
    assert sc.metadata.shared_mule_payee_hash in sc.metadata.mule_payee_hashes
