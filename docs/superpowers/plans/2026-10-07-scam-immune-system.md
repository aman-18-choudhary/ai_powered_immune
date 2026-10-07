# Federated Scam Immune System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Dockerised, microservice, finance-grade platform that detects digital-arrest scams, holds risky transfers, shares hashed threat "antibodies" across banks, maps fraud rings, and produces tamper-evident evidence packages, all on a realistic synthetic UPI/IMPS/NEFT ecosystem.

**Architecture:** Python 3.11 FastAPI services communicate only through a Redpanda event bus and versioned JSON contracts in a shared `contracts` package. Each service owns its datastore. A seeded simulator produces labelled data so every claim (precision, recall, false-positive rate, lead time) is measured. A React console fronts three personas.

**Tech Stack:** Python 3.11, FastAPI, pydantic v2, pytest, aiokafka/Redpanda, Postgres + PostGIS, Redis, Neo4j, scikit-learn/LightGBM, networkx, React + TypeScript + Tailwind + shadcn/ui, deck.gl, Docker Compose, Prometheus/Grafana/OpenTelemetry.

**Spec:** `docs/superpowers/specs/2026-10-07-scam-immune-system-design.md`

## Global Constraints

- Counterfeit currency detection is out of scope; real telecom/bank integrations are simulated adapters.
- All state-changing events carry an `idempotency_key`; handlers must be idempotent.
- txn-guard p99 latency < 300 ms; benign-transaction hold rate < 0.1%.
- Blocking is never silent: the only protective decision beyond `allow` is `step_up` or `hold_verify`.
- Raw PII (names, phone numbers, account numbers) never crosses a bank boundary or enters the ledger; only tokens or keyed hashes do.
- Every verdict carries `reasons: list[Reason]`, a calibrated `score` in [0,1], and `model_version`.
- Service-to-service access is events or HTTP APIs only; no cross-database reads.
- Synthetic realism: UPI per-transaction limit Rs 1,00,000; IMPS limit Rs 5,00,000; NEFT in half-hourly batches 24x7; benign traffic >= 99% of volume; all timestamps IST-aware (`Asia/Kolkata`).
- Python 3.11, type-hinted, `ruff` clean; every service ships `Dockerfile`, `/healthz`, `/readyz`.
- Citizen languages: English, Hindi plus 2 regional (Tamil, Bengali) initially; message keys structured for 12.

## Review Focus

1. Duplicate or replayed events (same `idempotency_key`) must not double-hold a transfer or double-write ledger entries.
2. A `call.risk` event arriving after the transaction it concerns (out-of-order); txn-guard must still re-evaluate held/pending items, not crash.
3. A legitimate merchant account wrongly published as an antibody; antibodies need analyst confirmation, TTL expiry, and a revoke path.
4. Citizen messages that are empty, very long, mixed-script, or in an unsupported language; must return a safe verdict, not an error.
5. Zero, negative, over-limit, or boundary amounts and IST day-boundary timestamps in transactions; must validate, not mis-score.

## File Structure

```
contracts/                 shared pydantic models + topic names (package: scam_contracts)
libs/svckit/               Bus protocol, InMemoryBus, KafkaBus, idempotency, health, DLQ, tokenizer
services/sim_engine/       generators, ground truth, replay
services/call_guard/       script/spoof scoring
services/txn_guard/        features, model, decisions, hold-verify API, antibody consumer
services/antibody_hub/     keyed-hash threat memory
services/graph_twin/       graph build, ring detection, takedown sim
services/geo_intel/        hotspot analytics
services/evidence_ledger/  hash chain, packages, verifier
services/citizen_shield/   multilingual verdicts
services/gateway/          auth, RBAC, rate limit, routing
web-console/               React app
infra/                     docker-compose.yml, k8s/, grafana/, prometheus.yml
tests/e2e/                 hero scenario, benchmark
docs/                      architecture diagram, deck, demo script
```

## Execution Waves (for parallel agents)

- Wave A: Task 1 -> Task 2 (sequential; everything depends on them)
- Wave B (parallel): Tasks 3, 4, 5
- Wave C (parallel): Tasks 6, 7, 8
- Wave D (parallel): Tasks 9, 10, 11, 12
- Wave E (parallel): Tasks 13, 14, 15, 16
- Wave F: Tasks 17 -> 18 -> 19
- Wave G: Task 20 -> Task 21

