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
)

FeatureTransform = Callable[[dict[str, float]], dict[str, float]]

TXN_GUARD_RISK_WINDOW_MIN = 15
ANTIBODY_UNAVAILABLE = "antibody-hub not built yet; wired in a later task"


# ------------------------------------------------------------------------------- ablations


def _no_call_signal() -> FeatureTransform:
    def transform(features: dict[str, float]) -> dict[str, float]:
        return {**features, "active_call_risk": 0.0}

    return transform


def _no_antibody() -> FeatureTransform:
    raise NotImplementedError(ANTIBODY_UNAVAILABLE)


ABLATIONS: dict[str, Callable[[], FeatureTransform]] = {
    "no_call_signal": _no_call_signal,
    "no_antibody": _no_antibody,
}


def resolve_ablations(names: Sequence[str]) -> list[FeatureTransform]:
    """Validate and build the transforms for ``names`` (raises before any work is done)."""
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
    from txn_guard.simdata import _mule_history

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
    history = [payload for c in campaigns for _, _, payload in _mule_history(mrng, c, world.start)]
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


# ------------------------------------------------------------------------------------ stages


class Pipeline:
    """txn-guard side of the pipeline for one pass over the event stream."""

    def __init__(
        self,
        scorer: Any,
        variants: dict[str, list[FeatureTransform]],
        feature_stages: Sequence[FeatureTransform] = (),
    ) -> None:
        from txn_guard.history import InMemoryHistoryStore

        self.store = InMemoryHistoryStore()
        self.scorer = scorer
        self.variants = variants
        self.feature_stages = list(feature_stages)  # shared by every variant (antibody goes here)
        self.latencies_ms: list[float] = []

    def on_call_risk(self, risk: CallRisk) -> None:
        self.store.record_call_risk(risk)

    def on_history_txn(self, txn: Transaction) -> None:
        self.store.record_txn(txn)

    def on_txn(self, txn: Transaction) -> dict[str, TxnDecision]:
        from txn_guard.decision import make_decision
        from txn_guard.features import extract_features

        t0 = time.perf_counter()
        ctx = self.store.context_for(txn, txn.ts)
        feats = extract_features(txn, ctx)
        for stage in self.feature_stages:
            feats = stage(feats)
        out: dict[str, TxnDecision] = {}
        for i, (name, transforms) in enumerate(self.variants.items()):
            f = feats
            for tf in transforms:
                f = tf(f)
            out[name] = make_decision(txn.txn_id, f, self.scorer, txn.ts)
            if i == 0:  # baseline is first: its per-transaction latency is what we report
                self.latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        self.store.record_txn(txn)
        return out


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

    decisions: dict[str, list[TxnDecision]] = {name: [] for name in pipe.variants}
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
            for name, d in pipe.on_txn(payload).items():
                decisions[name].append(d)
    versions = {"call_guard": call_scorer.model_version, "txn_guard": pipe.scorer.model_version}
    return decisions, risks, versions


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


def default_report_path() -> Path:
    return Path(__file__).resolve().parents[4] / "docs" / "benchmark_report.md"


