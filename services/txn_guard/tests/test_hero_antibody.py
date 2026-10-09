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


async def _run_seed(world, seed, scorer, with_antibody: bool):
    sc = build_hero_scenario(world, seed=seed)
    meta = sc.metadata
    vt = sorted((t for t in sc.txns if sc.campaign.txn_roles[t.txn_id] == "victim_transfer"),
                key=lambda t: t.ts)  # fmt: skip
    a_txns = [t for t in vt if t.payer_token == meta.victim_a_token]
    b_first = next(t for t in vt if t.payer_token == meta.victim_b_token)
    clock = Clock(a_txns[0].ts)
    bus = InMemoryBus()
    bank_a, bank_b = _service(scorer, bus, clock), _service(scorer, bus, clock)
    _warm(bank_a.history, meta.victim_a_token, a_txns[0].device_id_token, a_txns[0].ts)
    _warm(bank_b.history, meta.victim_b_token, b_first.device_id_token, b_first.ts)
    task = run_antibody_consumer(bus, bank_b, InMemoryIdempotencyStore(), "bank_b")
    try:
        published_at = None
        first_hold_ts = None
        for t in a_txns:
            clock.t = t.ts
            d = await bank_a.handle_txn(t)
            if first_hold_ts is None and d.decision == "hold_verify":
                first_hold_ts = t.ts
        # the analyst confirms 60 s (sim time) after A's first hold; that must precede B's transfer
        confirmed = first_hold_ts is not None and first_hold_ts + CONFIRM_DELAY <= b_first.ts
        applied_s = None
        if with_antibody and confirmed:
            clock.t = first_hold_ts + CONFIRM_DELAY
            published_at = time.perf_counter()
            await bus.publish(Topics.ANTIBODIES, meta.shared_mule_payee_hash,
                              _mule_antibody(meta.shared_mule_payee_hash, clock.t))  # fmt: skip
            while bank_b.antibodies.lookup(meta.shared_mule_payee_hash) is None:
                assert time.perf_counter() - published_at < 5.0
                await asyncio.sleep(0.002)
            applied_s = time.perf_counter() - published_at
        clock.t = b_first.ts
        d_b: TxnDecision = await bank_b.handle_txn(b_first)
    finally:
        task.cancel()
    return confirmed, d_b, applied_s


async def test_hero_second_bank_victim_is_blocked_end_to_end_across_two_instances(capsys):
    world, scorer = build_world(5, 600), Scorer()
    stats = {"confirmed": 0, "blocked": 0, "control_already_held": 0, "max_apply_s": 0.0}
    for seed in range(1, 41):
        confirmed, with_ab, applied = await _run_seed(world, seed, scorer, True)
        _, control, _ = await _run_seed(world, seed, scorer, False)
        assert confirmed, f"seed {seed}: A was never held >= 60 s before B's first transfer"
        stats["confirmed"] += 1
        assert with_ab.decision == "hold_verify", seed
        assert "ANTIBODY_MATCH" in {r.code for r in with_ab.reasons}, seed
        assert with_ab.score >= control.score  # the antibody never weakens the verdict
        stats["blocked"] += 1
        stats["control_already_held"] += control.decision == "hold_verify"
        assert "ANTIBODY_MATCH" not in {r.code for r in control.reasons}
        stats["max_apply_s"] = max(stats["max_apply_s"], applied or 0.0)
    assert stats["max_apply_s"] < 5.0
    with capsys.disabled():
        print(f"\nhero seeds 1..40: blocked {stats['blocked']}/40 with the antibody; the model alone "
              f"already held B's first transfer in {stats['control_already_held']}/40 "
              f"(max apply latency {stats['max_apply_s'] * 1000:.0f} ms)")  # fmt: skip


@pytest.mark.parametrize("seed", [1, 14])
def test_hero_payee_is_shared_with_victim_a(seed):
    sc = build_hero_scenario(build_world(5, 600), seed=seed)
    assert sc.metadata.shared_mule_payee_hash in sc.metadata.mule_payee_hashes
