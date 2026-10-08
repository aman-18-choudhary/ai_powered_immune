# txn-guard

Scores every payment (UPI / IMPS / NEFT) and returns `allow`, `step_up` or `hold_verify`.

* `extract_features(txn, ctx) -> dict[str, float]` (`features.py`): pure and deterministic; amount
  z-score and ratio vs the payer's history, payee account age, new-payee flag, velocity
  (count / amount, 1 h and 24 h), rail, IST hour and night flag, active call risk (max
  `CallRisk.score` for the payer in the last 15 min), device novelty, `payee_in_antibody`
  (placeholder, fixed 0.0 until Task 11), future-dated flag. NaN/inf are sanitised.
* `Context` / `HistoryStore` / `InMemoryHistoryStore` (`history.py`): what extraction needs. A
  Redis store is Task 9.
* `Scorer().score(features) -> (score, reasons)` (`model.py`): `gbm-v1` = scikit-learn
  `HistGradientBoostingClassifier` (monotone in the risk-only cues) + isotonic calibration.
  `score_with_version` also returns the version that actually scored (`gbm-v1` or
  `rules-fallback-v1`). Every verdict carries up to 4 reasons (`NEW_PAYEE`,
  `YOUNG_PAYEE_ACCOUNT`, `ACTIVE_SCAM_CALL`, `AMOUNT_ANOMALY`, `VELOCITY_SPIKE`,
  `NIGHT_TRANSFER`, `NEW_DEVICE`, `PAYEE_IN_ANTIBODY`, `FUTURE_DATED_TIMESTAMP`, or
  `NO_RISK_INDICATORS`); details are numbers only, never identifiers.
* `decide(score)` (`decision.py`): `STEP_UP_AT = 0.5`, `HOLD_AT = 0.8` (>= comparisons);
  `make_decision(...)` builds the `TxnDecision` and logs only txn_id, decision and score.

## What the score means

A calibrated probability **inside the simulator's world** (`CALIBRATED_PREVALENCE = 0.01`, about 1
scam transaction per 100 benign, imperfect call-risk signal), not a real-world fraud probability.
**Prior shift:** on real traffic the base rate is very different, so the score must be
recalibrated on real labelled traffic before it is read as a probability; until then use it as a
ranking-quality risk score.

The booster never sees `rail` (simulated scams are almost all UPI, so it would learn "IMPS/NEFT is
benign"); a load-time smoke test scores the same scam on UPI/IMPS/NEFT and refuses an artifact whose
rails disagree.

### Policy overlays (`policy.py`, every rail)

After the model, floors are applied: `final = max(model, floor)`. When one lifts the score the
returned score is a **policy-lifted value, not a calibrated probability**, the verdict carries a
reason with the overlay's own code (`YOUNG_PAYEE_LARGE_AMOUNT_FLOOR`, `CALL_RISK_AMOUNT_GUARD`,
`FUTURE_DATED_TIMESTAMP`; `model.overlay_applied(reasons)` tells consumers and the audit ledger)
and the model-derived reason weights are untouched (overlay weight = lift over the model score).

* young payee (< 30 d) + payee not *established* (established = >= 3 prior payments AND first
  paid >= 7 days ago, so a small "test" payment does not disable the rule) + large amount (z >= 3 and >= Rs 5,000; or >= Rs 25,000 for
  a payer with < 3 prior transfers): at least step_up; hold_verify if >= Rs 50,000 or payee < 7 d.
* active call risk >= 0.7 + amount anomaly (z >= 3 and >= Rs 10,000, or short-history >= Rs
  25,000): at least step_up for any payee; hold_verify if the payee is new/young, was first paid
  < 24 h ago, or already received >= Rs 10,000 from this payer in the last 60 min.
* `PAYEE_AMOUNT_ESCALATION`: payee < 30 d old + amount >= 10x the largest amount this payer ever
  sent it + >= Rs 25,000: at least step_up; hold_verify from Rs 50,000 when also z >= 3 and not
  established.
* `NEW_PAYEE_EXTREME_AMOUNT`: payee not established + z >= 10 + >= Rs 50,000, any payee age:
  step_up only.
* timestamp > 5 min in the future: at least step_up.

### Per-rail scales (task 7b)

Every absolute-rupee threshold (Rs 5k / 10k / 25k / 50k and the Rs 10k "repeat large" amount) is
multiplied by a per-rail scale (`features.RAIL_AMOUNT_SCALE`). Derivation: benign median amount on
the 4 held-out simulator seeds is UPI Rs 452, IMPS Rs 4,288, NEFT Rs 24,157 (p90 2.7k / 19.5k /
135k); NEFT / IMPS = 5.6x, rounded down to 5 so Rs 3L NEFT with z >= 10 is still caught.

| rail | scale | young-payee abs / call-guard abs / short-history / hold / extreme | overlay z | call-guard z |
|---|---|---|---|---|
| UPI | 1 | 5k / 10k / 25k / 50k / 50k | 3 (extreme 10) | 3 |
| IMPS | 1 | 5k / 10k / 25k / 50k / 50k | 3 (extreme 10) | 3 |
| NEFT | 5 | 25k / 50k / 125k / 250k / 250k | 6 (extreme 10) | 3 if payee young / recently new / repeat, else 4 |

