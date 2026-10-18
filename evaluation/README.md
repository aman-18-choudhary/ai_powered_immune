# Evaluation

**Everything here is SYNTHETIC, and both datasets were written by the author of the rules: scores are regression evidence, not real-world accuracy.** The datasets are hand-written probe texts, not real complaints or
messages. Results describe behaviour on these cases only; they are not real-world accuracy, and the
datasets are far too small for statistical claims.

| Dataset | Cases | sha256 | Notes |
|---|---|---|---|
| `datasets/accuracy_probes_v1.jsonl` | 20 (10 scam, 8 legitimate, 2 insufficient) | `c17a6209a3fbde447664ccb55d817bef3cefcb695d14e8c608be3162a8d7e863` | Frozen on 2026-10-18 from the accuracy audit probes (`docs/ACCURACY_AUDIT.md`). Do not edit: add a new version instead |
| `datasets/accuracy_probes_v2.jsonl` | 73 (36 scam, 28 legitimate, 5 ambiguous, 4 insufficient; 56 en, 11 Hinglish, 6 Hindi) | `d2966d6e357530c1aa7cc32db55d621092d1bef894d1018165c27d3b72ec5d15` | Written and frozen on 2026-10-18 **before** any accuracy batch 2 rule was written, by the same author as the rules (author bias is possible). Per-category scams, hard negatives (bank/financial SMS, awareness, ordinary conversation), victim narratives, embedded and deceptive links, ambiguous and malformed inputs. v1 stays unchanged |

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
| `results/accuracy_probes_v1__baseline-clean-branch.json` | `89f2aff` | Baseline code on `accuracy-batch1`, corrected runner (real Docker checker path) |
| `results/accuracy_probes_v1__after-review-clean-branch.json` | `f8f4162` | After the batch 1 review fixes on `accuracy-batch1` |
| `results/accuracy_probes_v2__baseline-before-batch2.json` | `b62260d` | v2 on the code before any batch 2 rule |
| `results/accuracy_probes_v1__final-batch2.json` | `74dbf9b` | v1 after accuracy batch 2 ([report](../docs/reports/ACCURACY_BATCH_2_REPORT.md)) |
| `results/accuracy_probes_v2__final-batch2.json` | `74dbf9b` | v2 after accuracy batch 2 |

The first two files were recorded on `build/implementation` (which also holds paused Phase 9
commits); their `stack_label` column used an approximation (`policy.label_for`) and is superseded
by the clean-branch files. Their complaints-pipeline rows are identical to the clean-branch runs.
