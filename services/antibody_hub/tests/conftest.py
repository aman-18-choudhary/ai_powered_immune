import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from scam_contracts.hashing import keyed_hash
from scam_contracts.topics import Topics
from svckit.bus import InMemoryBus

from antibody_hub.api import create_app

T0 = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
FED_KEY = b"test-federation-key"


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


class FlakyBus(InMemoryBus):
    """Fails the next ``fail_next`` publishes, then behaves."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_next = 0

    async def publish_raw(self, topic: str, key: str, raw: bytes) -> None:
        if self.fail_next:
            self.fail_next -= 1
            raise ConnectionError("bus down")
        await super().publish_raw(topic, key, raw)


def hdr(role: str, sub: str = "analyst-1", bank: str | None = None) -> dict[str, str]:
    h = {"X-Principal-Role": role, "X-Principal-Sub": sub}
    if bank:
        h["X-Principal-Bank"] = bank
    return h


ANALYST = hdr("analyst", "analyst-1")
ADMIN = hdr("admin", "admin-1")
BANK_A = hdr("bank", "svc-bank-a", "bank_a")


def mule(account: str = "123456789012") -> str:
    return keyed_hash(account, "mule_account", FED_KEY)


@pytest.fixture(autouse=True)
def _trust(monkeypatch):
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "1")
    monkeypatch.delenv("HUB_GATEWAY_SECRET", raising=False)
    monkeypatch.delenv("HUB_METRICS_TOKEN", raising=False)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def bus() -> FlakyBus:
    return FlakyBus()


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'hub.db'}"


@pytest.fixture
def app(db_url, bus, clock):
    return create_app(
        database_url=db_url, bus=bus, clock=clock, banks=["bank_a", "bank_b"], drain_interval_s=0
    )


@pytest.fixture
def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def events(bus: InMemoryBus, topic: str = Topics.ANTIBODIES) -> list[dict]:
    return [json.loads(raw) for _, raw in bus.messages(topic)]
