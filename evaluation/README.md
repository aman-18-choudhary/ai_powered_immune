# Evaluation

**Everything here is SYNTHETIC.** The datasets are hand-written probe texts, not real complaints or
messages. Results describe behaviour on these cases only; they are not real-world accuracy, and the
datasets are far too small for statistical claims.

| Dataset | Cases | sha256 | Notes |
|---|---|---|---|
| `datasets/accuracy_probes_v1.jsonl` | 20 (10 scam, 8 legitimate, 2 insufficient) | `c17a6209a3fbde447664ccb55d817bef3cefcb695d14e8c608be3162a8d7e863` | Frozen on 2026-10-18 from the accuracy audit probes (`docs/ACCURACY_AUDIT.md`). Do not edit: add a new version instead |

Run (from the repository root):

```bash
.venv/bin/python evaluation/run_accuracy_eval.py --label NAME --out evaluation/results/accuracy_probes_v1__NAME.json
```

It runs the real complaints pipeline (no LLM), the console's citizen checker and the stack's
citizen policy. Metric definitions are in the docstring of `run_accuracy_eval.py`. Results in
`results/` are kept as recorded; a later run is added as a new file, never written over an old one.

## Recorded results

| File | Code | Notes |
|---|---|---|
| `results/accuracy_probes_v1__baseline.json` | `5a106c3` | Before any accuracy fix |
| `results/accuracy_probes_v1__after-batch1.json` | `7e04928` | After fixes 1, 2, 3, 4, 8 ([report](../docs/reports/ACCURACY_BATCH_1_REPORT.md)) |
