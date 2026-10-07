"""Message bus abstraction, in-memory and Kafka implementations, and the consume loop."""

import asyncio
import hashlib
import logging
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel
from scam_contracts.topics import Topics

from svckit.idempotency import IdempotencyStore

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class Bus(Protocol):
    async def publish(self, topic: str, key: str, value: BaseModel) -> None: ...

    def subscribe(self, topic: str, group: str) -> AsyncIterator[bytes]: ...


def message_key(raw: bytes) -> str:
    """Idempotency key for a raw message: SHA-256 of its payload."""
    return hashlib.sha256(raw).hexdigest()


class InMemoryBus:
    """Per-group queues; a group only receives messages published after it subscribed
    or already buffered for it. Messages published before any subscription are replayed."""

    def __init__(self) -> None:
        self._log: dict[str, list[tuple[str, bytes]]] = defaultdict(list)
        self._cond = asyncio.Condition()

    def messages(self, topic: str) -> list[tuple[str, bytes]]:
        return list(self._log[topic])

    async def publish(self, topic: str, key: str, value: BaseModel) -> None:
        async with self._cond:
            self._log[topic].append((key, value.model_dump_json().encode()))
            self._cond.notify_all()

    async def publish_raw(self, topic: str, key: str, raw: bytes) -> None:
        async with self._cond:
            self._log[topic].append((key, raw))
            self._cond.notify_all()

    async def subscribe(self, topic: str, group: str) -> AsyncIterator[bytes]:
        offset = 0
        while True:
            async with self._cond:
                await self._cond.wait_for(lambda o=offset: len(self._log[topic]) > o)
                _, raw = self._log[topic][offset]
            offset += 1
            yield raw


class KafkaBus:
    """Kafka-backed bus. Does not connect until first use."""

    def __init__(self, bootstrap: str) -> None:
        self._bootstrap = bootstrap
        self._producer: Any = None

    async def _get_producer(self) -> Any:
        if self._producer is None:
            from aiokafka import AIOKafkaProducer

            producer = AIOKafkaProducer(bootstrap_servers=self._bootstrap)
            await producer.start()
            self._producer = producer
        return self._producer

    async def publish(self, topic: str, key: str, value: BaseModel) -> None:
        await self.publish_raw(topic, key, value.model_dump_json().encode())

    async def publish_raw(self, topic: str, key: str, raw: bytes) -> None:
        producer = await self._get_producer()
        await producer.send_and_wait(topic, raw, key=key.encode())

    async def subscribe(self, topic: str, group: str) -> AsyncIterator[bytes]:
        from aiokafka import AIOKafkaConsumer

        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=self._bootstrap,
            group_id=group,
            enable_auto_commit=True,
            auto_offset_reset="earliest",
        )
        await consumer.start()
        try:
            async for msg in consumer:
                yield msg.value
        finally:
            await consumer.stop()

    async def close(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None


async def consume(
    bus: Bus,
    topic: str,
    group: str,
    model: type[T],
    handler: Callable[[T], Awaitable[None]],
    store: IdempotencyStore,
    max_retries: int = 3,
) -> None:
    """Consume `topic`, calling `handler` once per distinct message.

    The idempotency key is marked only after the handler succeeds. After `max_retries`
    failed attempts the raw message is published to `topic + DLQ_SUFFIX`.
    """
    async for raw in bus.subscribe(topic, group):
        key = message_key(raw)
        if await store.seen(key):
            continue
        done = False
        for attempt in range(1, max_retries + 1):
            try:
                await handler(model.model_validate_json(raw))
            except Exception:
                log.warning("handler failed on %s (attempt %d/%d)", topic, attempt, max_retries)
            else:
                await store.mark(key)
                done = True
                break
        if not done:
            await _publish_dlq(bus, topic + Topics.DLQ_SUFFIX, key, raw)


async def _publish_dlq(bus: Any, topic: str, key: str, raw: bytes) -> None:
    await bus.publish_raw(topic, key, raw)
