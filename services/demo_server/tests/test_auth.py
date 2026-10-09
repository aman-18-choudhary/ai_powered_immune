import time

import jwt
import pytest
from conftest import SECRET, instant, login, make_settings
from fastapi.testclient import TestClient

from demo_server import auth
from demo_server.api import create_app
from demo_server.config import ConfigError, Settings

ALL = {"officer", "analyst", "citizen", "admin"}
READ3 = {"officer", "analyst", "admin"}
OFF = {"officer", "admin"}
ENDPOINTS = [
    ("POST", "/api/demo/start", {"seed": 1, "speed": 20}, OFF),
    ("POST", "/api/demo/control", {"action": "pause"}, OFF),
    ("GET", "/api/demo/state", None, ALL),
    ("GET", "/api/demo/runs", None, ALL),
    ("GET", "/api/holds", None, READ3),
    (
        "POST",
        "/api/holds/txn_0000000000000000/resolve",
        {"action": "release"},
        {"analyst", "admin"},
    ),
    ("GET", "/api/antibodies", None, READ3),
    ("GET", "/api/ledger/head", None, READ3),
    ("GET", "/api/ledger/entries", None, READ3),
    ("GET", "/api/ledger/checkpoints/latest", None, READ3),
    ("GET", "/api/ledger/checkpoints", None, READ3),
    ("POST", "/api/cases", {}, OFF),
    ("GET", "/api/packages/hero-case", None, OFF),
    ("POST", "/api/verify", {"case_id": "hero-case"}, OFF),
    ("GET", "/api/ring/current/graph", None, READ3),
    ("POST", "/api/ring/current/simulate", {"k": 1}, READ3),
    ("GET", "/api/ring/current/best-takedown", None, READ3),
    ("GET", "/api/geo/hotspots", None, READ3),
    ("GET", "/api/benchmark/summary", None, ALL),
    ("POST", "/api/citizen/assess", {"message": "hello"}, ALL),
]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    s = make_settings(
        tmp_path_factory.mktemp("auth"),
        rate_limits={"citizen": 10_000, "analyst": 10_000, "officer": 10_000, "admin": 10_000,
                     "anon": 10_000},
    )  # fmt: skip
    with TestClient(create_app(s, sleep=instant)) as c:
        yield c


def call(c, method, path, body, headers):
    return c.request(method, path, json=body, headers=headers)


@pytest.mark.parametrize("method,path,body,allowed", ENDPOINTS)
def test_every_endpoint_by_role(client, method, path, body, allowed):
    r = call(client, method, path, body, {})
    assert r.status_code == 401 and r.json() == {"error": "unauthorized"}, path
    for role in sorted(ALL):
        r = call(client, method, path, body, login(client, role))
        if role in allowed:
            assert r.status_code not in (401, 403, 429), (role, path, r.status_code, r.text)
        else:
            assert r.status_code == 403 and r.json() == {"error": "forbidden"}, (role, path)


def test_health_is_public(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["simulated"] is True


def claims(**kw):
    now = int(time.time())
    c = {"sub": "demo-officer-1", "role": "officer", "iat": now, "exp": now + 600, "jti": "j"}
    c.update(kw)
    return {k: v for k, v in c.items() if v is not None}


@pytest.mark.parametrize(
    "token",
    [
        jwt.encode(claims(), key=None, algorithm="none"),  # alg=none
        jwt.encode(claims(exp=int(time.time()) - 10), SECRET, algorithm="HS256"),  # expired
        jwt.encode(claims(role="superuser"), SECRET, algorithm="HS256"),  # unknown role
        jwt.encode(claims(role=None), SECRET, algorithm="HS256"),  # missing role
        jwt.encode(claims(sub=None), SECRET, algorithm="HS256"),  # missing sub
        jwt.encode(claims(jti=None), SECRET, algorithm="HS256"),  # missing jti
        jwt.encode(claims(iat=None), SECRET, algorithm="HS256"),  # missing iat
        jwt.encode(claims(exp=None), SECRET, algorithm="HS256"),  # missing exp
        jwt.encode(claims(), SECRET + "x", algorithm="HS256"),  # wrong secret
        jwt.encode(claims(), SECRET.ljust(64, "x"), algorithm="HS512"),  # other algorithm
        jwt.encode(claims(role=["officer"]), SECRET, algorithm="HS256"),  # role not a string
        "not.a.jwt",
        "",
    ],
)
def test_bad_tokens_are_rejected(client, token):
    r = client.get("/api/demo/state", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_valid_hand_made_token_and_scheme(client):
    ok = jwt.encode(claims(), SECRET, algorithm="HS256")
    assert (
        client.get("/api/demo/state", headers={"Authorization": f"Bearer {ok}"}).status_code == 200
    )
    assert (
        client.get("/api/demo/state", headers={"Authorization": f"Basic {ok}"}).status_code == 401
    )
    assert client.get("/api/demo/state", headers={"Authorization": ok}).status_code == 401
    assert client.get(f"/api/demo/state?token={ok}").status_code == 401  # query token: WS only


def test_token_shape_and_ttl():
    tok, sub, ttl = auth.issue_token(SECRET, "analyst", now=1_000_000)
    c = jwt.decode(tok, SECRET, algorithms=["HS256"], options={"verify_exp": False})
    assert ttl == 8 * 3600 and c["exp"] - c["iat"] == 8 * 3600
    assert c["role"] == "analyst" and c["sub"] == sub and c["jti"]
    assert jwt.get_unverified_header(tok)["alg"] == "HS256"
    with pytest.raises(ValueError):
        auth.issue_token(SECRET, "root")


def test_login_rejects_unknown_role_and_extra_fields(client):
    assert client.post("/api/auth/demo-login", json={"role": "root"}).status_code == 422
    assert client.post("/api/auth/demo-login", json={"role": "officer", "x": 1}).status_code == 422
    assert client.post("/api/auth/demo-login", json={}).status_code == 422


def test_login_code_is_enforced_in_constant_time_compare(tmp_path):
    s = make_settings(tmp_path, login_code="open-sesame-123")
    with TestClient(create_app(s, sleep=instant)) as c:
        assert c.post("/api/auth/demo-login", json={"role": "officer"}).status_code == 401
        r = c.post("/api/auth/demo-login", json={"role": "officer", "code": "wrong"})
        assert r.status_code == 401
        r = c.post("/api/auth/demo-login", json={"role": "officer", "code": "open-sesame-123"})
        assert r.status_code == 200
        r = c.post("/api/auth/demo-login", json={"role": "officer", "code": "ö" * 10})
        assert r.status_code == 401  # non-ASCII fails the compare, no error
    assert auth.code_ok("a", None) and not auth.code_ok(None, "a") and auth.code_ok("a", "a")


def test_secret_length_enforced_and_random_default_warns(caplog):
    with pytest.raises(ConfigError):
        Settings.from_env({"DEMO_JWT_SECRET": "short"})
    with caplog.at_level("WARNING", logger="demo_server"):
        s = Settings.from_env({})
    assert s.secret_generated and len(s.jwt_secret) >= 32
    assert any("DEMO_JWT_SECRET not set" in r.getMessage() for r in caplog.records)
    assert Settings.from_env({"DEMO_JWT_SECRET": "k" * 32}).jwt_secret == "k" * 32
    with pytest.raises(ConfigError):
        Settings.from_env({"DEMO_JWT_SECRET": "k" * 32, "DEMO_CORS_ORIGINS": "*"})
