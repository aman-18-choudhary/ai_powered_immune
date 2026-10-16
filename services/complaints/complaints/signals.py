"""Offline, rule-based signal extractors for complaint analysis (no network, no fetching).

* ``analyse_url``: lexical heuristics on the URL string only. The page is never fetched, no
  reputation service is queried and the target of a shortener is unknown.
* ``check_entities``: format and plausibility checks for phone, UPI id and email. These produce
  data-quality notes with weight 0: an unusual or placeholder value is not evidence of fraud.
* ``context_fields``: presence of amount, payment method and location, recorded as context only.

Weights are hand-set heuristics (0-1) used by a noisy-OR in ``analysis.py``; they are not fitted
and not calibrated. The static lists below are small, authored and not exhaustive.
"""

import ipaddress
import re
from typing import Any
from urllib.parse import urlsplit

from .identity import normalise_email
from .validation import normalise_phone

MAX_URL = 2048
LONG_QUERY = 200

# Static, authored lists (documented in the README; extend by editing, no network lookups).
SUSPICIOUS_TLDS = frozenset({
    "xyz", "top", "click", "icu", "buzz", "cyou", "monster", "cam", "loan", "bid", "gq", "tk",
    "ml", "ga", "cf", "work", "rest", "zip", "mov", "country", "stream", "download", "review",
    "party", "support", "men", "link", "fit",
})  # fmt: skip
SHORTENERS = frozenset({
    "bit.ly", "tinyurl.com", "t.co", "cutt.ly", "rb.gy", "is.gd", "goo.gl", "ow.ly", "shorturl.at",
    "tiny.cc", "buff.ly", "rebrand.ly", "t.ly",
})  # fmt: skip
# brand token -> official registered domains (small, not exhaustive; a legitimate sub-brand
# or partner domain can be flagged, and an unlisted brand is never flagged)
BRANDS: dict[str, tuple[str, ...]] = {
    "sbi": ("sbi.co.in", "onlinesbi.sbi", "sbicard.com", "sbi.bank.in"),
    "hdfc": ("hdfcbank.com", "hdfc.com", "hdfcbank.net"),
    "hdfcbank": ("hdfcbank.com", "hdfcbank.net"),
    "icici": ("icicibank.com", "icicilombard.com", "iciciprulife.com"),
    "icicibank": ("icicibank.com",),
    "axis": ("axisbank.com",),
    "axisbank": ("axisbank.com",),
    "kotak": ("kotak.com", "kotakbank.com"),
    "pnb": ("pnbindia.in", "netpnb.com"),
    "canara": ("canarabank.com", "canarabank.in"),
    "paytm": ("paytm.com", "paytmbank.com"),
    "phonepe": ("phonepe.com",),
    "gpay": ("pay.google.com", "google.com"),
    "googlepay": ("pay.google.com", "google.com"),
    "bhim": ("bhimupi.org.in", "npci.org.in"),
    "npci": ("npci.org.in",),
    "uidai": ("uidai.gov.in",),
    "aadhaar": ("uidai.gov.in",),
    "incometax": ("incometax.gov.in", "incometaxindia.gov.in"),
    "irctc": ("irctc.co.in",),
    "epfo": ("epfindia.gov.in", "epfo.gov.in"),
    "rbi": ("rbi.org.in",),
    "indiapost": ("indiapost.gov.in",),
    "amazon": ("amazon.in", "amazon.com", "amazonaws.com"),
    "flipkart": ("flipkart.com",),
}
KEYWORDS_PATH = ("login", "signin", "verify", "kyc", "otp", "update", "secure", "refund", "claim")
# Second-level suffixes under which the registrable domain has three labels (also used by the
# complaint network's registrable-domain rule, ``complaints.network``).
SLD_SUFFIXES = frozenset({
    "co.in", "gov.in", "org.in", "net.in", "ac.in", "nic.in", "res.in", "co.uk", "com.au",
    "org.uk",
})  # fmt: skip
KNOWN_UPI_HANDLES = frozenset({
    "okaxis", "oksbi", "okhdfcbank", "okicici", "ybl", "ibl", "axl", "paytm", "apl", "upi", "sbi",
    "hdfcbank", "icici", "axisbank", "pnb", "boi", "cnrb", "unionbank", "kotak", "barodampay",
    "aubank", "yesbank", "idfcbank", "fbl", "ikwik", "postbank", "airtel", "jio", "freecharge",
    "slice", "waicici", "wahdfcbank", "wasbi", "waaxis", "axisb", "okbizaxis", "tapicici",
})  # fmt: skip
_UPI_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,255}@[a-z][a-z0-9]{1,63}$")
_SPLIT = re.compile(r"[-._]+")
_CTRL = re.compile(r"[\x00-\x20\x7f]")


def _ind(code: str, weight: float, text: str) -> dict[str, Any]:
    return {"code": code, "weight": weight, "text": text, "source": "url_heuristics"}


def _registrable(host: str) -> str:
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in SLD_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def _is_ip_literal(host: str) -> bool:
    h = host.strip("[]")
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        pass
    # decimal or hex integer hosts (http://3232235777/, http://0xC0A80001/)
    return bool(re.fullmatch(r"\d{8,10}|0x[0-9a-f]{6,8}", h, re.IGNORECASE))


