"""Bus wiring: TXN_EVENTS and CALL_RISK through ``svckit.consume`` (dedupe by the message's
idempotency key / payload hash, atomic claim and release, retries, DLQ)."""

import asyncio

from scam_contracts.models import CallRisk, Transaction
from scam_contracts.topics import Topics
from svckit.bus import Bus, consume
from svckit.idempotency import IdempotencyStore

from .service import TxnGuardService

GROUP = "txn-guard"


def run_consumers(
    bus: Bus,
    service: TxnGuardService,
    store: IdempotencyStore,
    max_retries: int = 3,
    backoff_s: float = 0.0,
) -> list[asyncio.Task[None]]:
    async def on_txn(txn: Transaction) -> None:
        await service.handle_txn(txn)

    async def on_risk(risk: CallRisk) -> None:
        await service.handle_call_risk(risk)

    return [
        asyncio.create_task(
            consume(
                bus, Topics.TXN_EVENTS, GROUP, Transaction, on_txn, store, max_retries, backoff_s
            )
        ),
        asyncio.create_task(
            consume(bus, Topics.CALL_RISK, GROUP, CallRisk, on_risk, store, max_retries, backoff_s)
        ),
    ]
