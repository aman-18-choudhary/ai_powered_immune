"""Local identity provider for machines without Clerk keys (DEVELOPMENT ONLY).

Email + one-time code: ``start`` makes a 6-digit code, keeps only a salted HMAC of it in memory
(10 minutes, at most 5 wrong attempts) and "delivers" it by printing ONE clearly marked line to
the server's stderr::

    [LOCAL DEV MAIL] sign-in code for <email>: 123456

That line is the local stand-in for sending an email: whoever can read the server terminal can
sign in as any address, which is acceptable only on a developer's own machine. It is enabled only
when ``COMPLAINTS_AUTH_MODE`` is ``demo`` or ``local`` and is absent (404) in ``clerk`` mode.

``verify`` issues an HS256 token (8 h) that ``DemoVerifier`` accepts: ``sub`` is ``local:`` plus a
hash of the email, ``email``, ``email_verified: true`` (the code proved the person can read the
"mailbox", here the terminal) and optionally ``name``. There is no role claim; roles are decided by
the complaints service from its users table, the allowlist and the bootstrap list.

Nothing here logs the email, the code or its hash; responses never contain the code and never
reveal whether an account exists for the address.
"""

import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import jwt

CODE_TTL_S = 600
MAX_ATTEMPTS = 5
TOKEN_TTL_S = 8 * 3600
RATE_WINDOW_S = 900  # 15 minutes
START_PER_EMAIL = 5
START_PER_IP = 20
VERIFY_PER_IP = 30
MAX_PENDING = 10_000
ISSUER = "complaints-local-dev"
SUBJECT_PREFIX = "local:"


class LocalAuthRateLimited(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("rate_limited")
        self.retry_after = retry_after


class LocalAuthInvalid(Exception):
    """Wrong, expired, exhausted or never-issued code (deliberately indistinguishable)."""


def print_to_terminal(email: str, code: str) -> None:
    """The dev-only "mail delivery": one marked line on stderr (not through ``logging``)."""
    print(f"[LOCAL DEV MAIL] sign-in code for {email}: {code}", file=sys.stderr, flush=True)


def file_outbox(path: Path) -> Callable[[str, str], None]:
    """TEST ONLY (``COMPLAINTS_LOCAL_AUTH_OUTBOX``): print to the terminal as usual and also
    append ``{"email", "code"}`` as one JSON line to ``path`` (created 0600) so an automated
    end-to-end test can read the code. Only reachable in local/demo mode (config refuses it in
    clerk mode)."""

    def deliver(email: str, code: str) -> None:
        print_to_terminal(email, code)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps({"email": email, "code": code}) + "\n")

    return deliver


def local_subject(email: str) -> str:
    digest = hashlib.sha256(b"complaints-local-subject:v1:" + email.encode()).hexdigest()
    return SUBJECT_PREFIX + digest[:40]


@dataclass
class _Pending:
    salt: bytes
    digest: bytes
    expires: float
    attempts: int = 0


class _Window:
    """Fixed-window counter per key (in memory, bounded)."""

    def __init__(self, limit: int, window: int, clock: Callable[[], float]) -> None:
        self.limit, self.window, self.clock = limit, window, clock
        self.hits: dict[str, tuple[int, int]] = {}

    def hit(self, key: str) -> int | None:
        now = self.clock()
        slot = int(now // self.window)
        if len(self.hits) > 20_000:
            self.hits = {k: v for k, v in self.hits.items() if v[0] == slot}
        cur, n = self.hits.get(key, (slot, 0))
        n = 1 if cur != slot else n + 1
        self.hits[key] = (slot, n)
        if n > self.limit:
            return max(1, int((slot + 1) * self.window - now))
        return None


class LocalAuth:
    def __init__(
        self,
        secret: str,
        *,
        deliver: Callable[[str, str], None] = print_to_terminal,
        clock: Callable[[], float] = time.time,
        code_ttl_s: int = CODE_TTL_S,
        max_attempts: int = MAX_ATTEMPTS,
        token_ttl_s: int = TOKEN_TTL_S,
        start_per_email: int = START_PER_EMAIL,
        start_per_ip: int = START_PER_IP,
        verify_per_ip: int = VERIFY_PER_IP,
    ) -> None:
        self.__secret = secret
        self._deliver = deliver
        self._clock = clock
        self.code_ttl_s, self.max_attempts, self.token_ttl_s = code_ttl_s, max_attempts, token_ttl_s
        self._pending: dict[str, _Pending] = {}
        self._start_email = _Window(start_per_email, RATE_WINDOW_S, clock)
        self._start_ip = _Window(start_per_ip, RATE_WINDOW_S, clock)
        self._verify_ip = _Window(verify_per_ip, RATE_WINDOW_S, clock)

    def __repr__(self) -> str:
        return "LocalAuth()"

    @staticmethod
    def _digest(salt: bytes, code: str) -> bytes:
        return hmac.new(salt, code.encode(), hashlib.sha256).digest()

    def _gc(self) -> None:
        now = self._clock()
        if len(self._pending) >= MAX_PENDING:
            self._pending = {k: v for k, v in self._pending.items() if v.expires > now}
        while len(self._pending) >= MAX_PENDING:  # still full: drop the oldest entry
            self._pending.pop(next(iter(self._pending)))

    def start(self, email: str, ip: str) -> None:
        """``email`` must already be normalised. Replaces any earlier code for the address."""
        for window, key in ((self._start_ip, ip), (self._start_email, email)):
            wait = window.hit(key)
            if wait is not None:
                raise LocalAuthRateLimited(wait)
        self._gc()
        code = f"{secrets.randbelow(1_000_000):06d}"
        salt = secrets.token_bytes(16)
        self._pending.pop(email, None)
        self._pending[email] = _Pending(
            salt, self._digest(salt, code), self._clock() + self.code_ttl_s
        )
        self._deliver(email, code)

    def verify(self, email: str, code: str, ip: str, name: str | None = None) -> str:
        wait = self._verify_ip.hit(ip)
        if wait is not None:
            raise LocalAuthRateLimited(wait)
        p = self._pending.get(email)
        if p is None:
            raise LocalAuthInvalid()
        if self._clock() >= p.expires:
            self._pending.pop(email, None)
            raise LocalAuthInvalid()
        if not hmac.compare_digest(p.digest, self._digest(p.salt, code)):
            p.attempts += 1
            if p.attempts >= self.max_attempts:
                self._pending.pop(email, None)  # exhausted: a new code must be requested
            raise LocalAuthInvalid()
        self._pending.pop(email, None)  # single use
        return self.issue_token(email, name)

    def issue_token(self, email: str, name: str | None = None) -> str:
        now = int(self._clock())
        claims: dict[str, object] = {
            "iss": ISSUER, "sub": local_subject(email), "email": email, "email_verified": True,
            "iat": now, "exp": now + self.token_ttl_s, "jti": str(uuid.uuid4()), "amr": ["otp"],
        }  # fmt: skip
        if name:
            claims["name"] = name
        return jwt.encode(claims, self.__secret, algorithm="HS256")

    def pending_count(self) -> int:
        return len(self._pending)