---

### Task 1: Repo skeleton, contracts, compose

**Files:**
- Create: `contracts/scam_contracts/models.py`, `contracts/scam_contracts/topics.py`, `contracts/pyproject.toml`, `infra/docker-compose.yml`, `Makefile`, `.github/workflows/ci.yml`
- Test: `contracts/tests/test_models.py`

**Interfaces:**
- Produces (all pydantic v2 models, frozen):
  - `Reason(code: str, weight: float, detail: str)`
  - `Transaction(txn_id, idempotency_key, bank_id, payer_token, payee_hash, rail: Literal["UPI","IMPS","NEFT"], amount_inr: Decimal, ts: datetime, payee_account_age_days: int, device_id_token: str)`; validators: `amount_inr > 0`, UPI <= 100000, IMPS <= 500000, `ts` tz-aware
  - `CallEvent(call_id, idempotency_key, victim_token, caller_number_hash, ts, transcript_chunk: str, channel: Literal["pstn","voip","video"], lang: str)`
  - `CallRisk(call_id, victim_token, score, reasons: list[Reason], model_version, ts)`
  - `TxnDecision(txn_id, decision: Literal["allow","step_up","hold_verify"], score, reasons, model_version, ts)`
  - `Antibody(antibody_id, kind: Literal["mule_account","script","device"], key_hash: str, source_bank, confirmed_by, created_at, expires_at, revoked: bool=False)`
  - `LedgerEntry(seq: int, ts, service, actor, event_type, payload_hash, prev_hash, entry_hash, model_version: str | None)`
  - `LedgerEntryIn(service, actor, event_type, payload_hash, model_version: str | None)`
  - `keyed_hash(value: str, kind: str, federation_key: bytes) -> str` in `scam_contracts/hashing.py` (HMAC-SHA256 hex; single shared implementation used by antibody-hub and txn-guard)
  - `Topics` constants: `TXN_EVENTS="txn.events"`, `CALL_EVENTS="call.events"`, `CALL_RISK="call.risk"`, `TXN_DECISIONS="txn.decisions"`, `ANTIBODIES="antibody.published"`, `LEDGER="ledger.append"`, `COMPLAINTS="complaints"`, `DLQ_SUFFIX=".dlq"`

- [ ] **Step 1: Write failing tests** in `test_models.py` (plus `test_keyed_hash_deterministic_and_kind_separated`): `test_upi_over_limit_rejected` (amount 100001 on UPI raises `ValidationError`), `test_imps_500000_allowed`, `test_naive_ts_rejected`, `test_zero_and_negative_amount_rejected`, `test_models_are_frozen`.
- [ ] **Step 2: Run** `pytest contracts -v` -> FAIL (module missing).
- [ ] **Step 3: Implement** the models and topics above; add `docker-compose.yml` with redpanda, postgres(+postgis), redis, neo4j, prometheus, grafana; `Makefile` targets `up`, `down`, `test`, `lint`; CI runs `ruff` and `pytest` for every package.
- [ ] **Step 4: Run** `pytest contracts -v` -> PASS; `docker compose -f infra/docker-compose.yml config` -> valid.
- [ ] **Step 5: Commit** `feat: contracts package, compose skeleton and CI`

### Task 2: svckit (bus, idempotency, health, DLQ, PII tokenizer)

**Files:**
- Create: `libs/svckit/svckit/bus.py`, `idempotency.py`, `health.py`, `tokenizer.py`, `libs/svckit/pyproject.toml`
- Test: `libs/svckit/tests/test_bus.py`, `test_idempotency.py`, `test_tokenizer.py`

**Interfaces:**
- Consumes: `Topics`, models from Task 1
- Produces:
  - `class Bus(Protocol)`: `async publish(topic: str, key: str, value: BaseModel) -> None`; `subscribe(topic: str, group: str) -> AsyncIterator[bytes]`
  - `InMemoryBus` and `KafkaBus(bootstrap: str)` implementing `Bus`
  - `async def consume(bus, topic, group, model: type[T], handler: Callable[[T], Awaitable[None]], store: IdempotencyStore, max_retries: int = 3) -> None` (retries, then publishes raw message to `topic + ".dlq"`)
  - `IdempotencyStore` with `async seen(key: str) -> bool` and `async mark(key: str) -> None` (`InMemoryIdempotencyStore`, `RedisIdempotencyStore`)
  - `make_health_router(ready_check: Callable[[], Awaitable[bool]]) -> APIRouter` exposing `/healthz`, `/readyz`
  - `Tokenizer(secret: bytes)` with `token(value: str, kind: str) -> str` (deterministic HMAC-SHA256, prefixed `tok_`), no reverse method

