"""Structured (JSON) benchmark summary for programmatic consumers (the demo-server console).

``build_summary(result)`` turns the dict returned by ``run_benchmark`` into a plain JSON-able
structure (schema in the README). It is a pure function of the result: the same seed, config and
code give byte-identical JSON. Machine-dependent fields (``runtime_s``, ``latency_ms``) are
``null`` unless the caller passes them in explicitly.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from .antibody_stage import DEFAULT_CONFIRM_DELAY_S
from .metrics import Metrics

SCHEMA_VERSION = 1


def _val(v: float | None, ci: tuple[float, float] | None = None) -> dict[str, Any]:
    return {
        "value": None if v is None else round(v, 6),
        "ci95": None if ci is None else [round(ci[0], 6), round(ci[1], 6)],
    }


def _metrics(m: Metrics) -> dict[str, Any]:
    return {
        "n_benign": m.n_benign,
        "n_scam": m.n_scam,
        "tp": m.tp,
        "fp": m.fp,
        "fn": m.fn,
        "tn": m.tn,
        "precision": _val(m.precision, m.ci.get("precision")),
        "recall": _val(m.recall, m.ci.get("recall")),
        "f1": _val(m.f1),
        "fpr": _val(m.fpr, m.ci.get("fpr")),
        "step_up_benign_rate": _val(m.step_up_benign_rate, m.ci.get("step_up_benign_rate")),
        "flagged_recall": _val(m.flagged_recall, m.ci.get("flagged_recall")),
        "flagged_fpr": _val(m.flagged_fpr, m.ci.get("flagged_fpr")),
    }


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _variant(v: dict[str, Any]) -> dict[str, Any]:
    m: Metrics = v["metrics"]
    shares = [
        r.protection_hold.fraction for r in v["lead"] if r.protection_hold.fraction is not None
    ]
    money = sum((r.protection_hold.money_prevented_inr for r in v["lead"]), Decimal(0))
    at_risk = sum((r.protection_hold.money_at_risk_inr for r in v["lead"]), Decimal(0))
    vt = m.by_role.get("victim_transfer")
    mf = m.by_role.get("mule_forward")
    return {
        **_metrics(m),
        "victim_transfer_recall": _val(vt.recall if vt else None, vt.recall_ci if vt else None),
        "mule_forward_recall": _val(mf.recall if mf else None, mf.recall_ci if mf else None),
        "mean_share_post_detection_held": _val(_mean(shares)),
        "money_prevented_upper_bound_inr": str(money),
        "money_at_risk_after_first_hold_inr": str(at_risk),
    }


def _limitations(markdown: str) -> list[str]:
    m = re.search(r"^## Limitations\s*$", markdown, re.M)
    if not m:
        return []
    tail = markdown[m.end() :]
    nxt = re.search(r"^## ", tail, re.M)
    body = tail[: nxt.start()] if nxt else tail
    return [ln[2:].strip() for ln in body.splitlines() if ln.startswith("- ")]


def build_summary(
    result: dict[str, Any],
    report_markdown: str,
    *,
    runtime_s: float | None = None,
    include_latency: bool = False,
) -> dict[str, Any]:
    variants = result["variants"]
    base = variants["baseline"]
    cfg = result["config"]
    m: Metrics = base["metrics"]
    sens_rows: list[dict[str, Any]] = []
    sens = result.get("sensitivity") or {}
    if sens and "no_antibody" in variants:
        by_delay = {float(k.split("_")[-1]): k for k in sens if k.startswith("__delay_")}
        stats = result.get("sensitivity_stats", {})

        def row(label: str, delay: float | None, gate: str, v: Any, st: Any) -> dict[str, Any]:
            d = _variant(v)
            return {
                "label": label,
                "gate": gate,
                "delay_s": delay,
                "victim_transfer_recall": d["victim_transfer_recall"],
                "mean_share_post_detection_held": d["mean_share_post_detection_held"],
                "antibodies_published": st["published"] if st else None,
                "antibody_matches": st["matches"] if st else None,
                "benign_matches": st["benign_matches"] if st else None,
            }

        sens_rows.append(row("no antibody", None, "none", variants["no_antibody"], None))
        for d in sorted({*by_delay, DEFAULT_CONFIRM_DELAY_S}):
            if d == DEFAULT_CONFIRM_DELAY_S:
                sens_rows.append(
                    row(
                        f"label-gated, {int(d)} s (baseline)",
                        d,
                        "label",
                        base,
                        result["antibody_stats"].get("baseline"),
                    )  # fmt: skip
                )
            else:
                k = by_delay[d]
                sens_rows.append(
                    row(sens[k]["label"], d, "label", sens[k], stats.get(k))
                )  # fmt: skip
        if "__hold_gate" in sens:
            sens_rows.append(
                row(
                    sens["__hold_gate"]["label"],
                    DEFAULT_CONFIRM_DELAY_S,
                    "hold",
                    sens["__hold_gate"],
                    stats.get("__hold_gate"),
                )  # fmt: skip
            )
    hero = result.get("hero")
    lat = result["latency_ms"]
    return {
        "schema_version": SCHEMA_VERSION,
        "simulated": True,
        "meta": {
            "seed": result["seed"],
            "git_commit": result["git_commit"],
            "runtime_s": (
                round(runtime_s, 1) if include_latency and runtime_s is not None else None
            ),
            "model_versions": result["model_versions"],
            "forbidden_seeds": {k: list(v) for k, v in result["forbidden_seeds"].items()},
            "command": f"python -m scam_bench.run_benchmark --seed {result['seed']}",
            "ablations_requested": list(result["ablations"]),
        },
        "config": cfg,
        "headline": _metrics(m),
        "per_rail": {rail: _metrics(rm) for rail, rm in m.by_rail.items()},
        "per_role": {
            role: {
                "n": r.n,
                "held": r.held,
                "flagged": r.flagged,
                "recall": _val(r.recall, r.recall_ci),
                "flagged_recall": _val(r.flagged_recall, r.flagged_recall_ci),
            }
            for role, r in m.by_role.items()
        },
        "ablations": {name: _variant(v) for name, v in variants.items()},
        "antibody": {
            "confirm_delay_s": DEFAULT_CONFIRM_DELAY_S,
            "stats": result.get("antibody_stats", {}),
            "stage_enabled": result.get("antibody_stage", True),
        },
        "calls": result["calls"],
        "sensitivity": sens_rows,
        "hero": (
            None
            if not hero
            else {
                "seeds": hero["seeds"],
                "model_only": hero["model_only"],
                "with_antibody": hero["with_antibody"],
                "counts": hero["counts"],
                "mule_age_days_range": list(hero["age_range"]),
            }
        ),
        "latency_ms": (
            {"p50": lat["p50"], "p99": lat["p99"], "n": lat["n"]} if include_latency else None
        ),
        "limitations": _limitations(report_markdown),
    }


def write_summary(path: str | Path, summary: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
