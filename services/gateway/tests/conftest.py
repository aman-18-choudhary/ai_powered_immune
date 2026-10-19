from collections.abc import Callable

import fakeredis.aioredis
import httpx
import pytest

from gateway.main import create_app
from gateway.ratelimit import RateLimiter

MID_WINDOW = 6_000_000 * 60 + 10.0  # 10 s into a 60 s window
SECRET = "test-secret-test-secret-test-secret-0123"  # test-only, 32+ bytes for HS256


class Upstream:
    """Records requests seen by the fake upstream."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.down = False
        self.override: Callable[[httpx.Request], httpx.Response] | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("boom", request=request)
        self.requests.append(request)
        if self.override:
            return self.override(request)
        return httpx.Response(200, json={"path": request.url.path, "q": request.url.query.decode()})


@pytest.fixture
def upstream() -> Upstream:
    return Upstream()


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis()


@pytest.fixture
def make_client(upstream, redis):
    def _make(**kw) -> httpx.AsyncClient:
        the_redis = kw.pop("redis", redis)
        if "limiter" not in kw:
            # The limiters use fixed windows aligned to the wall clock (60 s). On the real clock a
            # test's requests can straddle a minute boundary and the count restarts (seen in a
            # Python 3.11 run, 2026-10-19). Pin the clock inside one window so tests check the
            # counting, not the time of day.
            kw["limiter"] = RateLimiter(the_redis, kw.pop("limits", None), clock=lambda: MID_WINDOW)
        app = create_app(
            secret=SECRET,
            redis=the_redis,
            upstream_transport=httpx.MockTransport(upstream.handler),
            upstreams={
                p: f"http://{p}.internal" for p in ("txn", "graph", "geo", "ledger", "citizen")
            },
            demo_passwords={r: f"pw-{r}" for r in ("officer", "analyst", "citizen", "admin")},
            **kw,
        )
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")

    return _make


@pytest.fixture
def client(make_client) -> httpx.AsyncClient:
    return make_client()


async def login(client: httpx.AsyncClient, role: str) -> str:
    r = await client.post("/auth/login", json={"username": role, "password": f"pw-{role}"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture
def auth():
    async def _auth(client: httpx.AsyncClient, role: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {await login(client, role)}"}

    return _auth
