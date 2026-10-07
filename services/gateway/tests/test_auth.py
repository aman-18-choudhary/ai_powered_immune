import time

import jwt
import pytest
from conftest import SECRET

from gateway.auth import InMemoryUserStore, hash_password, verify_password


def mint(claims: dict, secret: str = SECRET, alg: str = "HS256") -> str:
    return jwt.encode(claims, secret, algorithm=alg)


def now_claims(role="analyst", **kw) -> dict:
    t = int(time.time())
    return {"sub": "u1", "role": role, "iat": t, "exp": t + 600, **kw}


async def test_login_returns_token_with_role_claims(client):
    r = await client.post("/auth/login", json={"username": "officer", "password": "pw-officer"})
    assert r.status_code == 200
    claims = jwt.decode(r.json()["access_token"], SECRET, algorithms=["HS256"])
    assert claims["role"] == "officer" and claims["sub"] == "officer"
    assert claims["exp"] - claims["iat"] == 30 * 60


async def test_wrong_password_and_unknown_user_are_indistinguishable(client):
    a = await client.post("/auth/login", json={"username": "officer", "password": "nope"})
    b = await client.post("/auth/login", json={"username": "ghost", "password": "nope"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


async def test_passwords_are_hashed_and_salted():
    h1, h2 = hash_password("pw"), hash_password("pw")
    assert h1 != h2 and "pw" not in h1
    assert verify_password("pw", h1) and not verify_password("px", h1)
    assert not verify_password("pw", "garbage")


async def test_user_store_never_holds_plaintext():
    store = InMemoryUserStore({"bob": ("hunter2-demo", "citizen")})
    assert "hunter2-demo" not in repr(vars(store))


async def test_missing_token_401(client):
    assert (await client.get("/api/txn/x")).status_code == 401


async def test_expired_token_rejected(client):
    t = int(time.time())
    tok = mint({"sub": "u", "role": "analyst", "iat": t - 100, "exp": t - 10})
    r = await client.get("/api/txn/x", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401


async def test_alg_none_rejected(client):
    tok = jwt.encode(now_claims(), None, algorithm="none")
    r = await client.get("/api/txn/x", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401


async def test_bad_signature_rejected(client):
    tok = mint(now_claims(), secret="other-secret-other-secret-other-secret-1")
    assert (
        await client.get("/api/txn/x", headers={"Authorization": f"Bearer {tok}"})
    ).status_code == 401


@pytest.mark.parametrize("claims", [{"role": "root"}, {"role": None}])
async def test_unknown_or_missing_role_rejected(client, claims):
    c = now_claims()
    c.update(claims)
    if c["role"] is None:
        del c["role"]
    r = await client.get("/api/txn/x", headers={"Authorization": f"Bearer {mint(c)}"})
    assert r.status_code == 401


async def test_missing_exp_rejected(client):
    c = now_claims()
    del c["exp"]
    assert (
        await client.get("/api/txn/x", headers={"Authorization": f"Bearer {mint(c)}"})
    ).status_code == 401


async def test_secret_required_outside_test_mode(monkeypatch):
    from gateway.main import create_app

    monkeypatch.delenv("GATEWAY_JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="GATEWAY_JWT_SECRET"):
        create_app()
