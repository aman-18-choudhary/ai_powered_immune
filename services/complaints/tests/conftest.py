"""Shared fixtures: a local RSA identity provider (stands in for Clerk), apps and clients.

No network and no Clerk instance: tokens are signed with a locally generated RSA key and the
verifier is given the matching JWKS directly.
"""

import json
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from complaints.analysis import CAPABILITIES_NONE, AnalysisOutcome
from complaints.api import create_app
from complaints.config import Settings

ISSUER = "https://clerk.test.example"
AZP = "http://localhost:5173"
INVESTIGATOR_EMAIL = "Inspector.One@Example.org"  # allowlist entry spelled with other casing
ADMIN_EMAIL = "root@example.org"


class Idp:
    """A local identity provider: RSA key, JWKS document and token minting."""

    def __init__(self, kid: str = "kid-1") -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = kid

    def jwk(self) -> dict:
        d = json.loads(RSAAlgorithm.to_jwk(self.key.public_key()))
        d.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        return d

    def jwks(self) -> dict:
        return {"keys": [self.jwk()]}

    def token(
        self,
        sub: str = "user_citizen_a",
        email: str | None = "citizen.a@example.org",
        verified: bool | str | None = True,
        *,
        name: str | None = "Test User",
        ttl: int = 300,
        key=None,
        alg: str = "RS256",
        kid: str | None = None,
        drop: tuple[str, ...] = (),
        **extra,
    ) -> str:
        now = int(time.time())
        claims: dict = {
            "iss": ISSUER, "sub": sub, "azp": AZP, "iat": now - 5, "nbf": now - 5,
            "exp": now + ttl, "jti": f"j-{sub}-{now}",
        }  # fmt: skip
        if email is not None:
            claims["email"] = email
        if verified is not None:
            claims["email_verified"] = verified
        if name is not None:
            claims["name"] = name
        claims.update(extra)
        for k in drop:
            claims.pop(k, None)
        return jwt.encode(claims, key or self.key, algorithm=alg, headers={"kid": kid or self.kid})


@pytest.fixture(scope="session")
def idp() -> Idp:
    return Idp()


class FakeAnalysis:
    """Deterministic AnalysisService used by most API tests."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.outcome = AnalysisOutcome(
            status="COMPLETED",
            pipeline_version="test-pipeline",
            capabilities={**CAPABILITIES_NONE, "text_risk_scorer": "available"},
            risk_score=20,
            risk_level="LOW",
            indicators=[],
            recommended_actions=[],
            limitations=["test"],
        )

    async def analyse(self, complaint: dict) -> AnalysisOutcome:
        self.calls.append(complaint)
        return self.outcome


@pytest.fixture
def analysis() -> FakeAnalysis:
    return FakeAnalysis()


PG_URL = os.getenv("COMPLAINTS_TEST_POSTGRES_URL")


def _db_url(tmp_path: Path) -> str:
    if not PG_URL:
        return f"sqlite:///{tmp_path / 'c.db'}"
    from sqlalchemy import create_engine

    from complaints.schema import metadata

    eng = create_engine(PG_URL)
    with eng.begin() as conn:  # fresh tables for every test
        metadata.drop_all(conn)
    eng.dispose()
    return PG_URL


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=_db_url(tmp_path),
        auth_mode="clerk",
        clerk_issuer=ISSUER,
        clerk_jwks_url="https://clerk.test.example/.well-known/jwks.json",
        clerk_authorized_parties=(AZP,),
        investigator_allowlist=frozenset({INVESTIGATOR_EMAIL.casefold()}),
        admin_bootstrap_emails=frozenset({ADMIN_EMAIL}),
        evidence_dir=tmp_path / "evidence",
        cors_origins=(AZP,),
    )


@pytest.fixture
def make_app(settings, idp, analysis) -> Iterator[Callable[..., object]]:
    made = []

    def _make(**kw):
        s = kw.pop("settings", settings)
        jwks = kw.pop("jwks", idp.jwks())
        app = create_app(s, jwks=jwks, analysis=kw.pop("analysis", analysis), **kw)
        made.append(app)
        return app

    yield _make
    for app in made:  # tests do not run the lifespan: release DB connections explicitly
        app.state.store.close()


@pytest.fixture
def app(make_app):
    return make_app()


@pytest.fixture
def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def tok(idp):
    """Token minting by actor name."""
    actors = {
        "a": dict(sub="user_a", email="citizen.a@example.org"),
        "b": dict(sub="user_b", email="citizen.b@example.org"),
        "inv": dict(sub="user_inv", email=INVESTIGATOR_EMAIL),
        "inv2": dict(sub="user_inv2", email="other.inspector@example.org"),
        "admin": dict(sub="user_admin", email=ADMIN_EMAIL),
        "admin2": dict(sub="user_admin2", email="second.admin@example.org"),
    }

    def _tok(who: str, **kw) -> dict[str, str]:
        return bearer(idp.token(**{**actors[who], **kw}))

    return _tok


@pytest.fixture
async def inv(client, tok):
    """Headers for a user that has completed the investigator entry (allowlisted, verified)."""
    h = tok("inv")
    r = await client.post("/auth/entry", json={"entry": "investigator"}, headers=h)
    assert r.status_code == 200, r.text
    return h


@pytest.fixture
async def admin(client, tok):
    h = tok("admin")
    r = await client.get("/auth/session", headers=h)
    assert r.status_code == 200 and r.json()["user"]["role"] == "ADMIN", r.text
    return h


MIN = {
    "category": "PHISHING",
    "title": "Fake bank SMS",
    "description": "I received an SMS asking me to click a link and update KYC.",
}


async def create(client, headers, **over) -> dict:
    body = {**MIN, **over}
    r = await client.post("/complaints", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# --- tiny real-format file samples ---------------------------------------------------------
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 32
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" + b"\x00" * 64
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 24
OGG = b"OggS\x00\x02" + b"\x00" * 32
M4A = b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 24
EXE = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 64
