import asyncio
import logging

import fakeredis
import fakeredis.aioredis
import httpx
import jwt
import pytest
from conftest import SECRET

from gateway.auth import InMemoryUserStore, demo_users_from_env
from gateway.main import create_app

BAD = {"username": "officer", "password": "wrong"}


# --- login brute force ------------------------------------------------------------------------
async def test_eleventh_failed_login_is_429(client):
    codes = [(await client.post("/auth/login", json=BAD)).status_code for _ in range(11)]
    assert codes == [401] * 10 + [429]
    r = await client.post("/auth/login", json={"username": "officer", "password": "pw-officer"})
    assert r.status_code == 429  # still locked this window, even with right password
    assert 1 <= int(r.headers["retry-after"]) <= 60


async def test_failed_logins_for_one_user_block_other_ips_too(client, redis):
    for _ in range(10):
        await client.post("/auth/login", json=BAD)
    keys = {k.decode() for k in await redis.keys("gw:rl:login:*")}
    assert any(k.startswith("gw:rl:login:ip:") for k in keys)
    assert any(k.startswith("gw:rl:login:user:") for k in keys)
    assert not any("officer" in k for k in keys)  # username only stored hashed


async def test_successful_login_does_not_count(client):
    for _ in range(15):
        r = await client.post("/auth/login", json={"username": "admin", "password": "pw-admin"})
        assert r.status_code == 200


async def test_login_works_when_redis_down(make_client, caplog):
    server = fakeredis.FakeServer()
    c = make_client(redis=fakeredis.aioredis.FakeRedis(server=server))
    server.connected = False
    with caplog.at_level(logging.WARNING):
        ok = await c.post("/auth/login", json={"username": "admin", "password": "pw-admin"})
        bad = await c.post("/auth/login", json=BAD)
    assert ok.status_code == 200 and bad.status_code == 401
    assert "unavailable" in caplog.text


async def test_login_field_length_capped(client):
    r = await client.post("/auth/login", json={"username": "u" * 129, "password": "x"})
    assert r.status_code == 422
    r = await client.post("/auth/login", json={"username": "u", "password": "x" * 129})
    assert r.status_code == 422


async def test_password_check_runs_off_event_loop(monkeypatch):
    import threading

    import gateway.auth as auth_mod

    seen = []
    real = auth_mod.verify_password

    def spy(p, h):
        seen.append(threading.current_thread() is threading.main_thread())
        return real(p, h)

    monkeypatch.setattr(auth_mod, "verify_password", spy)
    store = InMemoryUserStore({"bob": ("pw", "citizen")}, max_concurrent_hashes=2)
    assert await store.authenticate("bob", "pw") is not None
    assert await store.authenticate("ghost", "pw") is None  # dummy hash still verified
    assert seen == [False, False]


# --- body / response caps ---------------------------------------------------------------------
async def test_body_rejected_by_content_length(make_client, client, auth):
    c = make_client(max_body_bytes=10)
    r = await c.post("/api/txn/x", content=b"x" * 11, headers=await auth(client, "analyst"))
    assert r.status_code == 413


async def test_body_rejected_by_streaming_guard(make_client, client, auth, upstream):
    c = make_client(max_body_bytes=10)

    async def gen():
        for _ in range(5):
            yield b"xxxx"

    r = await c.post("/api/txn/x", content=gen(), headers=await auth(client, "analyst"))
    assert r.status_code == 413 and not upstream.requests


async def test_response_too_large_by_content_length(make_client, auth, upstream):
    c = make_client(max_response_bytes=10)
    upstream.override = lambda req: httpx.Response(200, content=b"x" * 11)
    r = await c.get("/api/txn/x", headers=await auth(c, "analyst"))
    assert r.status_code == 502 and r.json()["error"] == "upstream_response_too_large"


async def test_response_too_large_when_streamed(make_client, auth, upstream):
    c = make_client(max_response_bytes=10)

    async def gen():
        for _ in range(5):
            yield b"xxxx"

    upstream.override = lambda req: httpx.Response(200, content=gen())
    r = await c.get("/api/txn/x", headers=await auth(c, "analyst"))
    assert r.status_code == 502 and r.json()["error"] == "upstream_response_too_large"


# --- request id -------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["a" * 65, "has space", "bad/slash"])
async def test_bad_request_id_replaced(client, auth, upstream, bad):
    h = await auth(client, "analyst")
    r = await client.get("/api/txn/x", headers={**h, "X-Request-Id": bad})
    rid = r.headers["x-request-id"]
    assert rid != bad and len(rid) == 36
    assert upstream.requests[-1].headers["x-request-id"] == rid


async def test_newline_request_id_via_raw_asgi(make_client, auth):
    c = make_client()
    h = await auth(c, "analyst")
    sent = {}

    async def send(msg):
        if msg["type"] == "http.response.start":
            sent["headers"] = dict(msg["headers"])

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
        "path": "/api/txn/x", "raw_path": b"/api/txn/x", "query_string": b"", "scheme": "http",
        "server": ("t", 80), "client": ("1.2.3.4", 1), "root_path": "",
        "headers": [
            (b"authorization", h["Authorization"].encode()),
            (b"x-request-id", b"evil\r\nSet-Cookie: a=b"),
        ],
    }  # fmt: skip
    await c._transport.app(scope, receive, send)  # type: ignore[attr-defined]
    assert b"\n" not in sent["headers"][b"x-request-id"]
    assert len(sent["headers"][b"x-request-id"]) == 36


# --- limiter timeouts -------------------------------------------------------------------------
class HangingLimiter:
    async def check(self, role, sub):
        await asyncio.sleep(3600)

    async def login_blocked(self, ip, username):
        await asyncio.sleep(3600)

    async def login_failed(self, ip, username):
        await asyncio.sleep(3600)


