"""Fraud DNA evaluation on the SYNTHETIC set ``datasets/fraud_dna_v1.jsonl`` (Phase 8).

    HF_HUB_OFFLINE=1 .venv/bin/python evaluation/run_dna_eval.py --out evaluation/results/fraud_dna_v1__NAME.json

What is measured. Every report goes through the service's own ``dna_text`` (identifier
redaction) and the cached embedding model; pairs inside one split are scored by cosine
similarity.

* related pair: two scam reports of the same family (same scam method);
* unrelated pair: different families (scam vs other scam, scam vs legitimate, legitimate vs
  legitimate). Pairs with an ambiguous report are reported separately, not in precision/recall.

Threshold rule, fixed before any score was looked at: on the DEV families only, the "similar"
threshold is the lowest value (grid 0.40-0.95, step 0.01) with pairwise precision >= 0.90, and
the "strong" band the lowest with precision >= 0.98. The TEST families (never used for the
choice) give the reported numbers. Also reported: per query, whether the nearest other report
above the threshold is of the same family (retrieval precision@1) and how many queries get no
match (no false link, but a miss).

The set is small, hand-written by the feature's author and synthetic: the numbers describe
behaviour on these 76 texts only, not real-world accuracy.
"""

import argparse
import hashlib
import itertools
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "evaluation" / "datasets" / "fraud_dna_v1.jsonl"
sys.path.insert(0, str(ROOT / "services" / "complaints"))

from complaints.dna import DEFAULT_MODEL, SentenceTransformerEmbedder, dna_text  # noqa: E402

GRID = [round(0.40 + i / 100, 2) for i in range(56)]


def _rev() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()  # fmt: skip
    except Exception:
        return "unknown"


def pairs(rows, split):
    sub = [r for r in rows if r["split"] == split]
    for a, b in itertools.combinations(sub, 2):
        amb = "ambiguous" in (a["kind"], b["kind"])
        related = a["kind"] == b["kind"] == "scam" and a["family"] == b["family"]
        yield a, b, related, amb


def metrics(scored, t):
    tp = sum(1 for s, rel, amb in scored if not amb and rel and s >= t)
    fp = sum(1 for s, rel, amb in scored if not amb and not rel and s >= t)
    fn = sum(1 for s, rel, amb in scored if not amb and rel and s < t)
    tn = sum(1 for s, rel, amb in scored if not amb and not rel and s < t)
    amb_above = sum(1 for s, _, amb in scored if amb and s >= t)
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    return {"threshold": t, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(prec, 3) if prec is not None else None,
            "recall": round(rec, 3) if rec is not None else None,
            "false_positive_rate": round(fp / (fp + tn), 4) if fp + tn else None,
            "ambiguous_pairs_above": amb_above}  # fmt: skip


def pick(scored, target):
    for t in GRID:
        m = metrics(scored, t)
        if m["precision"] is not None and m["precision"] >= target:
            return t
    return GRID[-1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(DATASET))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    path = Path(args.dataset).resolve()
    raw = path.read_bytes()
    rows = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    emb = SentenceTransformerEmbedder(DEFAULT_MODEL)
    emb.load()
    texts = [dna_text(r["title"], r["description"]) for r in rows]
    assert all(texts), "every report in the set has enough text"
    vecs = dict(zip((r["id"] for r in rows), emb.embed(texts), strict=True))

    def cos(a, b):
        return sum(x * y for x, y in zip(vecs[a["id"]], vecs[b["id"]], strict=True))

    scored = {s: [(cos(a, b), rel, amb, a, b) for a, b, rel, amb in pairs(rows, s)]
              for s in ("dev", "test")}  # fmt: skip
    flat = {s: [(x[0], x[1], x[2]) for x in v] for s, v in scored.items()}
    t_sim = pick(flat["dev"], 0.90)
    t_strong = pick(flat["dev"], 0.98)

    def per_query(split, t):
        sub = [r for r in rows if r["split"] == split and r["kind"] == "scam"]
        others = [r for r in rows if r["split"] == split]
        hit = miss = wrong = 0
        for q in sub:
            best = max((o for o in others if o["id"] != q["id"]), key=lambda o: cos(q, o))
            if cos(q, best) < t:
                miss += 1
            elif best["kind"] == "scam" and best["family"] == q["family"]:
                hit += 1
            else:
                wrong += 1
        return {"queries": len(sub), "top1_same_family": hit, "top1_other_family": wrong,
                "no_match_above_threshold": miss}  # fmt: skip

    def by_language(split, t):
        out = {}
        for lang_pair in ("en-en", "en-hi", "en-hinglish", "hi-hinglish"):
            la, lb = lang_pair.split("-")
            sel = [(s, rel, amb) for s, rel, amb, a, b in scored[split]
                   if {a["language"], b["language"]} == {la, lb} and (la != lb or a["language"] == la)]  # fmt: skip
            out[lang_pair] = metrics(sel, t)
        return out

    legit_fp = sorted(
        ({"pair": [a["id"], b["id"]], "similarity": round(s, 3)}
         for s, rel, amb, a, b in scored["test"] + scored["dev"]
         if not rel and not amb and s >= t_sim and "legitimate" in (a["kind"], b["kind"])),
        key=lambda x: -x["similarity"],
    )  # fmt: skip
    top_unrelated = sorted(
        ({"pair": [a["id"], b["id"]], "similarity": round(s, 3)}
         for s, rel, amb, a, b in scored["test"] if not rel and not amb),
        key=lambda x: -x["similarity"],
    )[:10]  # fmt: skip
    st = emb.status()
    result = {
        "synthetic": True,
        "dataset": str(path.relative_to(ROOT)), "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "code_revision": _rev(), "model": st["model"], "model_revision": st["revision"],
        "rule": "dev precision >= 0.90 (similar) / >= 0.98 (strong), grid 0.40-0.95",
        "chosen": {"similar": t_sim, "strong": t_strong},
        "dev": {"similar": metrics(flat["dev"], t_sim), "strong": metrics(flat["dev"], t_strong),
                "retrieval": per_query("dev", t_sim)},
        "test": {"similar": metrics(flat["test"], t_sim), "strong": metrics(flat["test"], t_strong),
                 "retrieval": per_query("test", t_sim),
                 "by_language_pair": by_language("test", t_sim),
                 "curve": [metrics(flat["test"], t) for t in GRID[::5]]},
        "legitimate_pairs_above_threshold": legit_fp,
        "highest_unrelated_test_pairs": top_unrelated,
    }  # fmt: skip
    out = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(out + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
