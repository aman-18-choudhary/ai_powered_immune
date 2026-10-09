import hashlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from scam_contracts.canonical import payload_hash
from scam_contracts.models import LedgerEntryIn
from svckit.bus import InMemoryBus

from evidence_ledger.keys import KeyRing, Signer
from evidence_ledger.store import LedgerStore

T0 = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


def make_signer(seed: bytes = b"\x07" * 32) -> Signer:
    return Signer.from_seed(seed)


def tid(n: int) -> str:
    return "txn_" + hashlib.sha256(str(n).encode()).hexdigest()[:16]


def entry_in(
    n: int = 1, *, event_type: str = "hold.created", service: str = "txn-guard",
    payload: dict | None = ..., refs: list[str] | None = None, actor: str = "system:txn-guard",
) -> LedgerEntryIn:  # fmt: skip
    if payload is ...:
        payload = {"txn_id": tid(n), "decision": "hold_verify", "reasons": ["URGENCY"]}
    ph = payload_hash(payload) if payload is not None else payload_hash({"n": n, "e": event_type})
    return LedgerEntryIn(
        service=service, actor=actor, event_type=event_type, payload_hash=ph,
        model_version="m1", payload=payload,
        case_refs=refs if refs is not None else [tid(n)],
    )  # fmt: skip


@pytest.fixture
def signer() -> Signer:
    return make_signer()


@pytest.fixture
def keyring(signer) -> KeyRing:
    return KeyRing(signer)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(signer, clock, tmp_path):
    s = LedgerStore(f"sqlite:///{tmp_path}/l.db", signer=signer, checkpoint_every=5, clock=clock)
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _trust(monkeypatch):
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "1")
    monkeypatch.delenv("LEDGER_GATEWAY_SECRET", raising=False)


def hdr(role: str, sub: str = "officer-1") -> dict[str, str]:
    return {"X-Principal-Role": role, "X-Principal-Sub": sub}


OFFICER, ADMIN, ANALYST = hdr("officer"), hdr("admin", "admin-1"), hdr("analyst", "analyst-1")


@pytest.fixture
async def client(keyring, clock, tmp_path):
    bus = InMemoryBus()
    from evidence_ledger.api import create_app

    app = create_app(
        database_url=f"sqlite:///{tmp_path}/api.db", bus=bus, clock=clock, keyring=keyring,
        checkpoint_every=5, maintenance_interval_s=0, consume=True,
    )  # fmt: skip
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            c.app = app  # type: ignore[attr-defined]
            c.bus = bus  # type: ignore[attr-defined]
            yield c
