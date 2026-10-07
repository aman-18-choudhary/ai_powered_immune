import asyncio

import httpx
from fastapi import FastAPI
from pydantic import BaseModel
from scam_contracts.models import Reason

from svckit.bus import InMemoryBus, KafkaBus, consume, message_key
from svckit.health import make_health_router
from svckit.idempotency import InMemoryIdempotencyStore


async def _run(bus, handler, store, topic="t", max_retries=3, model=Reason):
    task = asyncio.create_task(consume(bus, topic, "g", model, handler, store, max_retries))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def _reason() -> Reason:
    return Reason(code="X", detail="d", weight=0.5)


async def test_duplicate_idempotency_key_handled_once():
    bus = InMemoryBus()
    calls: list[Reason] = []

    async def handler(m: Reason) -> None:
        calls.append(m)

    await bus.publish("t", "k", _reason())
    await bus.publish("t", "k", _reason())
    await _run(bus, handler, InMemoryIdempotencyStore())
    assert len(calls) == 1


async def test_handler_failure_goes_to_dlq_after_three_tries():
    bus = InMemoryBus()
    attempts = 0

    async def handler(m: Reason) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("boom")

    store = InMemoryIdempotencyStore()
    await bus.publish("t", "k", _reason())
    await _run(bus, handler, store)
    assert attempts == 3
    dlq = bus.subscribe("t.dlq", "g")
    raw = await asyncio.wait_for(anext(dlq), 1)
    assert Reason.model_validate_json(raw) == _reason()
    assert len(bus.messages("t.dlq")) == 1
    assert not await store.seen(f"t:g:{message_key(raw)}")


async def test_failed_then_succeeds_marks_after_success():
    bus = InMemoryBus()
    n = 0

    async def handler(m: Reason) -> None:
        nonlocal n
        n += 1
        if n < 2:
            raise RuntimeError("once")

    await bus.publish("t", "k", _reason())
    await _run(bus, handler, InMemoryIdempotencyStore())
    assert n == 2
    assert bus.messages("t.dlq") == []


def test_kafka_bus_constructible_without_connecting():
    assert KafkaBus("localhost:9092")


async def test_health_router():
    async def ready() -> bool:
        return False

    app = FastAPI()
    app.include_router(make_health_router(ready))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/healthz")).status_code == 200
        assert (await c.get("/readyz")).status_code == 503


class _Keyed(BaseModel):
    idempotency_key: str
    ts: int


async def test_same_idempotency_key_different_bytes_handled_once():
    bus = InMemoryBus()
    calls: list[_Keyed] = []

    async def handler(m: _Keyed) -> None:
        calls.append(m)

    await bus.publish("t", "k", _Keyed(idempotency_key="a", ts=1))
    await bus.publish("t", "k", _Keyed(idempotency_key="a", ts=2))
    await _run(bus, handler, InMemoryIdempotencyStore(), model=_Keyed)
    assert len(calls) == 1


async def test_concurrent_consumers_same_group_handle_once():
    bus = InMemoryBus()
    store = InMemoryIdempotencyStore()
    calls = 0

    async def handler(m: Reason) -> None:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)

    await bus.publish("t", "k", _reason())
    await bus.publish("t", "k", _reason())
    tasks = [asyncio.create_task(consume(bus, "t", "g", Reason, handler, store)) for _ in range(2)]
    await asyncio.sleep(0.15)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert calls == 1


async def test_unparseable_goes_to_dlq():
    bus = InMemoryBus()

    async def handler(m: Reason) -> None:
        raise AssertionError("not called")

    await bus.publish_raw("t", "k", b"not json")
    await _run(bus, handler, InMemoryIdempotencyStore())
    assert bus.messages("t.dlq") == [("t:g:" + message_key(b"not json"), b"not json")]


async def test_cancellation_releases_claim_and_redelivery_is_handled():
    bus = InMemoryBus()
    store = InMemoryIdempotencyStore()
    started = asyncio.Event()
    calls: list[Reason] = []

    async def slow(m: Reason) -> None:
        started.set()
        await asyncio.sleep(10)

    await bus.publish("t", "k", _reason())
    task = asyncio.create_task(consume(bus, "t", "g", Reason, slow, store))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    async def handler(m: Reason) -> None:
        calls.append(m)

    # Simulate redelivery of the uncommitted message to a restarted consumer.
    await bus.publish("t", "k", _reason())
    await _run(bus, handler, store)
    assert len(calls) == 1


async def test_readyz_extra_fields():
    import httpx
    from fastapi import FastAPI

    async def ready() -> bool:
        return True

    app = FastAPI()
    app.include_router(make_health_router(ready, extra=lambda: {"model_version": "m1"}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/readyz")).json() == {"status": "ready", "model_version": "m1"}
