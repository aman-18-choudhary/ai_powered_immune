# Confirm-to-hub report

Gap: an analyst's `POST /holds/{txn}/resolve confirm_block` on txn-guard only resolved the hold;
nothing created an antibody at antibody-hub in the multi-container stack (only the single-process
demo server called `hub.submit`).

## Log

### Step 1 - tests first (RED)
- `services/txn_guard/tests/test_hub_submit.py` (about 35 cases over memory + fakeredis stores,
  API metrics/readyz, env config) and a Drainer `name` test in `libs/svckit/tests/test_drain.py`.
- RED: collection error `ModuleNotFoundError: txn_guard.hub_submit` (module and hold fields do not
  exist yet).

### Step 2 - implementation (GREEN)
- `txn_guard/hub_submit.py` (config, `HubSubmission`, outcome classification, `evidence_ref_for`),
  `Hold` gains `payee_hash`, `hub_pending`, `hub_result`, `hub_reason`; `resolve(confirm_block)`
  writes the submission (or a recorded skip + audit entry) in the same CAS; `drain_hub` /
  `_hub_apply` deliver and record; sweep delivers due items in a batch; Redis keeps
  `txnguard:holds:hub` and persists (no 90-day expiry) a closed hold with an undelivered item.
  `HttpxHubClient.submit_antibody` (async). `svckit.Drainer(name=...)` additive.
- API: view hides `payee_hash`/`hub_pending` (adds `hub_pending` bool), metrics
  `txn_guard_hub_*`, `/readyz` `hub_submit` + `hub_reachable` (informational).
- GREEN: `test_hub_submit.py` 43 passed (memory + fakeredis), svckit drain test passed.

### Design decisions worth knowing
- `evidence_ref` is NOT the raw `txn_ref`: the hub's identifier guard is stricter than the ledger's
  (9 digits with up to 3 non-digits between them are refused; about 10% of `txn_<16 hex>` refs
  would 422). The ref is `txn_` + the hex with digits mapped to g-p (digit-free, reversible).
- Backoff clock is in memory (monotonic), attempts + last error class are persisted.
- The demo harness builds hold stores without a hub config, so `confirm_block` there queues
  nothing; the engine's own `hub.submit` remains the only submission.

### Step 3 - verification
| suite | result |
|---|---|
| services/txn_guard | 376 passed (105 s) |
| services/antibody_hub | 160 passed, 5 skipped |
| tests/e2e/audit | 32 passed (incl. new test_confirm_to_hub.py, 5 tests) |
| tests/e2e/bench | 38 passed |
| services/demo_server | 184 passed (incl. new one-antibody-per-confirm test) |
| libs/svckit | 180 passed |
| contracts | 44 passed |
| ruff check / format | clean |
| docker compose config -q | ok |
| check_links / check_dockerfiles | 0 broken / 0 problems |

### Known limits
- Counters and the retry clock are per process; undelivered submissions are in the hold store.
- A skipped (cap, bad hash, missing bank) or terminally rejected submission is not retried
  automatically; pre-existing holds (no stored `payee_hash`) skip with `invalid_key_hash`.
- Not exercised against Postgres/Kafka/Redpanda (no Docker daemon): compose config is validated only.
- The shared HUB_GATEWAY_SECRET lets any holder assert any principal (documented).