- [ ] **Step 1: Write failing tests**: `test_duplicate_idempotency_key_handled_once` (publish same event twice, handler call count == 1), `test_handler_failure_goes_to_dlq_after_three_tries`, `test_token_is_deterministic_and_differs_by_kind`, `test_token_does_not_contain_raw_value`.
- [ ] **Step 2: Run** `pytest libs/svckit -v` -> FAIL.
- [ ] **Step 3: Implement** per signatures; `consume` marks the key only after the handler succeeds.
- [ ] **Step 4: Run** -> PASS.
- [ ] **Step 5: Commit** `feat: svckit bus, idempotency, health, tokenizer`

### Task 3: sim-engine generators with ground truth

**Files:**
- Create: `services/sim_engine/sim_engine/world.py`, `benign.py`, `scam.py`, `calls.py`, `labels.py`, `pyproject.toml`, `Dockerfile`
- Test: `services/sim_engine/tests/test_realism.py`, `test_ground_truth.py`

**Interfaces:**
- Consumes: `Transaction`, `CallEvent`
- Produces:
  - `build_world(seed: int, n_citizens: int, n_banks: int = 4) -> World` (citizens with state/district, banks, accounts)
  - `gen_benign_txns(world: World, days: int, seed: int) -> Iterator[Transaction]` (lognormal amounts; rails mix about 85% UPI/10% IMPS/5% NEFT; diurnal hour-of-day curve)
  - `gen_scam_campaign(world: World, campaign_id: str, n_victims: int, seed: int) -> Campaign` with `.calls: list[CallEvent]`, `.txns: list[Transaction]`, `.mule_chain: list[str]`, `.cashout_points: list[tuple[float, float]]`
  - `class GroundTruth` with `is_scam_txn(txn_id) -> bool`, `campaign_of(txn_id) -> str | None`, `first_signal_ts(campaign_id) -> datetime`, `mass_victimisation_ts(campaign_id) -> datetime` (time when victim count crosses 10)

- [ ] **Step 1: Write failing tests**: `test_benign_share_at_least_99_percent` (full run), `test_no_upi_over_one_lakh`, `test_amount_distribution_lognormal` (KS test p > 0.01 against fitted lognormal), `test_neft_timestamps_on_half_hour_batches`, `test_scam_victim_transfer_follows_call_within_minutes`, `test_mule_chain_pass_through_under_one_hour` (money exits mule accounts quickly), `test_same_seed_is_deterministic`.
- [ ] **Step 2: Run** `pytest services/sim_engine -v` -> FAIL.
- [ ] **Step 3: Implement** generators; scam victim amounts skew high (Rs 20k-100k split across UPI limits, repeat transfers), mule accounts young (< 30 days), fan-in to 3-6 mules then fan-out to a cash-out account.
- [ ] **Step 4: Run** -> PASS.
- [ ] **Step 5: Commit** `feat: realistic synthetic ecosystem with ground truth`

### Task 4: sim-engine replay service

**Files:**
- Create: `services/sim_engine/sim_engine/replay.py`, `api.py`
- Test: `services/sim_engine/tests/test_replay.py`

**Interfaces:**
- Consumes: Task 2 `Bus`, Task 3 generators
- Produces: `async def replay(bus: Bus, world: World, scenarios: list[Scenario], speed: float = 1.0) -> None` publishing in timestamp order to `CALL_EVENTS` and `TXN_EVENTS`; HTTP `POST /scenarios/hero` runs the hero scenario (one campaign, two banks, victim B starts 90 s after victim A is confirmed)

- [ ] **Step 1: Test**: `test_events_published_in_timestamp_order`, `test_speed_multiplier_scales_delays`, `test_hero_scenario_has_victims_in_two_banks_and_two_states`.
- [ ] **Step 2: Run** -> FAIL. **Step 3: Implement.** **Step 4: Run** -> PASS.
- [ ] **Step 5: Commit** `feat: scenario replay with hero scenario`

### Task 5: Gateway (auth, RBAC, rate limit)

