"""Evaluation metrics: transaction-level detection quality, lead time and money protected.

Conventions
-----------
* Two operating points. The *hold* point treats ``decision == "hold_verify"`` as a positive
  prediction (the transaction is stopped). The *flagged* point also counts ``step_up`` (friction
  without stopping). Fields prefixed ``flagged_`` are the second point.
* A transaction is scam when ``GroundTruth.is_scam_txn`` says so (victim transfers and mule
  forwards); everything else is benign. ``fpr`` and ``held_benign_rate`` are both computed on
  benign transactions only (held benign / all benign); they are the same quantity by design
  (a benign transaction that is held *is* the false positive), kept as two names so reports can
  say "FPR" for the statistician and "held-benign rate" for the product owner.
* Every rate with an empty denominator is ``None`` (never NaN, never ZeroDivisionError).
* Confidence intervals are Wilson 95% score intervals on the underlying binomial counts.
* Money is ``Decimal`` throughout.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal

from scam_contracts.models import CallRisk, Transaction, TxnDecision
from sim_engine.labels import GroundTruth

Z95 = 1.959963984540054
CALL_ALERT_THRESHOLD = 0.7  # mirrors call_guard.rules.CALL_RISK_THRESHOLD
ROLES = ("victim_transfer", "mule_forward")

Detection = TxnDecision | CallRisk
Interval = tuple[float, float]


def wilson_interval(k: int, n: int, z: float = Z95) -> Interval | None:
    """Wilson score interval for k successes in n trials; None when n == 0."""
    if n < 0 or k < 0 or k > n:
        raise ValueError(f"need 0 <= k <= n, got k={k}, n={n}")
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    lo = 0.0 if k == 0 else max(0.0, centre - half)
    hi = 1.0 if k == n else min(1.0, centre + half)
    return (lo, hi)


def _rate(k: int, n: int) -> float | None:
    return k / n if n else None


def _f1(p: float | None, r: float | None) -> float | None:
    if p is None or r is None:
        return None
    return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def prevalence_adjusted_precision(
    recall: float | None, fpr: float | None, prevalence: float
) -> float | None:
    """Precision at a different scam prevalence pi: TPR*pi / (TPR*pi + FPR*(1-pi)).
    None when recall or fpr is unavailable or the denominator is zero."""
    if recall is None or fpr is None or not 0.0 < prevalence < 1.0:
        return None
    num = recall * prevalence
    den = num + fpr * (1.0 - prevalence)
    return num / den if den > 0 else None


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolation percentile (numpy's default); None for an empty sequence."""
    if not values:
        return None
    xs = sorted(values)
    pos = (len(xs) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


@dataclass(frozen=True)
class RoleMetrics:
    role: str
    n: int
    held: int
    flagged: int
    recall: float | None
    recall_ci: Interval | None
    flagged_recall: float | None
    flagged_recall_ci: Interval | None


@dataclass(frozen=True)
class Metrics:
    n_benign: int
    n_scam: int
    tp: int
    fp: int
    fn: int
    tn: int
    flagged_tp: int
    flagged_fp: int
    flagged_fn: int
    flagged_tn: int
    precision: float | None
    recall: float | None
    f1: float | None
    fpr: float | None
    held_benign_rate: float | None
    step_up_benign_rate: float | None
    flagged_precision: float | None
    flagged_recall: float | None
    flagged_f1: float | None
    flagged_fpr: float | None
    ci: dict[str, Interval | None] = field(default_factory=dict)
    by_rail: dict[str, Metrics] = field(default_factory=dict)
    by_role: dict[str, RoleMetrics] = field(default_factory=dict)


def _counts(rows: Iterable[tuple[bool, str]]) -> dict[str, int]:
    c = dict.fromkeys(("tp", "fp", "fn", "tn", "ftp", "ffp", "ffn", "ftn", "su_benign"), 0)
    for scam, d in rows:
        held, flagged = d == "hold_verify", d != "allow"
        if scam:
            c["tp" if held else "fn"] += 1
            c["ftp" if flagged else "ffn"] += 1
        else:
            c["fp" if held else "tn"] += 1
            c["ffp" if flagged else "ftn"] += 1
            c["su_benign"] += d == "step_up"
    return c


def _build(c: dict[str, int]) -> Metrics:
    n_ben, n_scam = c["fp"] + c["tn"], c["tp"] + c["fn"]
    precision, recall = _rate(c["tp"], c["tp"] + c["fp"]), _rate(c["tp"], n_scam)
    fprec, frec = _rate(c["ftp"], c["ftp"] + c["ffp"]), _rate(c["ftp"], n_scam)
    fpr = _rate(c["fp"], n_ben)
    ci = {
        "precision": wilson_interval(c["tp"], c["tp"] + c["fp"]),
        "recall": wilson_interval(c["tp"], n_scam),
        "fpr": wilson_interval(c["fp"], n_ben),
        "held_benign_rate": wilson_interval(c["fp"], n_ben),
        "step_up_benign_rate": wilson_interval(c["su_benign"], n_ben),
        "flagged_precision": wilson_interval(c["ftp"], c["ftp"] + c["ffp"]),
        "flagged_recall": wilson_interval(c["ftp"], n_scam),
        "flagged_fpr": wilson_interval(c["ffp"], n_ben),
    }
    return Metrics(
        n_benign=n_ben, n_scam=n_scam, tp=c["tp"], fp=c["fp"], fn=c["fn"], tn=c["tn"],
        flagged_tp=c["ftp"], flagged_fp=c["ffp"], flagged_fn=c["ffn"], flagged_tn=c["ftn"],
        precision=precision, recall=recall, f1=_f1(precision, recall), fpr=fpr,
        held_benign_rate=fpr, step_up_benign_rate=_rate(c["su_benign"], n_ben),
        flagged_precision=fprec, flagged_recall=frec, flagged_f1=_f1(fprec, frec),
        flagged_fpr=_rate(c["ffp"], n_ben), ci=ci,
    )  # fmt: skip


def compute_metrics(
    decisions: Iterable[TxnDecision],
    truth: GroundTruth,
    txns: Mapping[str, Transaction] | None = None,
) -> Metrics:
    """Transaction-level metrics. ``txns`` (txn_id -> Transaction) enables the per-rail breakdown;
    decisions for transaction ids missing from it are grouped under ``"UNKNOWN"``."""
    ds = list(decisions)
    rows = [(truth.is_scam_txn(d.txn_id), d.decision) for d in ds]
    total = _build(_counts(rows))
    by_rail: dict[str, Metrics] = {}
    if txns is not None:
        groups: dict[str, list[tuple[bool, str]]] = {}
        for d, row in zip(ds, rows, strict=True):
            t = txns.get(d.txn_id)
            groups.setdefault(t.rail if t else "UNKNOWN", []).append(row)
        by_rail = {r: _build(_counts(g)) for r, g in sorted(groups.items())}
        for rail in ("UPI", "IMPS", "NEFT"):  # always show the three rails, even when empty
            by_rail.setdefault(rail, _build(_counts([])))
        by_rail = dict(sorted(by_rail.items()))
    by_role: dict[str, RoleMetrics] = {}
    for role in ROLES:
        sel = [d.decision for d in ds if truth.txn_role(d.txn_id) == role]
        n, held, flagged = len(sel), sel.count("hold_verify"), len(sel) - sel.count("allow")
        by_role[role] = RoleMetrics(
            role, n, held, flagged, _rate(held, n), wilson_interval(held, n),
            _rate(flagged, n), wilson_interval(flagged, n),
        )  # fmt: skip
    return replace(total, by_rail=by_rail, by_role=by_role)


# --------------------------------------------------------------------------- lead time


@dataclass(frozen=True)
class LeadTime:
    campaign_id: str
    reaches_mass: bool  # the campaign gets to 10 victims at all
    detected: bool  # any detection for the campaign
    first_detection_ts: datetime | None
    mass_victimisation_ts: datetime | None
    lead: timedelta | None  # mass_ts - first_detection_ts (positive = before the 10th victim)
    campaign_detected_before_mass: bool


def first_detection_ts(
    campaign_id: str,
    detections: Iterable[Detection],
    truth: GroundTruth,
    include_calls: bool = False,
    call_threshold: float = CALL_ALERT_THRESHOLD,
) -> datetime | None:
    """Earliest detection attributable to the campaign. A TxnDecision detects when it is
    ``hold_verify``; a CallRisk detects (only with ``include_calls``) when score >= threshold."""
    best: datetime | None = None
    for d in detections:
        if isinstance(d, TxnDecision):
            hit = d.decision == "hold_verify" and truth.campaign_of(d.txn_id) == campaign_id
        else:
            hit = (
                include_calls
                and d.score >= call_threshold
                and truth.campaign_of_call(d.call_id) == campaign_id
            )
        if hit and (best is None or d.ts < best):
            best = d.ts
    return best


def lead_time_detail(
    campaign_id: str,
    detections: Iterable[Detection],
    truth: GroundTruth,
    include_calls: bool = False,
) -> LeadTime:
    mass = truth.mass_victimisation_ts_or_none(campaign_id)
    first = first_detection_ts(campaign_id, detections, truth, include_calls)
    lead = mass - first if (mass is not None and first is not None) else None
    return LeadTime(
        campaign_id, mass is not None, first is not None, first, mass, lead,
        lead is not None and lead > timedelta(0),
    )  # fmt: skip


def lead_time(
    campaign_id: str,
    detections: Iterable[Detection],
    truth: GroundTruth,
    include_calls: bool = False,
) -> timedelta | None:
    """First detection relative to the 10th victim's first transfer (positive = before it).
    None when the campaign never reaches 10 victims or is never detected."""
    return lead_time_detail(campaign_id, detections, truth, include_calls).lead


# --------------------------------------------------------------------- victims protected


@dataclass(frozen=True)
class Protection:
    campaign_id: str
    first_detection_ts: datetime | None
    victim_txns_after_detection: int
    victim_txns_held_after_detection: int
    fraction: float | None  # share of post-detection victim TRANSFERS that were held
    money_at_risk_inr: Decimal  # victim transfers strictly after first detection
    money_prevented_inr: Decimal  # UPPER BOUND: held transfers assumed stopped for good
    victims_with_post_detection_txns: int = 0
    victims_fully_held: int = 0  # victims whose every post-detection transfer was held
    victims_fully_held_fraction: float | None = None


def campaign_protection(
    campaign_id: str,
    detections: Sequence[Detection],
    txns: Mapping[str, Transaction],
    truth: GroundTruth,
    include_calls: bool = False,
) -> Protection:
    """Victim transfers strictly after the first detection, and how many were held.

    The detecting transaction itself is not counted (it is the detection, not a later victim)."""
    first = first_detection_ts(campaign_id, detections, truth, include_calls)
    after = held = 0
    at_risk = prevented = Decimal(0)
    per_victim: dict[str, list[bool]] = {}
    if first is not None:
        decided = {d.txn_id: d for d in detections if isinstance(d, TxnDecision)}
        for tid, t in txns.items():
            if truth.campaign_of(tid) != campaign_id or truth.txn_role(tid) != "victim_transfer":
                continue
            if t.ts <= first:
                continue
            after += 1
            at_risk += t.amount_inr
            d = decided.get(tid)
            was_held = d is not None and d.decision == "hold_verify"
            per_victim.setdefault(t.payer_token, []).append(was_held)
            if was_held:
                held += 1
                prevented += t.amount_inr
    full = sum(1 for v in per_victim.values() if all(v))
    return Protection(
        campaign_id, first, after, held, _rate(held, after), at_risk, prevented,
        len(per_victim), full, _rate(full, len(per_victim)),
    )  # fmt: skip


def victims_protected_fraction(
    campaign_id: str,
    detections: Sequence[Detection],
    txns: Mapping[str, Transaction],
    truth: GroundTruth,
    include_calls: bool = False,
) -> float | None:
    """Share of the campaign's victim *transfers* after its first detection that were held.
    None when the campaign is never detected or no victim transfer follows the detection."""
    return campaign_protection(campaign_id, detections, txns, truth, include_calls).fraction