**Model damper** (`RAIL_TYPICAL_AMOUNT_DAMPER`, NEFT only): the booster is rail-blind and measures
amounts against the payer's UPI-dominated history, so ordinary large NEFT payments look anomalous.
A model score above 0.49 (`STEP_UP_AT - 0.01`) is capped at 0.49 unless z >= 6. It is **skipped**
when an active call risk >= 0.7 exists or the payer has < 3 prior transfers (z is then "unknown",
not "typical"). The damper reason states the original and capped score and appears only when the
cap changed the score. It is a model-path correction only: in rules-fallback mode there is no
damper (the fallback does not use z-scored amounts for its score); overlays apply in both modes.
The booster itself stays rail-invariant (`rail` is not in `MODEL_FEATURES`; tested).

**Derivation of the NEFT z values** (benign NEFT, 4 held-out seeds, n=7,769; hold / flag %):

| overlay z (NEFT) | call-guard z 3 (all payees) | call-guard z 4 (non-young payees) | call-guard z 5 (non-young payees) |
|---|---|---|---|
| 3 | 0.49 / 2.28 | 0.39 / 2.06 | 0.37 / 2.03 |
| 4 | 0.36 / 1.44 | 0.26 / 1.22 | 0.25 / 1.20 |
| 5 | 0.28 / 1.24 | 0.18 / 1.02 | 0.17 / 0.99 |
| 6 | 0.22 / 1.08 | **0.12 / 0.86** | 0.10 / 0.84 |
| 8 | 0.22 / 1.04 | 0.12 / 0.82 | 0.10 / 0.80 |

(In the z 4 / z 5 columns young, recently-new and repeat payees always use call-guard z 3.)
Targets: NEFT hold <= 0.2%, flag <= 1.0%. z = 6 is the smallest overlay z meeting both with margin
(z = 5 is on the 1.0% line); z = 6 is partly a round number chosen from this simulator sweep, not
a real-data estimate.

**Short-history payers and the call-risk guard.** For a payer with fewer than 3 prior transfers
the call-risk guard uses the *unscaled* Rs 25,000 short-history floor on NEFT as well (not
Rs 1.25L), so cold-start NEFT decisions with an active call match IMPS (tested over payee age
1/5/45/900 x Rs 25k-300k). Amount thresholds compare the raw INR amount (`amount_inr`), so exact
boundaries (e.g. Rs 1,25,000 vs Rs 1,24,999) behave as documented (tested for every scaled floor).

**What the NEFT scaling costs (read this).**
* The Rs 25k-250k NEFT band is open for payees >= 30 days old with no call: a Rs 500-typical
  payer sending Rs 60k-240k to a 35-45 day payee is `allow` (tested as a documented gap).
* NEFT test-then-escalate holds for payees < 7 days old, or at amounts >= Rs 2.5L at any age;
  Rs 90k at 10 or 25 days is step_up (tested).
* NEFT-typical payers (e.g. Rs 24k typical) are only escalated when z reaches the NEFT thresholds.
* NEFT scams in the Rs 25k-125k range rely on the young-payee floors.
* The simulator has zero NEFT scams, so NEFT recall is by hand-written scenarios only.
* The scale, z thresholds and damper are derived from simulator benign amounts, not real data.

### Reliability (raw model, 4 held-out eval seeds, 144,993 benign + 1,032 scam txns)

| predicted bin | n | mean predicted | observed |
|---|---|---|---|
| 0.0-0.1 | 144,844 | 0.000 | 0.000 |
| 0.1-0.2 | 40 | 0.161 | 0.125 |
| 0.2-0.3 | 11 | 0.286 | 0.091 |
| 0.3-0.4 | 117 | 0.337 | 0.436 |
| 0.4-0.5 | 22 | 0.437 | 0.500 |
| 0.5-0.6 | 77 | 0.500 | 0.701 |
| 0.6-0.7 | 30 | 0.675 | 0.500 |
| 0.8-0.9 | 90 | 0.858 | 0.878 |
| 0.9-1.0 | 794 | 0.984 | 0.979 |

ECE 0.0004 (raw), 0.0008 after overlays (dominated by the empty-ish upper bins; mid bins have few
samples). Also stored in the artifact (`reliability`, `ece`).

## Fallback

The artifact is loaded only if its SHA-256 matches `artifact_pin.py`, the scikit-learn version
and feature names match, and a smoke test passes; otherwise (or if `predict_proba` raises) the
transparent noisy-OR `rules-fallback-v1` scores, with a WARNING log and `/readyz`
`fallback_mode=true`.

## Retraining

```bash
pip install -e "services/txn_guard[train]"   # needs sim-engine (training only)
python -m txn_guard.train                     # rewrites artifacts/gbm-v1.joblib + artifact_pin.py
```

Commit the joblib and `artifact_pin.py` together; retrain after upgrading scikit-learn. Train,
calibration and evaluation seeds are disjoint (`train.py`); the run prints the reliability table,
permutation importance, held-out precision / recall / FPR / held-benign rate and ablations.
Training data attaches an imperfect call risk (85% recall, 1-3 chunk lag, 1.5% spurious on
benign) so the model cannot learn "call risk == scam" (`simdata.py`).
When Task 11 fills `payee_in_antibody`, the feature is constant in training data today, so the
booster ignores it: retrain with antibody-labelled data.
