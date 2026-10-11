# Benchmark report

In-process evaluation of the scam-immune-system pipeline (call-guard -> txn-guard) over a fresh simulator world. Decisions are transaction-level; a *hold* is `hold_verify`. All rates carry 95% Wilson confidence intervals. The simulator is the only data source (see Limitations).

World: 1500 citizens, 14 days (5 warm-up days only build history), 5 scam campaigns x 25 victims. Scored transactions: 22416 (21736 benign, 680 scam; scam prevalence 3.034%).

## Headline metrics

Operating point: positive prediction = `hold_verify` (score >= 0.8).

| Metric | Value (95% Wilson CI) |
|---|---|
| Precision | 100.00% [99.32, 100.00] |
| Recall (all scam txns) | 82.65% [79.62, 85.31] |
| F1 | 90.50% |
| FPR (held benign / benign, all rails pooled) | 0.000% [0.000, 0.018] |
| Held-benign rate | 0.000% [0.000, 0.018] |
| **Worst-rail FPR** (IMPS) | 0.000% [0.000, 0.178] |
| Counts (TP / FP / FN / TN) | 562 / 0 / 118 / 21736 |

**Per-rail false-positive rate (read this next to the pooled FPR above).** The pooled FPR is dominated by UPI volume and hides rail-level problems.

| Rail | Benign txns | FPR, hold (95% CI) | FPR, step-up or hold (95% CI) |
|---|---|---|---|
| IMPS | 2156 | 0.000% [0.000, 0.178] | 0.371% [0.188, 0.731] |
| NEFT | 1208 | 0.000% [0.000, 0.317] | 0.828% [0.450, 1.517] |
| UPI | 18372 | 0.000% [0.000, 0.021] | 0.016% [0.006, 0.048] |

## Operating point: step-up or hold

Second operating point: `step_up` (score >= 0.5) also counts as flagged.

| Metric | Value (95% Wilson CI) |
|---|---|
| Flagged precision | 96.58% [94.83, 97.75] |
| Flagged recall | 87.21% [84.48, 89.51] |
| Flagged F1 | 91.65% |
| Flagged FPR | 0.097% [0.063, 0.148] |
| Benign step-up only rate | 0.097% [0.063, 0.148] |
| Counts (TP / FP / FN / TN) | 593 / 21 / 87 / 21715 |

## Precision at realistic prevalence

Precision depends on scam prevalence pi: precision = TPR*pi / (TPR*pi + FPR*(1-pi)), with TPR and FPR taken from this run. The pooled-FPR upper bound is the upper end of its 95% Wilson interval (a conservative reading).

| Prevalence | pi | Precision, hold | Precision, hold (FPR upper bound) | Precision, step-up or hold |
|---|---|---|---|---|
| simulator | 3.034% | 100.00% | 99.32% | 96.58% |
| 0.1% | 0.100% | 100.00% | 82.40% | 47.47% |
| 0.01% | 0.010% | 100.00% | 31.87% | 8.28% |

The headline precision will NOT transfer to real traffic: at realistic prevalence the same recall and FPR give a far lower precision.

## Per-rail breakdown

| Rail | Benign | Scam | Recall (hold) | Flagged recall | FPR (hold) | Flagged FPR |
|---|---|---|---|---|---|---|
| IMPS | 2156 | 169 | 92.3% [87.3, 95.4] | 92.9% [88.0, 95.9] | 0.000% [0.000, 0.178] | 0.371% [0.188, 0.731] |
| NEFT | 1208 | 0 | n/a (empty denominator) | n/a (empty denominator) | 0.000% [0.000, 0.317] | 0.828% [0.450, 1.517] |
| UPI | 18372 | 511 | 79.5% [75.7, 82.7] | 85.3% [82.0, 88.1] | 0.000% [0.000, 0.021] | 0.016% [0.006, 0.048] |

## Per-role breakdown

| Role | Transactions | Held | Recall (hold) | Flagged | Flagged recall |
|---|---|---|---|---|---|
| victim_transfer | 366 | 366 | 100.0% [99.0, 100.0] | 366 | 100.0% [99.0, 100.0] |
| mule_forward | 314 | 196 | 62.4% [56.9, 67.6] | 227 | 72.3% [67.1, 77.0] |

Mule-forward recall (62.4%) is the weakest role. The antibody stage blocks the mule payees of confirmed victim transfers, so it lifts victim-transfer recall; it does not change mule-forward recall here, because forwards go to a different (cash-out) payee_hash that no analyst confirmation covers.

## Lead time before mass victimisation

