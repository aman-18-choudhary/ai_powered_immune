"""Role-based access control dependency."""

import base64
import json
from collections.abc import Callable

from fastapi import HTTPException, Request

from .auth import AuthError, Principal, decode_token


def require_role(*roles: str) -> Callable[[Request], Principal]:
    """FastAPI dependency: 401 on missing/invalid token, 403 on a role not in ``roles``."""

    def dependency(request: Request) -> Principal:
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(401, "authentication required", {"WWW-Authenticate": "Bearer"})
        try:
            principal = decode_token(request.app.state.jwt_secret, token.strip())
        except AuthError as exc:
            raise HTTPException(
                401, "invalid or expired token", {"WWW-Authenticate": "Bearer"}
            ) from exc
        if principal.role not in roles:
            raise HTTPException(403, "forbidden")
        request.state.principal = principal
        return principal

    return dependency


EXTERNAL_ROLE = "external"


def _looks_like_rs256_jwt(token: str) -> bool:
    """Structure-only check (NO verification): three base64url parts, header alg == RS256.

    Used solely to decide whether to hand a bearer token to a service that verifies an external
    identity provider's token itself. Tokens declaring any other algorithm (``none``, HS*, ES*) are
    never passed through.
    """
    parts = token.split(".")
    if len(parts) != 3 or not all(parts) or len(token) > 8192:
        return False
    try:
        raw = parts[0] + "=" * (-len(parts[0]) % 4)
        header = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")))
    except (ValueError, UnicodeError):
        return False
    return isinstance(header, dict) and header.get("alg") == "RS256"


def require_role_or_external(*roles: str) -> Callable[[Request], Principal]:
    """Like ``require_role`` but a bearer token that is NOT a valid gateway token and declares
    RS256 is passed through as an ``external`` principal (sub = client IP) so the upstream can
    verify it against the identity provider. The gateway asserts nothing about such a token."""

    def dependency(request: Request) -> Principal:
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, "authentication required", {"WWW-Authenticate": "Bearer"})
        try:
            principal = decode_token(request.app.state.jwt_secret, token)
        except AuthError as exc:
            if _looks_like_rs256_jwt(token):
                ip = request.client.host if request.client else "unknown"
                principal = Principal(sub=ip, role=EXTERNAL_ROLE)
                request.state.principal = principal
                return principal
            raise HTTPException(
                401, "invalid or expired token", {"WWW-Authenticate": "Bearer"}
            ) from exc
        if principal.role not in roles:
            raise HTTPException(403, "forbidden")
        request.state.principal = principal
        return principal

    return dependency