**Files:**
- Create: `services/gateway/gateway/main.py`, `auth.py`, `rbac.py`, `ratelimit.py`, `pyproject.toml`, `Dockerfile`
- Test: `services/gateway/tests/test_auth.py`

**Interfaces:**
- Produces: JWT login `POST /auth/login` returning token with claim `role` in `{"officer","analyst","citizen","admin"}`; dependency `require_role(*roles) -> Callable`; routes proxied by prefix (`/api/txn`, `/api/graph`, `/api/geo`, `/api/ledger`, `/api/citizen`); `X-Request-Id` propagated; per-role rate limit via Redis

- [ ] **Step 1: Test**: `test_citizen_cannot_call_ledger_export` (403), `test_expired_token_rejected` (401), `test_rate_limit_returns_429`, `test_request_id_propagated`.
- [ ] **Step 2: Run** -> FAIL. **Step 3: Implement.** **Step 4: Run** -> PASS.
- [ ] **Step 5: Commit** `feat: gateway with RBAC and rate limiting`

### Task 6: call-guard scoring

**Files:**
- Create: `services/call_guard/call_guard/rules.py`, `model.py`, `session.py`, `consumer.py`, `pyproject.toml`, `Dockerfile`
- Test: `services/call_guard/tests/test_rules.py`, `test_session.py`

**Interfaces:**
- Consumes: `CallEvent`, `CallRisk`, `Reason`, `consume`
- Produces: `score_chunk(event: CallEvent) -> tuple[float, list[Reason]]` (rules for authority impersonation (CBI/ED/customs/police), "digital arrest", isolation demands ("do not disconnect", "do not tell anyone"), urgency, account-verification asks; also supports Hindi keywords); `SessionScorer.update(event) -> CallRisk` accumulates risk per `call_id` in Redis with decay; consumer publishes `CallRisk` to `CALL_RISK` when score >= 0.7 (threshold constant `CALL_RISK_THRESHOLD = 0.7`); `model_version = "rules-v1"`, then a TF-IDF + logistic regression layer `clf-v1` trained on simulator transcripts

- [ ] **Step 1: Test**: `test_digital_arrest_script_scores_above_threshold`, `test_normal_bank_call_scores_below_0_2`, `test_hindi_script_detected`, `test_risk_accumulates_across_chunks`, `test_reasons_include_codes` (e.g. `AUTHORITY_IMPERSONATION`).
- [ ] **Step 2: Run** -> FAIL. **Step 3: Implement** rules first, then classifier trained in `train.py` from sim transcripts, blended by max with rules. **Step 4: Run** -> PASS.
- [ ] **Step 5: Commit** `feat: call-guard digital arrest scoring`

### Task 7: txn-guard scoring and decisions

**Files:**
- Create: `services/txn_guard/txn_guard/features.py`, `model.py`, `decision.py`, `train.py`, `pyproject.toml`, `Dockerfile`
- Test: `services/txn_guard/tests/test_features.py`, `test_decision.py`, `test_latency.py`

**Interfaces:**
- Consumes: `Transaction`, `CallRisk`, `TxnDecision`
- Produces:
  - `extract_features(txn: Transaction, ctx: Context) -> dict[str, float]` (amount z-score vs payer history, payee account age, new-payee flag, velocity, active call risk, device novelty)
  - `class Scorer: score(features) -> tuple[float, list[Reason]]` (LightGBM + isotonic calibration; `model_version="gbm-v1"`; rules-only fallback `rules-fallback-v1` if model fails to load)
  - `decide(score: float) -> Literal["allow","step_up","hold_verify"]` with constants `STEP_UP_AT = 0.5`, `HOLD_AT = 0.8`

- [ ] **Step 1: Test**: `test_boundary_scores` (0.49 allow, 0.5 step_up, 0.8 hold_verify), `test_call_risk_raises_score`, `test_fallback_when_model_missing`, `test_p99_latency_under_300ms` (1000 scores, p99 < 0.3 s), `test_future_dated_and_day_boundary_ts_scored` (23:59:59 / 00:00:00 IST).
- [ ] **Step 2: Run** -> FAIL. **Step 3: Implement**; training uses sim data with ground-truth labels. **Step 4: Run** -> PASS.
- [ ] **Step 5: Commit** `feat: txn-guard scoring and decisioning`

### Task 8: eval harness

**Files:**
- Create: `tests/e2e/bench/metrics.py`, `tests/e2e/bench/run_benchmark.py`
- Test: `tests/e2e/bench/test_metrics.py`

