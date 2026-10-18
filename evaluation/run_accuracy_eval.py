#!/usr/bin/env python3
"""Accuracy evaluation on a frozen, versioned SYNTHETIC dataset.

Usage (from the repository root, with the project venv):

    .venv/bin/python evaluation/run_accuracy_eval.py [--dataset PATH] [--out PATH] [--label NAME]

It runs the REAL code paths, with no LLM and no network:

* complaints analysis pipeline (call-guard rules + clf-v1 + category-v1 + risk formula),
* the console's citizen checker (demo_server.citizen.assess),
* the stack's citizen checker policy (citizen_shield.policy.label_for on the call-guard score).

The dataset is SYNTHETIC: hand-written probe texts, not real reports. Results describe behaviour on
these cases only. They are NOT real-world accuracy, precision or recall estimates, and a dataset of
this size cannot support statistical claims.

Definitions (stable across versions so before/after runs are comparable):

* flagged = risk_level in {MEDIUM, HIGH, CRITICAL}; strong flag = {HIGH, CRITICAL}.
* For cases labelled scam or legit: TP = scam flagged, FN = scam not flagged, FP = legit flagged,
  TN = legit not flagged. Cases labelled insufficient are scored separately.
* rule-backed indicator = an indicator whose source is "rule" (deterministic rule with a text span
  or field). ml indicator = source "ml" (model output).
* unsupported flag = a flagged result with no rule-backed indicator and no ml indicator at all.
* prior-only flag = a flagged result whose score equals a category prior and exceeds every
  evidence-derived score component (only meaningful while the category prior exists).
* citizen "safe" on scam = the citizen checker labelled a scam case "safe".
* dataset v2 adds: expected_label "ambiguous" (scored only by "never HIGH/CRITICAL"), per-category
  scam recall, per-language and per-kind binary metrics (scam vs legitimate cases only), a
  confusion matrix of expected label vs result bucket (high_or_critical, medium,
  low_with_indicators, no_indicators, insufficient, not_assessed), and URL findings for cases
  whose text contains a link. Denominators: binary metrics use scam + legitimate cases only;
  insufficient and ambiguous cases are reported separately.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = ROOT / "evaluation" / "datasets" / "accuracy_probes_v1.jsonl"
FLAGGED = {"MEDIUM", "HIGH", "CRITICAL"}
STRONG = {"HIGH", "CRITICAL"}


def _git_rev() -> str:
    try:
        rev = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--",
                                "services", "libs"], capture_output=True, text=True).stdout.strip()
        return rev + ("+uncommitted-code-changes" if dirty else "")
    except Exception:  # noqa: BLE001 - provenance only
        return "unknown"


def _ratio(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


def binary_metrics(rows: list[dict], flag_key: str) -> dict:
    tp = sum(1 for r in rows if r["expected_label"] == "scam" and r[flag_key])
    fn = sum(1 for r in rows if r["expected_label"] == "scam" and not r[flag_key])
    fp = sum(1 for r in rows if r["expected_label"] == "legit" and r[flag_key])
    tn = sum(1 for r in rows if r["expected_label"] == "legit" and not r[flag_key])
    prec, rec = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    f1 = round(2 * prec * rec / (prec + rec), 4) if prec and rec else (0.0 if prec == 0 or rec == 0 else None)
    return {"tp": tp, "fn": fn, "fp": fp, "tn": tn, "precision": prec, "recall": rec, "f1": f1,
            "false_positive_rate": _ratio(fp, fp + tn), "false_negative_rate": _ratio(fn, fn + tp),
            "false_positive_ids": [r["id"] for r in rows if r["expected_label"] == "legit" and r[flag_key]],
            "false_negative_ids": [r["id"] for r in rows if r["expected_label"] == "scam" and not r[flag_key]]}


def _bucket(status: str, level: str | None, ind: list[dict]) -> str:
    """One label per result for confusion matrices (added for dataset v2, 2026-10-18)."""
    if status == "INSUFFICIENT_INPUT":
        return "insufficient"
    if level is None:
        return "not_assessed"
    if level in STRONG:
        return "high_or_critical"
    if level == "MEDIUM":
        return "medium"
    return "low_with_indicators" if ind else "no_indicators"


async def run_pipeline(cases: list[dict]) -> list[dict]:
    from complaints.analysis import ComplaintAnalysisPipeline

    pipe = ComplaintAnalysisPipeline()  # no explainer: deterministic, no LLM
    out = []
    for c in cases:
        r = await pipe.analyse({"category": c["selected_category"], "title": "",
                                "description": c["text"], "evidence": []})
        ind = [{"source": i.get("source"), "code": i.get("code")} for i in r.indicators]
        comp = r.extra.get("score_components") or {}
        rule_backed = sum(1 for i in ind if i["source"] == "rule")
        ml = sum(1 for i in ind if i["source"] == "ml")
        level = r.risk_level
        flagged = level in FLAGGED
        prior = comp.get("category_prior")
        evid = [v for v in (comp.get("text_score"), comp.get("transcript_score")) if v is not None]
        prior_only = bool(flagged and prior is not None and r.risk_score == prior + comp.get(
            "url_adjustment", 0) and all(prior > v for v in evid))
        out.append({
            "id": c["id"], "expected_label": c["expected_label"], "language": c["language"],
            "expected_category": c["expected_category"], "status": r.status, "risk_level": level,
            "risk_score": r.risk_score, "classification": r.classification,
            "confidence": round(r.confidence, 4) if r.confidence is not None else None,
            "assessment": r.extra.get("assessment"), "indicators": ind,
            "rule_backed_indicators": rule_backed, "ml_indicators": ml,
            "flagged": flagged, "strong_flag": level in STRONG,
            "kind": c.get("kind"), "has_url": c.get("has_url", False),
            "url_indicators": sum(1 for i in r.indicators if i.get("stage") == "url_heuristics"),
            "result_bucket": _bucket(r.status, level, ind),
            "unsupported_flag": bool(flagged and rule_backed == 0 and ml == 0),
            "prior_only_flag": prior_only,
            "score_components": {k: comp.get(k) for k in
                                 ("text_score", "transcript_score", "category_prior",
                                  "url_adjustment") if k in comp},
        })
    return out


async def run_citizen(cases: list[dict]) -> list[dict]:
    """Both citizen checkers through their real code paths: the console's (demo_server.citizen,
    in-process scorer) and the Docker stack's (citizen_shield.assess -> CallGuardClient -> HTTP
    POST /score of the real call-guard app, served in-process through httpx's ASGI transport).
    Until 2026-10-18 the stack column was approximated with policy.label_for on the score; that
    approximation skipped the service's own logic and is no longer used."""
    import httpx
    from call_guard.api import create_app
    from call_guard.model import Scorer, load_classifier
    from citizen_shield.assess import assess as stack_assess
    from citizen_shield.guard_client import CallGuardClient
    from demo_server.citizen import assess

    scorer = Scorer(load_classifier())
    guard = CallGuardClient("http://call-guard", timeout_s=30.0,
                            transport=httpx.ASGITransport(app=create_app(scorer)))
    out = []
    try:
        for c in cases:
            a = await assess(scorer, c["text"], "en")
            s = await stack_assess(guard, c["text"], "en")
            out.append({"id": c["id"], "expected_label": c["expected_label"],
                        "console_label": a.label, "console_score": round(float(a.score), 4),
                        "console_reasons": [r["code"] for r in a.reasons],
                        "stack_label": s.label, "stack_score": round(float(s.score), 4),
                        "stack_reasons": [r["code"] for r in s.reasons]})
    finally:
        await guard.aclose()
    return out


