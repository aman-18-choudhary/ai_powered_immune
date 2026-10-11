import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from scam_contracts.models import Complaint

from geo_intel.api import create_app
from geo_intel.config import Settings
from geo_intel.store import GeoStore

T0 = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
HDR = {"X-Principal-Role": "analyst", "X-Principal-Sub": "u1"}


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


_n = [0]


def complaint(
    ts: datetime = T0, city: str | None = "MUM", kind: str = "scam_call", *,
    cid: str | None = None, state: str | None = None, bucket: str | None = None,
    case_ref: str | None = None,
) -> Complaint:  # fmt: skip
    _n[0] += 1
    return Complaint(
        complaint_id=cid or f"cmp-{_n[0]:08d}", ts=ts, kind=kind, city_code=city,
        state_code=state, amount_bucket=bucket, case_ref=case_ref,
    )  # fmt: skip


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("GEO_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "1")
    monkeypatch.delenv("GATEWAY_SHARED_SECRET", raising=False)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def make_app(clock):
    def _make(**kw):
        kw.setdefault("clock", clock)
        kw.setdefault("background", False)
        kw.setdefault("settings", Settings(min_count=3, z_threshold=3.0))
        return create_app(**kw)

    return _make


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def minutes(n: float) -> timedelta:
    return timedelta(minutes=n)


@pytest.fixture
def mkstore():
    """GeoStore factory; every store is closed at teardown (no unclosed sqlite handles)."""
    made: list[GeoStore] = []

    def _make(url: str = "sqlite://") -> GeoStore:
        s = GeoStore(url)
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()
