"""Why does Fraud DNA rarely match Hinglish reports? A DIAGNOSTIC on the SYNTHETIC set
``datasets/fraud_dna_v1.jsonl`` (release hardening). It changes nothing in the service.

    HF_HUB_OFFLINE=1 .venv/bin/python evaluation/diagnose_dna_hinglish.py \\
        --out evaluation/results/fraud_dna_v1__hinglish_diagnosis.json

Questions and how each is measured (dev and test pooled: no threshold is chosen here):

1. Evaluation design: how many related pairs exist per language pair at all.
2. Embedding quality: per language pair, the similarity of related pairs (same scam family) vs
   unrelated pairs, and the AUC (probability that a random related pair scores above a random
   unrelated pair). A low AUC means no threshold can separate them.
3. Threshold: the score the related Hinglish pairs actually reach, against the deployed 0.58.
4. Script/language bias: the mean similarity of UNRELATED pairs within one language vs across
   languages. If unrelated Hinglish texts score close to each other but far from English, the
   model encodes "romanised Hindi" more than the scam method.
5. Text normalisation: the same pairs scored on the raw text instead of ``dna_text`` (identifier
   redaction), to see whether redaction removes useful Hinglish content.

The set is small, hand-written and synthetic: this explains behaviour on these texts only.
"""

import argparse
import hashlib
import itertools
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "evaluation" / "datasets" / "fraud_dna_v1.jsonl"
sys.path.insert(0, str(ROOT / "services" / "complaints"))

from complaints.dna import (
    DEFAULT_MODEL,
    SentenceTransformerEmbedder,
    dna_text,
)

DEPLOYED_THRESHOLD = 0.58
LANG_PAIRS = ("en-en", "hi-hi", "hinglish-hinglish", "en-hi", "en-hinglish", "hi-hinglish")


def lang_pair(a, b):
    order = {"en": 0, "hi": 1, "hinglish": 2}
    la, lb = sorted((a["language"], b["language"]), key=order.__getitem__)
    return f"{la}-{lb}"


def auc(pos, neg):
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 3)


def summary(xs):
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    return {"n": len(s), "mean": round(statistics.fmean(s), 3), "min": round(s[0], 3),
            "median": round(statistics.median(s), 3), "max": round(s[-1], 3)}  # fmt: skip


def analyse(rows, vecs):
    def cos(a, b):
        return sum(x * y for x, y in zip(vecs[a["id"]], vecs[b["id"]], strict=True))

    by = {lp: {"related": [], "unrelated": []} for lp in LANG_PAIRS}
    for split in ("dev", "test"):
        sub = [r for r in rows if r["split"] == split and r["kind"] != "ambiguous"]
        for a, b in itertools.combinations(sub, 2):
            related = a["kind"] == b["kind"] == "scam" and a["family"] == b["family"]
            by[lang_pair(a, b)]["related" if related else "unrelated"].append(cos(a, b))
    out = {}
    for lp, d in by.items():
        rel, unr = d["related"], d["unrelated"]
        out[lp] = {
            "related": summary(rel), "unrelated": summary(unr), "auc": auc(rel, unr),
            "related_at_or_above_deployed": sum(1 for x in rel if x >= DEPLOYED_THRESHOLD),
            "unrelated_at_or_above_deployed": sum(1 for x in unr if x >= DEPLOYED_THRESHOLD),
        }  # fmt: skip
    return out


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
    ids = [r["id"] for r in rows]
    redacted = dict(zip(ids, emb.embed([dna_text(r["title"], r["description"]) for r in rows]),
                        strict=True))  # fmt: skip
    plain = dict(zip(ids, emb.embed([f"{r['title']}. {r['description']}" for r in rows]),
                     strict=True))  # fmt: skip
    deployed = analyse(rows, redacted)
    st = emb.status()
    result = {
        "synthetic": True, "diagnostic_only": True,
        "dataset": str(path.relative_to(ROOT)), "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "model": st["model"], "model_revision": st["revision"],
        "deployed_threshold": DEPLOYED_THRESHOLD,
        "pooled_splits": ["dev", "test"],
        "as_deployed_dna_text": deployed,
        "raw_text_without_redaction": {
            lp: {"auc": v["auc"], "related": v["related"]}
            for lp, v in analyse(rows, plain).items()
        },
    }
    out = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(out + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
