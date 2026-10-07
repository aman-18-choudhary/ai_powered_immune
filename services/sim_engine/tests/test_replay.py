import json
import math
from datetime import timedelta

import httpx
import pytest
from scam_contracts.topics import Topics
from svckit.bus import InMemoryBus

from sim_engine.api import create_app
from sim_engine.replay import Scenario, build_hero_scenario, replay
from sim_engine.world import build_world


@pytest.fixture(scope="module")
def world_():
    return build_world(seed=5, n_citizens=600)


class FakeClock:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def sleep(self, s: float) -> None:
        self.sleeps.append(s)


def _decode(bus: InMemoryBus, topic: str) -> list[tuple[str, dict]]:
    return [(k, json.loads(v)) for k, v in bus.messages(topic)]


def _hero(world):
    return build_hero_scenario(world, seed=3)


async def test_events_published_in_timestamp_order(world_):
    bus = InMemoryBus()
    sc = _hero(world_)
    await replay(bus, world_, [sc], speed=0)
    calls = bus.messages(Topics.CALL_EVENTS)
    txns = bus.messages(Topics.TXN_EVENTS)
    assert len(calls) == len(sc.calls) and len(txns) == len(sc.txns)
    for topic in (Topics.CALL_EVENTS, Topics.TXN_EVENTS):
        ts = [e["ts"] for _, e in _decode(bus, topic)]
        assert ts == sorted(ts)
    # keyed by idempotency key
    for k, e in _decode(bus, Topics.TXN_EVENTS) + _decode(bus, Topics.CALL_EVENTS):
        assert k == e["idempotency_key"]


async def test_merged_publish_order_across_topics(world_):
    order: list[tuple[str, str]] = []

    class Rec(InMemoryBus):
        async def publish(self, topic, key, value):
            order.append((topic, value.ts.isoformat()))
            await super().publish(topic, key, value)

    sc = _hero(world_)
    await replay(Rec(), world_, [sc], speed=0)
    stamps = [e.ts for e in sorted([*sc.calls, *sc.txns], key=lambda e: e.ts)]
    assert [t for _, t in order] == [s.isoformat() for s in stamps]


async def test_speed_multiplier_scales_delays(world_):
    sc = _hero(world_)
    total = {}
    for speed in (1.0, 10.0):
        clock = FakeClock()
        await replay(InMemoryBus(), world_, [sc], speed=speed, sleep=clock.sleep)
        total[speed] = sum(clock.sleeps)
    span = max(e.ts for e in [*sc.calls, *sc.txns]) - min(e.ts for e in [*sc.calls, *sc.txns])
    assert total[1.0] == pytest.approx(span.total_seconds())
    assert total[10.0] == pytest.approx(total[1.0] / 10)


@pytest.mark.parametrize("speed", [0, math.inf])
async def test_zero_or_inf_speed_never_sleeps(world_, speed):
    clock = FakeClock()
    await replay(InMemoryBus(), world_, [_hero(world_)], speed=speed, sleep=clock.sleep)
    assert clock.sleeps == []


def test_hero_scenario_has_victims_in_two_banks_and_two_states(world_):
    sc = _hero(world_)
    m = sc.metadata
    assert m is not None
    assert len({c for c in [m.victim_a_bank, m.victim_b_bank]}) == 2
    assert len({m.victim_a_state, m.victim_b_state}) == 2
    assert m.victim_a_token != m.victim_b_token
    assert m.mule_payee_hashes
    vt = [t for t in sc.txns if sc.campaign.txn_roles[t.txn_id] == "victim_transfer"]
    a = [t for t in vt if t.payer_token == m.victim_a_token]
    b = [t for t in vt if t.payer_token == m.victim_b_token]
    assert {t.bank_id for t in a} == {m.victim_a_bank}
    assert {t.bank_id for t in b} == {m.victim_b_bank}
    assert min(t.ts for t in b) - max(t.ts for t in a) == timedelta(seconds=90)
    assert {t.payee_hash for t in vt} <= set(m.mule_payee_hashes)
    assert len({c.campaign_id for c in [sc.campaign]}) == 1
    assert sc.campaign.campaign_id == m.campaign_id
    assert all(e.ts.tzinfo is not None for e in [*sc.calls, *sc.txns])


def test_hero_is_deterministic():
    a = build_hero_scenario(build_world(5, 600), seed=3)
    b = build_hero_scenario(build_world(5, 600), seed=3)
    assert a.metadata == b.metadata
    assert [e.model_dump_json() for e in a.txns] == [e.model_dump_json() for e in b.txns]
    assert [e.model_dump_json() for e in a.calls] == [e.model_dump_json() for e in b.calls]


def test_scenario_is_a_dataclass_of_events(world_):
    assert isinstance(_hero(world_), Scenario)


async def test_post_hero_returns_202_and_publishes(world_):
    bus = InMemoryBus()
    app = create_app(bus=bus, world=world_, speed=0, seed=3)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/scenarios/hero")
        assert r.status_code == 202
        body = r.json()
        assert body["campaign_id"] == _hero(world_).metadata.campaign_id
        assert body["victim_a_bank"] != body["victim_b_bank"]
        await app.state.wait_idle()
        assert (await c.get("/healthz")).status_code == 200
        assert (await c.get("/readyz")).status_code == 200
    assert bus.messages(Topics.TXN_EVENTS) and bus.messages(Topics.CALL_EVENTS)
