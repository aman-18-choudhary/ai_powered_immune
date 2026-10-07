"""Message bus abstraction, in-memory and Kafka implementations, and the consume loop."""

import asyncio
import hashlib
import logging
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError
from scam_contracts.topics import Topics

from svckit.idempotency import IdempotencyStore

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class Bus(Protocol):
    async def publish(self, topic: str, key: str, value: BaseModel) -> None: ...

    async def publish_raw(self, topic: str, key: str, raw: bytes) -> None: ...

    def subscribe(self, topic: str, group: str) -> AsyncIterator[bytes]: ...


def message_key(raw: bytes) -> str:
    """Idempotency key for a raw message: SHA-256 of its payload."""
    return hashlib.sha256(raw).hexdigest()


class InMemoryBus:
    """Append-only log with a shared offset per (topic, group): consumers in the same
    group split messages; different groups each see every message."""

    def __init__(self) -> None:
        self._offsets: dict[tuple[str, str], int] = defaultdict(int)
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
        gk = (topic, group)
        while True:
            async with self._cond:
                await self._cond.wait_for(lambda: len(self._log[topic]) > self._offsets[gk])
                _, raw = self._log[topic][self._offsets[gk]]
                self._offsets[gk] += 1
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
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        await consumer.start()
        try:
            async for msg in consumer:
                yield msg.value
                # Resumed after the consumer finished handling (and any DLQ publish).
                await consumer.commit()
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
    backoff_s: float = 0.0,
    claim_ttl_s: float = 300,
) -> None:
    """Consume `topic`, calling `handler` once per distinct event.

    The dedupe key is the model's `idempotency_key` when present, else the SHA-256 of the
    raw bytes, namespaced as `{topic}:{group}:{key}`. The key is claimed atomically before
    the handler runs and released if the handler ultimately fails, so only successes stay
    marked. After `max_retries` failed attempts the raw message goes to `topic + DLQ_SUFFIX`.
    """
    async for raw in bus.subscribe(topic, group):
        msg: T | None
        try:
            msg = model.model_validate_json(raw)
        except ValidationError:
            log.warning("unparseable message on %s", topic, exc_info=True)
            msg = None
        base = getattr(msg, "idempotency_key", None) or message_key(raw)
        key = f"{topic}:{group}:{base}"
        if await store.seen(key) or not await store.claim(key, claim_ttl_s):
            continue
        done = False
        try:
            for attempt in range(1, max_retries + 1):
                try:
                    if msg is None:
                        raise ValueError("unparseable message")
                    await handler(msg)
                except Exception:
                    log.warning(
                        "handler failed on %s (attempt %d/%d)", topic, attempt, max_retries,
                        exc_info=True,
                    )
                    if attempt < max_retries and backoff_s > 0:
                        await asyncio.sleep(backoff_s * 2 ** (attempt - 1))
                else:
                    await store.mark(key)
                    done = True
                    break
            if not done:
                await bus.publish_raw(topic + Topics.DLQ_SUFFIX, key, raw)
                await store.release(key)
        except BaseException:
            if not done:
                await store.release(key)
            raise
