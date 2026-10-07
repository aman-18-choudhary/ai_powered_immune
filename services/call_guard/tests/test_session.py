import asyncio
from datetime import UTC, datetime, timedelta

from call_guard.rules import CALL_RISK_THRESHOLD
from call_guard.session import InMemorySessionStore, SessionScorer

T0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
INTRO = "This is Inspector Rajesh from the CBI, please confirm your name."
ARREST = "You are under digital arrest. Do not disconnect this video call."
SAFE = "Transfer your funds to the RBI safe account for verification right now."
AUTH_LEGAL = "This is Inspector Rajesh from the CBI. A case is registered against you under PMLA."


async def test_risk_accumulates_across_chunks(make_event):
    sc = SessionScorer(InMemorySessionStore())
    r1 = await sc.update(make_event(INTRO, ts=T0))
    r2 = await sc.update(make_event("Okay sir.", ts=T0 + timedelta(seconds=30)))
    r3 = await sc.update(make_event(ARREST, ts=T0 + timedelta(seconds=60)))
    r4 = await sc.update(make_event(SAFE, ts=T0 + timedelta(seconds=90)))
    assert r1.score < CALL_RISK_THRESHOLD
    assert r2.score <= r1.score
    assert r3.score > r2.score
    assert r4.score >= r3.score and r4.score >= CALL_RISK_THRESHOLD
    assert r4.call_id == "c1" and r4.reasons and r4.model_version
    assert {"AUTHORITY_IMPERSONATION", "SAFE_ACCOUNT_TRANSFER"} <= {x.code for x in r4.reasons}


async def test_weak_chunks_cross_threshold_together(make_event):
    sc = SessionScorer(InMemorySessionStore())
    r = None
    for i, t in enumerate(
        [INTRO, "Your name is in the FIR.", "Do not tell anyone about this call."]
    ):
        r = await sc.update(make_event(t, ts=T0 + timedelta(seconds=20 * i)))
    assert r is not None and r.score >= CALL_RISK_THRESHOLD


async def test_decay_over_time(make_event):
    sc = SessionScorer(InMemorySessionStore(), half_life_s=300)
    r1 = await sc.update(make_event(INTRO, ts=T0))
    r2 = await sc.update(make_event("Okay.", ts=T0 + timedelta(seconds=600)))
    assert abs(r2.score - r1.score / 4) < 0.02


async def test_calls_are_isolated(make_event):
    sc = SessionScorer(InMemorySessionStore())
    await sc.update(make_event(ARREST, call_id="a"))
    r = await sc.update(make_event("Your order is downstairs.", call_id="b"))
    assert r.score == 0.0 and r.reasons


async def test_replayed_event_not_double_counted(make_event):
    sc = SessionScorer(InMemorySessionStore())
    ev = make_event(INTRO, ts=T0)
    r1 = await sc.update(ev)
    r2 = await sc.update(ev)
    assert r1.score == r2.score


async def test_pending_until_marked_published(make_event):
    sc = SessionScorer(InMemorySessionStore())
    flags = []
    for i, t in enumerate([INTRO, ARREST, SAFE]):
        _, pending = await sc.update_with_crossing(make_event(t, ts=T0 + timedelta(seconds=10 * i)))
        flags.append(pending)
    assert flags == [False, True, True]  # stays pending until publish succeeded
    await sc.mark_published("c1", await sc.crossing_no("c1"))
    _, pending = await sc.update_with_crossing(make_event(SAFE, ts=T0 + timedelta(seconds=40)))
    assert pending is False


async def test_hysteresis_and_cooldown(make_event):
    sc = SessionScorer(InMemorySessionStore(), half_life_s=60, cooldown_s=300)
    await sc.update(make_event(AUTH_LEGAL, ts=T0))
    assert await sc.crossing_no("c1") == 1
    # decays below 0.5 quickly but inside the cooldown: a new spike must not re-arm
    await sc.update(make_event("Okay.", ts=T0 + timedelta(seconds=200)))
    await sc.update(make_event(AUTH_LEGAL, ts=T0 + timedelta(seconds=210)))
    assert await sc.crossing_no("c1") == 1
    # after cooldown and a fall below 0.5, a new spike is a second crossing
    await sc.update(make_event("Okay.", ts=T0 + timedelta(seconds=900)))
    await sc.update(make_event(AUTH_LEGAL, ts=T0 + timedelta(seconds=910)))
    assert await sc.crossing_no("c1") == 2


async def test_no_flapping_while_score_hovers_above_floor(make_event):
    sc = SessionScorer(InMemorySessionStore(), cooldown_s=0)
    for i, t in enumerate([AUTH_LEGAL, SAFE, ARREST, SAFE, ARREST]):
        await sc.update(make_event(t, ts=T0 + timedelta(seconds=20 * i)))
    assert await sc.crossing_no("c1") == 1


class SlowStore(InMemorySessionStore):
    """Yields between get and put so unserialised updates would interleave."""

    async def get(self, call_id):
        s = await super().get(call_id)
        await asyncio.sleep(0.01)
        return s


async def test_concurrent_updates_are_serialised(make_event):
    sc = SessionScorer(InMemorySessionStore(), half_life_s=1e9)
    seq = SessionScorer(InMemorySessionStore(), half_life_s=1e9)
    a, b = make_event(INTRO, ts=T0), make_event("Do not tell anyone about this call.", ts=T0)
    await asyncio.gather(sc.update(a), sc.update(b))
    sc2 = SessionScorer(SlowStore(), half_life_s=1e9)
    await asyncio.gather(sc2.update(a), sc2.update(b))
    await seq.update(a)
    expect = (await seq.update(b)).score
    got = (await sc2.update(make_event("Okay.", ts=T0))).score
    assert abs(got - expect) < 1e-6 and expect > 0.6


async def test_redis_store_roundtrip(make_event):
    import fakeredis.aioredis

    from call_guard.session import RedisSessionStore

    sc = SessionScorer(RedisSessionStore(fakeredis.aioredis.FakeRedis()))
    await sc.update(make_event(INTRO, ts=T0))
    r = await sc.update(make_event(ARREST, ts=T0 + timedelta(seconds=30)))
    assert r.score >= CALL_RISK_THRESHOLD


async def test_redis_lock_serialises_concurrent_updates(make_event):
    import fakeredis.aioredis

    from call_guard.session import RedisSessionStore

    sc = SessionScorer(RedisSessionStore(fakeredis.aioredis.FakeRedis()), half_life_s=1e9)
    a, b = make_event(INTRO, ts=T0), make_event("Do not tell anyone about this call.", ts=T0)
    await asyncio.gather(sc.update(a), sc.update(b))
    assert (await sc.update(make_event("Okay.", ts=T0))).score > 0.6
