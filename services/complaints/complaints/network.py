"""Complaint network (Phase 9): identifiers, their normalisation and masked display values.

Identifiers come ONLY from the structured complaint fields (owner decision): ``suspect_phone``,
``upi_id``, ``suspect_email``, ``suspicious_url`` (the full URL and its registrable domain),
``bank_account`` (the account number alone is the identifier; the IFSC is compared when the
pair is shown) and ``social_platform`` + ``social_account`` together. Free text (title,
description, location, transcripts) is never parsed for identifiers.

Normalisation reuses the complaint form's validators (``validation.py``). A value that does not
normalise (only possible in rows stored before validation existed) produces no identifier.

Registrable domain: a small, authored rule set (``signals.SLD_SUFFIXES``: co.in, gov.in, ac.in,
org.in, net.in, nic.in, res.in, co.uk, org.uk, com.au), otherwise the last two labels. It is NOT
the Public Suffix List: a suffix that is not listed (for example ``edu.in`` or ``blogspot.com``)
gives a too-broad domain, and hosting platforms that give each customer a subdomain make
unrelated sites share a "domain". No network lookup is made. That is one reason a shared domain is
only ever an *inferred* lead.

Display values are masked (phone ``••••••3210``, UPI ``ab•••@okaxis``, email ``a•••@domain``,
account ``••••••1234``, URL host + a truncated path without the query). The full value is shown
only on the complaint detail, where the investigator already sees it.
"""

import hashlib
import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .identity import normalise_email
from .schema import ENTITY_TYPES
from .signals import SLD_SUFFIXES
from .validation import _bank_account, _upi, normalise_phone

__all__ = ["ENTITY_TYPES", "Entity", "extract_entities", "normalise_query", "value_hash"]

# the field each type is read from (for the evidence shown with a link)
SOURCE_FIELD = {
    "phone": "suspect_phone", "upi": "upi_id", "email": "suspect_email",
    "url": "suspicious_url", "domain": "suspicious_url", "bank_account": "bank_account",
    "social_account": "social_account",
}  # fmt: skip
# fields the extractor reads; nothing else of a complaint is looked at
IDENTIFIER_FIELDS = (
    "suspect_phone", "upi_id", "suspect_email", "suspicious_url", "bank_account", "ifsc",
    "social_platform", "social_account",
)  # fmt: skip
DOT = "•••"
URL_PATH_SHOWN = 16
# short names people type for the same platform (casefolded, non-alphanumerics removed)
PLATFORM_ALIASES = {
    "insta": "instagram", "ig": "instagram", "fb": "facebook", "meta": "facebook",
    "wa": "whatsapp", "whatsappbusiness": "whatsapp", "tg": "telegram", "twitter": "x",
    "xcom": "x", "twitterx": "x", "yt": "youtube", "li": "linkedin",
}  # fmt: skip
_PLATFORM_STRIP = re.compile(r"[^a-z0-9]+")
_ACCOUNT_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Entity:
    entity_type: str
    value_norm: str
    display: str
    source_field: str


def value_hash(entity_type: str, value_norm: str) -> str:
    """Join key for an identifier (indexed instead of the up to 2048-character value, which a
    Postgres b-tree index entry cannot always hold)."""
    return hashlib.sha256(f"{entity_type}\x1f{value_norm}".encode()).hexdigest()


# ---------------------------------------------------------------------------- masking
def mask_phone(v: str) -> str:
    return "••••••" + v[-4:]


def mask_account(v: str) -> str:
    return "••••••" + v[-4:]


def mask_upi(v: str) -> str:
    local, _, handle = v.partition("@")
    keep = 2 if len(local) > 2 else 1
    return f"{local[:keep]}{DOT}@{handle}"


def mask_email(v: str) -> str:
    local, _, domain = v.rpartition("@")
    return f"{local[:1]}{DOT}@{domain}"


def mask_url(url_norm: str) -> str:
    parts = urlsplit(url_norm)
    host = parts.netloc
    path = parts.path or "/"
    shown = path if len(path) <= URL_PATH_SHOWN else path[:URL_PATH_SHOWN] + "…"
    return host + shown


def mask_social(value_norm: str) -> str:
    platform, _, account = value_norm.partition(":")
    keep = 2 if len(account) > 2 else 1
    return f"{platform}: {account[:keep]}{DOT}"


# ---------------------------------------------------------------------------- normalisers
def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def registrable_domain(host: str) -> str | None:
    """The registrable domain of ``host`` under the small rule set above, or None for an IP
    address, a single-label host or a bare suffix."""
    h = host.strip().casefold().rstrip(".")
    if not h or _is_ip(h):
        return None
    labels = [lb for lb in h.split(".") if lb]
    if len(labels) < 2:
        return None
    if ".".join(labels[-2:]) in SLD_SUFFIXES:
        return ".".join(labels[-3:]) if len(labels) >= 3 else None
    return ".".join(labels[-2:])


