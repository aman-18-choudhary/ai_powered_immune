"""Demo-login JWTs (HS256 only) and role guards.

Demo mode has no passwords: ``POST /api/auth/demo-login`` hands a token for the requested role
to anyone who can reach the server (optionally gated by ``DEMO_LOGIN_CODE``, compared in constant
time over bytes). That is a simulation convenience, not an authentication scheme.
"""

from __future__ import annotations

import hmac
import secrets
import time
import uuid
from dataclasses import dataclass

import jwt

from .config import ROLES

ALGORITHM = "HS256"
TOKEN_TTL_S = 8 * 3600
_REQUIRED = ["exp", "iat", "sub", "role", "jti"]


class AuthError(Exception):
    """Token missing, malformed, expired, wrong algorithm or unknown role."""


@dataclass(frozen=True)
class Principal:
    sub: str
    role: str


def issue_token(secret: str, role: str, now: int | None = None) -> tuple[str, str, int]:
    """Returns (token, sub, expires_in)."""
    if role not in ROLES:
        raise ValueError("unknown role")
    iat = int(time.time()) if now is None else now
    sub = f"demo-{role}-{secrets.token_hex(4)}"
    claims = {
        "sub": sub, "role": role, "iat": iat, "exp": iat + TOKEN_TTL_S, "jti": str(uuid.uuid4()),
    }  # fmt: skip
    return jwt.encode(claims, secret, algorithm=ALGORITHM), sub, TOKEN_TTL_S


def decode_token(secret: str, token: str) -> Principal:
    try:
        claims = jwt.decode(token, secret, algorithms=[ALGORITHM], options={"require": _REQUIRED})
    except jwt.PyJWTError as e:
        raise AuthError("invalid token") from e
    role, sub = claims.get("role"), claims.get("sub")
    if not isinstance(role, str) or role not in ROLES:
        raise AuthError("invalid token")
    if not isinstance(sub, str) or not sub or len(sub) > 100:
        raise AuthError("invalid token")
    return Principal(sub=sub, role=role)


def code_ok(given: str | None, expected: str | None) -> bool:
    """Constant-time compare of UTF-8 bytes (non-ASCII input just fails the compare)."""
    if not expected:
        return True
    return hmac.compare_digest((given or "").encode("utf-8", "replace"), expected.encode("utf-8"))
