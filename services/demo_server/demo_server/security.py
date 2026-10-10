"""Gateway-style hardening: request ids, body cap, headers, minimal access log, rate limits.

Patterns copied from ``services/gateway`` and ``services/evidence_ledger``:
request ids kept only if they match ``^[A-Za-z0-9._-]{1,64}$`` (else a fresh uuid4); a pure-ASGI
body cap that checks ``Content-Length`` up front AND counts streamed bytes (chunked bodies have no
length); the access log carries method, route template, status, request id and duration only
(never the query string, headers, body or token; run uvicorn with ``access_log=False``).
"""

from __future__ import annotations

import logging
import math
import re
import time
import uuid
from collections.abc import Callable
from typing import Any

log = logging.getLogger("demo_server.access")

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_FIXED = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
]


class _TooLarge(Exception):
    pass


class SecurityMiddleware:
    def __init__(self, app: Any, max_body_bytes: int) -> None:
        self.app = app
        self.max = max_body_bytes

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k: v for k, v in scope["headers"]}
        raw_rid = headers.get(b"x-request-id", b"").decode("latin-1", "replace")
        rid = raw_rid if REQUEST_ID_RE.fullmatch(raw_rid) else str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = rid
        t0 = time.monotonic()
        status = 500
        is_api = scope.get("path", "").startswith("/api")
        try:
            declared = int(headers.get(b"content-length", b"0") or 0)
        except ValueError:
            declared = 0
        seen = 0
        rejected = False
        started = False

        async def counting_receive() -> Any:
            nonlocal seen, rejected
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.max and not rejected:
                    rejected = True
                    await tracking_send_raw(413, "payload_too_large")
                    raise _TooLarge
            return msg

        async def tracking_send_raw(code: int, error: str) -> None:
            nonlocal status, started
            body = b'{"error":"' + error.encode() + b'"}'
            status = code
            started = True
            hdrs = [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"x-request-id", rid.encode()),
                *_FIXED,
            ]
            await send({"type": "http.response.start", "status": code, "headers": hdrs})
            await send({"type": "http.response.body", "body": body})

        async def tracking_send(msg: Any) -> None:
            nonlocal status, started
            if rejected:
                return
            if msg["type"] == "http.response.start":
                status = msg["status"]
                started = True
                hs = [
                    (k, v)
                    for k, v in msg.get("headers", [])
                    if k.lower() not in (b"x-request-id", b"server")
                ]
                hs.append((b"x-request-id", rid.encode()))
                have = {k.lower() for k, _ in hs}
                hs.extend((k, v) for k, v in _FIXED if k not in have)
                if is_api and b"cache-control" not in have:
                    hs.append((b"cache-control", b"no-store"))
                msg = {**msg, "headers": hs}
            await send(msg)

        try:
            if declared > self.max:
                rejected = True
                await tracking_send_raw(413, "payload_too_large")
                return
            await self.app(scope, counting_receive, tracking_send)
        except _TooLarge:
            pass
        except Exception:
            if not rejected:
                if not started:
                    log.error("unhandled error rid=%s", rid)
                    await tracking_send_raw(500, "internal_error")
                    return
                raise
        finally:
            route = scope.get("route")
            log.info(
                "method=%s route=%s status=%d rid=%s ms=%d",
                scope.get("method", "?"), getattr(route, "path", "unmatched"), status, rid,
                int((time.monotonic() - t0) * 1000),
            )  # fmt: skip


class RateLimiter:
    """Fixed-window per-key counter in memory (bounded). ``check`` fails open on any error."""

    def __init__(
        self, limits: dict[str, int], window_s: int = 60, clock: Callable[[], float] = time.time
    ) -> None:  # fmt: skip
        self.limits = limits
        self.window = window_s
        self._clock = clock
        self._counts: dict[tuple[str, str, int], int] = {}

    def check(self, role: str, key: str) -> int | None:
        """Count one request; ``None`` if allowed, else seconds until the window resets."""
        try:
            now = self._clock()
            win = int(now // self.window)
            if len(self._counts) > 10_000:
                self._counts = {k: v for k, v in self._counts.items() if k[2] >= win}
            k = (role, key, win)
            n = self._counts.get(k, 0) + 1
            self._counts[k] = n
            limit = self.limits.get(role, self.limits.get("anon", 60))
            if n > limit:
                return max(1, math.ceil((win + 1) * self.window - now))
        except Exception:  # fail open
            log.warning("rate limiter failed; allowing request")
        return None
