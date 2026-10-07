# call-guard

Digital-arrest call scoring: multilingual rules (`rules-v1`) blended with a TF-IDF + logistic
regression classifier (`clf-v1`, isotonic-calibrated). The score is a *risk score calibrated
against simulator + authored data only*, not a real-world probability. See `call_guard/model.py`
and `call_guard/rules.py` docstrings for exactly how it is produced.

* `POST /score {message, lang?}` stateless scoring (1..4000 chars); `/healthz`, `/readyz`
  (reports `model_version`, `fallback_mode`), `/metrics` (`call_guard_fallback_mode`).
* With `KAFKA_BOOTSTRAP` set the service consumes `call.events` and publishes `call.risk` when a
  call's accumulated score crosses 0.7 (session state in Redis when `REDIS_URL` is set).

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
