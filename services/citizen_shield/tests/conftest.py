import os
from datetime import UTC, datetime

import httpx
import pytest
from call_guard.api import create_app as create_guard_app
from scam_contracts.topics import Topics  # noqa: F401
from svckit.bus import InMemoryBus

from citizen_shield.api import create_app
from citizen_shield.config import Settings
from citizen_shield.guard_client import CallGuardClient
from citizen_shield.store import ReportStore

T0 = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
HDR = {"X-Principal-Role": "citizen", "X-Principal-Sub": "c1"}
KEY = b"test-report-key-0123456789abcdef"
SCAM = (
    "This is the CBI. You are under digital arrest. Transfer your money to the RBI safe "
    "account now and do not tell anyone."
)
BENIGN = "Hi, are we still meeting at 5 pm for tea tomorrow with the family?"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("CITIZEN_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "1")


@pytest.fixture(scope="module")
def guard_app():
    """The real call-guard app (rules + classifier when artifacts load), in process."""
    return create_guard_app()


class Down(httpx.AsyncBaseTransport):
    def __init__(self, exc: Exception | None = None, status: int = 0, body: bytes = b"") -> None:
        self.exc, self.status, self.body, self.calls = exc, status, body, 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return httpx.Response(self.status, content=self.body, request=request)


@pytest.fixture
def mkstore():
    made: list[ReportStore] = []

    def _make() -> ReportStore:
        s = ReportStore("sqlite://")
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()


@pytest.fixture
def make_app(guard_app, mkstore):
    def _make(*, transport=None, bus=None, key=KEY, settings=None, **kw):
        cfg = settings or Settings(assess_per_min=10_000, report_per_min=10_000, peer_per_min=10**6)
        guard = CallGuardClient(
            "http://call-guard",
            cfg.call_guard_timeout_s,
            breaker_failures=cfg.breaker_failures,
            breaker_open_s=cfg.breaker_open_s,
            transport=transport or httpx.ASGITransport(app=guard_app),
        )
        kw.setdefault("store", mkstore())
        kw.setdefault("background", False)
        return create_app(
            guard=guard, bus=bus if bus is not None else InMemoryBus(), report_key=key,
            settings=cfg, **kw,
        )  # fmt: skip

    return _make


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
