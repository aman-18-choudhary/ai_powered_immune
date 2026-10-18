# Accuracy batch 1: before/after report

> **Update:** accuracy batch 2 (fixes 5 and 6) is reported in
> [ACCURACY_BATCH_2_REPORT.md](ACCURACY_BATCH_2_REPORT.md). The false positive and false negatives
> listed below as remaining were addressed there; this report is kept as recorded.

Date: 2026-10-18 (review addendum in section 8). Branch `accuracy-batch1` (the accuracy commits
cherry-picked onto `6cc5554` without the paused Phase 9 work; see section 8), local only (not
pushed). Commit hashes in sections 1-7 refer to the original commits on `build/implementation`;
the matching commits on this branch are listed in section 8. Scope approved by the
owner: freeze the 20 audit cases as a SYNTHETIC evaluation set, record the baseline, then fixes
1, 2, 3, 4 and 8 from [ACCURACY_AUDIT.md](../ACCURACY_AUDIT.md).

> **Read this first.** The evaluation set is 20 hand-written SYNTHETIC probes (10 scam, 8
> legitimate, 2 insufficient). The numbers below describe behaviour on these cases only. They are
> not real-world accuracy, precision or recall, and a set this small cannot support statistical
> claims.

## 1. What changed

| Fix | Change | Commit |
|---|---|---|
| 2 | Frozen dataset `evaluation/datasets/accuracy_probes_v1.jsonl` (sha256 `c17a6209…`, every case `synthetic: true`), runner `evaluation/run_accuracy_eval.py`, baseline recorded **before** any code change | `3a714f2` |
| 3 | call-guard `score_text(require_explained=True)`: the classifier counts only when it adds its `SCRIPT_CLASSIFIER_MATCH` reason; default (simulator) unchanged | `bdfe20c` |
| 1, 3, 8 | Complaints pipeline: no category prior in the score; unexplained points ignored and recorded; HIGH/CRITICAL need a rule-based indicator (model-only capped at MEDIUM); new `assessment`; citizen alerts need a rule-based indicator; regression guards on the frozen set | `05ed50c` |
| 4 | Both citizen checkers: no `safe` label; `no_indicators_found`, `weak_indicators`, `insufficient_input`; console path uses `require_explained` | `0463a08`, `7e04928` |
| 4, 8 | Console: evidence wording ("Strong scam indicators · 80/100", "No scam indicators found", "Insufficient evidence", "model prediction only"), neutral styling instead of a green tick, category marked "not used in the score", new checker headings (en/hi/ta/bn) | `e51bcc6` |

## 2. Baseline vs after (complaints pipeline, flagged = MEDIUM or higher)

Baseline: code `5a106c3`; after: code `7e04928`; same dataset sha256. Result files:
`evaluation/results/accuracy_probes_v1__baseline.json`, `…__after-batch1.json`.

| Metric (18 labelled cases) | Baseline | After |
|---|---|---|
| True positives / false negatives (scams) | 10 / 0 | 2 / 8 |
| False positives / true negatives (legitimate) | 6 / 2 | 1 / 7 |
| Precision | 0.625 | 0.667 |
| Recall | 1.0 | 0.2 |
| F1 | 0.769 | 0.308 |
| False-positive rate | 0.75 | 0.125 |
| False-negative rate | 0.0 | 0.8 |
| Flags with no indicator at all | 11 | **0** |
| Flags without a rule-based indicator | 11 | **0** |
| Flags produced by the category prior | 13 | **0** |
| Category correct on scams (model guess, display only) | 9/10 | 9/10 |
| Insufficient inputs handled (no level) | 2/2 | 2/2 |

HIGH-or-higher view: baseline TP 4, FN 6, FP 3, TN 5; after TP 2, FN 8, FP 1, TN 7.

**How to read the recall drop.** At baseline every scam was "caught" only because the category
guess added 50-70 points with no evidence. The same mechanism flagged 6 of 8 legitimate texts. The
8 scams now missed were never detected by evidence: there are no rules for UPI collect requests,
task-job fees, guaranteed returns, sextortion, OTP theft in Hinglish or KYC-link phishing. They now
read **"No scam indicators found"** (or "weak" with the cue listed) with the limitation "this is not
a finding that the message or event is safe", and the category guess is still shown, labelled as a
model guess. Recovering them needs rule coverage (fix 6), not a prior.

