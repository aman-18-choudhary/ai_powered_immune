"""Identity verification (Clerk RS256 JWTs, injectable JWKS), session upsert, demo mode."""

import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from conftest import ISSUER, Idp, bearer

from complaints.identity import (
    ClerkBackendClient,
    ClerkJwtVerifier,
    DemoVerifier,
    IdentityError,
    IdentityUnavailable,
)


def verifier(idp, **kw):
    return ClerkJwtVerifier(issuer=ISSUER, jwks=idp.jwks(), **kw)


async def test_valid_token_creates_citizen(client, tok):
    r = await client.get("/auth/session", headers=tok("a"))
    assert r.status_code == 200
    u = r.json()["user"]
    assert u["role"] == "CITIZEN" and u["email"] == "citizen.a@example.org"
    assert u["email_verified"] is True and u["is_active"] is True and u["id"]
    assert "capabilities" in r.json()
    # second call: same internal user
    r2 = await client.get("/auth/session", headers=tok("a"))
    assert r2.json()["user"]["id"] == u["id"]


async def test_no_token_or_garbage_is_401(client):
    assert (await client.get("/auth/session")).status_code == 401
    assert (await client.get("/auth/session", headers=bearer("abc"))).status_code == 401
    r = await client.get("/auth/session", headers={"Authorization": "Basic abc"})
    assert r.status_code == 401


@pytest.mark.parametrize(
    "mutate",
    ["tamper", "expired", "wrong_issuer", "no_sub", "wrong_azp", "no_azp", "future_nbf",
     "unknown_kid", "no_kid", "other_key"],
)  # fmt: skip
async def test_bad_tokens_rejected(client, idp, mutate):
    now = int(time.time())
    t: str
    if mutate == "tamper":
        good = idp.token()
        h, p, s = good.split(".")
        t = f"{h}.{p[:-2]}AA.{s}"
    elif mutate == "expired":
        t = idp.token(ttl=-120)
    elif mutate == "wrong_issuer":
        t = idp.token(iss="https://evil.example")
    elif mutate == "no_sub":
        t = idp.token(drop=("sub",))
    elif mutate == "wrong_azp":
        t = idp.token(azp="https://evil.example")
    elif mutate == "no_azp":
        t = idp.token(drop=("azp",))
    elif mutate == "future_nbf":
        t = idp.token(nbf=now + 3600)
    elif mutate == "unknown_kid":
        t = idp.token(kid="nope")
    elif mutate == "no_kid":
        t = jwt.encode({"iss": ISSUER, "sub": "x", "exp": now + 99, "iat": now}, idp.key, "RS256")
    else:
        t = idp.token(key=rsa.generate_private_key(public_exponent=65537, key_size=2048))
    r = await client.get("/auth/session", headers=bearer(t))
    assert r.status_code == 401
    assert r.json() == {"error": "invalid_token", "message": "The access token is not valid."}


