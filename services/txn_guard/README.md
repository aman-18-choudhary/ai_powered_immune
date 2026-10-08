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

Absolute-rupee thresholds above (Rs 5k / 10k / 25k / 50k) are multiplied by a per-rail scale
(`policy.RAIL_AMOUNT_SCALE`). Derivation: benign median amount on the 4 held-out simulator seeds is
UPI Rs 452, IMPS Rs 4,288, NEFT Rs 24,157 (p90 2.7k / 19.5k / 135k); NEFT / IMPS = 5.6x, rounded
down to 5 so Rs 3L NEFT with z >= 10 is still caught (3L >= 50k x 5).

| rail | scale | thresholds (young-payee abs / call-guard abs / short-history / hold / extreme) | z threshold for overlays |
|---|---|---|---|
| UPI | 1 | 5k / 10k / 25k / 50k / 50k | 3 (extreme 10) |
| IMPS | 1 | 5k / 10k / 25k / 50k / 50k | 3 (extreme 10) |
| NEFT | 5 | 25k / 50k / 125k / 250k / 250k | 6 (extreme 10) |

On NEFT the booster's score is also capped just below step-up (`RAIL_TYPICAL_AMOUNT_DAMPER`, own
reason code) unless z >= 6: the booster is rail-blind and measures amounts against the payer's
UPI-dominated history, so ordinary large NEFT payments look anomalous to it. The booster itself
stays rail-invariant (smoke test and `test_policy_thresholds_are_rail_scaled_...`); only the policy
layer knows the rail. Anomalous transfers still hold on NEFT (Rs 2L to a 2-day payee from a
Rs 500-typical payer: hold_verify; Rs 3L to a 40-day payee at z >= 10: step_up). NEFT detection is
tested by scenarios only; the simulator has no NEFT scams.

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
