"""In-process benchmark: simulator world -> call-guard -> txn-guard -> metrics -> markdown report.

Pipeline (small pluggable pieces, so later stages slot in without a rewrite)::

    events (calls + txns, timestamp order)
      call event -> SessionScorer (call-guard) -> CallRisk on threshold crossing -> history store
      txn event  -> Context (history store, call risk within 15 min)
                 -> extract_features -> FEATURE_STAGES (shared; the antibody lookup goes here)
                 -> per-variant FeatureTransform (ablations) -> Scorer -> decide -> TxnDecision
                 -> history store records the txn

A *variant* is the baseline or one ablation; all variants are scored in the same pass over the
same event stream, so their metrics are directly comparable.

    python -m scam_bench.run_benchmark --seed 42 --ablations no_call_signal \\
        --out docs/benchmark_report.md
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
from scam_contracts.models import CallEvent, CallRisk, Transaction, TxnDecision

from .metrics import (
    Metrics,
    Protection,
    campaign_protection,
    compute_metrics,
    lead_time_detail,
    percentile,
    prevalence_adjusted_precision,
)

FeatureTransform = Callable[[dict[str, float]], dict[str, float]]

TXN_GUARD_RISK_WINDOW_MIN = 15
ANTIBODY_UNAVAILABLE = "antibody-hub not built yet; wired in a later task"


# ------------------------------------------------------------------------------- ablations


@dataclass(frozen=True)
class Ablation:
    """How a variant differs from the baseline. ``call_source`` "real" feeds txn-guard the
    call-guard alerts produced in this run; "degraded" replaces them by training-like imperfect
    call risk (85% recall, 1.5% spurious benign risk; see ``degraded_call_risks``)."""

    transform: FeatureTransform | None = None
    call_source: str = "real"


def _no_call_signal() -> Ablation:
    def transform(features: dict[str, float]) -> dict[str, float]:
        return {**features, "active_call_risk": 0.0}

    return Ablation(transform)


def _degraded_call_signal() -> Ablation:
    return Ablation(None, "degraded")


def _no_antibody() -> Ablation:
    raise NotImplementedError(ANTIBODY_UNAVAILABLE)


ABLATIONS: dict[str, Callable[[], Ablation]] = {
    "no_call_signal": _no_call_signal,
    "degraded_call_signal": _degraded_call_signal,
    "no_antibody": _no_antibody,
}
DEFAULT_ABLATIONS = ["no_call_signal", "degraded_call_signal"]


def resolve_ablations(names: Sequence[str]) -> list[Ablation]:
    """Validate and build the ablations for ``names`` (raises before any work is done)."""
    unknown = [n for n in names if n not in ABLATIONS]
    if unknown:
        raise ValueError(f"unknown ablation(s) {unknown}; known: {sorted(ABLATIONS)}")
    return [ABLATIONS[n]() for n in names]


# ------------------------------------------------------------------------------------ config


@dataclass(frozen=True)
class BenchConfig:
    n_citizens: int = 1500
    days: int = 14
    warmup_days: int = 5  # transactions before this only build history; never scored
    n_campaigns: int = 5
    victims_per_campaign: int = 25
    benign_call_rate: float = 0.05  # benign calls per citizen per day


def forbidden_seeds() -> dict[str, tuple[int, ...]]:
    """Seeds used to fit or calibrate the models; the benchmark must not reuse them."""
    from call_guard.train import CALIB_SEED, REPORT_SEED, TRAIN_SEED
    from txn_guard.train import CALIB_SEEDS, EVAL_SEEDS, TRAIN_SEEDS

    return {
        "txn_guard_train": tuple(TRAIN_SEEDS),
        "txn_guard_calibration": tuple(CALIB_SEEDS),
        "call_guard_train": (TRAIN_SEED, CALIB_SEED, REPORT_SEED),
        "txn_guard_eval (held out, reported only)": tuple(EVAL_SEEDS),
    }


def check_seed(seed: int) -> None:
    f = forbidden_seeds()
    for role, seeds in f.items():
        if "eval" in role:
            continue
        if seed in seeds:
            raise ValueError(f"seed {seed} was used for model training/calibration ({role})")


# --------------------------------------------------------------------------------- scenario


@dataclass
class Scenario:
    world: Any
    campaigns: list[Any]
    truth: Any
    calls: list[CallEvent]
    txns: list[Transaction]
    history_txns: list[Transaction]  # injected prior history for mule payers; never scored
    warm_end: datetime
    scam_call_ids: set[str] = field(default_factory=set)
    benign_call_ids: set[str] = field(default_factory=set)


def build_scenario(seed: int, cfg: BenchConfig) -> Scenario:
    from sim_engine.benign import gen_benign_txns
    from sim_engine.calls import gen_benign_calls
    from sim_engine.labels import GroundTruth
    from sim_engine.scam import gen_scam_campaign
    from sim_engine.world import build_world
    from txn_guard.simdata import mule_history

    world = build_world(seed, cfg.n_citizens)
    benign = list(gen_benign_txns(world, cfg.days, seed))
    benign_calls = list(gen_benign_calls(world, cfg.days, seed, cfg.benign_call_rate))
    rng = np.random.default_rng([seed, 8008])
    campaigns = []
    for k in range(cfg.n_campaigns):
        start = world.start + timedelta(
            days=int(rng.integers(cfg.warmup_days, cfg.days - 1)), hours=float(rng.uniform(8, 18))
        )
        campaigns.append(
            gen_scam_campaign(world, f"bench{k}", cfg.victims_per_campaign, seed, start_ts=start)
        )
    truth = GroundTruth(campaigns)
    mrng = np.random.default_rng([seed, 9009])
    history = [payload for c in campaigns for _, _, payload in mule_history(mrng, c, world.start)]
    scam_calls = [e for c in campaigns for e in c.calls]
    return Scenario(
        world=world,
        campaigns=campaigns,
        truth=truth,
        calls=sorted(benign_calls + scam_calls, key=lambda e: e.ts),
        txns=benign + [t for c in campaigns for t in c.txns],
        history_txns=history,
        warm_end=world.start + timedelta(days=cfg.warmup_days),
        scam_call_ids={e.call_id for e in scam_calls},
        benign_call_ids={e.call_id for e in benign_calls},
    )


def degraded_call_risks(sc: Scenario, seed: int) -> list[CallRisk]:
    """Training-like imperfect call risk (what txn-guard was trained and calibrated on), seeded.

    Mirrors ``txn_guard.simdata.build_stream``: each scam victim's call is detected with
    probability ``CALL_RECALL``, 1-3 chunks after its last chunk, then re-emitted every minute
    for 20 minutes; ``SPURIOUS_RATE`` of benign transactions get a spurious risk 1-10 minutes
    earlier. Sorted by time."""
    from txn_guard import simdata as sd

    rng = np.random.default_rng([seed, 6006])

    def risk(token: str, call_id: str, score: float, ts: datetime) -> CallRisk:
        return CallRisk(
            call_id=call_id, victim_token=token, score=round(float(score), 3), reasons=[],
            model_version=sd.RISK_MODEL, ts=ts,
        )  # fmt: skip

    out: list[CallRisk] = []
    for t in sc.txns:
        if not sc.truth.is_scam_txn(t.txn_id) and rng.random() < sd.SPURIOUS_RATE:
            ts = t.ts - timedelta(minutes=float(rng.uniform(1, 10)))
            out.append(risk(t.payer_token, f"spur-{t.txn_id}", rng.uniform(0.7, 0.95), ts))
    for c in sc.campaigns:
        by_victim: dict[str, list[CallEvent]] = {}
        for e in c.calls:
            by_victim.setdefault(e.victim_token, []).append(e)
        for tok, evs in by_victim.items():
            if rng.random() >= sd.CALL_RECALL:
                continue
            n_chunks = int(rng.integers(sd.DETECT_LAG_CHUNKS[0], sd.DETECT_LAG_CHUNKS[1] + 1))
            lag = float(rng.uniform(*sd.CHUNK_GAP_S, n_chunks).sum())
            first = max(e.ts for e in evs) + timedelta(seconds=lag)
            ts = first
            while ts <= first + sd.RISK_REEMIT_FOR:
                out.append(risk(tok, evs[-1].call_id, rng.uniform(0.7, 1.0), ts))
                ts += sd.RISK_REEMIT_EVERY
    out.sort(key=lambda r: (r.ts, r.call_id))
    return out


# ------------------------------------------------------------------------------------ stages


LATENCY_SAMPLE_EVERY = 10  # in fast mode, 1 in N transactions is also timed on the full path


class Pipeline:
    """txn-guard side of the pipeline for one pass over the event stream."""

    def __init__(
        self,
        scorer: Any,
        variants: dict[str, Ablation],
        feature_stages: Sequence[FeatureTransform] = (),
        degraded_risks: Sequence[CallRisk] = (),
        with_reasons: bool = False,
    ) -> None:
        from txn_guard.history import InMemoryHistoryStore

        self.store = InMemoryHistoryStore()
        self.degraded_store = InMemoryHistoryStore()  # call risks only, for degraded variants
        self._degraded = list(degraded_risks)
        self._deg_i = 0
        self.scorer = scorer
        self.variants = variants
        self.with_reasons = with_reasons
        self.feature_stages = list(feature_stages)  # shared by every variant (antibody goes here)
        self.latencies_ms: list[float] = []
        self._n = 0
        # fast mode (no reasons): features are decision-independent, so scoring is deferred and
        # done in one batch per variant by ``flush`` (identical decisions, far fewer model calls)
        self._pending: dict[str, list[tuple[str, dict[str, float], datetime]]] = {
            name: [] for name in variants
        }
        self.decisions: dict[str, list[TxnDecision]] = {name: [] for name in variants}

    def on_call_risk(self, risk: CallRisk) -> None:
        self.store.record_call_risk(risk)

    def on_history_txn(self, txn: Transaction) -> None:
        self.store.record_txn(txn)

    def _degraded_call_feature(self, txn: Transaction) -> float:
        from txn_guard.features import extract_features

        while self._deg_i < len(self._degraded) and self._degraded[self._deg_i].ts <= txn.ts:
            self.degraded_store.record_call_risk(self._degraded[self._deg_i])
            self._deg_i += 1
        ctx = self.degraded_store.context_for(txn, txn.ts)
        return extract_features(txn, ctx)["active_call_risk"]

    def on_txn(self, txn: Transaction) -> dict[str, TxnDecision]:
        from txn_guard.decision import make_decision
        from txn_guard.features import extract_features

        t0 = time.perf_counter()
        ctx = self.store.context_for(txn, txn.ts)
        feats = extract_features(txn, ctx)
        for stage in self.feature_stages:
            feats = stage(feats)
        t_feat = time.perf_counter()
        out: dict[str, TxnDecision] = {}
        base_feats = feats
        for i, (name, ab) in enumerate(self.variants.items()):
            f = base_feats
            if ab.call_source == "degraded":
                f = {**f, "active_call_risk": self._degraded_call_feature(txn)}
            if ab.transform is not None:
                f = ab.transform(f)
            if self.with_reasons:
                out[name] = make_decision(txn.txn_id, f, self.scorer, txn.ts)
            else:
                self._pending[name].append((txn.txn_id, f, txn.ts))
            if i == 0:  # baseline is first: its per-transaction latency is what we report
                if self.with_reasons:
                    self.latencies_ms.append((time.perf_counter() - t0) * 1000.0)
                elif self._n % LATENCY_SAMPLE_EVERY == 0:
                    # fast mode: time the full production path (reasons on) on a 1-in-N sample
                    t1 = time.perf_counter()
                    make_decision(txn.txn_id, f, self.scorer, txn.ts, with_reasons=True)
                    self.latencies_ms.append(((t_feat - t0) + (time.perf_counter() - t1)) * 1000.0)
        self._n += 1
        self.store.record_txn(txn)
        for name, d in out.items():
            self.decisions[name].append(d)
        return out

    def flush(self) -> dict[str, list[TxnDecision]]:
        """Score deferred (fast-mode) transactions in batch; returns decisions per variant."""
        from txn_guard.decision import make_decisions

        for name, items in self._pending.items():
            self.decisions[name].extend(make_decisions(items, self.scorer))
            items.clear()
        return self.decisions


async def _run_stream(
    sc: Scenario, pipe: Pipeline
) -> tuple[dict[str, list[TxnDecision]], list[CallRisk], dict[str, str]]:
    from call_guard.model import Scorer as CallScorer
    from call_guard.model import load_classifier
    from call_guard.session import InMemorySessionStore, SessionScorer

    call_scorer = CallScorer(load_classifier())
    sessions = SessionScorer(InMemorySessionStore(), call_scorer)
    events: list[tuple[datetime, int, int, str, Any]] = []
    for i, e in enumerate(sc.calls):
        events.append((e.ts, 0, i, "call", e))
    for i, t in enumerate(sc.history_txns):
        events.append((t.ts, 1, i, "hist", t))
    for i, t in enumerate(sc.txns):
        events.append((t.ts, 1, len(sc.history_txns) + i, "txn", t))
    events.sort(key=lambda x: x[:3])

    risks: list[CallRisk] = []
    for ts, _, _, kind, payload in events:
        if kind == "call":
            _, pending = await sessions.update_with_crossing(payload)
            if pending:
                risk = await sessions.crossing_risk(payload)
                if risk is not None:
                    await sessions.mark_published(
                        payload.call_id, await sessions.crossing_no(payload.call_id)
                    )
                    risks.append(risk)
                    pipe.on_call_risk(risk)
        elif kind == "hist":
            pipe.on_history_txn(payload)
        else:
            if ts < sc.warm_end:
                pipe.on_history_txn(payload)  # warm-up: build history only
                continue
            pipe.on_txn(payload)
    versions = {"call_guard": call_scorer.model_version, "txn_guard": pipe.scorer.model_version}
    return pipe.flush(), risks, versions


# ------------------------------------------------------------------------------------ result


@dataclass(frozen=True)
class CampaignRow:
    campaign_id: str
    n_victims: int
    lead_hold: Any  # LeadTime, detection = first hold_verify
    lead_alert: Any  # LeadTime, detection = first hold_verify or call alert
    protection_hold: Protection
    protection_alert: Protection


def git_commit() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True, timeout=5,
            cwd=Path(__file__).resolve().parent,
        )  # fmt: skip
        return r.stdout.strip() or "unavailable"
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def _degraded_params() -> dict[str, float]:
    from txn_guard import simdata as sd

    return {"call_recall": sd.CALL_RECALL, "spurious_rate": sd.SPURIOUS_RATE}


def default_report_path() -> Path:
    return Path(__file__).resolve().parents[4] / "docs" / "benchmark_report.md"


def run_benchmark(
    seed: int,
    ablations: list[str],
    config: BenchConfig | None = None,
    out: Path | str | None = None,
    include_volatile: bool = False,
    generated_at: str | None = None,
    with_reasons: bool = False,
) -> dict[str, Any]:
    """Run baseline + ``ablations`` over a fresh world; write the markdown report to ``out``
    (default ``docs/benchmark_report.md`` at the repo root; ``out=''`` skips writing).

    ``with_reasons=False`` (default) skips the occlusion-reason computation in txn-guard scoring;
    scores and decisions are identical, only faster. The report omits machine-dependent latency
    unless ``include_volatile`` (so a given seed gives a byte-identical report)."""
    cfg = config or BenchConfig()
    abl = resolve_ablations(ablations)  # NotImplementedError / ValueError before any work
    check_seed(seed)
    from txn_guard.model import Scorer as TxnScorer

    sc = build_scenario(seed, cfg)
    variants: dict[str, Ablation] = {"baseline": Ablation()}
    variants.update(dict(zip(ablations, abl, strict=True)))
    degraded = (
        degraded_call_risks(sc, seed)
        if any(a.call_source == "degraded" for a in variants.values())
        else []
    )
    pipe = Pipeline(TxnScorer(None), variants, degraded_risks=degraded, with_reasons=with_reasons)
    decisions, risks, versions = asyncio.run(_run_stream(sc, pipe))

    txn_by_id = {t.txn_id: t for t in sc.txns}
    res_variants: dict[str, Any] = {}
    for name, ds in decisions.items():
        detections = [*ds, *(degraded if variants[name].call_source == "degraded" else risks)]
        rows = []
        for c in sc.campaigns:
            rows.append(
                CampaignRow(
                    c.campaign_id,
                    len(c.victim_first_txn_ts),
                    lead_time_detail(c.campaign_id, detections, sc.truth),
                    lead_time_detail(c.campaign_id, detections, sc.truth, include_calls=True),
                    campaign_protection(c.campaign_id, detections, txn_by_id, sc.truth),
                    campaign_protection(
                        c.campaign_id, detections, txn_by_id, sc.truth, include_calls=True
                    ),
                )
            )
        res_variants[name] = {
            "metrics": compute_metrics(ds, sc.truth, txn_by_id),
            "lead": rows,
            "decision_count": len(ds),
        }
    alerted = {r.call_id for r in risks}
    calls = {
        "scam_calls": len(sc.scam_call_ids),
        "scam_calls_alerted": len(sc.scam_call_ids & alerted),
        "benign_calls": len(sc.benign_call_ids),
        "benign_calls_alerted": len(sc.benign_call_ids & alerted),
        "alerts_published": len(risks),
    }
    lat = pipe.latencies_ms
    result: dict[str, Any] = {
        "seed": seed,
        "ablations": list(ablations),
        "config": asdict(cfg),
        "variants": res_variants,
        "calls": calls,
        "latency_ms": {"p50": percentile(lat, 50), "p99": percentile(lat, 99), "n": len(lat)},
        "model_versions": versions,
        "git_commit": git_commit(),
        "forbidden_seeds": forbidden_seeds(),
        "mule_history_share": __import__("txn_guard.simdata", fromlist=["x"]).MULE_HISTORY_SHARE,
        "degraded_params": _degraded_params(),
        "truth": sc.truth,
        "txns": txn_by_id,
        "decisions": decisions,
        "call_risks": risks,
    }
    path = default_report_path() if out is None else (Path(out) if out != "" else None)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            render_report(result, include_volatile=include_volatile, generated_at=generated_at)
        )
        result["report_path"] = str(path)
    return result


# ------------------------------------------------------------------------------------ report


def _pct(v: float | None, ci: tuple[float, float] | None = None, digits: int = 2) -> str:
    if v is None:
        return "n/a (empty denominator)"
    s = f"{v * 100:.{digits}f}%"
    if ci is not None:
        s += f" [{ci[0] * 100:.{digits}f}, {ci[1] * 100:.{digits}f}]"
    return s


def _td(td: timedelta | None) -> str:
    if td is None:
        return "n/a"
    total = int(td.total_seconds())
    sign = "-" if total < 0 else "+"
    h, rem = divmod(abs(total), 3600)
    return f"{sign}{h}h{rem // 60:02d}m{rem % 60:02d}s"


def _inr(x: Decimal) -> str:
    return f"INR {x:,.2f}"


def _ms(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.2f} ms"


def _headline(m: Metrics) -> list[str]:
    ci = m.ci
    rails = [(r, x) for r, x in m.by_rail.items() if x.fpr is not None]
    worst = max(rails, key=lambda rx: rx[1].fpr or 0.0) if rails else None
    wrow = (
        f"| **Worst-rail FPR** ({worst[0]}) | {_pct(worst[1].fpr, worst[1].ci['fpr'], 3)} |"
        if worst
        else "| **Worst-rail FPR** | n/a |"
    )
    return [
        "| Metric | Value (95% Wilson CI) |",
        "|---|---|",
        f"| Precision | {_pct(m.precision, ci['precision'])} |",
        f"| Recall (all scam txns) | {_pct(m.recall, ci['recall'])} |",
        f"| F1 | {_pct(m.f1)} |",
        f"| FPR (held benign / benign, all rails pooled) | {_pct(m.fpr, ci['fpr'], 3)} |",
        f"| Held-benign rate | {_pct(m.held_benign_rate, ci['held_benign_rate'], 3)} |",
        wrow,
        f"| Counts (TP / FP / FN / TN) | {m.tp} / {m.fp} / {m.fn} / {m.tn} |",
    ]


def _rail_fpr_table(m: Metrics) -> list[str]:
    lines = [
        "**Per-rail false-positive rate (read this next to the pooled FPR above).** "
        "The pooled FPR is dominated by UPI volume and hides rail-level problems.",
        "",
        "| Rail | Benign txns | FPR, hold (95% CI) | FPR, step-up or hold (95% CI) |",
        "|---|---|---|---|",
    ]
    for rail, r in m.by_rail.items():
        lines.append(
            f"| {rail} | {r.n_benign} | {_pct(r.fpr, r.ci['fpr'], 3)} | "
            f"{_pct(r.flagged_fpr, r.ci['flagged_fpr'], 3)} |"
        )
    return lines


def _flagged(m: Metrics) -> list[str]:
    ci = m.ci
    return [
        "| Metric | Value (95% Wilson CI) |",
        "|---|---|",
        f"| Flagged precision | {_pct(m.flagged_precision, ci['flagged_precision'])} |",
        f"| Flagged recall | {_pct(m.flagged_recall, ci['flagged_recall'])} |",
        f"| Flagged F1 | {_pct(m.flagged_f1)} |",
        f"| Flagged FPR | {_pct(m.flagged_fpr, ci['flagged_fpr'], 3)} |",
        f"| Benign step-up only rate | {_pct(m.step_up_benign_rate, ci['step_up_benign_rate'], 3)} |",
        f"| Counts (TP / FP / FN / TN) | {m.flagged_tp} / {m.flagged_fp} / "
        f"{m.flagged_fn} / {m.flagged_tn} |",
    ]


def _prevalence_table(m: Metrics) -> list[str]:
    sim_pi = m.n_scam / (m.n_scam + m.n_benign) if (m.n_scam + m.n_benign) else None
    pis = [("simulator", sim_pi), ("0.1%", 0.001), ("0.01%", 0.0001)]
    lines = [
        "Precision depends on scam prevalence pi: precision = TPR*pi / (TPR*pi + FPR*(1-pi)), "
        "with TPR and FPR taken from this run. The pooled-FPR upper bound is the upper end of "
        "its 95% Wilson interval (a conservative reading).",
        "",
        "| Prevalence | pi | Precision, hold | Precision, hold (FPR upper bound) "
        "| Precision, step-up or hold |",
        "|---|---|---|---|---|",
    ]
    f_hi = m.ci["fpr"][1] if m.ci.get("fpr") else None
    for label, pi in pis:
        if pi is None:
            continue
        lines.append(
            f"| {label} | {pi * 100:.3f}% | "
            f"{_pct(prevalence_adjusted_precision(m.recall, m.fpr, pi), None, 2)} | "
            f"{_pct(prevalence_adjusted_precision(m.recall, f_hi, pi), None, 2)} | "
            f"{_pct(prevalence_adjusted_precision(m.flagged_recall, m.flagged_fpr, pi), None, 2)} |"
        )
    lines += [
        "",
        "The headline precision will NOT transfer to real traffic: at realistic prevalence the "
        "same recall and FPR give a far lower precision.",
    ]
    return lines


def _yn(lt: Any) -> str:
    if not lt.reaches_mass:
        return "n/a (<10 victims)"
    return "yes" if lt.campaign_detected_before_mass else "no"


def _protection_rows(rows: list[CampaignRow]) -> tuple[list[str], dict[str, Decimal]]:
    tot = {"prevented": Decimal(0), "at_risk": Decimal(0)}
    lines = [
        "| Campaign | Victims | Lead, first hold | Before 10th victim | Lead, first alert or hold "
        "| Before 10th victim |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.campaign_id} | {r.n_victims} | {_td(r.lead_hold.lead)} | "
            f"{_yn(r.lead_hold)} | {_td(r.lead_alert.lead)} | {_yn(r.lead_alert)} |"
        )
    lines += [
        "",
        "Lead = time of the 10th victim's first transfer minus the first detection "
        "(positive: detected before mass victimisation). `n/a` means the campaign never "
        "reaches 10 victims or was never detected.",
        "",
        "Share of post-detection victim transfers held. This is a *transaction* fraction (a "
        "victim makes several split transfers), counted over victim transfers strictly after the "
        "first detection; the detecting transfer itself is excluded. The per-victim column counts "
        "victims all of whose post-detection transfers were held.",
        "",
        "| Campaign | Victim txns after first hold | Held | Share held (hold) "
        "| Victims fully held | Money prevented, upper bound (hold) "
        "| Victim txns after first alert/hold | Held | Share held (alert) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        h, a = r.protection_hold, r.protection_alert
        tot["prevented"] += h.money_prevented_inr
        tot["at_risk"] += h.money_at_risk_inr
        lines.append(
            f"| {r.campaign_id} | {h.victim_txns_after_detection} | "
            f"{h.victim_txns_held_after_detection} | {_pct(h.fraction, None, 1)} | "
            f"{h.victims_fully_held} of {h.victims_with_post_detection_txns} "
            f"({_pct(h.victims_fully_held_fraction, None, 1)}) | "
            f"{_inr(h.money_prevented_inr)} | {a.victim_txns_after_detection} | "
            f"{a.victim_txns_held_after_detection} | {_pct(a.fraction, None, 1)} |"
        )
    return lines, tot


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def render_report(
    result: dict[str, Any], include_volatile: bool = False, generated_at: str | None = None
) -> str:
    """Markdown report. Machine-dependent latency is only included with ``include_volatile``;
    without it the output is a pure function of the seed, config and code (byte-reproducible)."""
    base = result["variants"]["baseline"]
    m: Metrics = base["metrics"]
    cfg = result["config"]
    total = m.n_benign + m.n_scam
    sim_pi = m.n_scam / total if total else None
    L: list[str] = ["# Benchmark report", ""]
    if generated_at:
        L += [f"_Generated at {generated_at}._", ""]
    L += [
        "In-process evaluation of the scam-immune-system pipeline (call-guard -> txn-guard) over a "
        "fresh simulator world. Decisions are transaction-level; a *hold* is `hold_verify`. "
        "All rates carry 95% Wilson confidence intervals. The simulator is the only data source "
        "(see Limitations).",
        "",
        f"World: {cfg['n_citizens']} citizens, {cfg['days']} days ({cfg['warmup_days']} warm-up "
        f"days only build history), {cfg['n_campaigns']} scam campaigns x "
        f"{cfg['victims_per_campaign']} victims. Scored transactions: {total} "
        f"({m.n_benign} benign, {m.n_scam} scam; scam prevalence {_pct(sim_pi, None, 3)}).",
        "",
        "## Headline metrics",
        "",
        "Operating point: positive prediction = `hold_verify` (score >= 0.8).",
        "",
        *_headline(m),
        "",
        *_rail_fpr_table(m),
        "",
        "## Operating point: step-up or hold",
        "",
        "Second operating point: `step_up` (score >= 0.5) also counts as flagged.",
        "",
        *_flagged(m),
        "",
        "## Precision at realistic prevalence",
        "",
        *_prevalence_table(m),
        "",
        "## Per-rail breakdown",
        "",
        "| Rail | Benign | Scam | Recall (hold) | Flagged recall | FPR (hold) | Flagged FPR |",
        "|---|---|---|---|---|---|---|",
    ]
    for rail, r in m.by_rail.items():
        L.append(
            f"| {rail} | {r.n_benign} | {r.n_scam} | {_pct(r.recall, r.ci['recall'], 1)} | "
            f"{_pct(r.flagged_recall, r.ci['flagged_recall'], 1)} | "
            f"{_pct(r.fpr, r.ci['fpr'], 3)} | {_pct(r.flagged_fpr, r.ci['flagged_fpr'], 3)} |"
        )
    L += [
        "",
        "## Per-role breakdown",
        "",
        "| Role | Transactions | Held | Recall (hold) | Flagged | Flagged recall |",
        "|---|---|---|---|---|---|",
    ]
    for role, r in m.by_role.items():
        L.append(
            f"| {role} | {r.n} | {r.held} | {_pct(r.recall, r.recall_ci, 1)} | {r.flagged} | "
            f"{_pct(r.flagged_recall, r.flagged_recall_ci, 1)} |"
        )
    mule = m.by_role.get("mule_forward")
    L += [
        "",
        f"Mule-forward recall ({_pct(mule.recall if mule else None, None, 1)}) is the weakest "
        "role; it is what the antibody stage (shared mule intelligence) is meant to improve.",
    ]
    plines, tot = _protection_rows(base["lead"])
    mass = [r for r in base["lead"] if r.lead_hold.reaches_mass]
    before = sum(1 for r in mass if r.lead_hold.campaign_detected_before_mass)
    shares = [
        r.protection_hold.fraction for r in base["lead"] if r.protection_hold.fraction is not None
    ]
    L += [
        "",
        "## Lead time before mass victimisation",
        "",
        *plines,
        "",
        f"Campaigns reaching 10 victims: {len(mass)} of {len(base['lead'])}; detected by a hold "
        f"before the 10th victim: {before} of {len(mass)}. Mean share of post-detection victim "
        f"transfers held (over detected campaigns): {_pct(_mean(shares), None, 1)}. Money "
        f"prevented (upper bound) on victim transfers after first hold: {_inr(tot['prevented'])} "
        f"of {_inr(tot['at_risk'])} at risk after first hold.",
        "",
        "Money-prevented assumption: a held transfer is treated as stopped for good. The "
        "simulator does not model a victim retrying through another payee or channel, and "
        "`hold_verify` is assumed to end in rejection, not release; real prevented loss is lower.",
        "",
        "Campaign-structure caveat: each campaign has "
        f"{cfg['victims_per_campaign']} victims and detection happens near victim 1-3, so most "
        "victims fall after detection; the held share is inflated relative to slower, "
        "less concentrated real campaigns.",
        "",
        "Call-guard (session level, real run): "
        f"{result['calls']['scam_calls_alerted']} of {result['calls']['scam_calls']} scam calls "
        f"and {result['calls']['benign_calls_alerted']} of {result['calls']['benign_calls']} "
        f"benign calls crossed the alert threshold ({result['calls']['alerts_published']} alerts "
        "published). This is an idealised, simulator-perfect call signal; see the "
        "`degraded_call_signal` variant below.",
        "",
        "## Ablations",
        "",
        "| Variant | Precision | Recall (hold) | Flagged recall | FPR (hold) "
        "| Mean share of post-detection transfers held | Money prevented (upper bound) |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, v in result["variants"].items():
        vm: Metrics = v["metrics"]
        fr = [
            r.protection_hold.fraction for r in v["lead"] if r.protection_hold.fraction is not None
        ]
        money = sum((r.protection_hold.money_prevented_inr for r in v["lead"]), Decimal(0))
        L.append(
            f"| {name} | {_pct(vm.precision, vm.ci['precision'], 1)} | "
            f"{_pct(vm.recall, vm.ci['recall'], 1)} | {_pct(vm.flagged_recall, None, 1)} | "
            f"{_pct(vm.fpr, vm.ci['fpr'], 3)} | {_pct(_mean(fr), None, 1)} | {_inr(money)} |"
        )
    dp = result["degraded_params"]
    L += [
        "",
        "`no_call_signal`: txn-guard sees `active_call_risk = 0`. `degraded_call_signal`: the "
        "call-guard output is replaced by training-like imperfect call risk (seeded): each scam "
        f"victim's call is detected with probability {dp['call_recall']:.0%}, 1-3 chunks late, "
        f"and {dp['spurious_rate']:.1%} of benign transactions receive a spurious risk. "
        "`no_antibody` is registered but not runnable until the antibody-hub exists; requesting "
        "it raises `NotImplementedError`.",
        "",
    ]
    if include_volatile:
        lt = result["latency_ms"]
        L += [
            "## Scoring latency (machine-dependent)",
            "",
            "Per-transaction txn-guard latency on the full production path (history context + "
            "features + model + reasons + decision), baseline variant, measured on this machine "
            f"(1 in {LATENCY_SAMPLE_EVERY} transactions when reasons are skipped for speed). "
            "Not reproducible across machines or runs; omitted from the default report.",
            "",
            "| Statistic | Value |",
            "|---|---|",
            f"| p50 | {_ms(lt['p50'])} |",
            f"| p99 | {_ms(lt['p99'])} |",
            f"| Transactions timed | {lt['n']} |",
            "",
        ]
    fb = result["forbidden_seeds"]
    L += [
        "## Reproducibility",
        "",
        f"- Benchmark seed: {result['seed']} (world, benign traffic, benign calls); campaign "
        "placement uses substreams `[seed, 8008]`, mule history `[seed, 9009]`, degraded call "
        "signal `[seed, 6006]`.",
        f"- Ablations requested: {', '.join(result['ablations']) or 'none'}.",
        f"- Git commit: {result['git_commit']}.",
        f"- Models: call-guard `{result['model_versions']['call_guard']}`, txn-guard "
        f"`{result['model_versions']['txn_guard']}`.",
        f"- As in training, {result['mule_history_share']:.0%} of mule payers received injected "
        "prior benign UPI history (5-40 transfers) so that 'no history' is not a scam giveaway.",
        "- Seeds used to train or calibrate the models (the benchmark seed is checked to be "
        "disjoint from these):",
    ]
    for role, seeds in fb.items():
        L.append(f"  - {role}: {', '.join(str(x) for x in seeds)}")
    L += [
        f"- Command: `python -m scam_bench.run_benchmark --seed {result['seed']} "
        f"--ablations {' '.join(result['ablations'])}`",
        "",
        "## Limitations",
        "",
        "- Simulator only. Every number here is measured on synthetic data generated by the same "
        "codebase that the models were trained on (disjoint seeds, shared generators). Treat the "
        "figures as a regression and ablation harness, not as real-world performance.",
        "- NEFT has no scam transactions: the simulator's scam flows use UPI and IMPS only, so "
        "NEFT recall is undefined and the model never saw NEFT positives. NEFT amounts are "
        "naturally ~5x larger, so txn-guard scales its absolute-amount policy thresholds per rail "
        "(NEFT x5), requires z >= 6 on NEFT before the overlays escalate and caps the model at "
        "step-up-minus-0.01 there unless a call risk is active or history is short. "
        f"NEFT benign transactions are held at {_pct(_rate_of(m, 'NEFT', 'fpr'), None, 2)} (hold) "
        f"and flagged at {_pct(_rate_of(m, 'NEFT', 'flagged_fpr'), None, 2)} (step-up or hold) in "
        "this run. The scaling has a cost: Rs 25k-250k NEFT to payees >= 30 days old with no call "
        "passes, test-then-escalate on NEFT holds only below 7 days, and NEFT scams in the "
        "Rs 25k-125k range rely on the young-payee floors. NEFT detection is tested by "
        "hand-written scenarios only (no NEFT scams in the simulator) and the scale and z values "
        "are simulator-derived, not real-data-derived.",
        "- Call signal is idealised in the main run: simulator call-guard alerts every scam call "
        "and no benign call, whereas txn-guard was trained and calibrated on 85% call recall and "
        "1.5% spurious benign risk. The call signal matters: recall falls from "
        f"{_pct(m.recall, None, 1)} with calls to "
        f"{_pct(_variant_recall(result, 'no_call_signal'), None, 1)} without them. Compare with "
        "the `degraded_call_signal` row for a training-like imperfect signal.",
        "- Calibration prevalence: the txn-guard model was calibrated at roughly 1% scam "
        f"prevalence; this run's prevalence is {_pct(sim_pi, None, 2)} and real prevalence is far "
        "lower. Precision depends directly on prevalence (see the prevalence table); recall and "
        "FPR do not.",
        "- Call alerts reach txn-guard once, at the threshold crossing (as in production "
        "call-guard), and count as active for 15 minutes; long calls can outlast the window.",
        "- Held transactions are still recorded in the payer's history (the pipeline does not "
        "model the hold changing later behaviour). Duplicate and out-of-order events are not "
        "exercised here.",
        "- Lead time uses the simulator's 10th victim as the mass-victimisation point; it is "
        "measured per campaign with few campaigns, so it carries wide uncertainty.",
        "- Antibody (cross-bank mule sharing) is not part of the pipeline yet; the `no_antibody` "
        "ablation will be enabled when the antibody-hub is built.",
        "",
    ]
    return "\n".join(L)


def _rate_of(m: Metrics, rail: str, field_name: str) -> float | None:
    r = m.by_rail.get(rail)
    return getattr(r, field_name) if r else None


def _variant_recall(result: dict[str, Any], name: str) -> float | None:
    v = result["variants"].get(name)
    return v["metrics"].recall if v else None


# ----------------------------------------------------------------------------------- CLI


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scam_bench.run_benchmark")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--ablations",
        nargs="*",
        default=None,
        help=f"any of {sorted(ABLATIONS)} (default: {' '.join(DEFAULT_ABLATIONS)}; "
        "pass the flag with no names for baseline only)",
    )
    ap.add_argument("--out", default=str(default_report_path()))
    ap.add_argument("--citizens", type=int, default=BenchConfig.n_citizens)
    ap.add_argument("--days", type=int, default=BenchConfig.days)
    ap.add_argument("--campaigns", type=int, default=BenchConfig.n_campaigns)
    ap.add_argument("--victims", type=int, default=BenchConfig.victims_per_campaign)
    ap.add_argument(
        "--include-latency",
        action="store_true",
        help="add the machine-dependent latency block (makes the report non-reproducible)",
    )
    ap.add_argument(
        "--reasons",
        action="store_true",
        help="compute occlusion reasons while scoring (slower; decisions are identical)",
    )
    ap.add_argument(
        "--stamp", action="store_true", help="add a generated-at line (non-deterministic)"
    )
    a = ap.parse_args(argv)
    ablations = DEFAULT_ABLATIONS if a.ablations is None else a.ablations
    try:
        resolve_ablations(ablations)
        check_seed(a.seed)
    except (ValueError, NotImplementedError) as e:
        ap.error(str(e))
    cfg = BenchConfig(
        n_citizens=a.citizens, days=a.days, n_campaigns=a.campaigns, victims_per_campaign=a.victims
    )
    stamp = datetime.now().astimezone().isoformat(timespec="seconds") if a.stamp else None
    t0 = time.perf_counter()
    res = run_benchmark(
        a.seed,
        ablations,
        config=cfg,
        out=a.out,
        include_volatile=a.include_latency,
        generated_at=stamp,
        with_reasons=a.reasons,
    )
    m: Metrics = res["variants"]["baseline"]["metrics"]
    print(
        f"seed={a.seed} scored={m.n_benign + m.n_scam} precision={m.precision} recall={m.recall} "
        f"fpr={m.fpr} runtime={time.perf_counter() - t0:.1f}s report={res.get('report_path')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
