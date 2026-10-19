# Phase 5: held-out metrics for the call-guard classifier `clf-v1`

Date: 2026-10-19. Produced by `python -m call_guard.evaluate`, which scores the **shipped,
SHA-256-pinned** artifact (nothing was retrained). Output:
`services/call_guard/call_guard/artifacts/clf-v1.metrics.json`, now shown on the Models page and
returned by `GET /models`.

> All data is SYNTHETIC. None of these numbers is real-world performance.

## Results (the classifier alone, calibrated probability ≥ 0.5)

| View | n | Precision | Recall | ROC-AUC | False-positive rate |
|---|---|---|---|---|---|
| Report split from the **same generators** as training (different seeds; not the headline) | 5292 chunks | 1.000 | 0.998 | 0.9994 | 0.0% |
| Held-out hand-written benign lines and hard negatives (never seen in training) | 29 | n/a | n/a | n/a | 0 of 29 flagged |
| **Independent** accuracy set v1 (hand-written messages) | 18 | 1.000 | 0.20 | 0.70 | 0.0% |
| **Independent** accuracy set v2 (hand-written messages) — **headline** | 64 | 0.857 | 0.167 | 0.592 | 3.6% |

## What this means

* **It learned its own templates, not scams in general.** It is close to perfect on text from its
  own generators and close to chance on independently written messages (ROC-AUC 0.59).
* **It rarely raises false alarms on its own:** 0 of 29 held-out benign lines, 1 of 28
  legitimate v2 messages. But it misses most independently phrased scams.
* **This matches how the service already uses it.**
  * The classifier is a booster, never a standalone alarm: without two independent rule cue
    classes its contribution is capped below the alert threshold (`call_guard/model.py`).
  * Since accuracy batch 1 it counts only when it adds its own `SCRIPT_CLASSIFIER_MATCH` reason.
  * The rule-based indicators carry the detections: the frozen v2 set reaches 36/36 scams and
    0/28 legitimate with rules-v2.
* **There is a domain shift:** the independent sets are chat and SMS messages, while `clf-v1` was
  trained on call-transcript chunks.

## Not done

* **Retraining:** no retraining with a template-grouped split. It would change the shipped model,
  and it needs more varied and ideally real data.
* **Real data:** no real-world evaluation.