def analyse_url(raw: str) -> dict[str, Any]:
    """Lexical URL indicators. Returns ``{"status", "host", "indicators", "notes"}``; never
    raises. ``status`` is ``analysed`` or ``unparseable``."""
    limits = [
        "The page was not fetched and no reputation service was queried.",
        "Whether the text a victim saw matches the real destination cannot be known from this "
        "string.",
    ]
    base: dict[str, Any] = {
        "status": "unparseable", "scheme": None, "host": None, "indicators": [],
        "fetched": False, "reputation": "unavailable", "limits": limits,
    }  # fmt: skip
    if (
        not isinstance(raw, str)
        or not raw.strip()
        or len(raw) > MAX_URL
        or _CTRL.search(raw.strip())
    ):
        return base
    s = raw.strip()
    if "://" not in s:
        s = "http://" + s  # users often paste a bare host; scheme heuristics then apply to a guess
        base["notes"] = ["no scheme was given; http was assumed for analysis"]
    try:
        parts = urlsplit(s)
        host = (parts.hostname or "").casefold()
        port = parts.port
    except ValueError:
        return base
    if not host:
        return base
    ind: list[dict[str, Any]] = []
    scheme = parts.scheme.casefold()
    base.update(status="analysed", scheme=scheme, host=host, indicators=ind)
    if scheme not in ("http", "https"):
        ind.append(_ind("UNUSUAL_SCHEME", 0.3, f"the URL uses the scheme '{scheme[:12]}'"))
    elif scheme == "http":
        ind.append(_ind("NOT_HTTPS", 0.15, "the link does not use HTTPS"))
    if port not in (None, 80, 443):
        ind.append(_ind("NONSTANDARD_PORT", 0.15, "the link points to a non-standard port"))
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        ind.append(_ind("CREDENTIALS_IN_URL", 0.5,
                        "the link contains a user-info part before the host, a common way to "
                        "disguise the real address"))  # fmt: skip
    if _is_ip_literal(host):
        ind.append(
            _ind("IP_LITERAL_HOST", 0.45, "the link uses a raw IP address instead of a name")
        )
        return base  # no domain-based checks apply
    labels = [lb for lb in host.split(".") if lb]
    if any(lb.startswith("xn--") for lb in labels):
        ind.append(_ind("PUNYCODE_HOST", 0.35,
                        "the host uses punycode (internationalised name), which can hide a "
                        "look-alike of a known name"))  # fmt: skip
    elif not host.isascii():
        ind.append(_ind("NON_ASCII_HOST", 0.35, "the host contains non-ASCII characters"))
    reg = _registrable(host)
    tld = labels[-1] if labels else ""
    if len(labels) - len(reg.split(".")) >= 3:
        ind.append(_ind("EXCESSIVE_SUBDOMAINS", 0.25,
                        "the host has an unusually large number of subdomain labels"))  # fmt: skip
    if tld in SUSPICIOUS_TLDS:
        ind.append(_ind("SUSPICIOUS_TLD", 0.25,
                        f"the top-level domain '.{tld}' is on a static list often used in "
                        "throwaway scam sites"))  # fmt: skip
    if reg in SHORTENERS or host in SHORTENERS:
        ind.append(_ind("URL_SHORTENER", 0.3,
                        "the link is shortened, so its real destination is hidden"))  # fmt: skip
    # brand look-alike: a brand token inside the host whose registrable domain is not official
    tokens = set()
    deleet = host.translate(str.maketrans("013", "ole"))
    for h in {host, deleet}:
        for part in h.split("."):
            tokens.update(t for t in _SPLIT.split(part) if t)
    for brand, official in BRANDS.items():
        hit = brand in tokens or (len(brand) >= 5 and any(t.startswith(brand) for t in tokens))
        if hit and reg not in official and not any(host.endswith("." + o) for o in official):
            ind.append(_ind("BRAND_LOOKALIKE", 0.5,
                            f"the host mentions '{brand}' but is not a known official domain "
                            "for it"))  # fmt: skip
            break
    if len(parts.query) > LONG_QUERY:
        ind.append(_ind("LONG_QUERY", 0.15, "the link carries a very long query string"))
    path = (parts.path or "").casefold()
    if any(k in path for k in KEYWORDS_PATH) and reg not in sum(BRANDS.values(), ()):
        ind.append(_ind("SENSITIVE_PATH_KEYWORD", 0.1,
                        "the path contains words such as login, verify or KYC"))  # fmt: skip
    return base


# ------------------------------------------------------------------------- links in free text
# accuracy batch 2 (2026-10-18): links written inside the title/description are extracted and
# given the same offline lexical checks as the URL field. Extraction is not verification: nothing
# is fetched or resolved (no network access at all, so no SSRF surface), and an address that cannot
# be parsed is reported as such, never as malicious and never as safe.
MAX_EMBEDDED_URLS = 5
_EMBEDDED_URL = re.compile(
    r"(?i)(?:\b(?:https?|hxxps?)(?::|\[:\])//[^\s<>\"'`]+|\bwww(?:\.|\[\.\])[^\s<>\"'`]+)"
)
_TRAILING = ".,;:!?)]}'\"»”’"


