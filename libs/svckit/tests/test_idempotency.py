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