async def test_alg_none_and_hs256_key_confusion_rejected(client, idp):
    now = int(time.time())
    claims = {"iss": ISSUER, "sub": "x", "azp": "http://localhost:5173", "iat": now,
              "exp": now + 300}  # fmt: skip
    none_tok = jwt.encode(claims, key=None, algorithm="none", headers={"kid": idp.kid})
    # HS256 signed with the PUBLIC key bytes (classic algorithm confusion)
    from cryptography.hazmat.primitives import serialization

    pub_pem = idp.key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    import base64
    import hashlib
    import hmac
    import json

    def b64(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    hdr = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": idp.kid}).encode())
    pay = b64(json.dumps(claims).encode())
    sig = b64(hmac.new(pub_pem, f"{hdr}.{pay}".encode(), hashlib.sha256).digest())
    for t in (none_tok, f"{hdr}.{pay}.{sig}"):
        r = await client.get("/auth/session", headers=bearer(t))
        assert r.status_code == 401


async def test_verifier_unit_pins_algorithms(idp):
    v = verifier(idp)
    ident = await v.verify(idp.token(sub="s1", email="X@Example.org"))
    assert ident.subject == "s1" and ident.email == "x@example.org" and ident.email_verified
    with pytest.raises(IdentityError):
        await v.verify(idp.token(alg="RS512"))  # only RS256 accepted


async def test_clock_skew_tolerance_30s(idp):
    v = verifier(idp)
    now = int(time.time())
    await v.verify(idp.token(exp=now - 10))  # 10 s past expiry: inside the 30 s skew
    with pytest.raises(IdentityError):
        await v.verify(idp.token(exp=now - 90))


async def test_missing_email_claims_is_valid_identity_but_unverified(idp):
    ident = await verifier(idp).verify(idp.token(email=None, verified=None))
    assert ident.email is None and ident.email_verified is False


@pytest.mark.parametrize("claim", [False, "false", "yes", 1, None])
async def test_email_verified_must_be_true(idp, claim):
    ident = await verifier(idp).verify(idp.token(verified=claim))
    assert ident.email_verified is False


async def test_email_verified_string_true_accepted(idp):
    ident = await verifier(idp).verify(idp.token(verified="true"))
    assert ident.email_verified is True


async def test_email_claim_is_sanitised(idp):
    ident = await verifier(idp).verify(idp.token(email="not an email", name="N" * 500))
    assert ident.email is None and ident.email_verified is False
    assert len(ident.display_name or "") <= 100


async def test_jwks_rotation_refreshes_on_kid_miss():
    old, new = Idp("old"), Idp("new")
    state = {"doc": old.jwks(), "fetches": 0}
    clock = {"t": 1000.0}

    async def fetch():
        state["fetches"] += 1
        return state["doc"]

    v = ClerkJwtVerifier(
        issuer=ISSUER, jwks_fetcher=fetch, clock=lambda: clock["t"], min_refresh_interval=30
    )
    await v.verify(old.token())
    assert state["fetches"] == 1
    state["doc"] = {"keys": [old.jwk(), new.jwk()]}
    clock["t"] += 31
    await v.verify(new.token())  # kid miss -> refresh
    assert state["fetches"] == 2


async def test_kid_miss_refresh_is_rate_limited():
    idp, other = Idp("k1"), Idp("zzz")
    fetches = []

    async def fetch():
        fetches.append(1)
        return idp.jwks()

    clock = {"t": 0.0}
    v = ClerkJwtVerifier(
        issuer=ISSUER, jwks_fetcher=fetch, clock=lambda: clock["t"], min_refresh_interval=30
    )
    await v.verify(idp.token())
    for _ in range(20):  # attacker sprays unknown kids
        with pytest.raises(IdentityError):
            await v.verify(other.token())
    assert len(fetches) <= 2  # initial + at most one refresh inside the interval


async def test_jwks_fetch_failure_is_unavailable_not_valid():
    async def fetch():
        raise httpx.ConnectError("down")

    v = ClerkJwtVerifier(issuer=ISSUER, jwks_fetcher=fetch)
    with pytest.raises(IdentityUnavailable):
        await v.verify(Idp().token())


async def test_idp_down_is_503_no_detail(make_app, idp):
    async def fetch():
        raise httpx.ConnectError("secret-host.internal refused")

    app = make_app(jwks=None, jwks_fetcher=fetch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/auth/session", headers=bearer(idp.token()))
    assert r.status_code == 503
    assert "secret-host" not in r.text


async def test_weak_rsa_key_in_jwks_ignored():
    weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)

    class W(Idp):
        def __init__(self):
            self.key = weak
            self.kid = "weak"

    w = W()
    v = ClerkJwtVerifier(issuer=ISSUER, jwks=w.jwks())
    with pytest.raises(IdentityError):
        await v.verify(w.token())


async def test_authorized_parties_optional_when_unset(idp):
    v = ClerkJwtVerifier(issuer=ISSUER, jwks=idp.jwks(), authorized_parties=())
    await v.verify(idp.token(drop=("azp",)))


async def test_audience_checked_when_configured(idp):
    v = ClerkJwtVerifier(issuer=ISSUER, jwks=idp.jwks(), audience="complaints-api")
    with pytest.raises(IdentityError):
        await v.verify(idp.token())
    await v.verify(idp.token(aud="complaints-api"))


# ---- demo mode ---------------------------------------------------------------------------
SECRET = "demo-secret-demo-secret-demo-secret-1234"


def demo_token(**claims):
    now = int(time.time())
    base = {"sub": "citizen", "role": "admin", "iat": now, "exp": now + 300}
    base.update(claims)
    return jwt.encode(base, SECRET, "HS256")


async def test_demo_verifier_ignores_role_claim_and_prefixes_subject():
    ident = await DemoVerifier(SECRET).verify(demo_token(role="admin"))
    assert ident.subject == "demo:citizen" and ident.email is None
    assert ident.email_verified is False


async def test_demo_verifier_rejects_other_algs_and_bad_signature():
    v = DemoVerifier(SECRET)
    now = int(time.time())
    with pytest.raises(IdentityError):
        await v.verify(jwt.encode({"sub": "x", "iat": now, "exp": now + 9}, "y" * 40, "HS256"))
    with pytest.raises(IdentityError):
        await v.verify(jwt.encode({"sub": "x", "iat": now, "exp": now + 9}, SECRET, "HS384"))


async def test_demo_mode_app_role_claim_ignored(make_app, settings):
    import dataclasses

    s = dataclasses.replace(
        settings, auth_mode="demo", demo_jwt_secret=SECRET, clerk_issuer=None, clerk_jwks_url=None
    )
    app = make_app(settings=s, jwks=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.get("/auth/session", headers=bearer(demo_token(role="admin")))
        assert r.status_code == 200 and r.json()["user"]["role"] == "CITIZEN"
        r = await c.post(
            "/auth/entry", json={"entry": "investigator"}, headers=bearer(demo_token(role="admin"))
        )
        assert r.status_code == 403  # no verified email in a plain demo token


# ---- optional backend client (never live-tested) --------------------------------------------
async def test_backend_client_parses_primary_verified_email_and_hides_key():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "primary_email_address_id": "idn_2",
                "email_addresses": [
                    {"id": "idn_1", "email_address": "old@example.org",
                     "verification": {"status": "unverified"}},
                    {"id": "idn_2", "email_address": "Primary@Example.org",
                     "verification": {"status": "verified"}},
                ],
            },
        )  # fmt: skip

    c = ClerkBackendClient(
        secret_key="sk_test_dummy", transport=httpx.MockTransport(handler)
    )
    got = await c.lookup_primary_email("user_1")
    assert got == ("primary@example.org", True)
    assert seen["auth"] == "Bearer sk_test_dummy" and seen["url"].endswith("/v1/users/user_1")
    assert "sk_test_dummy" not in repr(c)
    await c.aclose()


async def test_backend_client_failure_returns_none():
    c = ClerkBackendClient(
        secret_key="sk_test_dummy",
        transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom")),
    )
    assert await c.lookup_primary_email("user_1") is None
    await c.aclose()