**Interfaces:**
- Consumes: `GroundTruth`, `TxnDecision`, `CallRisk`
- Produces: `compute_metrics(decisions, truth) -> Metrics(precision, recall, f1, fpr, held_benign_rate)`; `lead_time(campaign_id, detections, truth) -> timedelta` (first detection ts relative to `mass_victimisation_ts`; positive means detected before); `run_benchmark(seed: int, ablations: list[str]) -> dict` writing `docs/benchmark_report.md` with ablations `no_antibody`, `no_call_signal`

- [ ] **Step 1: Test**: `test_perfect_detection_metrics`, `test_fpr_computed_on_benign_only`, `test_lead_time_positive_when_early`. **Step 2: Run** -> FAIL. **Step 3: Implement.** **Step 4: Run** -> PASS.
- [ ] **Step 5: Commit** `feat: benchmark harness`

### Task 9: txn-guard hold-and-verify API and consumers

**Files:**
- Create: `services/txn_guard/txn_guard/api.py`, `consumer.py`, `holds.py`
- Test: `services/txn_guard/tests/test_holds.py`

**Interfaces:**
- Consumes: Tasks 6, 7; `consume`; `RedisIdempotencyStore`
- Produces: consumer of `TXN_EVENTS` and `CALL_RISK` publishing `TxnDecision` to `TXN_DECISIONS`; `HoldStore` with `create(txn_id)`, `resolve(txn_id, action: Literal["release","confirm_block"], actor: str)`; HTTP `GET /holds`, `POST /holds/{txn_id}/resolve` (roles `analyst`, `citizen` for own transfer step-up); late `CALL_RISK` re-scores pending/held transactions of the same `victim_token` within 15 min

- [ ] **Step 1: Test**: `test_replayed_txn_creates_one_hold` (Review Focus 1), `test_late_call_risk_upgrades_pending_txn` (Review Focus 2), `test_resolve_requires_actor`, `test_decision_event_published`.
- [ ] **Step 2: Run** -> FAIL. **Step 3: Implement.** **Step 4: Run** -> PASS.
- [ ] **Step 5: Commit** `feat: hold-and-verify workflow`

### Task 10: antibody-hub

**Files:**
- Create: `services/antibody_hub/antibody_hub/hashing.py`, `store.py`, `api.py`, `bloom.py`, `consumer.py`, `pyproject.toml`, `Dockerfile`
- Test: `services/antibody_hub/tests/test_hashing.py`, `test_lifecycle.py`

**Interfaces:**
- Consumes: `Antibody`, `Bus`
- Produces: re-exports `keyed_hash` from `scam_contracts.hashing` (no local copy); HTTP `POST /antibodies` (requires `confirmed_by`; unconfirmed submissions return 422), `DELETE /antibodies/{id}` (revoke, publishes tombstone), `GET /antibodies/bloom?bank_id=` returning a Bloom filter snapshot; `ANTIBODY_TTL_DAYS = 14`; publishes to `ANTIBODIES` on create and revoke

- [ ] **Step 1: Test**: `test_raw_account_number_never_persisted` (inspect DB rows and published events for the raw value), `test_unconfirmed_antibody_rejected`, `test_expired_antibody_not_in_bloom`, `test_revoked_antibody_removed_and_tombstone_published` (Review Focus 3), `test_hash_differs_by_kind`.
- [ ] **Step 2: Run** -> FAIL. **Step 3: Implement.** **Step 4: Run** -> PASS.
- [ ] **Step 5: Commit** `feat: federated antibody hub`

### Task 11: txn-guard consumes antibodies (second-victim protection)

**Files:**
- Create: `services/txn_guard/txn_guard/antibody_cache.py`
- Modify: `services/txn_guard/txn_guard/features.py` (add `payee_in_antibody` feature), `consumer.py`
- Test: `services/txn_guard/tests/test_antibody_cache.py`

**Interfaces:**
- Consumes: `Antibody` events, `scam_contracts.hashing.keyed_hash`
- Produces: `AntibodyCache.apply(event: Antibody) -> None`, `AntibodyCache.contains(key_hash: str) -> bool`; a payee hit forces `score >= 0.9` and reason code `ANTIBODY_MATCH`

