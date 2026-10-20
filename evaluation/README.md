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

### Complaint narratives (release hardening, 2026-10-20)

Running the app end to end showed that a complaint written as the victim's story ("he asked me to
approve a collect request and share the OTP; after I entered my UPI PIN, Rs 25,000 was
debited") scored 0/100 with no indicators. The call-guard rules score what a scammer says and
ignore relayed speech on purpose, and the probe sets above are mostly scammer messages, so they
did not show it. `complaints.narrative` adds deterministic cues for the fraud mechanism as victims
describe it.

`run_narrative_eval.py` runs the real pipeline (category OTHER, no LLM) on the 76 synthetic
reports of `datasets/fraud_dna_v1.jsonl` (sha256 `bc3158d2…`). The cues were written while
looking at the **dev** split only; the **test** split was not used to change them. Caveat: some
Hinglish test texts had been read earlier the same day during the Fraud DNA similarity diagnosis.

| Split | Scam reports flagged (MEDIUM+) | Legitimate flagged | Ambiguous HIGH+ | File |
|---|---|---|---|---|
| dev, before | 10 / 30 | 0 / 5 | 0 / 3 | `results/narrative_v1__baseline.json` (`42b2dbd`) |
| dev, after | 29 / 30 | 0 / 5 | 0 / 3 | `results/narrative_v1__narrative-v1.json` (`e269f1f`) |
| **test, before** | **16 / 30** | 0 / 5 | 0 / 3 | as above |
| **test, after** | **20 / 30** | 0 / 5 | 0 / 3 | as above |

The dev figure is fitted and says little. On test, the families the cues were not written for
(task jobs, sextortion, loan-app harassment, some OTP calls) are still mostly missed. The
accuracy probe sets v1 and v2 give identical results before and after (no new false positives).
Thirty test reports in six families are far too few for a recall estimate.

`diagnose_dna_hinglish.py` (result `results/fraud_dna_v1__hinglish_diagnosis.json`) explains the
weak Hinglish similarity matching: ranking is good (AUC 0.99 for English-Hinglish pairs) but the
scores sit lower (related pairs mean 0.45 vs 0.64 for English-English), and unrelated
Hinglish-Hinglish pairs reach 0.64, so lowering the threshold would add false links. The set has
no Hinglish-Hinglish related pairs at all. Thresholds were not changed.

The first two files were recorded on `build/implementation` (which also holds paused Phase 9
commits); their `stack_label` column used an approximation (`policy.label_for`) and is superseded
by the clean-branch files. Their complaints-pipeline rows are identical to the clean-branch runs.
