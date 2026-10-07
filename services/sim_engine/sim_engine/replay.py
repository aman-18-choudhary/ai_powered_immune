"""Scenario replay: publish synthetic calls and transactions to the bus in time order."""

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np
from pydantic import BaseModel
from scam_contracts.models import CallEvent, Transaction
from scam_contracts.topics import Topics
from svckit.bus import Bus

from .scam import Campaign, gen_scam_campaign
from .world import World

HERO_CAMPAIGN_ID = "hero-digital-arrest"
HERO_GAP = timedelta(seconds=90)  # victim B's transfer starts this long after A's completes

SleepFn = Callable[[float], Awaitable[None]]


class HeroMetadata(BaseModel):
    """What the e2e test needs to assert on the hero scenario (all event-safe values)."""

    campaign_id: str
    victim_a_token: str
    victim_b_token: str
    victim_a_bank: str
    victim_b_bank: str
    victim_a_state: str
    victim_b_state: str
    mule_payee_hashes: list[str]
    cashout_payee_hash: str
    victim_b_gap_s: float


@dataclass
class Scenario:
    name: str
    campaign: Campaign
    metadata: HeroMetadata | None = None
    calls: list[CallEvent] = field(default_factory=list)
    txns: list[Transaction] = field(default_factory=list)


def build_hero_scenario(world: World, seed: int = 1) -> Scenario:
    """One campaign; victim A and B at different banks and in different states; B's first
    transfer starts exactly 90 s after A's last transfer, so an antibody published in
    between can block it. Deterministic per (world, seed)."""
    rng = np.random.default_rng([seed, 90])
    order = [int(i) for i in rng.permutation(len(world.citizens))]
    a_i = order[0]
    a = world.citizens[a_i]
    b_i = next(
        i for i in order[1:]
        if world.citizens[i].bank_id != a.bank_id and world.citizens[i].state != a.state
    )  # fmt: skip
    b = world.citizens[b_i]
    camp = gen_scam_campaign(
        world, HERO_CAMPAIGN_ID, 2, seed, victim_indices=[a_i, b_i], second_victim_gap=HERO_GAP
    )
    a_tok, b_tok = camp.victim_tokens
    victim_txns = [t for t in camp.txns if camp.txn_roles[t.txn_id] == "victim_transfer"]
    mule_hashes = sorted({t.payee_hash for t in victim_txns})
    meta = HeroMetadata(
        campaign_id=camp.campaign_id,
        victim_a_token=a_tok,
        victim_b_token=b_tok,
        victim_a_bank=a.bank_id,
        victim_b_bank=b.bank_id,
        victim_a_state=a.state,
        victim_b_state=b.state,
        mule_payee_hashes=mule_hashes,
        cashout_payee_hash=camp.mule_chain[-1],
        victim_b_gap_s=HERO_GAP.total_seconds(),
    )
    return Scenario("hero", camp, meta, list(camp.calls), list(camp.txns))


async def replay(
    bus: Bus,
    world: World,
    scenarios: list[Scenario],
    speed: float = 1.0,
    sleep: SleepFn = asyncio.sleep,
) -> None:
    """Publish all scenarios' events merged in timestamp order (calls before txns on ties).

    Sim time between consecutive events is divided by ``speed``; speed 0 or inf means no
    sleeping. ``sleep`` is injectable for tests."""
    merged: list[tuple[CallEvent | Transaction, str]] = []
    for sc in scenarios:
        merged += [(e, Topics.CALL_EVENTS) for e in sc.calls]
        merged += [(e, Topics.TXN_EVENTS) for e in sc.txns]
    merged.sort(key=lambda p: (p[0].ts, p[1] != Topics.CALL_EVENTS))
    realtime = speed > 0 and not math.isinf(speed)
    prev = None
    for event, topic in merged:
        if realtime and prev is not None:
            delay = (event.ts - prev).total_seconds() / speed
            if delay > 0:
                await sleep(delay)
        prev = event.ts
        await bus.publish(topic, event.idempotency_key, event)