- [ ] **Step 1: Test**: `test_published_antibody_blocks_other_bank_transfer_within_5_seconds` (InMemoryBus), `test_tombstone_removes_hit`, `test_expired_antibody_ignored`.
- [ ] **Step 2: Run** -> FAIL. **Step 3: Implement.** **Step 4: Run** -> PASS.
- [ ] **Step 5: Commit** `feat: cross-bank antibody enforcement`

### Task 12: evidence-ledger

**Files:**
- Create: `services/evidence_ledger/evidence_ledger/chain.py`, `store.py`, `package.py`, `verify.py`, `api.py`, `consumer.py`, `pyproject.toml`, `Dockerfile`
- Test: `services/evidence_ledger/tests/test_chain.py`, `test_package.py`

**Interfaces:**
- Consumes: `LedgerEntry`, `LEDGER` topic, `consume`
- Produces: `append(entry_in: LedgerEntryIn) -> LedgerEntry` where `entry_hash = sha256(prev_hash || canonical_json(entry_without_hash))`, genesis `prev_hash = "0"*64`; `verify_chain(entries: list[LedgerEntry]) -> VerifyResult(ok: bool, first_bad_seq: int | None)`; `build_package(case_id: str, seqs: list[int]) -> bytes` (zip: `entries.json`, `chain_proof.json`, `explanations.md`, `manifest.json` Ed25519-signed, standalone `verify.py`); HTTP `GET /packages/{case_id}` (roles officer, admin); consumer deduplicates by `payload_hash + service + event_type` (Review Focus 1)

- [ ] **Step 1: Test**: `test_chain_verifies`, `test_tamper_detected` (mutate payload in entry 3, `first_bad_seq == 3`), `test_signature_invalid_when_manifest_edited`, `test_duplicate_event_written_once`, `test_package_verify_script_runs_offline`.
- [ ] **Step 2: Run** -> FAIL. **Step 3: Implement.** **Step 4: Run** -> PASS.
- [ ] **Step 5: Commit** `feat: tamper-evident evidence ledger`

### Task 13: Ledger emitters in services

**Files:**
- Create: `libs/svckit/svckit/ledger.py`
- Modify: `services/call_guard/call_guard/consumer.py`, `services/txn_guard/txn_guard/consumer.py`, `services/antibody_hub/antibody_hub/api.py`

**Interfaces:**
- Produces: `async def emit_ledger(bus, service: str, actor: str, event_type: str, payload: BaseModel | dict, model_version: str | None) -> None` (computes `payload_hash`; never includes raw PII fields; raises if payload has keys named `phone`, `account_number`, `name`)

- [ ] **Step 1: Test**: `test_emit_rejects_pii_keys`, `test_decision_and_antibody_events_reach_ledger_topic`. **Step 2: Run** -> FAIL. **Step 3: Implement and wire.** **Step 4: Run** -> PASS.
- [ ] **Step 5: Commit** `feat: ledger emission across services`

### Task 14: graph-twin

**Files:**
- Create: `services/graph_twin/graph_twin/ingest.py`, `rings.py`, `takedown.py`, `api.py`, `pyproject.toml`, `Dockerfile`
- Test: `services/graph_twin/tests/test_rings.py`, `test_takedown.py`

**Interfaces:**
- Consumes: `TXN_EVENTS`, `TXN_DECISIONS`, `ANTIBODIES`; Neo4j via `neo4j` driver
- Produces: `detect_rings(g: nx.DiGraph, min_size: int = 4) -> list[Ring]` (fan-in/fan-out, pass-through time < 1 h, community detection); `simulate_takedown(g, ring: Ring, remove: list[str]) -> TakedownResult(harm_before_inr: Decimal, harm_after_inr: Decimal, reduction_pct: float)` (harm = max-flow of future victim money to cash-out nodes); `best_takedown(g, ring, k: int) -> list[str]` greedy by flow reduction; HTTP `GET /rings`, `POST /rings/{id}/simulate`

- [ ] **Step 1: Test**: `test_ring_found_in_hero_campaign`, `test_benign_traffic_yields_no_ring`, `test_removing_all_cashout_nodes_reduces_harm_to_zero`, `test_greedy_k1_beats_random_node`. **Step 2: Run** -> FAIL. **Step 3: Implement.** **Step 4: Run** -> PASS.
- [ ] **Step 5: Commit** `feat: fraud ring detection and takedown simulation`

### Task 15: geo-intel