| Campaign | Victims | Lead, first hold | Before 10th victim | Lead, first alert or hold | Before 10th victim |
|---|---|---|---|---|---|
| bench0 | 25 | +7h43m41s | yes | +7h49m59s | yes |
| bench1 | 25 | +3h57m54s | yes | +4h07m02s | yes |
| bench2 | 25 | +6h56m33s | yes | +7h15m44s | yes |
| bench3 | 25 | +8h33m39s | yes | +8h40m01s | yes |
| bench4 | 25 | +5h14m20s | yes | +5h32m57s | yes |

Lead = time of the 10th victim's first transfer minus the first detection (positive: detected before mass victimisation). `n/a` means the campaign never reaches 10 victims or was never detected.

Share of post-detection victim transfers held. This is a *transaction* fraction (a victim makes several split transfers), counted over victim transfers strictly after the first detection; the detecting transfer itself is excluded. The per-victim column counts victims all of whose post-detection transfers were held.

| Campaign | Victim txns after first hold | Held | Share held (hold) | Victims fully held | Money prevented, upper bound (hold) | Victim txns after first alert/hold | Held | Share held (alert) |
|---|---|---|---|---|---|---|---|---|
| bench0 | 69 | 69 | 100.0% | 25 of 25 (100.0%) | INR 2,963,604.14 | 70 | 70 | 100.0% |
| bench1 | 69 | 69 | 100.0% | 25 of 25 (100.0%) | INR 2,364,698.19 | 70 | 70 | 100.0% |
| bench2 | 75 | 75 | 100.0% | 25 of 25 (100.0%) | INR 4,128,310.32 | 76 | 76 | 100.0% |
| bench3 | 76 | 76 | 100.0% | 25 of 25 (100.0%) | INR 4,102,411.67 | 77 | 77 | 100.0% |
| bench4 | 72 | 72 | 100.0% | 25 of 25 (100.0%) | INR 4,668,487.37 | 73 | 73 | 100.0% |

Campaigns reaching 10 victims: 5 of 5; detected by a hold before the 10th victim: 5 of 5. Mean share of post-detection victim transfers held (over detected campaigns): 100.0%. Money prevented (upper bound) on victim transfers after first hold: INR 18,227,511.69 of INR 18,227,511.69 at risk after first hold.

Money-prevented assumption: a held transfer is treated as stopped for good. The simulator does not model a victim retrying through another payee or channel, and `hold_verify` is assumed to end in rejection, not release; real prevented loss is lower.

Campaign-structure caveat: each campaign has 25 victims and detection happens near victim 1-3, so most victims fall after detection; the held share is inflated relative to slower, less concentrated real campaigns.

Call-guard (session level, real run): 163 of 163 scam calls and 0 of 1076 benign calls crossed the alert threshold (163 alerts published). This is an idealised, simulator-perfect call signal; see the `degraded_call_signal` variant below.

## Ablations

| Variant | Precision | Recall (hold) | Flagged recall | FPR (hold) | Mean share of post-detection transfers held | Money prevented (upper bound) |
|---|---|---|---|---|---|---|
| baseline | 100.0% [99.3, 100.0] | 82.6% [79.6, 85.3] | 87.2% | 0.000% [0.000, 0.018] | 100.0% | INR 18,227,511.69 |
| no_call_signal | 100.0% [99.3, 100.0] | 81.6% [78.5, 84.3] | 86.3% | 0.000% [0.000, 0.018] | 99.1% | INR 18,017,534.20 |
| degraded_call_signal | 99.8% [99.0, 100.0] | 82.2% [79.2, 84.9] | 87.1% | 0.005% [0.001, 0.026] | 99.4% | INR 18,155,236.69 |
| no_antibody | 100.0% [99.3, 100.0] | 79.7% [76.5, 82.6] | 84.9% | 0.000% [0.000, 0.018] | 94.5% | INR 17,892,546.17 |

`no_call_signal`: txn-guard sees `active_call_risk = 0`. `degraded_call_signal`: the call-guard output is replaced by training-like imperfect call risk (seeded): each scam victim's call is detected with probability 85%, 1-3 chunks late, and 1.5% of benign transactions receive a spurious risk. `no_antibody` turns the antibody stage off (see the next section).

## Antibody stage (analyst-confirmation model)

Model: when a campaign victim transfer is first held (`hold_verify`), an analyst confirms the payee after 60 s (deterministic, simulator ground truth as the oracle) and an antibody (kind `mule_account`, 14-day TTL) is applied to every bank's cache; later transfers to that payee hit `ANTIBODY_MATCH` (hold_verify, or step-up for an established payee). `baseline` has the stage on, `no_antibody` is the same pipeline with it off (the pre-antibody baseline).