## 3. False positives and false negatives after the batch

| Case | Expected | After | Why |
|---|---|---|---|
| N1 genuine bank OTP SMS | legitimate | **HIGH 71** (false positive) | Rules misfire on "Do not share this OTP with anyone" (ISOLATION_DEMAND, CREDENTIAL_ASK): fix 5 |
| S2 UPI collect / PIN to receive | scam | LOW 0, no indicators | No rule: fix 6 |
| S3 task job with fee | scam | LOW 0, no indicators | No rule: fix 6 |
| S4 guaranteed returns | scam | LOW 0, no indicators | No rule: fix 6 |
| S5 KYC block + link in text | scam | LOW 15, weak (URGENCY) | Link inside text not analysed; no KYC rule: fix 6 |
| S7 sextortion | scam | LOW 0, no indicators | No rule; the classifier's unexplained 0.40 no longer counts: fix 6 |
| S8 victim narrative | scam | LOW 0, no indicators | Rules target the caller's script, not a victim's account: fix 6 |
| S9 OTP theft (Hinglish) | scam | LOW 15, weak (URGENCY) | Hinglish OTP request not covered: fix 6 |
| E3 job scam, complainant chose UPI | scam | LOW 0, no indicators | No rule: fix 6 |

Correct: S1 and S6 (digital arrest, English and Hinglish) CRITICAL 100 with 5 and 4 rule-based
indicators; N2-N8 no flag; E1/E2 insufficient evidence.

### Citizen checkers (same 20 texts)

| | Baseline | After |
|---|---|---|
| Console checker: scams labelled `safe` | 6 | 0 (label removed) |
| Console checker: scams with no or weak indicators | 6 (as `safe`) | 8 (`no_indicators_found` 6, `weak_indicators` 2): same coverage gap, honest wording |
| Console checker: blank or too-short input | `safe` (2) | `insufficient_input` (2) |
| Console checker: legitimate labelled `likely_scam` | 1 (N1) | 1 (N1): fix 5 |
| Stack checker (`citizen-shield`, over HTTP) | *(corrected in review, section 8: this row was measured with an approximation and was wrong)* | *(see section 8)* |

## 4. Tests actually executed (2026-10-18, by the controller)

| Suite | Result |
|---|---|
| `scripts/test_all.sh` on Python 3.13 (16 Python packages + console vitest) | 17/17 packages passed (complaints 580 passed/7 skipped; call_guard 143; citizen_shield 74; demo_server 211; console vitest 270) |
| CI test job reproduced on Python 3.11 from a clean clone (`pip check`, then `ruff check` + `pytest -W error` per package) | 16/16 packages passed, `pip check` clean |
| Playwright, all specs, against a dedicated demo-server on port 18190 (local sign-in, real faster-whisper) | 23/23 passed |
| Real services, opt-in: real Whisper on uploaded evidence (en WAV, en M4A, hi, Hinglish) and live Ollama qwen2.5:0.5b | 6/6 passed |
| Console `tsc --noEmit`, `eslint`, `vite build` | clean; main chunk 102.9 KB gzip |
| `ruff check` on every touched package | clean |

New or changed tests (no assertion weakened to pass): 9 regression guards on the frozen set
(`services/complaints/tests/test_accuracy_regression.py`), alert gating without a rule-based
indicator, `require_explained` (call-guard), label policy and weak-cue tests (both checkers), result
wording and "never safe" UI tests (console). Existing tests that encoded the removed behaviour (category prior in the
score: 2; unexplained points counted: 2; alerts without evidence: 1; the `safe` label: the
citizen-checker label tests in both services and the console) were changed to assert the new
contract, never loosened to pass; each change is described in its commit message.

## 5. Remaining known failure cases

