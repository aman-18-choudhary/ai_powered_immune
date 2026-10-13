"""Confirm-to-hub: types, configuration and mapping for the antibody submission a ``confirm_block``
resolve queues on the hold record (delivery lives in ``holds.py``, the HTTP call in
``antibody_cache.HttpxHubClient.submit_antibody``).

What is guaranteed (and what is not):

* The submission is written into the hold record in the SAME compare-and-set as the resolution
  (like ``audit_pending``), so it survives restarts and is never created for a hold that was not
  blocked. Delivery is at-least-once; the hub answers 200 for an identical active antibody, so a
  duplicate is harmless.
* The resolve response never waits for the hub. While the hub is down the hold is resolved, the
  submission stays pending (retried with a capped exponential backoff) and other banks are NOT
  protected until it is delivered.
* Only ``confirm_block`` queues a submission. Revocation stays a hub-side operation.
* Nothing but the opaque payee hash (already a keyed hash), the bank slug, the analyst's
  subject (through ``safe_actor``) and an opaque evidence reference is ever sent or logged.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel
from svckit.ledger import txn_ref
from svckit.pii import string_has_identifier

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
BANK_SLUG_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")  # the hub's SOURCE_BANK_RE
MAX_HUB_PENDING_PER_HOLD = 4  # one per (kind, key) in practice; the list can never grow past this
DEFAULT_TIMEOUT_S = 2.0
DEFAULT_MAX_BACKOFF_S = 60.0
DEFAULT_BASE_BACKOFF_S = 1.0
DEFAULT_MAX_PENDING = 10_000
DEFAULT_SWEEP_BATCH = 100

# result labels exported as txn_guard_hub_submissions_total{result=...}
RESULTS: tuple[str, ...] = (
    "ok", "duplicate", "protected", "rejected", "retried", "skipped", "exhausted",
)  # fmt: skip

_DIGITS_TO_LETTERS = str.maketrans("0123456789", "ghijklmnop")


class HubSubmission(BaseModel):
    """One queued antibody submission; deterministic and PII-free by construction."""

    kind: Literal["mule_account"] = "mule_account"
    key_hash: str
    source_bank: str
    actor: str  # the resolving analyst's subject after ``safe_actor``
    evidence_ref: str
    attempts: int = 0
    last_error: str | None = None  # an error CLASS name only


@dataclass(frozen=True)
class HubOutcome:
    """The classified answer of one delivery attempt.

    ``result``: ok (201) | duplicate (200) | protected (409) | rejected (terminal 4xx, e.g. 422)
    | misconfig (401/403/404/405: kept pending, loud) | retry (5xx, 408/429, network, timeout).
    """

    result: Literal["ok", "duplicate", "protected", "rejected", "misconfig", "retry"]
    status: int | None = None
    error_class: str | None = None


@dataclass
class HubSubmitConfig:
    bank_id: str  # normalised TXN_BANK_ID; "" is recorded as a skipped submission
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_attempts: int | None = None  # None: retry retryable failures indefinitely
    max_backoff_s: float = DEFAULT_MAX_BACKOFF_S
    base_backoff_s: float = DEFAULT_BASE_BACKOFF_S
    max_pending: int = DEFAULT_MAX_PENDING  # global cap of pending submissions per instance
    sweep_batch: int = DEFAULT_SWEEP_BATCH

    def backoff_s(self, attempts: int) -> float:
        """Deterministic capped exponential backoff after the ``attempts``-th failure."""
        return float(min(self.max_backoff_s, self.base_backoff_s * 2 ** max(0, attempts - 1)))


def evidence_ref_for(txn_id: str) -> str:
    """Opaque evidence reference for the hub: ``txn_ref(txn_id)`` with its hex digits mapped to
    the letters g-p. The hub's identifier guard is stricter than the ledger's (a 16-hex block
    holding 9+ digits can be refused), a digit-free reference can never trip it, and the mapping
    is reversible, so the ledger's ``txn_<hex>`` reference is recoverable. Never the raw id."""
    ref = txn_ref(txn_id)
    head, _, tail = ref.partition("_")
    return f"{head}_{tail.translate(_DIGITS_TO_LETTERS)}"


def skip_reason(key_hash: str, bank_id: str) -> str | None:
    """Why a submission cannot be made at all (recorded, counted and audited), else None."""
    if not bank_id:
        return "no_bank_id"
    if not BANK_SLUG_RE.match(bank_id) or string_has_identifier(bank_id):
        return "invalid_bank_id"
    if not HEX64_RE.match(key_hash or ""):
        return "invalid_key_hash"
    return None


def hub_config_from_env(env: Mapping[str, str]) -> HubSubmitConfig | None:
    """``TXN_HUB_SUBMIT`` default is ON when ``HUB_URL`` and ``TXN_BANK_ID`` are both set, OFF
    otherwise (single-instance dev/test unchanged). ``1`` forces it on (a missing bank id is then
    recorded as skipped submissions), ``0`` forces it off; it is never on without a ``HUB_URL``."""
    hub_url = (env.get("HUB_URL") or "").strip()
    bank = (env.get("TXN_BANK_ID") or "").strip().casefold()
    flag = (env.get("TXN_HUB_SUBMIT") or "").strip()
    on = bool(hub_url and bank) if flag == "" else flag == "1"
    if not on or not hub_url:
        return None
    max_attempts = (env.get("HUB_SUBMIT_MAX_ATTEMPTS") or "").strip()
    return HubSubmitConfig(
        bank_id=bank,
        timeout_s=float(env.get("HUB_SUBMIT_TIMEOUT_S") or DEFAULT_TIMEOUT_S),
        max_attempts=int(max_attempts) if max_attempts else None,
        max_pending=int(env.get("HUB_SUBMIT_MAX_PENDING") or DEFAULT_MAX_PENDING),
        sweep_batch=int(env.get("HUB_SUBMIT_SWEEP_BATCH") or DEFAULT_SWEEP_BATCH),
    )


def classify_status(status: int) -> HubOutcome:
    if status == 201:
        return HubOutcome("ok", status)
    if status == 200:
        return HubOutcome("duplicate", status)
    if status == 409:
        return HubOutcome("protected", status)
    if status in (401, 403, 404, 405):
        return HubOutcome("misconfig", status, f"HTTP{status}")
    if status in (408, 425, 429) or status >= 500:
        return HubOutcome("retry", status, "HTTP5xx" if status >= 500 else f"HTTP{status}")
    if 400 <= status < 500:
        return HubOutcome("rejected", status, f"HTTP{status}")
    return HubOutcome("retry", status, f"HTTP{status}")  # 1xx/3xx: not an answer we understand
