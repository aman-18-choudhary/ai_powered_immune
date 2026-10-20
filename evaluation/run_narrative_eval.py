"""Complaint-narrative evaluation on the SYNTHETIC set ``datasets/fraud_dna_v1.jsonl``.

    .venv/bin/python evaluation/run_narrative_eval.py --label NAME \\
        --out evaluation/results/narrative_v1__NAME.json

Why: citizens describe a fraud after the fact, in the first or third person ("he asked me to
share the OTP", "I clicked the link and entered my password"). The call-guard rules score what a
scammer SAYS and deliberately ignore relayed speech, so complaint narratives can get no
rule-backed indicator. This measures that, on the real analysis pipeline (no LLM, no network).

Each report is analysed with category OTHER (no category prior helps it), title + description.
Per split and language:
* scam reports: flagged (risk MEDIUM or above) and "with a rule-backed indicator";
* legitimate reports: flagged (false alarm) and with any rule-backed indicator;
* ambiguous reports: strong flag (HIGH or above) is counted as a failure.

Development of complaint-narrative cues uses the DEV split only; TEST is reported, never tuned
on. The set is small, hand-written and synthetic: this is NOT real-world accuracy.
"""

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "evaluation" / "datasets" / "fraud_dna_v1.jsonl"
sys.path.insert(0, str(ROOT / "services" / "complaints"))
FLAGGED = {"MEDIUM", "HIGH", "CRITICAL"}
STRONG = {"HIGH", "CRITICAL"}


def _rev() -> str:
    try:
        cmd = ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"]
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


async def analyse_all(rows: list[dict]) -> list[dict]:
    from complaints.analysis import ComplaintAnalysisPipeline

    pipe = ComplaintAnalysisPipeline()  # no explainer: deterministic, no LLM
    out = []
    for r in rows:
        res = await pipe.analyse({"category": "OTHER", "title": r["title"],
                                  "description": r["description"], "evidence": []})  # fmt: skip
        rule = sorted({i.get("code") for i in res.indicators if i.get("source") == "rule"})
        out.append({"id": r["id"], "split": r["split"], "language": r["language"],
                    "kind": r["kind"], "risk_level": res.risk_level, "risk_score": res.risk_score,
                    "flagged": res.risk_level in FLAGGED, "strong": res.risk_level in STRONG,
                    "rule_codes": rule})  # fmt: skip
    return out


def summarise(res: list[dict]) -> dict:
    out: dict = {}
    for split in ("dev", "test"):
        groups: dict = defaultdict(lambda: defaultdict(int))
        for r in (x for x in res if x["split"] == split):
            for key in ("all", r["language"]):
                g = groups[key]
                g[f"{r['kind']}_n"] += 1
                if r["kind"] == "scam":
                    g["scam_flagged"] += r["flagged"]
                    g["scam_with_rule_indicator"] += bool(r["rule_codes"])
                elif r["kind"] == "legitimate":
                    g["legit_flagged"] += r["flagged"]
                    g["legit_with_rule_indicator"] += bool(r["rule_codes"])
                else:
                    g["ambiguous_strong_flag"] += r["strong"]
        out[split] = {k: dict(v) for k, v in groups.items()}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(DATASET))
    ap.add_argument("--label", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    path = Path(a.dataset).resolve()
    raw = path.read_bytes()
    rows = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    res = asyncio.run(analyse_all(rows))
    result = {
        "WARNING": "SYNTHETIC hand-written reports; not real-world accuracy.",
        "label": a.label, "dataset": str(path.relative_to(ROOT)),
        "dataset_sha256": hashlib.sha256(raw).hexdigest(), "code_revision": _rev(),
        "summary": summarise(res),
        "missed_scams": [r["id"] for r in res if r["kind"] == "scam" and not r["flagged"]],
        "false_alarms": [r["id"] for r in res if r["kind"] == "legitimate" and r["flagged"]],
        "ambiguous_strong": [r["id"] for r in res if r["kind"] == "ambiguous" and r["strong"]],
        "cases": res,
    }  # fmt: skip
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("summary", "missed_scams", "false_alarms",
                                              "ambiguous_strong")}, indent=1))  # fmt: skip


if __name__ == "__main__":
    main()