**Files:**
- Create: `services/geo_intel/geo_intel/store.py`, `hotspots.py`, `api.py`, `pyproject.toml`, `Dockerfile`
- Test: `services/geo_intel/tests/test_hotspots.py`

**Interfaces:**
- Consumes: `COMPLAINTS` events with `lat`, `lon`, `ts`, `campaign_hint`
- Produces: `hotspots(points, resolution: int = 7, window: timedelta) -> list[Hotspot(cell, count, z_score)]` (H3 cells, Poisson z-score vs trailing baseline); HTTP `GET /hotspots?window=` returning GeoJSON; PostGIS persistence

- [ ] **Step 1: Test**: `test_cluster_flagged_over_baseline`, `test_uniform_noise_not_flagged`, `test_geojson_valid`. **Step 2: Run** -> FAIL. **Step 3: Implement.** **Step 4: Run** -> PASS.
- [ ] **Step 5: Commit** `feat: geospatial hotspot analytics`

### Task 16: citizen-shield

**Files:**
- Create: `services/citizen_shield/citizen_shield/verdict.py`, `i18n/en.json`, `hi.json`, `ta.json`, `bn.json`, `llm.py`, `api.py`, `pyproject.toml`, `Dockerfile`
- Test: `services/citizen_shield/tests/test_verdict.py`, `test_i18n.py`

**Interfaces:**
- Consumes: `score_chunk` logic via the call-guard HTTP API (no import)
- Produces: `async def assess(message: str, lang: str | None) -> Verdict(label: Literal["safe","suspicious","likely_scam"], score, reasons, advice_key, lang, uncertain: bool)`; `detect_lang(message) -> str`; empty, > 4000 chars, or unsupported-language input returns `suspicious` with `uncertain=True` and generic advice; LLM wording runs behind a guardrail that only fills pre-approved advice templates (no free-form financial advice); HTTP `POST /assess`, `POST /report` (creates a `COMPLAINTS` event)

- [ ] **Step 1: Test**: `test_digital_arrest_message_likely_scam`, `test_ordinary_bank_alert_safe`, `test_empty_message_returns_uncertain_not_error` (Review Focus 4), `test_4001_chars_handled`, `test_mixed_script_message`, `test_all_advice_keys_exist_in_each_language_file`, `test_safe_message_false_positive_rate_under_1_percent` (on 500 simulated benign messages).
- [ ] **Step 2: Run** -> FAIL. **Step 3: Implement.** **Step 4: Run** -> PASS.
- [ ] **Step 5: Commit** `feat: multilingual citizen shield`

### Task 17: web-console design system and shell

**Files:**
- Create: `web-console/package.json`, `tailwind.config.ts`, `src/styles/tokens.css`, `src/components/ui/*` (shadcn: button, card, badge, dialog, table), `src/components/VerdictBadge.tsx`, `src/components/AlertCard.tsx`, `src/App.tsx`, `src/auth/AuthProvider.tsx`
- Test: `web-console/src/components/__tests__/VerdictBadge.test.tsx`

**Interfaces:**
- Produces: three-layer CSS variable tokens (primitive -> semantic `--risk-critical`, `--risk-warn`, `--safe`, `--surface`, `--text-muted` -> component); light and dark themes; `<VerdictBadge label score />` always renders icon + text; `<AlertCard decision countdownMs onResolve />`; role-routed shell (`/command`, `/analyst`, `/citizen`)

- [ ] **Step 1: Test** (vitest + testing-library): `renders icon and text not colour only`, `meets AA contrast tokens` (token pairs checked by a contrast helper), `unauthorised role redirected`. **Step 2: Run** `npm test` -> FAIL. **Step 3: Implement.** **Step 4: Run** -> PASS.
- [ ] **Step 5: Commit** `feat: web console design system and shell`

### Task 18: Command centre and analyst console

**Files:**
- Create: `web-console/src/pages/CommandCentre.tsx`, `Analyst.tsx`, `src/components/MapLayer.tsx`, `GraphPanel.tsx`, `TakedownPanel.tsx`, `src/hooks/useLiveFeed.ts`
- Test: `web-console/src/pages/__tests__/Analyst.test.tsx`, `web-console/e2e/hero.spec.ts` (Playwright)