| Metric | baseline (antibody on) | no_antibody |
|---|---|---|
| Victim-transfer recall (hold) | 100.0% [99.0, 100.0] | 94.5% [91.7, 96.4] |
| Mule-forward recall (hold) | 62.4% [56.9, 67.6] | 62.4% [56.9, 67.6] |
| Share of post-detection victim transfers held | 100.0% | 94.5% |
| Campaigns detected before the 10th victim | 5 of 5 | 5 of 5 |
| Mean lead time, first hold | +6h29m13s | +6h29m13s |
| Benign hold rate (FPR) | 0.000% [0.000, 0.018] | 0.000% [0.000, 0.018] |

Sensitivity of the confirmation model (same seed, deterministic). The delay is a free parameter; the hold-gated row confirms EVERY first hold_verify to a payee with no ground-truth label, so any model false positive poisons its payee (benign matches below measure that).

| Confirmation model | Victim-transfer recall | Share of post-detection victim transfers held | Antibodies / matches / benign matches |
|---|---|---|---|
| no antibody | 94.5% | 94.5% | n/a |
| label-gated, 0 s | 100.0% | 100.0% | 21 / 345 / 0 |
| label-gated, 60 s (baseline) | 100.0% | 100.0% | 21 / 345 / 0 |
| label-gated, 300 s | 100.0% | 100.0% | 21 / 342 / 0 |
| label-gated, 900 s | 100.0% | 100.0% | 21 / 335 / 0 |
| label-gated, 1 h | 100.0% | 100.0% | 21 / 313 / 0 |
| label-gated, 6 h | 99.2% | 99.2% | 21 / 225 / 0 |
| label-gated, 24 h | 94.5% | 94.5% | 21 / 0 / 0 |
| hold-gated (no labels), 60 s | 100.0% | 100.0% | 26 / 650 / 0 |

Result: victim-transfer recall is 94.5% without antibodies and 100.0% with a 0 s confirmation; at 24 h it is 94.5%; it starts to fall below the 60 s value at 6 h.

Victims of a campaign are minutes to hours apart in this simulator (mean lead time before the 10th victim is hours), so short delays cost nothing; the longer delays show where the antibody stops helping. Deterministic for a given seed.


Baseline antibody stage: 21 antibodies published, 345 transactions matched (345 scam, 0 benign). Simulator benign payees are never mules, so the benign match count must stay 0 (antibodies add no benign holds here); in production this depends on analyst accuracy and the protected-merchant allowlist.

## Hero scenario (hard case: seasoned mule, call undetected)

A constructed hard case, NOT a prevalence estimate. Victim A (bank A) is called with a templated digital-arrest script that call-guard catches; A's largest transfer, to a seasoned mule account (68-398 days old, so young-payee signals do not fire), is held on A's own strong signals. Victim B (bank B, another state) hears an off-template 'relative in an emergency' script that the shipped call-guard does not alert on, and 90 s after A's last transfer pays the SAME mule. Nothing local flags B; only the shared threat memory can. Single-channel detection missing a novel script is the point.

How much is by construction: (1) A is held in every seed because the scenario sends A's largest transfer FIRST, so the system's hold lands inside the 15-minute call-risk window (a later transfer would fall outside it and not be held). (2) B's call is ONE fixed hand-written script (`evasive_scam_chunks()`, no RNG), authored to evade this call-guard, so 'undetected' is by construction; the 40 seeds vary only mule age, amounts and timing, they are not 40 independent evasions. (3) The model-only miss on B is a genuine blind spot, not noise: B's amount z-score is 9.1-10 (clipped at 10), about 24-107x B's typical transfer, yet the score is 0.0 in 39 seeds (0.5, step_up, in 1) because only 'new payee' fires and no young-payee or call signal does. B's history is the hand-built warm-up fixture (40 small UPI payments), not simulated behaviour.

* Hero seeds run: 40. Call-guard alerts on B's call: 0 (undetected in every seed); on A's call: alert in 40 of 40. A's transfer to the shared mule held by the system (no label): 40 of 40; antibody due (A's first hold + 60 s) before B's transfer: 40 of 40; 90 s gap held in 40 of 40.

| B's first transfer to the shared mule | allow | step_up | hold_verify |
|---|---|---|---|
| model + policy only (no antibody) | 39 | 1 | 0 |
| with the cross-bank antibody | 0 | 0 | 40 |

