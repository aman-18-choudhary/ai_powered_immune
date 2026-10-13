"""Identity verification.

* ``ClerkJwtVerifier``: Clerk session JWTs. RS256 only (algorithms pinned, ``none`` and HS*
  rejected before any key lookup), keys from a JWKS (TTL cache, rate-limited refresh on a ``kid``
  miss, optional injected JWKS for tests), ``iss``, ``exp``/``nbf``/``iat`` with 30 s skew, ``sub``
  required, ``azp`` in the configured authorized parties, optional ``aud``.
* ``email`` / ``email_verified`` / ``name`` are read from custom session-token claims (a Clerk JWT
  template). When absent the identity is still valid but ``email_verified`` is False, so
  investigator/admin eligibility fails closed.
* ``ClerkBackendClient``: optional primary-email lookup through Clerk's Backend API. OFF by
  default, never exercised against a live Clerk instance (only against a mock transport).
* ``DemoVerifier``: HS256 tokens for local demos only (``COMPLAINTS_AUTH_MODE=demo``). The ``role``
  claim is ignored.

Nothing here logs a token, a key or an email.
"""

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

SKEW_SECONDS = 30
JWKS_TTL_SECONDS = 3600.0
JWKS_MAX_STALE_SECONDS = 24 * 3600.0
JWKS_MIN_REFRESH_SECONDS = 30.0
JWKS_MAX_BYTES = 256 * 1024
MIN_RSA_BITS = 2048
_EMAIL_RE = re.compile(r"^[^@\s<>\"',;]{1,64}@[^@\s<>\"',;]{1,190}\.[^@\s<>\"',;.]{2,63}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class IdentityError(Exception):
    """The token is not acceptable (message is a short reason code, never token material)."""


class IdentityUnavailable(Exception):
    """The identity provider's keys cannot be obtained right now (fail closed, 503)."""


@dataclass(frozen=True)
class Identity:
    subject: str
    email: str | None
    email_verified: bool
    display_name: str | None


class IdentityVerifier(Protocol):
    async def verify(self, token: str) -> Identity: ...


def normalise_email(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.strip().casefold()
    return v if len(v) <= 254 and _EMAIL_RE.fullmatch(v) else None


def _display_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    v = _CONTROL_RE.sub("", value).strip()
    return v[:100] or None


def _verified_flag(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().casefold() == "true")


def _identity_from_claims(claims: dict[str, Any]) -> Identity:
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub.strip() or len(sub) > 255:
        raise IdentityError("bad_sub")
    email = normalise_email(claims.get("email"))
    verified = email is not None and _verified_flag(claims.get("email_verified"))
    return Identity(sub, email, verified, _display_name(claims.get("name")))


JwksFetcher = Callable[[], Awaitable[dict[str, Any]]]


class HttpJwksFetcher:
    """GET the JWKS document: no redirects, short timeout, bounded size."""

    def __init__(self, url: str, client: httpx.AsyncClient | None = None) -> None:
        self._url = url
        self._client = client or httpx.AsyncClient(timeout=3.0, follow_redirects=False)

    async def __call__(self) -> dict[str, Any]:
        async with self._client.stream("GET", self._url) as resp:
            resp.raise_for_status()
            size = 0
            chunks: list[bytes] = []
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > JWKS_MAX_BYTES:
                    raise ValueError("jwks too large")
                chunks.append(chunk)
        import json

        doc = json.loads(b"".join(chunks))
        if not isinstance(doc, dict):
            raise ValueError("jwks malformed")
        return doc

    async def aclose(self) -> None:
        await self._client.aclose()


class ClerkJwtVerifier:
    def __init__(
        self,
        issuer: str,
        *,
        jwks: dict[str, Any] | None = None,
        jwks_fetcher: JwksFetcher | None = None,
        authorized_parties: tuple[str, ...] = (),
        audience: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        min_refresh_interval: float = JWKS_MIN_REFRESH_SECONDS,
        ttl: float = JWKS_TTL_SECONDS,
    ) -> None:
        if jwks is None and jwks_fetcher is None:
            raise ValueError("a JWKS document or a fetcher is required")
        self._issuer = issuer.rstrip("/")
        self._fetcher = jwks_fetcher
        self._parties = frozenset(authorized_parties)
        self._audience = audience
        self._clock = clock
        self._min_refresh = min_refresh_interval
        self._ttl = ttl
        self._keys: dict[str, Any] = {}
        self._loaded_at: float | None = None
        self._attempted_at: float | None = None
        self._lock = asyncio.Lock()
        if jwks is not None:
            self._keys = self._parse(jwks)
            self._loaded_at = self._attempted_at = clock()

    @property
    def configured(self) -> str:
        return "injected" if self._fetcher is None else "configured"

    @staticmethod
    def _parse(doc: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for jwk in doc.get("keys", []) if isinstance(doc.get("keys"), list) else []:
            try:
                if not isinstance(jwk, dict) or jwk.get("kty") != "RSA":
                    continue
                kid = jwk.get("kid")
                if not isinstance(kid, str) or not kid or jwk.get("use", "sig") != "sig":
                    continue
                if jwk.get("alg", "RS256") != "RS256":
                    continue
                key = RSAAlgorithm.from_jwk(jwk)
                if key.key_size < MIN_RSA_BITS:
                    continue
                out[kid] = key
            except Exception:  # a malformed entry must not break the others
                continue
        return out

    async def _refresh(self) -> None:
        """Fetch and replace the key set. Caller holds the lock."""
        now = self._clock()
        self._attempted_at = now
        assert self._fetcher is not None
        try:
            doc = await self._fetcher()
            keys = self._parse(doc)
        except Exception as exc:
            stale_ok = (
                self._keys
                and self._loaded_at is not None
                and now - self._loaded_at < JWKS_MAX_STALE_SECONDS
            )
            if not stale_ok:
                raise IdentityUnavailable("jwks_unavailable") from exc
            return
        if not keys:
            if not self._keys:
                raise IdentityUnavailable("jwks_empty")
            return
        self._keys = keys
        self._loaded_at = now

    async def _key_for(self, kid: str) -> Any | None:
        async with self._lock:
            now = self._clock()
            if self._fetcher is not None and (
                not self._keys or self._loaded_at is None or now - self._loaded_at > self._ttl
            ):
                await self._refresh()
            key = self._keys.get(kid)
            if key is not None or self._fetcher is None:
                return key
            if self._attempted_at is None or now - self._attempted_at >= self._min_refresh:
                await self._refresh()  # kid miss: the provider may have rotated keys
                return self._keys.get(kid)
            return None

    async def verify(self, token: str) -> Identity:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise IdentityError("malformed") from exc
        if header.get("alg") != "RS256":
            raise IdentityError("alg")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid or len(kid) > 128:
            raise IdentityError("kid")
        key = await self._key_for(kid)
        if key is None:
            raise IdentityError("unknown_kid")
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self._issuer,
                audience=self._audience,
                leeway=SKEW_SECONDS,
                options={
                    "require": ["exp", "iat", "sub", "iss"],
                    "verify_aud": self._audience is not None,
                },
            )
        except jwt.PyJWTError as exc:
            raise IdentityError(type(exc).__name__) from exc
        if self._parties:
            azp = claims.get("azp")
            if not isinstance(azp, str) or azp not in self._parties:
                raise IdentityError("azp")
        return _identity_from_claims(claims)


class DemoVerifier:
    """HS256 demo tokens (e.g. from the gateway demo login). ``role`` is NOT read."""

    def __init__(self, secret: str) -> None:
        self._secret = secret

    async def verify(self, token: str) -> Identity:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                leeway=SKEW_SECONDS,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise IdentityError(type(exc).__name__) from exc
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub.strip() or len(sub) > 200:
            raise IdentityError("bad_sub")
        email = normalise_email(claims.get("email"))
        verified = email is not None and claims.get("email_verified") is True
        return Identity(f"demo:{sub.strip()}", email, verified, _display_name(claims.get("name")))


_SUBJECT_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")


class ClerkBackendClient:
    """Look up a user's primary email and its verification via Clerk's Backend API.

    Off by default (``CLERK_BACKEND_LOOKUP=1`` plus ``CLERK_SECRET_KEY``). The key is held in a
    private attribute and never appears in ``repr``, logs or errors. Not exercised live.
    """

    def __init__(
        self,
        secret_key: str,
        *,
        base_url: str = "https://api.clerk.com",
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 3.0,
    ) -> None:
        self.__key = secret_key
        self._client = httpx.AsyncClient(
            base_url=base_url, transport=transport, timeout=timeout, follow_redirects=False
        )

    def __repr__(self) -> str:
        return "ClerkBackendClient()"

    async def lookup_primary_email(self, subject: str) -> tuple[str, bool] | None:
        if not _SUBJECT_RE.fullmatch(subject):
            return None
        try:
            resp = await self._client.get(
                f"/v1/users/{subject}", headers={"Authorization": f"Bearer {self.__key}"}
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            primary = data.get("primary_email_address_id")
            for entry in data.get("email_addresses", []):
                if entry.get("id") == primary:
                    email = normalise_email(entry.get("email_address"))
                    status = (entry.get("verification") or {}).get("status")
                    return (email, status == "verified") if email else None
        except Exception:
            return None
        return None

    async def aclose(self) -> None:
        await self._client.aclose()