def _refang(s: str) -> tuple[str, bool]:
    out = s
    for a, b in (("[.]", "."), ("(.)", "."), ("[:]", ":"), ("[dot]", ".")):
        out = out.replace(a, b)
    low = out.lower()
    if low.startswith("hxxps"):
        out = "https" + out[5:]
    elif low.startswith("hxxp"):
        out = "http" + out[4:]
    return out, out != s


def extract_urls(text: str) -> list[dict[str, Any]]:
    """Links found in free text: original span (evidence), offsets, the normalised form used for
    the checks, and whether it was written in a disguised ("defanged") form. At most
    ``MAX_EMBEDDED_URLS``; longer than ``MAX_URL`` characters are reported as too long."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not isinstance(text, str) or not text:
        return out
    for m in _EMBEDDED_URL.finditer(text):
        raw = m.group(0)
        while raw and raw[-1] in _TRAILING:
            raw = raw[:-1]
        if not raw:
            continue
        norm, defanged = _refang(raw)
        if norm.lower() in seen:
            continue
        seen.add(norm.lower())
        out.append({
            "original": raw[:200], "start": m.start(), "end": m.start() + len(raw),
            "normalised": norm[:MAX_URL + 1], "defanged": defanged,
            "too_long": len(norm) > MAX_URL,
        })  # fmt: skip
        if len(out) >= MAX_EMBEDDED_URLS:
            break
    return out


# ----------------------------------------------------------------------------- entities
def _placeholder_digits(d: str) -> bool:
    if len(set(d)) <= 2 and len(d) >= 8:
        return True
    return d in ("1234567890", "0123456789", "9876543210", "12345678", "123456789")


def check_entities(complaint: dict[str, Any]) -> dict[str, Any]:
    """Format and plausibility notes. Nothing here carries a risk weight."""
    out: dict[str, Any] = {"checks": [], "reputation": "unavailable"}
    checks: list[dict[str, Any]] = out["checks"]

    phone = complaint.get("suspect_phone")
    if phone:
        try:
            e164 = normalise_phone(str(phone))
            digits = e164[1:]
            national = digits[2:] if digits.startswith("91") and len(digits) == 12 else digits
            ph = {"field": "phone", "format": "e164_like", "plausible": True, "notes": []}
            if _placeholder_digits(national) or _placeholder_digits(digits):
                ph.update(plausible=False)
                ph["notes"].append("looks like a placeholder or repeated-digit number")
            if digits.startswith("91") and len(digits) == 12 and digits[2] not in "6789":
                ph.update(plausible=False)
                ph["notes"].append("an Indian mobile number does not start with this digit")
            if digits.startswith("91") and len(digits) == 12:
                ph["country_code"] = "+91"
            checks.append(ph)
        except ValueError:
            checks.append({"field": "phone", "format": "invalid", "plausible": False,
                           "notes": ["not a valid phone number format"]})  # fmt: skip

    upi = complaint.get("upi_id")
    if upi:
        u = str(upi).strip().casefold()
        if _UPI_RE.fullmatch(u):
            handle = u.rsplit("@", 1)[1]
            known = handle in KNOWN_UPI_HANDLES
            checks.append({
                "field": "upi_id", "format": "valid", "handle_known": known,
                "plausible": known,
                "notes": [] if known else
                ["the handle is not on a static list of common UPI handles; new handles do "
                 "exist, so this is not evidence of fraud"],
            })  # fmt: skip
        else:
            checks.append({"field": "upi_id", "format": "invalid", "plausible": False,
                           "notes": ["does not match the UPI id format name@handle"]})  # fmt: skip

    email = complaint.get("suspect_email")
    if email:
        ok = normalise_email(str(email)) is not None
        checks.append({"field": "email", "format": "valid" if ok else "invalid", "plausible": ok,
                       "notes": [] if ok else ["not a valid email syntax"]})  # fmt: skip
    return out


def context_fields(complaint: dict[str, Any]) -> dict[str, Any]:
    """Presence of context fields. Recorded for the reader; never scored."""
    present = [
        f for f in ("amount", "payment_method", "city", "state", "location_text",
                    "social_platform", "social_account", "utr_reference")
        if complaint.get(f) not in (None, "")
    ]  # fmt: skip
    geo = [f for f in ("city", "state", "location_text") if complaint.get(f)]
    if complaint.get("latitude") is not None and complaint.get("longitude") is not None:
        geo.append("coordinates")
    return {
        "present_fields": present,
        "geographic": {
            "present": bool(geo),
            "fields": geo,
            "used_for_scoring": False,
            "note": "location is recorded as context only; hotspot comparison is not "
            "part of this analysis",
        },  # fmt: skip
        "amount_present": complaint.get("amount") not in (None, ""),
        "payment_method_present": complaint.get("payment_method") not in (None, ""),
        "used_for_scoring": False,
    }
