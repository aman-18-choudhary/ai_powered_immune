"""Bus wiring: TXN_EVENTS and CALL_RISK through ``svckit.consume`` (dedupe by the message's
idempotency key / payload hash, atomic claim and release, retries, DLQ)."""

import asyncio

from scam_contracts.models import Antibody, CallRisk, Transaction
from scam_contracts.topics import Topics
from svckit.bus import Bus, consume
from svckit.idempotency import IdempotencyStore

from .service import TxnGuardService

GROUP = "txn-guard"  # single-instance dev/test group (no bank configured)
BANK_GROUP_PREFIX = "txn-guard"  # + "-" + bank id: one consumer group per bank instance
ANTIBODY_GROUP_PREFIX = "txn-guard-antibody"  # + bank id: every bank instance sees every event


def group_for(bank_id: str | None) -> str:
    """Consumer group of the TXN_EVENTS / CALL_RISK consumers: per bank when a bank is configured
    (every bank's instance then receives the whole topic and filters), else the shared ``GROUP``."""
    return f"{BANK_GROUP_PREFIX}-{bank_id}" if bank_id else GROUP


def run_consumers(
    bus: Bus,
    service: TxnGuardService,
    store: IdempotencyStore,
    max_retries: int = 3,
    backoff_s: float = 0.0,
    bank_id: str | None = None,
    txn_topic: str = Topics.TXN_EVENTS,
) -> list[asyncio.Task[None]]:
    """``service.bank_id`` (the configured TXN_BANK_ID) selects the per-bank consumer group and
    the foreign-bank filter; ``bank_id`` only enables the antibody consumer. ``txn_topic`` allows a
    per-bank topic (``TXN_EVENTS_TOPIC``); CALL_RISK always stays on the shared topic (cross-bank
    by victim)."""
    group = group_for(service.bank_id)

    async def on_txn(txn: Transaction) -> None:
        if service.is_foreign(txn):  # acked (handler returns), counted, never scored/recorded
            service.foreign_bank_events += 1
            return
        await service.handle_txn(txn)

    async def on_risk(risk: CallRisk) -> None:
        await service.handle_call_risk(risk)

    tasks = [
        asyncio.create_task(
            consume(bus, txn_topic, group, Transaction, on_txn, store, max_retries, backoff_s)
        ),
        asyncio.create_task(
            consume(bus, Topics.CALL_RISK, group, CallRisk, on_risk, store, max_retries, backoff_s)
        ),
    ]
    if bank_id and service.antibodies is not None:
        tasks.append(run_antibody_consumer(bus, service, store, bank_id, max_retries, backoff_s))
    return tasks


def run_antibody_consumer(
    bus: Bus,
    service: TxnGuardService,
    store: IdempotencyStore,
    bank_id: str,
    max_retries: int = 3,
    backoff_s: float = 0.0,
) -> asyncio.Task[None]:
    """Subscribe to ``antibody.published`` with a consumer group per bank instance (each bank sees
    every event); malformed events are retried then dead-lettered; ``apply`` is idempotent."""

    async def on_antibody(ab: Antibody) -> None:
        await service.handle_antibody(ab)

    return asyncio.create_task(
        consume(
            bus,
            Topics.ANTIBODIES,
            f"{ANTIBODY_GROUP_PREFIX}-{bank_id}",
            Antibody,
            on_antibody,
            store,
            max_retries,
            backoff_s,
        )
    )