1. **N1**: genuine bank OTP SMS is HIGH 71 (rule misfire), fix 5.
2. **Eight scams have no rule coverage** (UPI collect, task job, guaranteed returns, sextortion,
   victim narratives, Hinglish OTP theft, KYC-link phishing, parcel redelivery-fee links such as the
   console's own sample 3), fix 6.
3. **Links written inside the text** are not analysed by the URL heuristics, fix 6.
4. ~~**Stack citizen checker** (`citizen-shield` calling call-guard over HTTP) does not use
   `require_explained`~~ (fixed in review, section 8); a classifier-only score can still produce `suspicious` with no listed reason.
5. **Live Protection** is unchanged in this batch: conversations about scams can still reach HIGH,
   fix 9.
6. **Transcripts** still carry no per-segment confidence, fix 7.
7. The hi/ta/bn checker headings added in this batch need a native-speaker review.

## 6. Trade-offs and regressions

* **Recall on the synthetic set fell from 10/10 to 2/10 at MEDIUM+**: deliberate. The removed
  "detections" had no evidence and came with 6 false positives out of 8. Users now see "No scam
  indicators found (not a finding of safety)" instead of a confident-looking score.
* **Investigator queue risk filters** will match fewer complaints at MEDIUM/HIGH for new analyses;
  older analyses keep their stored levels until re-run (they have no `assessment` and show "Weak or
  no indicators" for LOW).
* **API label change** for the citizen checkers (`safe` → `no_indicators_found` /
  `weak_indicators` / `insufficient_input`); the console treats an older server's `safe` as
  suspicious and uncertain.
* The simulator's call-guard default is unchanged, so simulator pages and the benchmark behave as
  before.

## 7. Push status (superseded by section 8)

Not pushed. All commits are on `build/implementation` locally, awaiting the owner's review.

## 8. Review addendum (2026-10-18)

The owner asked for a review before any push. Findings and fixes (each with a regression test):

| # | Finding | Fix | Commit (this branch) |
|---|---|---|---|
| R1 | Results with weak indicators only (S5, S9: LOW 15, URGENCY) lacked the "not a finding that the message or event is safe" limitation | Every LOW result carries it; guard over the frozen set | `d198637` |
| R2 | Analyses stored before batch 1 (no `assessment`) were shown with indicator wording; an old HIGH that rested on the removed category prior would read "Strong scam indicators" | API derives the assessment from stored status, level and indicators (`assessment_derived: true`) in full views and summaries | `d198637` |
| R3 | **Docker checker inconsistent with the console**: through HTTP, sextortion (S7) and the victim narrative (S8) were `suspicious` with **no listed reason**; the console said `no_indicators_found` | call-guard `POST /score` accepts `require_explained` (default false for simulator callers); citizen-shield sends true | `83c70d4` |
| R4 | **The report's Docker column was measured with an approximation** (`policy.label_for` on the score) and misreported labels | The runner now calls `citizen_shield.assess` through the real call-guard HTTP app (in process); new measures: warning verdicts without a reason, checker disagreements | `f8f4162` |
| R5 | **The accuracy commits sat on top of three unreviewed Phase 9 commits**; pushing `build/implementation` would have published Phase 9 | This branch, `accuracy-batch1`, was built from the pushed head `6cc5554` with only the accuracy commits (cherry-picked, `-x`); `build/implementation` is unchanged and keeps Phase 9 | — |

Commits on this branch: `7548666` (audit), `89f2aff` (dataset, runner, baseline), `4f68651`,
`f78b784`, `bda5e22`, `e35dd6d`, `416f508`, `0b70e3b` (batch 1), `d198637`, `83c70d4`, `f8f4162`
(review fixes), plus this documentation commit.

### Corrected citizen-checker results (real code paths, same dataset)

Baseline code `89f2aff` vs after-review code `f8f4162`, both measured on this branch with the
corrected runner (`results/accuracy_probes_v1__baseline-clean-branch.json`,
`…__after-review-clean-branch.json`):

| Measure | Baseline | After review |
|---|---|---|
| Scams labelled `safe` (console / Docker) | 6 / 6 | 0 / 0 |
| Blank or too-short input labelled `safe` (console) | 2 | 0 (`insufficient_input`) |
| Legitimate labelled `likely_scam` (both) | 1 (N1) | 1 (N1) |
| Warning verdicts with no listed reason | 4 (S7, S8 on both checkers) | 0 |
| Cases where the two checkers disagree | 0 | 0 |
| Docker labels for the 8 missed scams | `safe` ×6, `suspicious` (no reason) ×2 | `no_indicators_found` ×6, `weak_indicators` ×2 |

### Pipeline numbers re-verified

The complaints pipeline outputs on this branch (without Phase 9) are identical, case by case
(level, score, category, assessment, indicator codes), to the results recorded on
`build/implementation`, for both the baseline and the after state. Sections 2 and 3 stand.

### Push status

Not pushed. Awaiting the owner's approval to push `accuracy-batch1` (fast-forward from
`6cc5554`).
