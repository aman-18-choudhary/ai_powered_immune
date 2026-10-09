"""Ledger consumer (topic ``ledger.append``, group ``evidence-ledger``) and maintenance loop.

The topic is NOT ordered across replicas and delivery is at-least-once, so ordering is the
ledger's receipt order and duplicates are absorbed by the store's idempotency on
(service, event_type, payload_hash). Permanently bad entries (malformed JSON, payload-hash
mismatch, identifier-looking content) go to ``ledger.append.dlq`` and are counted, never silently
dropped. Malformed raw messages are forwarded as received by ``svckit.consume`` (restrict the DLQ
topic's ACL); entries the ledger itself rejects are forwarded as a REDACTED envelope.
"""

import asyncio
import json
import logging
import re
from typing import Any

from pydantic import BaseModel
from scam_contracts.models import LedgerEntryIn
from scam_contracts.topics import Topics
from svckit.bus import Bus, consume

from .chain import EntryRejected
from .store import LedgerStore

log = logging.getLogger("evidence_ledger")
GROUP = "evidence-ledger"
DLQ_TOPIC = Topics.LEDGER + Topics.DLQ_SUFFIX
_HASH = re.compile(r"^[0-9a-f]{64}$")


class _NoDedupe:
    """The store is idempotent, so the consumer needs no (unbounded) dedupe memory of its own."""

    async def seen(self, key: str) -> bool:
        return False

    async def mark(self, key: str) -> None:
        return None

    async def claim(self, key: str, claim_ttl_s: float = 300) -> bool:
        return True

    async def release(self, key: str) -> None:
        return None


class CountingBus:
    """Delegates to a Bus and counts everything published to a DLQ topic."""

    def __init__(self, bus: Bus, store: LedgerStore) -> None:
        self._bus, self._store = bus, store

    async def publish(self, topic: str, key: str, value: BaseModel) -> None:
        await self._bus.publish(topic, key, value)

    async def publish_raw(self, topic: str, key: str, raw: bytes) -> None:
        await self._bus.publish_raw(topic, key, raw)
        if topic.endswith(Topics.DLQ_SUFFIX):
            self._store.counters.inc("dlq")

    def subscribe(self, topic: str, group: str) -> Any:
        return self._bus.subscribe(topic, group)


def redacted_envelope(msg: LedgerEntryIn, code: str) -> bytes:
    ph = msg.payload_hash if _HASH.match(msg.payload_hash) else None
    return json.dumps({"reason": code, "payload_hash": ph}).encode()


async def run_ledger_consumer(
    bus: Bus, store: LedgerStore, *, max_retries: int = 3, backoff_s: float = 0.5
) -> None:
    counting = CountingBus(bus, store)

    async def handle(msg: LedgerEntryIn) -> None:
        try:
            await asyncio.to_thread(store.append, msg)
        except EntryRejected as exc:  # permanent: no point retrying
            log.warning("ledger entry rejected: %s", exc.code)
            await counting.publish_raw(
                DLQ_TOPIC, msg.payload_hash[:64], redacted_envelope(msg, exc.code)
            )

    await consume(
        counting, Topics.LEDGER, GROUP, LedgerEntryIn, handle, _NoDedupe(),
        max_retries=max_retries, backoff_s=backoff_s,
    )  # fmt: skip


async def run_supervised(bus: Bus, store: LedgerStore, **kw: Any) -> None:
    """Restart the consumer if the bus connection fails; stop only on cancellation."""
    while True:
        try:
            await run_ledger_consumer(bus, store, **kw)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("ledger consumer crashed; restarting", exc_info=True)
            await asyncio.sleep(1.0)


async def run_maintenance(
    store: LedgerStore, interval_s: float, checkpoint_interval_s: float
) -> None:
    while True:
        try:
            await asyncio.to_thread(store.checkpoint_if_due, checkpoint_interval_s)
        except Exception:
            log.warning("ledger maintenance failed", exc_info=True)
        await asyncio.sleep(interval_s)
