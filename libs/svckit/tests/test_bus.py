import asyncio

import httpx
from fastapi import FastAPI
from scam_contracts.models import Reason

from svckit.bus import InMemoryBus, KafkaBus, consume, message_key
from svckit.health import make_health_router
from svckit.idempotency import InMemoryIdempotencyStore


async def _run(bus, handler, store, topic="t", max_retries=3):
    task = asyncio.create_task(consume(bus, topic, "g", Reason, handler, store, max_retries))
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
    assert not await store.seen(message_key(raw))


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
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        assert (await c.get("/healthz")).status_code == 200
        assert (await c.get("/readyz")).status_code == 503
