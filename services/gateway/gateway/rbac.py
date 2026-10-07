"""Role-based access control dependency."""

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
