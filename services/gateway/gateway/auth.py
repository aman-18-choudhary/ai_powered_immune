"""Password hashing, user store and JWT issue/verify (HS256 only)."""

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from typing import Protocol

import jwt

ROLES: frozenset[str] = frozenset({"officer", "analyst", "citizen", "admin"})
ALGORITHM = "HS256"
DEFAULT_TTL_SECONDS = 30 * 60

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


class AuthError(Exception):
    """Token missing, malformed, expired or otherwise unacceptable."""


@dataclass(frozen=True)
class Principal:
    sub: str
    role: str


def hash_password(password: str, salt: bytes | None = None) -> str:
    """Salted scrypt hash, encoded as ``scrypt$<salt hex>$<hash hex>``."""
    salt = salt if salt is not None else os.urandom(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = bytes.fromhex(hash_password(password, bytes.fromhex(salt_hex)).split("$")[2])
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


class UserStore(Protocol):
    async def authenticate(self, username: str, password: str) -> Principal | None: ...


class InMemoryUserStore:
    """Users held in memory with hashed passwords. A Postgres store can replace it."""

    def __init__(self, users: dict[str, tuple[str, str]] | None = None) -> None:
        self._users: dict[str, tuple[str, str]] = {}
        # Verified against when the username is unknown, so timing does not leak existence.
        self._dummy = hash_password(secrets.token_hex(16))
        for username, (password, role) in (users or {}).items():
            self.add(username, password, role)

    def add(self, username: str, password: str, role: str) -> None:
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        self._users[username] = (hash_password(password), role)

    async def authenticate(self, username: str, password: str) -> Principal | None:
        record = self._users.get(username)
        ok = verify_password(password, record[0] if record else self._dummy)
        if record is None or not ok:
            return None
        return Principal(sub=username, role=record[1])


# Clearly-marked demo defaults, used only when GATEWAY_DEMO_USERS=1 and no override is set.
_DEMO_DEFAULT_PASSWORD = "demo-only-change-me"


def demo_users_from_env(overrides: dict[str, str] | None = None) -> dict[str, tuple[str, str]]:
    """One demo user per role (username == role). Password: GATEWAY_DEMO_PASSWORD_<ROLE>."""
    users: dict[str, tuple[str, str]] = {}
    for role in sorted(ROLES):
        password = (overrides or {}).get(role) or os.getenv(
            f"GATEWAY_DEMO_PASSWORD_{role.upper()}", _DEMO_DEFAULT_PASSWORD
        )
        users[role] = (password, role)
    return users


def issue_token(
    secret: str, principal: Principal, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> tuple[str, int]:
    now = int(time.time())
    claims = {"sub": principal.sub, "role": principal.role, "iat": now, "exp": now + ttl_seconds}
    return jwt.encode(claims, secret, algorithm=ALGORITHM), ttl_seconds


def decode_token(secret: str, token: str) -> Principal:
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(type(exc).__name__) from exc
    role, sub = claims.get("role"), claims.get("sub")
    if role not in ROLES or not isinstance(sub, str) or not sub:
        raise AuthError("bad claims")
    return Principal(sub=sub, role=role)