def normalise_url(raw: str) -> tuple[str, str] | None:
    """``(normalised URL, host)`` or None. Scheme and host are lower-cased, a default port and
    the fragment are dropped, an empty path becomes ``/``; path and query are kept as typed
    (they are case-sensitive)."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s or len(s) > 2048 or re.search(r"\s", s):
        return None
    try:
        parts = urlsplit(s)
        port = parts.port
        host = (parts.hostname or "").casefold().rstrip(".")
    except ValueError:
        return None
    scheme = parts.scheme.casefold()
    if scheme not in ("http", "https") or not host or "@" in parts.netloc:
        return None
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return None
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and port != {"http": 80, "https": 443}[scheme]:
        netloc += f":{port}"
    out = f"{scheme}://{netloc}{parts.path or '/'}"
    if parts.query:
        out += "?" + parts.query
    return out, host


def normalise_platform(raw: str) -> str | None:
    p = _PLATFORM_STRIP.sub("", raw.casefold())
    if not p:
        return None
    return PLATFORM_ALIASES.get(p, p)[:50]


def normalise_social(platform: str, account: str) -> str | None:
    """``platform:account`` (both casefolded; a leading ``@`` and spaces removed; a WhatsApp
    account that is a phone number is normalised like a phone), or None."""
    p = normalise_platform(platform or "")
    a = _ACCOUNT_SPACE.sub("", (account or "").strip().casefold()).lstrip("@")
    if not p or not a:
        return None
    if p == "whatsapp":
        try:
            a = normalise_phone(a)
        except ValueError:
            pass
    return f"{p}:{a}"[:200]


def _try(fn: Any, v: Any) -> Any:
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        return fn(v)
    except ValueError:
        return None


# ---------------------------------------------------------------------------- extraction
def extract_entities(c: Mapping[str, Any]) -> list[Entity]:
    """The identifiers of one complaint (from its structured fields only)."""
    out: list[Entity] = []
    if (v := _try(normalise_phone, c.get("suspect_phone"))) is not None:
        out.append(Entity("phone", v, mask_phone(v), "suspect_phone"))
    if (v := _try(_upi, c.get("upi_id"))) is not None:
        out.append(Entity("upi", v, mask_upi(v), "upi_id"))
    if (v := normalise_email(c.get("suspect_email"))) is not None:
        out.append(Entity("email", v, mask_email(v), "suspect_email"))
    url = normalise_url(c.get("suspicious_url") or "")
    if url is not None:
        out.append(Entity("url", url[0], mask_url(url[0]), "suspicious_url"))
        if (dom := registrable_domain(url[1])) is not None:
            out.append(Entity("domain", dom, dom, "suspicious_url"))
    if (v := _try(_bank_account, c.get("bank_account"))) is not None:
        out.append(Entity("bank_account", v, mask_account(v), "bank_account"))
    social = normalise_social(c.get("social_platform") or "", c.get("social_account") or "")
    if social is not None:
        out.append(Entity("social_account", social, mask_social(social), "social_account"))
    return out


def normalise_query(
    entity_type: str, value: str, *, platform: str | None = None
) -> tuple[str, str]:
    """Normalise a searched identifier: ``(value_norm, masked display)``. Raises ValueError
    with a message that never contains the value."""
    v = (value or "").strip()
    if not v:
        raise ValueError("a value is required")
    if entity_type == "phone":
        n = normalise_phone(v)
        return n, mask_phone(n)
    if entity_type == "upi":
        n = _upi(v)
        if n is None:
            raise ValueError("not a valid UPI id")
        return n, mask_upi(n)
    if entity_type == "email":
        n = normalise_email(v)
        if n is None:
            raise ValueError("not a valid email address")
        return n, mask_email(n)
    if entity_type == "url":
        u = normalise_url(v)
        if u is None:
            raise ValueError("not a valid http(s) URL")
        return u[0], mask_url(u[0])
    if entity_type == "domain":
        host = v
        if "://" in v:
            u = normalise_url(v)
            host = u[1] if u else ""
        d = registrable_domain(host) if host and not re.search(r"[\s/@]", host) else None
        if d is None:
            raise ValueError("not a valid domain name")
        return d, d
    if entity_type == "bank_account":
        n = _bank_account(v)
        if n is None:
            raise ValueError("account number must be 6-18 digits")
        return n, mask_account(n)
    if entity_type == "social_account":
        if not platform or not platform.strip():
            raise ValueError("the platform is required for a social account")
        s = normalise_social(platform, v)
        if s is None:
            raise ValueError("not a valid social account")
        return s, mask_social(s)
    raise ValueError("unknown identifier type")


def mask(entity_type: str, value_norm: str) -> str:
    return {
        "phone": mask_phone, "upi": mask_upi, "email": mask_email, "url": mask_url,
        "domain": lambda d: d, "bank_account": mask_account, "social_account": mask_social,
    }[entity_type](value_norm)  # fmt: skip