async def test_hanging_limiter_fails_open(make_client, auth, caplog):
    c = make_client(limiter=HangingLimiter(), limiter_timeout=0.05)
    with caplog.at_level(logging.WARNING):
        r = await c.get("/api/txn/x", headers=await auth(c, "analyst"))
        bad = await c.post("/auth/login", json=BAD)
    assert r.status_code == 200 and bad.status_code == 401
    assert "timed out" in caplog.text


# --- path traversal ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/api/txn/%2e%2e/ledger", "/api/txn/a/%2E/b", "/api/txn/a%5cb"])
async def test_dot_segments_rejected(client, auth, upstream, path):
    r = await client.get(path, headers=await auth(client, "analyst"))
    assert r.status_code == 400 and not upstream.requests


async def test_literal_dotdot_rejected(client, auth, upstream):
    h = await auth(client, "analyst")
    # httpx normalises "..", so hit the app directly with an unnormalised path
    got = {}

    async def send(msg):
        if msg["type"] == "http.response.start":
            got["status"] = msg["status"]

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    for path in ("/api/txn/../ledger", "/api/txn/a/./b"):
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
            "path": path, "raw_path": path.encode(), "query_string": b"", "scheme": "http",
            "server": ("t", 80), "client": ("1.2.3.4", 1), "root_path": "",
            "headers": [(b"authorization", h["Authorization"].encode())],
        }  # fmt: skip
        await client._transport.app(scope, receive, send)  # type: ignore[attr-defined]
        assert got["status"] == 400, path
    assert not upstream.requests


async def test_forwarded_path_is_requoted(client, auth, upstream):
    await client.get("/api/txn/a%20b/c%3Fd", headers=await auth(client, "analyst"))
    assert upstream.requests[0].url.raw_path == b"/a%20b/c%3Fd"


# --- headers ----------------------------------------------------------------------------------
async def test_forwarding_headers_stripped(client, auth, upstream):
    h = await auth(client, "analyst")
    await client.get(
        "/api/txn/x",
        headers={
            **h,
            "X-Forwarded-For": "6.6.6.6",
            "X-Forwarded-Host": "evil",
            "X-Real-IP": "6.6.6.6",
            "Cookie": "s=1",
            "Connection": "x-secret",
            "X-Secret": "s",
            "X-Keep": "yes",
        },
    )
    req = upstream.requests[0]
    for name in ("x-forwarded-for", "x-forwarded-host", "x-real-ip", "cookie", "x-secret"):
        assert name not in req.headers
    assert req.headers["x-keep"] == "yes"


async def test_multi_value_response_headers_kept_server_dropped(client, auth, upstream):
    upstream.override = lambda req: httpx.Response(
        200,
        headers=[("Set-Cookie", "a=1"), ("Set-Cookie", "b=2"), ("Server", "uvicorn")],
        content=b"ok",
    )
    r = await client.get("/api/txn/x", headers=await auth(client, "analyst"))
    assert r.headers.get_list("set-cookie") == ["a=1", "b=2"]
    assert "server" not in r.headers


# --- tokens / config --------------------------------------------------------------------------
async def test_unhashable_role_is_401_not_500(client):
    import time

    t = int(time.time())
    tok = jwt.encode({"sub": "u", "role": ["admin"], "iat": t, "exp": t + 60}, SECRET, "HS256")
    r = await client.get("/api/txn/x", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401


async def test_tokens_have_unique_jti(client):
    ids = set()
    for _ in range(2):
        r = await client.post("/auth/login", json={"username": "admin", "password": "pw-admin"})
        ids.add(jwt.decode(r.json()["access_token"], SECRET, algorithms=["HS256"])["jti"])
    assert len(ids) == 2


def test_short_secret_rejected():
    with pytest.raises(RuntimeError, match="at least 32"):
        create_app(secret="short")


def test_demo_users_random_password_when_unset(monkeypatch, caplog):
    for role in ("OFFICER", "ANALYST", "CITIZEN", "ADMIN"):
        monkeypatch.delenv(f"GATEWAY_DEMO_PASSWORD_{role}", raising=False)
    monkeypatch.delenv("GATEWAY_DEMO_PASSWORD", raising=False)
    with caplog.at_level(logging.WARNING):
        users = demo_users_from_env()
    passwords = {p for p, _ in users.values()}
    assert len(passwords) == 4 and "demo-only-change-me" not in passwords
    assert "Never use in production" in caplog.text


def test_demo_password_env(monkeypatch):
    monkeypatch.setenv("GATEWAY_DEMO_PASSWORD", "shared-env-pw")
    assert all(p == "shared-env-pw" for p, _ in demo_users_from_env().values())


# --- lifespan ---------------------------------------------------------------------------------
async def test_lifespan_closes_clients(redis, monkeypatch):
    closed = []
    real = redis.aclose

    async def spy():
        closed.append("redis")
        await real()

    monkeypatch.setattr(redis, "aclose", spy)
    app = create_app(secret=SECRET, redis=redis, upstreams={})
    async with app.router.lifespan_context(app):
        assert not app.state.http.is_closed
    assert app.state.http.is_closed and closed == ["redis"]


async def test_chunked_login_body_capped(make_client):
    c = make_client(max_body_bytes=100)

    async def gen():
        for _ in range(5):
            yield b"x" * 40

    r = await c.post("/auth/login", content=gen())
    assert r.status_code == 413
    ok = await c.post("/auth/login", json={"username": "admin", "password": "pw-admin"})
    assert ok.status_code == 200
