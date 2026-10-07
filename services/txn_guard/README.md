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

A calibrated probability **inside the simulator's world** (about 1 scam transaction per 100
benign, imperfect call-risk signal), not a real-world fraud probability. Use it as a ranking-quality
risk score. Two policy overlays sit after the model and are not part of calibration: a
future-dated timestamp (> 5 min ahead of `Context.now`) is at least `step_up`; an active call risk
>= 0.7 plus a strong amount anomaly (z >= 3) to a payee the payer has never paid is floored at
0.85 (`hold_verify`).

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