**Interfaces:**
- Consumes: gateway routes `/api/txn/holds`, `/api/graph/rings`, `/api/geo/hotspots`, `/api/ledger/packages/{id}`; WebSocket `/api/stream`
- Produces: Command centre map (deck.gl heat + cash-out points) with incident feed, ring graph, takedown simulate panel showing `reduction_pct`, evidence export button; Analyst queue sorted by time-to-money-movement with countdown, release / confirm-block actions, reasons in plain language; split-screen demo route `/demo`

- [ ] **Step 1: Test**: `queue sorted by soonest deadline`, `resolve action calls API with actor`, Playwright `hero.spec.ts` drives the hero scenario and expects the second-bank block to appear within 10 s. **Step 2: Run** -> FAIL. **Step 3: Implement.** **Step 4: Run** -> PASS.
- [ ] **Step 5: Commit** `feat: command centre and analyst console`

### Task 19: Citizen UI

**Files:**
- Create: `web-console/src/pages/Citizen.tsx`, `src/components/LanguagePicker.tsx`
- Test: `web-console/src/pages/__tests__/Citizen.test.tsx`

**Interfaces:**
- Consumes: gateway `/api/citizen/assess`, `/api/citizen/report`
- Produces: mobile-first single-verdict screen, language picker (en/hi/ta/bn), large Report action, explicit uncertainty copy when `uncertain=true`, calm tone

- [ ] **Step 1: Test**: `shows one verdict per screen`, `uncertain verdict shows uncertainty copy`, `language switch rerenders advice`, `layout usable at 360px width`. **Step 2: Run** -> FAIL. **Step 3: Implement.** **Step 4: Run** -> PASS.
- [ ] **Step 5: Commit** `feat: citizen shield UI`

### Task 20: Hardening and end-to-end hero test

**Files:**
- Create: `tests/e2e/test_hero_scenario.py`, `tests/e2e/test_privacy_boundary.py`, `tests/load/txn_guard_load.py` (locust), `infra/k8s/*.yaml`, `infra/prometheus.yml`, `infra/grafana/dashboards/overview.json`

**Interfaces:**
- Consumes: the whole stack via `docker compose up`

- [ ] **Step 1: Write failing tests**: `test_hero_scenario_end_to_end` (call flagged -> victim A held -> antibody published -> victim B at other bank held within 5 s -> ledger chain verifies -> package export verifies offline), `test_no_raw_pii_in_bus_or_ledger` (scan every topic and the ledger for seeded raw values), `test_service_restart_midstream_no_duplicates` (Review Focus 1), locust target p99 < 300 ms at 200 rps.
- [ ] **Step 2: Run** `make up && pytest tests/e2e -v` -> FAIL. **Step 3: Fix integration gaps; add k8s manifests, Prometheus scrape configs, Grafana dashboard (latency, hold rate, DLQ depth, antibody count).** **Step 4: Run** -> PASS.
- [ ] **Step 5: Commit** `feat: end-to-end hardening, load and privacy tests`

### Task 21: Benchmark report and delivery assets

**Files:**
- Create: `docs/architecture.md` (Mermaid diagram), `docs/demo_script.md`, `docs/deck/` (via the slides skill), `docs/benchmark_report.md` (generated)

- [ ] **Step 1: Run** `python tests/e2e/bench/run_benchmark.py --seed 42 --ablations no_antibody,no_call_signal` and confirm it writes the report with precision, recall, FPR, held-benign rate, and lead time for each ablation; **Expected:** held-benign rate < 0.1%, lead time positive for the hero campaign.
- [ ] **Step 2: Write** the architecture diagram, 3-minute demo script following the hero choreography, and the deck (problem, concept, architecture, live demo, benchmark numbers, finance-grade controls, business impact, scalability).
- [ ] **Step 3: Record** the demo using `/demo` split view.
- [ ] **Step 4: Commit** `docs: architecture, benchmark, deck and demo script`

---

## Self-Review Notes

- **Spec coverage:** services (Tasks 3-16), data flow (9, 11, 20), finance-grade (2, 10, 12, 13, 20), evaluation (8, 21), UI/UX (17-19), testing (per task + 20), phases (waves). Spec phases map: 0 -> T1-2, 1 -> T3-4, 2 -> T6-9, 3 -> T10-11, 4 -> T14-15, 5 -> T12-13, 16, 6 -> T17-20, 7 -> T21.
- **Three-week compression:** drop Tasks 15 and 19 to minimal versions, merge Tasks 3-4, skip k8s manifests in Task 20.