def summarise(pipe_rows: list[dict], cit_rows: list[dict]) -> dict:
    labelled = [r for r in pipe_rows if r["expected_label"] in ("scam", "legit")]
    insufficient = [r for r in pipe_rows if r["expected_label"] == "insufficient"]
    by_lang = {}
    for lang in sorted({r["language"] for r in labelled}):
        sub = [r for r in labelled if r["language"] == lang]
        by_lang[lang] = {"n": len(sub), **{k: v for k, v in binary_metrics(sub, "flagged").items()
                                           if not k.endswith("_ids")}}
    scams = [r for r in labelled if r["expected_label"] == "scam"]
    buckets = ("high_or_critical", "medium", "low_with_indicators", "no_indicators", "insufficient",
               "not_assessed")
    confusion = {lab: {b: sum(1 for r in pipe_rows if r["expected_label"] == lab and r["result_bucket"] == b)
                       for b in buckets}
                 for lab in sorted({r["expected_label"] for r in pipe_rows})}
    per_category = {}
    for cat in sorted({r["expected_category"] for r in scams if r["expected_category"]}):
        sub = [r for r in scams if r["expected_category"] == cat]
        per_category[cat] = {"n": len(sub), "flagged_medium_plus": sum(r["flagged"] for r in sub),
                             "missed_ids": [r["id"] for r in sub if not r["flagged"]]}
    per_kind = {}
    for kind in sorted({r.get("kind") or "unspecified" for r in labelled}):
        sub = [r for r in labelled if (r.get("kind") or "unspecified") == kind]
        per_kind[kind] = {"n": len(sub), **{k: v for k, v in binary_metrics(sub, "flagged").items()
                                            if not k.endswith("_ids")}}
    ambiguous = [r for r in pipe_rows if r["expected_label"] == "ambiguous"]
    safe_words = {"safe"}
    return {
        "n_cases": len(pipe_rows), "n_scam": len(scams),
        "n_legit": sum(1 for r in labelled if r["expected_label"] == "legit"),
        "n_insufficient": len(insufficient),
        "pipeline_flagged_medium_plus": binary_metrics(labelled, "flagged"),
        "pipeline_strong_flag_high_plus": binary_metrics(labelled, "strong_flag"),
        "pipeline_by_language_medium_plus": by_lang,
        "pipeline_by_kind_medium_plus": per_kind,
        "pipeline_scam_recall_by_category_medium_plus": per_category,
        "confusion_expected_label_vs_result": confusion,
        "ambiguous_with_high_or_critical": [r["id"] for r in ambiguous if r["strong_flag"]],
        "url_cases_with_url_findings": {r["id"]: r["url_indicators"] for r in pipe_rows if r.get("has_url")},
        "unsupported_flags": [r["id"] for r in pipe_rows if r["unsupported_flag"]],
        "prior_only_flags": [r["id"] for r in pipe_rows if r["prior_only_flag"]],
        "flags_without_rule_backed_indicator": [r["id"] for r in pipe_rows
                                                if r["flagged"] and r["rule_backed_indicators"] == 0],
        "insufficient_handled": {r["id"]: (r["status"] == "INSUFFICIENT_INPUT" and r["risk_level"] is None)
                                 for r in insufficient},
        "category_correct_on_scams": {
            "correct": sum(1 for r in scams if r["classification"] == r["expected_category"]),
            "total": len(scams),
            "wrong_ids": [r["id"] for r in scams if r["classification"] != r["expected_category"]]},
        "citizen_console_safe_on_scam": [c["id"] for c in cit_rows
                                         if c["expected_label"] == "scam" and c["console_label"] in safe_words],
        "citizen_console_safe_on_blank": [c["id"] for c in cit_rows
                                          if c["expected_label"] == "insufficient" and c["console_label"] in safe_words],
        "citizen_console_scam_on_legit": [c["id"] for c in cit_rows
                                          if c["expected_label"] == "legit" and c["console_label"] == "likely_scam"],
        "citizen_stack_safe_on_scam": [c["id"] for c in cit_rows
                                       if c["expected_label"] == "scam" and c["stack_label"] in safe_words],
        # added 2026-10-18 (review): a warning verdict must list at least one real reason
        "citizen_verdicts_without_reason": [
            f"{c['id']}:{side}" for c in cit_rows for side in ("console", "stack")
            if c[f"{side}_label"] in ("suspicious", "likely_scam")
            and not [x for x in c.get(f"{side}_reasons", []) if x != "NO_RISK_INDICATORS"]],
        "citizen_checkers_disagree": [c["id"] for c in cit_rows
                                      if c["console_label"] != c["stack_label"]],
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(DEFAULT_DATASET))
    ap.add_argument("--out", default=None)
    ap.add_argument("--label", default="run")
    args = ap.parse_args()
    path = Path(args.dataset).resolve()
    raw = path.read_bytes()
    cases = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    assert all(c.get("synthetic") is True for c in cases), "every case must be marked synthetic"
    pipe_rows = await run_pipeline(cases)
    cit_rows = await run_citizen(cases)
    report = {
        "WARNING": "SYNTHETIC dataset of hand-written probes. Not real-world accuracy. Too small for "
                   "statistical claims.",
        "label": args.label,
        "dataset": {"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(raw).hexdigest(),
                    "cases": len(cases)},
        "code_revision": _git_rev(),
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "llm": "not used",
        "summary": summarise(pipe_rows, cit_rows),
        "pipeline": pipe_rows,
        "citizen_checker": cit_rows,
    }
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
