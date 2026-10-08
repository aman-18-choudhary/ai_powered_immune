"""Bus wiring: TXN_EVENTS and CALL_RISK through ``svckit.consume`` (dedupe by the message's
idempotency key / payload hash, atomic claim and release, retries, DLQ)."""

import asyncio

from scam_contracts.models import Antibody, CallRisk, Transaction
from scam_contracts.topics import Topics
from svckit.bus import Bus, consume
from svckit.idempotency import IdempotencyStore

from .service import TxnGuardService

GROUP = "txn-guard"
ANTIBODY_GROUP_PREFIX = "txn-guard-antibody"  # + bank id: every bank instance sees every event


def run_consumers(
    bus: Bus,
    service: TxnGuardService,
    store: IdempotencyStore,
    max_retries: int = 3,
    backoff_s: float = 0.0,
    bank_id: str | None = None,
) -> list[asyncio.Task[None]]:
    async def on_txn(txn: Transaction) -> None:
        await service.handle_txn(txn)

    async def on_risk(risk: CallRisk) -> None:
        await service.handle_call_risk(risk)

    tasks = [
        asyncio.create_task(
            consume(
                bus, Topics.TXN_EVENTS, GROUP, Transaction, on_txn, store, max_retries, backoff_s
            )
        ),
        asyncio.create_task(
            consume(bus, Topics.CALL_RISK, GROUP, CallRisk, on_risk, store, max_retries, backoff_s)
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
