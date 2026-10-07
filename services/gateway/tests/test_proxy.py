import logging

import fakeredis.aioredis
import pytest

ROUTES = [
    ("txn", {"analyst", "officer", "admin"}),
    ("graph", {"officer", "analyst", "admin"}),
    ("geo", {"officer", "analyst", "admin"}),
    ("ledger", {"officer", "admin"}),
    ("citizen", {"citizen", "admin"}),
]
ROLES = ("officer", "analyst", "citizen", "admin")


@pytest.mark.parametrize(("prefix", "allowed"), ROUTES)
async def test_rbac_matrix(client, auth, prefix, allowed):
    for role in ROLES:
        r = await client.get(f"/api/{prefix}/ping", headers=await auth(client, role))
        assert r.status_code == (200 if role in allowed else 403), (prefix, role)


async def test_citizen_cannot_call_ledger_export(client, auth):
    r = await client.get("/api/ledger/export", headers=await auth(client, "citizen"))
    assert r.status_code == 403


async def test_forwards_path_and_query(client, auth, upstream):
    r = await client.get("/api/txn/a/b?x=1&y=2", headers=await auth(client, "analyst"))
    assert r.json() == {"path": "/a/b", "q": "x=1&y=2"}
    assert str(upstream.requests[0].url).startswith("http://txn.internal/a/b")


async def test_authorization_not_forwarded_and_principal_headers_added(client, auth, upstream):
    h = await auth(client, "analyst")
    await client.get(
        "/api/txn/x", headers={**h, "Connection": "close", "X-Principal-Role": "admin"}
    )
    req = upstream.requests[0]
    assert "authorization" not in req.headers
    assert req.headers.get("connection") != "close"  # httpx sets its own
    assert req.headers["x-principal-role"] == "analyst"
    assert req.headers["x-principal-sub"] == "analyst"


async def test_request_id_generated_and_echoed(client, auth, upstream):
    r = await client.get("/api/txn/x", headers=await auth(client, "analyst"))
    rid = r.headers["x-request-id"]
    assert len(rid) == 36
    assert upstream.requests[0].headers["x-request-id"] == rid


async def test_request_id_propagated(client, auth, upstream):
    r = await client.get(
        "/api/txn/x", headers={**await auth(client, "analyst"), "X-Request-Id": "abc-123"}
    )
    assert r.headers["x-request-id"] == "abc-123"
    assert upstream.requests[0].headers["x-request-id"] == "abc-123"


async def test_request_id_on_errors(client):
    r = await client.get("/api/txn/x", headers={"X-Request-Id": "rid-9"})
    assert r.status_code == 401 and r.headers["x-request-id"] == "rid-9"


async def test_post_body_forwarded(client, auth, upstream):
    await client.post("/api/txn/x", content=b'{"a":1}', headers=await auth(client, "analyst"))
    assert upstream.requests[0].method == "POST"
    assert upstream.requests[0].content == b'{"a":1}'


async def test_upstream_down_502_json(client, auth, upstream):
    upstream.down = True
    r = await client.get("/api/txn/x", headers=await auth(client, "analyst"))
    assert r.status_code == 502
    assert r.json()["error"] == "upstream_unavailable"
    assert "Traceback" not in r.text


async def test_rate_limit_returns_429(make_client, auth):
    c = make_client(limits={"citizen": 3})
    h = await auth(c, "citizen")
    codes = [(await c.get("/api/citizen/x", headers=h)).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    r = await c.get("/api/citizen/x", headers=h)
    assert 1 <= int(r.headers["retry-after"]) <= 60


async def test_rate_limit_is_per_subject(make_client, auth):
    c = make_client(limits={"analyst": 1, "admin": 1})
    assert (await c.get("/api/txn/x", headers=await auth(c, "analyst"))).status_code == 200
    assert (await c.get("/api/txn/x", headers=await auth(c, "analyst"))).status_code == 429
    assert (await c.get("/api/txn/x", headers=await auth(c, "admin"))).status_code == 200


async def test_default_limits():
    from gateway.ratelimit import DEFAULT_LIMITS

    assert DEFAULT_LIMITS == {"citizen": 30, "analyst": 300, "officer": 300, "admin": 600}


async def test_rate_limit_fails_open_when_redis_down(make_client, auth, caplog):
    server = fakeredis.FakeServer()
    r = fakeredis.aioredis.FakeRedis(server=server)
    c = make_client(redis=r, limits={"citizen": 1})
    h = await auth(c, "citizen")
    server.connected = False
    with caplog.at_level(logging.WARNING):
        codes = [(await c.get("/api/citizen/x", headers=h)).status_code for _ in range(3)]
    assert codes == [200, 200, 200]
    assert "rate limit" in caplog.text.lower()


async def test_no_secrets_in_logs(client, auth, caplog):
    with caplog.at_level(logging.DEBUG):
        h = await auth(client, "analyst")
        await client.get("/api/txn/x", headers=h)
        await client.post("/auth/login", json={"username": "officer", "password": "wrong-pw-zzz"})
    assert h["Authorization"].split()[1] not in caplog.text
    assert "wrong-pw-zzz" not in caplog.text


async def test_health(client):
    assert (await client.get("/healthz")).status_code == 200
