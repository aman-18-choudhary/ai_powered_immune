import fakeredis.aioredis

from svckit.idempotency import InMemoryIdempotencyStore, RedisIdempotencyStore


async def test_in_memory_store():
    s = InMemoryIdempotencyStore()
    assert not await s.seen("k")
    await s.mark("k")
    assert await s.seen("k")


async def test_redis_store():
    s = RedisIdempotencyStore(fakeredis.aioredis.FakeRedis())
    assert not await s.seen("k")
    await s.mark("k")
    await s.mark("k")
    assert await s.seen("k")
    assert not await s.seen("other")


async def test_claim_release_in_memory_and_redis():
    for s in (InMemoryIdempotencyStore(), RedisIdempotencyStore(fakeredis.aioredis.FakeRedis())):
        assert await s.claim("k")
        assert not await s.claim("k")
        await s.release("k")
        assert await s.claim("k")
