"""Log redaction for this process.

uvicorn's websocket protocol logs the request target (``WebSocket /api/stream?token=<jwt>
[accepted]``) even with the access log off, and call-guard logs raw call ids. ``install`` makes
every log record of this process pass through ``redact`` at creation (a ``LogRecord`` factory, so
no logger/handler configuration, including uvicorn's own ``dictConfig``, can bypass it) and also
attaches a ``logging.Filter`` to the uvicorn/websockets loggers and to the root handlers.

Redacted: ``token=<value>`` query values, ``Authorization``/``Bearer`` values, anything shaped
like a JWT, and ``call_<hex>`` call ids (rewritten to the ``call_ref`` form the API shows).
Guarantee: records created by Python logging in this process after ``install`` has run; output
written by other means (stderr prints, a C extension) is not covered.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from svckit.ledger import call_ref

_TOKEN = re.compile(r"(?i)\b(token|access_token|jwt)=[^&\s\"'>]+")
_AUTH = re.compile(r"(?i)\b(authorization)\s*[:=]\s*(?:bearer\s+)?[^\s\"',;]+")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
_CALL = re.compile(r"\bcall_[0-9a-f]{8,64}\b")
_MARKER = ("token", "jwt", "authorization", "bearer", "eyJ", "call_")
_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "websockets", "demo_server")
_installed = False


def redact(text: str) -> str:
    if not any(m in text.lower() or m in text for m in _MARKER):
        return text
    out = _AUTH.sub(r"\1: [redacted]", text)
    out = _TOKEN.sub(r"\1=[redacted]", out)
    out = _BEARER.sub("Bearer [redacted]", out)
    out = _JWT.sub("[redacted-jwt]", out)
    return _CALL.sub(lambda m: call_ref(m.group(0)), out)


def _redact_arg(a: Any) -> Any:
    return redact(a) if isinstance(a, str) else a


def redact_record(record: logging.LogRecord) -> None:
    if isinstance(record.msg, str):
        record.msg = redact(record.msg)
    if isinstance(record.args, tuple):
        record.args = tuple(_redact_arg(a) for a in record.args)
    elif isinstance(record.args, dict):
        record.args = {k: _redact_arg(v) for k, v in record.args.items()}


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        redact_record(record)
        return True


def install() -> None:
    """Idempotent; safe to call from the app factory and from ``__main__``."""
    global _installed
    flt = RedactFilter()
    if not _installed:
        _installed = True
        prev = logging.getLogRecordFactory()

        def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            rec = prev(*args, **kwargs)
            redact_record(rec)
            return rec

        logging.setLogRecordFactory(factory)
    for name in _LOGGERS:
        lg = logging.getLogger(name)
        if not any(isinstance(f, RedactFilter) for f in lg.filters):
            lg.addFilter(flt)
    for h in logging.getLogger().handlers:
        if not any(isinstance(f, RedactFilter) for f in h.filters):
            h.addFilter(flt)
