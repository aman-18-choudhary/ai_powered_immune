# call-guard

Digital-arrest call scoring: multilingual rules (`rules-v1`) blended with a TF-IDF + logistic
regression classifier (`clf-v1`, isotonic-calibrated). The score is a *risk score calibrated
against simulator + authored data only*, not a real-world probability. See `call_guard/model.py`
and `call_guard/rules.py` docstrings for exactly how it is produced.

* `POST /score {message, lang?}` stateless scoring (1..4000 chars); `/healthz`, `/readyz`
  (reports `model_version`, `fallback_mode`), `/metrics` (`call_guard_fallback_mode`).
* With `KAFKA_BOOTSTRAP` set the service consumes `call.events` and publishes `call.risk` when a
  call's accumulated score crosses 0.7 (session state in Redis when `REDIS_URL` is set).

## Ledger entry per alert (Task 13)

**Exact guarantee.** The CallRisk alert is published first and nothing about the ledger can
delay, fail, DLQ or repeat it. The `callrisk.alert` audit entry is built before the publish and
stored in the session store in the *same atomic update* that advances the crossing marker (Redis
MULTI, 35-day TTL; in-memory: one step), then delivered best-effort: a bounded wait
(`LEDGER_EMIT_TIMEOUT_S`, default 0.5 s; slow deliveries continue in the background) and a
periodic sweeper in the service lifespan (`LEDGER_DRAIN_INTERVAL_S`, default 5 s, batches of 50).
Delivery is at-least-once; the ledger's full-entry idempotency absorbs repeats. An audit entry
the PII guard refuses becomes a fixed-shape placeholder (`call_ref`, `crossing`, `audit:
payload_refused`). Honest limit: if the process dies after the alert is published but before the
marker/pending update, the retry re-publishes the alert (consumers dedupe on payload hash) and
rebuilds the entry, so neither is lost; if the session store itself is down the step is retried
like any publish. An entry can still be delayed arbitrarily while the ledger is down, and lost
only if the store loses it (Redis data loss, or 35 days pass).

Payload: `call_ref` (first 16 hex of `sha256(call_id)`; the call id is never emitted), `crossing`,
`score` (4 dp, the peak at the crossing), `reason_codes` (sorted codes, no free text),
`model_version`, `chunk_count` (events scored when the crossing happened), `threshold`,
`crossed_ts`. Case refs: `[call_ref:<16 hex>]`. Not in the ledger: call text, caller number or
hash, victim token, reason detail.

## Retraining the call-guard classifier

```bash
pip install -e "services/call_guard[train]"      # needs sim-engine (training only)
python -m call_guard.train                        # writes call_guard/artifacts/clf-v1.joblib
```

Training also rewrites `call_guard/artifact_pin.py` (SHA-256 of the artifact). **Commit the
joblib and `artifact_pin.py` together**: the service refuses an artifact whose hash differs from
the pin and falls back to rules-only. The artifact also records the scikit-learn version and is
rejected on mismatch, so retrain after upgrading scikit-learn. joblib output is not
byte-deterministic, so every retrain changes the hash.

After retraining check:

1. The printed reliability table / ECE (report split) and that the artifact is < 1 MB.
2. `pytest -W error services/call_guard`, in particular `tests/test_independent_eval.py`
   (frozen set, never tune on it), `test_model.py` (dev sets, KYC-notice and hard-negative gates,
   tamper / version / smoke fallbacks).
3. Add new benign out-of-template text to `call_guard/authored.py` for any false positive found,
   rather than loosening the rule caps: the classifier can only lift a chunk past 0.6 when rules
   corroborate it (see `CLF_CAP_*` in `model.py`).
