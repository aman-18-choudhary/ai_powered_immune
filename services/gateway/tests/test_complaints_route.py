"""Gateway wiring for the complaints service (additive): roles, bearer pass-through, log hygiene."""

import base64
import json
import logging
import time

import jwt
import pytest
from conftest import SECRET

from gateway.auth import ROLES, Principal, decode_token, demo_users_from_env, issue_token
from gateway.main import ROUTE_ROLES


def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def fake_jwt(alg: str, sig: str = "c2ln") -> str:
    """Structurally a JWT with the given alg header; the signature is meaningless."""
    head = _b64({"alg": alg, "typ": "JWT", "kid": "k1"})
    return f"{head}.{_b64({'sub': 'u', 'exp': 9999999999})}.{sig}"


def test_investigator_is_a_valid_role_and_existing_prefixes_untouched():
    assert "investigator" in ROLES
    assert ROUTE_ROLES["complaints"] == ("citizen", "investigator", "admin")
    assert ROUTE_ROLES["txn"] == ("analyst", "officer", "admin")
    assert ROUTE_ROLES["ledger"] == ("officer", "admin")
    assert all("investigator" not in v for k, v in ROUTE_ROLES.items() if k != "complaints")
    tok, _ = issue_token(SECRET, Principal(sub="x", role="investigator"))
    assert decode_token(SECRET, tok).role == "investigator"


async def test_demo_users_stay_the_original_four(monkeypatch):
    monkeypatch.setenv("GATEWAY_DEMO_PASSWORD", "shared-env-pw")
    assert set(demo_users_from_env()) == {"officer", "analyst", "citizen", "admin"}


async def _get(client, path, token=None, headers=None):
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    return await client.get(path, headers=h)


async def _client(upstream, **kw):
    import fakeredis.aioredis
    import httpx

    from gateway.main import create_app

    app = create_app(
        secret=SECRET,
        redis=fakeredis.aioredis.FakeRedis(),
        upstream_transport=httpx.MockTransport(upstream.handler),
        upstreams={"complaints": "http://complaints.internal", "txn": "http://txn.internal"},
        **kw,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


@pytest.mark.parametrize("role", ["citizen", "admin"])
async def test_gateway_roles_reach_complaints(role, upstream):
    c = await _client(upstream)
    tok, _ = issue_token(SECRET, Principal(sub=role, role=role))
    r = await _get(c, "/api/complaints/complaints", tok)
    assert r.status_code == 200
    seen = upstream.requests[-1]
    assert seen.headers["authorization"] == f"Bearer {tok}"  # the service verifies it itself
    assert seen.headers["x-principal-role"] == role
    r = await _get(c, "/api/txn/x", tok)
    assert r.status_code == (200 if role == "admin" else 403)
    if role == "admin":
        assert "authorization" not in upstream.requests[-1].headers  # still stripped elsewhere


async def test_analyst_and_officer_cannot_use_complaints(upstream):
    c = await _client(upstream)
    for role in ("analyst", "officer"):
        tok, _ = issue_token(SECRET, Principal(sub="s", role=role))
        assert (await _get(c, "/api/complaints/x", tok)).status_code == 403
    assert upstream.requests == []


async def test_investigator_gateway_token_reaches_complaints_not_old_services(upstream):
    c = await _client(upstream)
    tok, _ = issue_token(SECRET, Principal(sub="inv", role="investigator"))
    assert (await _get(c, "/api/complaints/x", tok)).status_code == 200
    assert (await _get(c, "/api/txn/x", tok)).status_code == 403


async def test_external_rs256_token_is_passed_through_to_complaints_only(upstream):
    c = await _client(upstream)
    ext = fake_jwt("RS256")
    r = await _get(c, "/api/complaints/auth/session", ext)
    assert r.status_code == 200
    seen = upstream.requests[-1]
    assert seen.headers["authorization"] == f"Bearer {ext}"
    assert "x-principal-role" not in seen.headers and "x-principal-sub" not in seen.headers
    # other prefixes never accept it
    assert (await _get(c, "/api/txn/x", ext)).status_code == 401


@pytest.mark.parametrize("alg", ["none", "HS256", "HS512", "ES256"])
async def test_non_rs256_unverified_tokens_are_not_passed_through(upstream, alg):
    c = await _client(upstream)
    assert (await _get(c, "/api/complaints/x", fake_jwt(alg))).status_code == 401
    assert upstream.requests == []


async def test_external_passthrough_needs_a_jwt_shape_and_client_cannot_forge_principal(upstream):
    c = await _client(upstream)
    assert (await _get(c, "/api/complaints/x", "not-a-jwt")).status_code == 401
    assert (await _get(c, "/api/complaints/x")).status_code == 401
    r = await _get(
        c,
        "/api/complaints/x",
        fake_jwt("RS256"),
        headers={"X-Principal-Role": "admin", "X-Principal-Sub": "root"},
    )
    assert r.status_code == 200
    assert "x-principal-role" not in upstream.requests[-1].headers


async def test_external_passthrough_is_rate_limited_per_ip(upstream):
    c = await _client(upstream)
    ext = fake_jwt("RS256")
    codes = [(await _get(c, "/api/complaints/x", ext)).status_code for _ in range(35)]
    assert 429 in codes and codes[0] == 200


async def test_expired_gateway_token_is_401_not_passed_through(upstream):
    c = await _client(upstream)
    t = int(time.time())
    claims = {"sub": "u", "role": "citizen", "iat": t - 100, "exp": t - 10}
    tok = jwt.encode(claims, SECRET, "HS256")
    assert (await _get(c, "/api/complaints/x", tok)).status_code == 401
    assert upstream.requests == []


def test_generated_demo_passwords_are_not_logged(monkeypatch, caplog):
    for role in ("OFFICER", "ANALYST", "CITIZEN", "ADMIN"):
        monkeypatch.delenv(f"GATEWAY_DEMO_PASSWORD_{role}", raising=False)
    monkeypatch.delenv("GATEWAY_DEMO_PASSWORD", raising=False)
    with caplog.at_level(logging.DEBUG):
        users = demo_users_from_env()
    assert len(users) == 4
    for password, _ in users.values():
        assert password not in caplog.text
    assert "generated demo password" not in caplog.text
    assert "GATEWAY_DEMO_PASSWORD" in caplog.text  # tells the operator where to set them