def run_benchmark(
    seed: int,
    ablations: list[str],
    config: BenchConfig | None = None,
    out: Path | str | None = None,
    include_volatile: bool = True,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Run baseline + ``ablations`` over a fresh world; write the markdown report to ``out``
    (default ``docs/benchmark_report.md`` at the repo root; ``out=''`` skips writing)."""
    cfg = config or BenchConfig()
    transforms = resolve_ablations(ablations)  # NotImplementedError / ValueError before any work
    check_seed(seed)
    from txn_guard.model import Scorer as TxnScorer

    sc = build_scenario(seed, cfg)
    variants: dict[str, list[FeatureTransform]] = {"baseline": []}
    variants.update({n: [t] for n, t in zip(ablations, transforms, strict=True)})
    pipe = Pipeline(TxnScorer(None), variants)
    decisions, risks, versions = asyncio.run(_run_stream(sc, pipe))

    txn_by_id = {t.txn_id: t for t in sc.txns}
    res_variants: dict[str, Any] = {}
    for name, ds in decisions.items():
        detections = [*ds, *risks]
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
    return [
        "| Metric | Value (95% Wilson CI) |",
        "|---|---|",
        f"| Precision | {_pct(m.precision, ci['precision'])} |",
        f"| Recall (all scam txns) | {_pct(m.recall, ci['recall'])} |",
        f"| F1 | {_pct(m.f1)} |",
        f"| FPR (held benign / benign) | {_pct(m.fpr, ci['fpr'], 3)} |",
        f"| Held-benign rate | {_pct(m.held_benign_rate, ci['held_benign_rate'], 3)} |",
        f"| Counts (TP / FP / FN / TN) | {m.tp} / {m.fp} / {m.fn} / {m.tn} |",
    ]


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
        "| Campaign | Victim txns after first hold | Held | Victims protected (hold) "
        "| Money prevented (hold) | Victim txns after first alert/hold | Held "
        "| Victims protected (alert) | Money prevented (alert) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        h, a = r.protection_hold, r.protection_alert
        tot["prevented"] += h.money_prevented_inr
        tot["at_risk"] += h.money_at_risk_inr
        lines.append(
            f"| {r.campaign_id} | {h.victim_txns_after_detection} | "
            f"{h.victim_txns_held_after_detection} | {_pct(h.fraction, None, 1)} | "
            f"{_inr(h.money_prevented_inr)} | {a.victim_txns_after_detection} | "
            f"{a.victim_txns_held_after_detection} | {_pct(a.fraction, None, 1)} | "
            f"{_inr(a.money_prevented_inr)} |"
        )
    return lines, tot


def _yn(lt: Any) -> str:
    if not lt.reaches_mass:
        return "n/a (<10 victims)"
    return "yes" if lt.campaign_detected_before_mass else "no"


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def render_report(
    result: dict[str, Any], include_volatile: bool = True, generated_at: str | None = None
) -> str:
    """Markdown report. ``include_volatile=False`` drops the machine-dependent timing section."""
    base = result["variants"]["baseline"]
    m: Metrics = base["metrics"]
    cfg = result["config"]
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
        f"{cfg['victims_per_campaign']} victims. Scored transactions: {m.n_benign + m.n_scam} "
        f"({m.n_benign} benign, {m.n_scam} scam; scam prevalence "
        f"{_pct(m.n_scam / (m.n_benign + m.n_scam) if (m.n_benign + m.n_scam) else None, None, 3)}).",
        "",
        "## Headline metrics",
        "",
        "Operating point: positive prediction = `hold_verify` (score >= 0.8).",
        "",
        *_headline(m),
        "",
        "## Operating point: step-up or hold",
        "",
        "Second operating point: `step_up` (score >= 0.5) also counts as flagged.",
        "",
        *_flagged(m),
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
    plines, tot = _protection_rows(base["lead"])
    mass = [r for r in base["lead"] if r.lead_hold.reaches_mass]
    before = sum(1 for r in mass if r.lead_hold.campaign_detected_before_mass)
    protected = [
        r.protection_hold.fraction for r in base["lead"] if r.protection_hold.fraction is not None
    ]
    L += [
        "",
        "## Lead time before mass victimisation",
        "",
        *plines,
        "",
        f"Campaigns reaching 10 victims: {len(mass)} of {len(base['lead'])}; detected by a hold "
        f"before the 10th victim: {before} of {len(mass)}. Mean victims-protected fraction "
        f"(hold-defined, over detected campaigns): {_pct(_mean(protected), None, 1)}. Money prevented "
        f"by holds on victim transfers after first hold: {_inr(tot['prevented'])} of "
        f"{_inr(tot['at_risk'])} at risk after first hold.",
        "",
        "Call-guard (session level): "
        f"{result['calls']['scam_calls_alerted']} of {result['calls']['scam_calls']} scam calls and "
        f"{result['calls']['benign_calls_alerted']} of {result['calls']['benign_calls']} benign calls "
        f"crossed the alert threshold ({result['calls']['alerts_published']} alerts published).",
        "",
        "## Ablations",
        "",
        "| Variant | Precision | Recall (hold) | Flagged recall | FPR (hold) | Mean victims protected "
        "| Money prevented |",
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
    L += [
        "",
        "`no_call_signal`: txn-guard sees `active_call_risk = 0`. `no_antibody` is registered "
        "but not runnable until the antibody-hub exists; requesting it raises "
        "`NotImplementedError`.",
        "",
    ]
    if include_volatile:
        lt = result["latency_ms"]
        L += [
            "## Scoring latency",
            "",
            "Per-transaction txn-guard latency (history context + features + model + reasons + "
            "decision), baseline variant, this machine. Machine-dependent and excluded from "
            "determinism checks.",
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
        "placement uses substreams `[seed, 8008]` and mule history `[seed, 9009]`.",
        f"- Ablations requested: {', '.join(result['ablations']) or 'none'}.",
        f"- Git commit: {result['git_commit']}.",
        f"- Models: call-guard `{result['model_versions']['call_guard']}`, txn-guard "
        f"`{result['model_versions']['txn_guard']}`.",
        "- Seeds used to train or calibrate the models (the benchmark seed is checked to be "
        "disjoint from these):",
    ]
    for role, seeds in fb.items():
        L.append(f"  - {role}: {', '.join(str(s) for s in seeds)}")
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
        "NEFT recall is undefined and only the NEFT false-positive rate is informative.",
        "- Calibration prevalence: the txn-guard model was calibrated at roughly 1% scam "
        "prevalence; this run's prevalence is shown above and differs. Precision depends "
        "directly on prevalence; recall and FPR do not.",
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


# ----------------------------------------------------------------------------------- CLI


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scam_bench.run_benchmark")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--ablations", nargs="*", default=[], help=f"any of {sorted(ABLATIONS)}")
    ap.add_argument("--out", default=str(default_report_path()))
    ap.add_argument("--citizens", type=int, default=BenchConfig.n_citizens)
    ap.add_argument("--days", type=int, default=BenchConfig.days)
    ap.add_argument("--campaigns", type=int, default=BenchConfig.n_campaigns)
    ap.add_argument("--victims", type=int, default=BenchConfig.victims_per_campaign)
    ap.add_argument(
        "--stamp", action="store_true", help="add a generated-at line (non-deterministic)"
    )
    a = ap.parse_args(argv)
    cfg = BenchConfig(
        n_citizens=a.citizens, days=a.days, n_campaigns=a.campaigns, victims_per_campaign=a.victims
    )
    stamp = datetime.now().astimezone().isoformat(timespec="seconds") if a.stamp else None
    t0 = time.perf_counter()
    res = run_benchmark(a.seed, a.ablations, config=cfg, out=a.out, generated_at=stamp)
    m: Metrics = res["variants"]["baseline"]["metrics"]
    print(
        f"seed={a.seed} scored={m.n_benign + m.n_scam} precision={m.precision} recall={m.recall} "
        f"fpr={m.fpr} runtime={time.perf_counter() - t0:.1f}s report={res.get('report_path')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