With the antibody, B's transfer is `hold_verify` with `ANTIBODY_MATCH` in 40 of 40 seeds. What this does NOT show: how often real mules are seasoned, how often a scam evades call-guard, or how fast real analysts confirm; the single-cache benchmark also does not time cross-bank propagation (the txn-guard two-instance test does: milliseconds, limit 5 s).

## Reproducibility

- Benchmark seed: 42 (world, benign traffic, benign calls); campaign placement uses substreams `[seed, 8008]`, mule history `[seed, 9009]`, degraded call signal `[seed, 6006]`.
- Ablations requested: no_call_signal, degraded_call_signal, no_antibody.
- Git commit: 87b9a12.
- Models: call-guard `clf-v1`, txn-guard `gbm-v1`.
- As in training, 40% of mule payers received injected prior benign UPI history (5-40 transfers) so that 'no history' is not a scam giveaway.
- Seeds used to train or calibrate the models (the benchmark seed is checked to be disjoint from these):
  - txn_guard_train: 101, 102, 103, 104, 105, 106
  - txn_guard_calibration: 201, 202, 203
  - call_guard_train: 1000, 2000, 3000
  - txn_guard_eval (held out, reported only): 9001, 9002, 9003, 9004
- Command: `python -m scam_bench.run_benchmark --seed 42 --ablations no_call_signal degraded_call_signal no_antibody`

## Limitations

- Simulator only. Every number here is measured on synthetic data generated by the same codebase that the models were trained on (disjoint seeds, shared generators). Treat the figures as a regression and ablation harness, not as real-world performance.
- NEFT has no scam transactions: the simulator's scam flows use UPI and IMPS only, so NEFT recall is undefined and the model never saw NEFT positives. NEFT amounts are naturally ~5x larger, so txn-guard scales its absolute-amount policy thresholds per rail (NEFT x5), requires z >= 6 on NEFT before the overlays escalate and caps the model at step-up-minus-0.01 there unless a call risk is active or history is short. NEFT benign transactions are held at 0.00% (hold) and flagged at 0.83% (step-up or hold) in this run. The scaling has a cost: Rs 25k-250k NEFT to payees >= 30 days old with no call passes, NEFT test-then-escalate holds for payees < 7 days old or at amounts >= Rs 2.5L at any age, and NEFT scams in the Rs 25k-125k range rely on the young-payee floors. NEFT detection is tested by hand-written scenarios only (no NEFT scams in the simulator) and the scale and z values are simulator-derived, not real-data-derived.
- Call signal is idealised in the main run: simulator call-guard alerts every scam call and no benign call, whereas txn-guard was trained and calibrated on 85% call recall and 1.5% spurious benign risk. The call signal matters: recall falls from 82.6% with calls to 81.6% without them. Compare with the `degraded_call_signal` row for a training-like imperfect signal.
- Calibration prevalence: the txn-guard model was calibrated at roughly 1% scam prevalence; this run's prevalence is 3.03% and real prevalence is far lower. Precision depends directly on prevalence (see the prevalence table); recall and FPR do not.
- Call alerts reach txn-guard once, at the threshold crossing (as in production call-guard), and count as active for 15 minutes; long calls can outlast the window.
- Held transactions are still recorded in the payer's history (the pipeline does not model the hold changing later behaviour). Duplicate and out-of-order events are not exercised here.
- Lead time uses the simulator's 10th victim as the mass-victimisation point; it is measured per campaign with few campaigns, so it carries wide uncertainty.
- The antibody gain (94.5% -> 100% victim recall at seed 42) depends on the simulator's structure: each campaign has 3-6 mule accounts reused across ~25 victims, and the oracle confirms every mule payee a fixed delay after its first held transfer, so almost every later victim transfer hits an already-confirmed payee. The 60 s delay is a free parameter (sensitivity table above). Antibody poisoning, false confirmations and antibodies on legitimate merchants are NOT simulated, and 'benign antibody matches = 0' holds by construction (no benign mule payees in the simulator; label-gated confirmation): a tautology, not evidence about analyst accuracy.
- The antibody stage rests on an ASSUMED analyst-confirmation model (first hold_verify on a campaign victim transfer, confirmed after a fixed delay, using simulator ground truth as the analyst's oracle), not on measured analyst behaviour; benign holds are never confirmed. The benchmark has one shared cache, and second-bank victims exist only in the hero scenario, so cross-bank propagation latency and hub outages are not exercised here (see the txn-guard acceptance test). Only the payee of a victim transfer is blocked; the cash-out account (a different payee_hash) is not.
