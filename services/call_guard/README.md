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

**Exact guarantee.** The consumer path contains no await on the ledger: the CallRisk alert is
published first and never waits for the ledger, however many crossings arrive at once.
Alerts are at-least-once (a duplicate is byte-identical; consumers dedupe on it); audit entries
are at-least-once and durable: the `callrisk.alert` entry is built before the publish and stored
in the session store in the *same atomic update* that advances the crossing marker (Redis MULTI,
35-day TTL; in-memory: one step). If storing that update fails after the alert went out, the claim
is released and the retry re-publishes the alert and stores the entry. The only way to lose an
audit entry is to lose the session store itself. Delivery is a background task per entry
(`svckit.drain.Drainer`): at most `LEDGER_DRAIN_CONCURRENCY` (8) at once, one per entry, each with
a `LEDGER_EMIT_TIMEOUT_S` (0.5 s) timeout, and a circuit breaker (5 consecutive failures, then
nothing is scheduled for `LEDGER_BREAKER_COOLDOWN_S`, default 10 s). The service lifespan sweeper
(`LEDGER_DRAIN_INTERVAL_S`, default 5 s; batches of 50, so one sweep is O(batch), a backlog drains
over several cycles; while the breaker is open it probes with one attempt) retries the rest.
Shutdown cancels the background tasks; pending entries stay in the store. A ledger outage
therefore only defers audit entries. An entry the PII guard refuses becomes a fixed-shape
placeholder (`call_ref`, `crossing`, `audit: payload_refused`).

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
