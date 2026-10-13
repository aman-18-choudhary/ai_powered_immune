# PROJECT PROGRESS REPORT

## 0. Header

| Item | Value |
|---|---|
| Generated | 2026-10-13 |
| Git commit | `6ba4072` (full hash `6ba4072ee80db9d866687212936bd1cf5a071b92`), subject "docs: branch references updated after fast-forwarding main" |
| Branch checked out | `build/implementation`. `origin/main` and `origin/build/implementation` both resolve to `6ba4072`. The LOCAL `main` ref is stale at `99196b7` (docs-only spec/plan commit, 150 commits behind); anyone running `git checkout main` locally without pulling gets a repository with no code. |
| Working tree | Clean before and after the inspection (`git status --short` printed nothing at both points). The only file added by this task is this report. |
| Scope | READ-ONLY inspection of `/Users/aman/lastproject` plus execution of the repository's own test, lint and build commands (build output sent to `/tmp`). No application code, data, models, configs, Docker or doc files were changed. No commit, no push, no network or LLM calls, nothing bound to ports 8080 or 5173-5199. |
| Naming mismatch | The brief calls the project "Digital Suraksha". This repository's product name is **"ScamShield Immune"** (README title, `package.json` name `scamshield-immune-console`, deck `ScamShield-Immune.pptx`). A repo-wide case-insensitive grep for "suraksha" returned no hits. **This report documents THIS repository (ScamShield Immune), not a separate "Digital Suraksha" codebase.** Any "Digital Suraksha" feature that is not in this repository is reported as Missing. |
| Status vocabulary | **Implemented** = code exists in the repo (cited by path/symbol). **Tested** = a test exercising it passed in MY run in this session (counts in section 7). **Deployed** = running in production. **Nothing is deployed**: the only verified runtime is the single-process demo server on a developer machine; the multi-container stack was never run (no Docker daemon). "Verification could not be completed" is written wherever I could not check a thing, with the reason. |
| Data | All data in the repository is simulator-generated (100% synthetic). No real calls, customers, banks or telecom feeds. |

Environment used for my runs: macOS (Darwin), Python 3.13.5 in `/Users/aman/lastproject/.venv` (repo declares `requires-python >=3.11`, Docker images use `python:3.11-slim`), Node v24.16.0 (Dockerfile console stage uses `node:20-slim`), Docker CLI 29.4.3 with Compose v5.1.4 but **no reachable daemon** (`docker info`: "failed to connect to the docker API").

---

> **Addendum (after the first version of this report):** the `confirm_block` -> antibody-hub submission gap (section 3 step 5, section 8, finding H1) was closed in commits 9293db4..26d4a73. Counts after that change, executed in the session that made it: txn-guard 376 passed, tests/e2e/audit 32 passed, antibody-hub 160 passed (+5 skipped), demo-server 184 passed, bench 38, svckit 180, contracts 44. Counts elsewhere in this report are from the earlier full run and were not re-executed for every package after the change.

## 1. PROJECT OVERVIEW

### 1.1 Problem statement and objectives

Source: `docs/challenge-statement.md` (the brief, "AI for Digital Public Safety: Defeating Counterfeiting, Fraud & Digital Arrest Scams") and `docs/superpowers/specs/2026-10-07-scam-immune-system-design.md`.

The brief lists five example areas: (1) digital-arrest scam detection and alerting, (2) counterfeit currency identification, (3) fraud network graph intelligence, (4) geospatial crime pattern intelligence, (5) citizen fraud shield (WhatsApp / IVR / app, 12 regional languages). Evaluation focus: counterfeit detection accuracy, digital-arrest precision/recall, network lead time, citizen-facing false-positive rate, auditability. Weights: innovation 25, business impact 25, technical excellence 20, scalability 15, UX 15.

The design spec's own concept: a "federated scam immune system": call-guard flags the call, txn-guard holds the transfer, a confirmed mule becomes a shared keyed-hash "antibody", graph-twin simulates takedown, an append-only hash-chained ledger seals evidence. Success criteria in the spec (section 1): precision/recall vs simulator ground truth, held-benign rate below 0.1%, lead time before mass victimisation, every verdict explainable, every action auditable.

Deliberate scope decisions recorded in the repo: counterfeit-currency detection is **dropped** (spec section 1, `docs/challenge-statement.md`); real telecom/bank integrations are simulated.

### 1.2 Key features actually present (Implemented; Tested status in section 7)

| Capability | Where |
|---|---|
| Seeded synthetic world, scam campaigns, hero scenario, ground-truth labels | `services/sim_engine/sim_engine/{world,benign,scam,calls,labels,replay}.py` |
| Digital-arrest call/chat scoring: rules-v1 + TF-IDF/logistic clf-v1 | `services/call_guard/call_guard/{rules,model,session,train}.py` |
| Transaction risk scoring, allow / step_up / hold_verify, holds, policy overlays | `services/txn_guard/txn_guard/{features,model,policy,decision,holds}.py` |
| Federated antibody hub (keyed hashes, TTL, revoke, outbox, Bloom snapshot) | `services/antibody_hub/antibody_hub/` |
| Append-only hash-chained ledger, Ed25519 checkpoints, signed evidence package, stdlib `verify.py` | `services/evidence_ledger/evidence_ledger/{chain,store,keys,package,verify}.py` |
| Mule-ring detection and max-flow takedown simulation | `services/graph_twin/graph_twin/{graph,rings,takedown,twin}.py` |
| Complaint hotspots (Poisson z-score), trend, cash-out points, 27-city static table | `services/geo_intel/geo_intel/{compute,table,store}.py` |
| Citizen message checker (4 languages), degraded mode, anonymous report | `services/citizen_shield/citizen_shield/{api,policy,texts,store}.py` |
| JWT login, role proxy, per-role rate limits | `services/gateway/gateway/{main,auth,rbac,ratelimit}.py` |
| Single-process composition + REST/WebSocket for the console | `services/demo_server/demo_server/` |
| 7-route React console, light/dark | `web-console/src/` |
| Benchmark harness and audit end-to-end tests | `tests/e2e/bench`, `tests/e2e/audit` |
| Docs, presentation deck (pptx/pdf), screen-recorded demo (webm/mp4/gif, no audio) | `docs/` |

### 1.3 Technology stack (versions are those installed in `.venv` / `node_modules` when I ran; manifests only give lower bounds for Python)

| Layer | Technology |
|---|---|
| Languages | Python (>=3.11 declared; 3.13.5 used), TypeScript 6.0.3, SQL, Bash, Make |
| Backend | FastAPI 0.142.2, Starlette 1.7.0, uvicorn 0.54.0, pydantic 2.13.5, PyJWT 2.15.1, httpx 0.28.1, websockets 17.2 |
| ML / numerics | scikit-learn 1.9.1 (TF-IDF, LogisticRegression, HistGradientBoostingClassifier, IsotonicRegression), numpy 2.5.3, scipy 1.18.1, joblib 1.6.0, networkx 3.7 |
| Persistence | SQLAlchemy 2.1.4 (Core `Table`s), psycopg2-binary 2.9.13 (optional `[postgres]` extra), SQLite in tests/demo, Redis client 8.1.0 |
| Messaging | aiokafka 0.14.0 against Redpanda v24.1.7 (compose); in-memory bus in demo and tests |
| Crypto | `cryptography` 50.0.2 (Ed25519 signing), HMAC keyed hashing in `contracts/scam_contracts/hashing.py`, scrypt password hashes in the gateway |
| Frontend | React 18.3.1, react-router-dom 6.30.6, Vite 8.3.4, Tailwind 4.3.3, zustand 5.0.15, framer-motion 14.1.0, Radix UI, d3-geo 3.1.1 (bundled `india.topo.json`), vitest 5.0.3, Playwright 1.64.0 + axe-core |
| Infra | Docker Compose (`infra/docker-compose.yml`), `postgis/postgis:16-3.4`, `redis:7-alpine`, Redpanda, Prometheus v2.55.1, Grafana 11.1.0 |
| Lint/format | ruff 0.16.10, eslint 10, prettier 3 |
| Lock files | `web-console/package-lock.json` exists. There is **no Python lock file**; `pyproject.toml` files use `>=` lower bounds only. |

Size (tracked files: 564): about 26.7k lines of Python source, 19.9k lines of Python tests, 10.2k lines of TS/TSX source, 2.0k lines of TS tests.

### 1.4 Architecture

Services (each `services/<name>`, own `pyproject.toml`, Dockerfile, tests); shared `contracts/` (pydantic models, topic names, canonical JSON, keyed hashing) and `libs/svckit` (bus, idempotency, health router, drainer/outbox, PII guard, tokenizer).

| Service | Port in compose | Role | State |
|---|---|---|---|
| sim-engine | 8000 (internal) | seeded world, `POST /scenarios/hero` | none (publishes to bus) |
| call-guard | 8000 (internal) | consumes `call.events`, publishes `call.risk`; `POST /score` | Redis sessions (`callguard:session:`), in-memory fallback |
| txn-guard x2 (bank A `bank_sbi`, bank B `bank_hdfc`) | 8000 (internal) | consumes `txn.events`, `call.risk`, `antibody.published`; holds API | Redis (`txnguard:h:`, `txnguard:ab:`, `txnguard:hold:`, `txnguard:holds:open`), in-memory fallback |
| antibody-hub | 8000 (internal) | antibody lifecycle | Postgres db `hub` |
| evidence-ledger | 8000 (internal) | consumes `ledger.append`, chain, checkpoints, cases, packages | Postgres db `ledger` + signing key volume |
| graph-twin | 8000 (internal) | consumes txn/decision/antibody, rings, takedown | in-memory networkx + Postgres db `graph` |
| geo-intel | 8000 (internal) | consumes `complaints`, hotspots | Postgres db `geo` |
| citizen-shield | 8000 (internal) | `/assess`, `/report`, `/languages` | Postgres db `citizen` |
| gateway | 127.0.0.1:8000 | JWT login + RBAC reverse proxy | Redis (`gw:rl:*`) |
| demo-server | 127.0.0.1:8080 | single-process composition + console static files | in-memory + temp dir |

Bus: Kafka API (Redpanda). Topics (`contracts/scam_contracts/topics.py`): `txn.events`, `call.events`, `call.risk`, `txn.decisions`, `antibody.published`, `ledger.append`, `complaints`, each with a `.dlq` suffix topic. Stores: Postgres (one database per service via `infra/postgres/init.sql`: `hub`, `ledger`, `graph`, `geo`, `citizen`), Redis (db 0 gateway, 1/2 txn-guard banks, 3 call-guard).

### 1.5 Single-process demo vs multi-container design

| Aspect | `make demo` (demo-server) | `make stack-up` (compose) |
|---|---|---|
| Process model | one Python process composes real call-guard, txn-guard (2 banks), antibody-hub, evidence-ledger code, plus its own ring and geo views | one container per service |
| Bus | in-memory | Redpanda |
| Persistence | none beyond a temp work dir; per-process signing key | Postgres, Redis, key volume |
| Auth | `POST /api/auth/demo-login` hands a JWT for any requested role (optional `DEMO_LOGIN_CODE`) | gateway `POST /auth/login` against an in-memory demo user store |
| Console | talks to `/api` of demo-server | **the console is only served by demo-server; it does not talk to the gateway** (different route names, see 5.2 and 6) |
| Status | Implemented, Tested (suites in 7), run by prior build reports | Implemented as files; `docker compose config -q` passed in my run; **never built or started (no daemon): verification could not be completed** |

---

## 2. IMPLEMENTED FEATURES (brief headings mapped to reality)

| Brief item | Verdict | Evidence |
|---|---|---|
| Citizen dashboard / features (`/citizen` page, citizen_shield service) | **Present (partial vs brief)** | Console route `/citizen` (`web-console/src/routes/Citizen.tsx`) calls `POST /api/citizen/assess` (`endpoints.assess`). Service `services/citizen_shield` implements `/assess`, `/report`, `/languages`. Four languages only (`LANGS = ["en","hi","ta","bn"]` in `web-console/src/lib/i18n/citizen.ts`, `SUPPORTED` in `citizen_shield/texts.py`) versus the brief's 12. No WhatsApp/IVR/mobile-app channel: grep finds none (spec says WhatsApp adapter "mocked"; no adapter code exists). The verdict is rule/classifier based. The page has no login-free mode: a token is required (demo-login). |
| Investigator dashboard and tools: analyst console `/analyst`, command centre `/command`, evidence `/evidence` | **Present** | `routes/Analyst.tsx` (hold queue, resolve release/confirm_block, shortcuts, countdown), `routes/Command.tsx` (KPIs, India map with hotspots, ring graph, takedown simulator, antibody ticker, benchmark mini), `routes/Evidence.tsx` (ledger chain view, create case, download package, offline-style verify). Plus `/` Live player, `/insights`, `/about`. All backed by demo-server routes (section 6). |
| Admin dashboard and user/role management | **Absent** | No admin page, no user CRUD, no role-assignment API. What exists: four fixed roles `officer, analyst, citizen, admin` (`gateway/auth.py: ROLES`; `demo_server/config.py: ROLES`). Gateway: `GATEWAY_DEMO_USERS=1` creates one in-memory user per role (username equals role) from env passwords (`demo_users_from_env`); the `UserStore` protocol comment says "A Postgres store can replace it" but none exists. Demo-server: no users, role chosen at login. The `admin` role is only a permission superset in a few APIs (`antibody_hub` admin-only revoke/delete/unpark/protected, txn-guard `/admin/antibodies/bootstrap`). The Splash screen role picker is the only "user management". |
| Complaint registration, evidence handling, analysis | **Partial / minimal** | Citizen `POST /report` (`citizen_shield/api.py:307`) accepts only `category` (enum `scam_call`/`held_transfer`/`cash_out`), optional 64-hex `message_hash`, `lang`, `city_code`, `state_code`; stores keyed hash id, category, coarse location in table `cs_reports`; emits a `Complaint` event on topic `complaints` only when a location is given. **No complaint text, no evidence upload, no attachment storage, no complainant identity, no case workflow, no complaint database beyond `cs_reports` and geo `gi_complaints`.** Evidence handling is the ledger: officers create immutable cases from opaque `case_refs`/`seqs` (`POST /cases`) and export signed packages. Analysis = geo hotspots and ring graph. The console does **not** call `/report` (see 6.3). |
| AI/ML fraud detection | **Present (simulator-trained, see 4)** | call-guard clf-v1 (TF-IDF + LR + isotonic) blended with rules; txn-guard gbm-v1 (HistGradientBoosting + isotonic) plus policy overlays; graph-twin heuristics and max-flow. Artifacts: `call_guard/artifacts/clf-v1.joblib` 112,387 B, `txn_guard/artifacts/gbm-v1.joblib` 170,772 B, SHA-256 pinned in `artifact_pin.py` (my `shasum` matched the pin prefixes). |
| Risk scoring | **Present** | call score in [0,1], alert threshold 0.7 (`CALL_RISK_THRESHOLD`); txn thresholds step_up 0.5 and hold 0.8 (`thresholds.py`, per benchmark report); citizen labels safe/suspicious (>=0.35)/likely_scam (>=0.7) (`citizen_shield/policy.py`). |
| LLM explanations | **Absent** | Repo-wide grep (py/ts/tsx/toml/yml/json/sql, excluding node_modules) for `anthropic`, `openai`, `langchain`, `llm`, `embedding`, `faiss`, `chroma` found no use of any LLM or embedding library (only unrelated regex `fullmatch` matches). Explanations are rule/classifier reason codes: call-guard `Reason(code, weight)` objects, txn-guard occlusion-attribution reasons (`txn_guard/reasons.py`), static localised text dictionaries (`citizen_shield/texts.py` `REASON_TEXT`, `web-console/src/lib/reasons.ts`). The spec's "Transformer classifier" and "LLM with guardrails" are not implemented. |
| "Fraud DNA (Qdrant)" | **Absent** | grep for `qdrant`, `fraud dna`, `vector store`, `faiss`, `chroma` in code, compose and docs: no hits. No vector database, no embeddings. The closest concept is the antibody (a keyed hash of a mule account, kind `mule_account|script|device` in `contracts/models.py`) which is an exact-match key, not a similarity fingerprint. |
| "Network Intelligence (Neo4j)" | **Absent as Neo4j; Present as NetworkX** | `graph_twin` keeps `networkx.DiGraph` in memory and persists via SQLAlchemy tables `gt_*` (`graph_twin/store.py` docstring: "There is no Neo4j here"). Compose header: "Neo4j from the earlier skeleton is removed". Neo4j is named only in the spec and the "deviation" notes (`services/graph_twin/README.md`, `docs/architecture.md` section 9). |
| "Geospatial Intelligence (PostGIS)" | **Absent as PostGIS; Present as city-level SQL analytics** | `geo_intel` uses SQLAlchemy table `gi_complaints` and a static 27-row city table (`geo_intel/table.py`, verified `len(CITIES) == 27`). The compose Postgres image is `postgis/postgis:16-3.4` but a grep for `ST_`, `geometry`, `geography`, `postgis` in service code finds none: **no PostGIS feature is used; the image is just Postgres**. Map is client-side d3-geo over a bundled TopoJSON. Hotspots are per-city Poisson z-scores (`geo-poisson-v1`), no polygons, no patrol routing. Counterfeit seizure points: not built. |
| Alerts | **Partial** | `call.risk` events on the bus; in the console, in-page incident feed, toasts and the live player. No SMS/email/push, no telecom or MHA alert generation (the brief's "automated MHA alert"). |
| Reports | **Partial** | Signed evidence package ZIP (`entries.json`, `chain_proof.json`, `explanations.md`, Section 63 certificate template, `verify.py`, `manifest.json`), benchmark report, Insights page. No generated investigator/management report export beyond that. |
| Authentication | **Partial (demo grade)** | Gateway: scrypt-hashed in-memory users, HS256 JWT, 30 min TTL, login failure rate limit via Redis (`gateway/main.py`, `auth.py`). Demo-server: no passwords, `demo-login` issues a token for any role, 8 h TTL (`demo_server/auth.py` docstring: "a simulation convenience, not an authentication scheme"). No MFA, no refresh, no revocation, no SSO. |
| Authorization | **Present (coarse RBAC)** | Gateway `ROUTE_ROLES` (prefix to roles) plus per-service role sets (e.g. txn-guard `STAFF_RESOLVE = {analyst, admin}`, ledger `OFFICERS = {officer, admin}`, hub `ANALYSTS`, `BANK_READERS`). Demo-server `require(...)` guards per route. Tested by gateway (60) and service suites. |

---

## 3. END-TO-END WORKFLOW

### 3.1 Citizen path

**Demo-server path (the verified one)**

1. Console `Citizen.tsx` -> `endpoints.assess(message, lang)` -> `POST /api/citizen/assess` (`demo_server/api.py:616`, any authenticated role). Message only in the POST body.
2. `demo_server/citizen.py: assess()` detects language (script ranges, `detect_lang`), runs `scorer.score_text(text)` (call-guard `Scorer` = rules + clf, in-process; `engine.call_scorer`), maps score to label with thresholds 0.35/0.7, localises reason codes through `REASON_TEXT`, advice from `ADVICE`, sets `uncertain` near thresholds / short / truncated / fallback.
3. Response: `{label, score, reasons[], advice, lang, uncertain, model_version, thresholds}`. Nothing stored; the log line has label and length only.
4. "Report this" button: **local only** (`Citizen.tsx: onReport={() => setReported(true)}`; `VerdictCard.tsx` shows `reportDone` text). No request is made. The web-console README says so explicitly ("There is no report route").

**Service path (compose design, never run)**

1. Gateway `/api/citizen/{path}` (roles `citizen`, `admin`) -> citizen-shield `POST /assess`.
2. `citizen_shield/guard_client.py` calls call-guard `POST /score` over HTTP with a circuit breaker; on outage the verdict is degraded "suspicious", never "safe" (compose comment; `run_assess` / `verdict.degraded`).
3. `POST /report` with `{category, message_hash?, city_code?, state_code?}`: id = `rpt-` + keyed hash of `message_hash` using `CITIZEN_REPORT_KEY` (503 "reporting not configured" if the key is absent); row in `cs_reports`; if a location was given and the bus exists, publishes `Complaint(complaint_id, ts, kind, city_code, state_code)` to `complaints`; marks `emitted`.
4. geo-intel consumes `complaints` into `gi_complaints` (unique `complaint_id`, retention `GEO_RETENTION_DAYS` default 30) and serves `/hotspots`, `/points`, `/cities`.

### 3.2 Transaction path

1. **Transaction**: `sim_engine` publishes `Transaction` to `txn.events` (keyed by `payer_token`); in the demo `demo_server/harness.py`/`engine.py` feeds an in-memory bus.
2. **Scoring**: `txn_guard/service.py` builds context (history in memory or Redis `txnguard:h:`), `features.extract_features` (25 feature names, `FEATURE_NAMES`; the booster uses `MODEL_FEATURES`, which excludes `POLICY_ONLY_FEATURES`), `model.Scorer` (gbm-v1 + isotonic, fallback `rules-fallback-v1`), `policy.py` overlays, `decision.decide` at 0.5/0.8.
3. **Hold**: `step_up` / `hold_verify` create a `Hold` (state in memory or Redis `txnguard:hold:{txn_id}`, deadline index `txnguard:holds:open`). Holds past `HOLD_DEADLINE_S` (default 120) are flagged overdue, never auto-released.
4. **Analyst resolve**: `POST /holds/{txn_id}/resolve` `{action: release|confirm_block, decision_seq?}` (analyst, admin; compare-and-set with `expect_seq`). Demo: `POST /api/holds/{txn_ref}/resolve`.
5. **Antibody**: in the demo, `engine._confirm` calls `hub.submit("mule_account", shared_mule_payee_hash, "bank_a", ...)` for the hero's shared hold, then waits for bank B's cache to apply it (`antibody.applied` with measured `latency_ms`). In the multi-container design the path is now implemented (added after this report's first version, commits 21bbb31/331b589): on `confirm_block` txn-guard records a durable `hub_pending` submission in the same compare-and-set as the resolution and a background submitter (svckit `Drainer`, breaker, sweeper) `POST`s `/antibodies` to the hub with the analyst principal, the bank id and `HUB_GATEWAY_SECRET`; a hub outage delays antibody creation (other banks are unprotected until delivery) but never blocks the resolve. Verified in-process only (real hub app over ASGI transport, SQLite, fakeredis; `tests/e2e/audit/tests/test_confirm_to_hub.py`); not run against containers, Postgres or Kafka. The gateway still has no hub route by design.
6. **Other bank**: hub publishes `antibody.published`; each txn-guard bank has its own consumer group (`txn-guard-antibody-<bank>`), applies it to its cache (Redis `txnguard:ab:` or in-memory). Next transfer to that payee gets `ANTIBODY_MATCH` (hold_verify, or step-up for an established payee).
7. **Ledger**: services emit `LedgerEntryIn` audit events to `ledger.append` via transactional outboxes (hub `audit_outbox`, graph `gt_audit_outbox`, call-guard/txn-guard drainers). The ledger consumer appends hash-chained rows (`ledger_entries`), writes a signed checkpoint every `LEDGER_CHECKPOINT_EVERY` (100) entries or `LEDGER_CHECKPOINT_INTERVAL_S` (60 s), quarantines PII-looking or hash-mismatch events, and dead-letters malformed ones (`dlq_events`).
8. **Package**: officer `POST /cases` (immutable) -> `GET /packages/{case_id}` builds a deterministic signed ZIP; each export appends a `package.exported` entry.
9. **Verify**: package's `verify.py` (stdlib only) or demo `POST /api/verify {case_id}`, which runs the package's own verifier in an isolated interpreter (`-I -S`, timeout, output cap) pinned to the ledger public key. My run of `scripts/verify_package.py 1` (hero package through the demo-server code path) printed `status: PASS_PINNED`, 12 entries, 1 checkpoint verified, `RESULT: PASS (pinned)`.

### 3.3 How investigators access intelligence

| Need | Console page -> API (demo-server) | Gateway route (compose) | Role gating |
|---|---|---|---|
| Hold queue and resolve | `/analyst`: `GET /api/holds?state=`, `POST /api/holds/{ref}/resolve` | `/api/txn/holds`, `/api/txn/holds/{id}/resolve` (bank A upstream only) | read: officer/analyst/admin; resolve: analyst/admin |
| Antibodies | `/command`: `GET /api/antibodies` | none (hub not routed) | officer/analyst/admin |
| Ring graph, takedown | `/command`: `GET /api/ring/current/graph`, `POST .../simulate`, `GET .../best-takedown` | `/api/graph/rings...` | officer/analyst/admin; graph-twin simulate: officer/admin |
| Geo hotspots | `/command`: `GET /api/geo/hotspots` | `/api/geo/hotspots`, `/points`, `/cities` | officer/analyst/admin |
| Ledger, cases, packages, verify | `/evidence`: `GET /api/ledger/head|entries|checkpoints`, `POST /api/cases`, `GET /api/packages/{id}`, `POST /api/verify` | `/api/ledger/*` | read: officer/analyst/admin (demo); gateway ledger prefix is officer/admin only |
| Benchmark | `/insights`, `/command`: `GET /api/benchmark/summary` | none | any authenticated |

### 3.4 What is stored where

| Data | Store | Persistent? |
|---|---|---|
| Ledger entries, refs, checkpoints, cases, DLQ | Postgres `ledger` tables with append-only triggers (compose); SQLite temp in demo | compose: yes (never run); demo: no |
| Antibodies, protected hashes, outbox, corroborations | Postgres `hub` | compose only |
| Graph nodes/edges/txns/decisions/rings | in-memory networkx + Postgres `graph` | compose only |
| Complaints (geo) | Postgres `geo` `gi_complaints` | compose only |
| Citizen reports | Postgres `citizen` `cs_reports` | compose only |
| Holds, history, antibody cache | Redis keys (compose) or in-memory (demo, default) | compose only |
| Call sessions, idempotency | Redis `callguard:session:`, `idem:` or in-memory | compose only |
| Rate limits | gateway: Redis `gw:rl:*`; citizen-shield and demo-server: in-process memory | gateway yes |
| Demo-server runs, WS history, signing key | process memory (`runs_kept` 20, `history_size` 5000), per-process Ed25519 key | no |
| Console session | `sessionStorage` (token), selected language in `sessionStorage` | tab lifetime |
| Analyst reason notes | React component state only; **never sent** | no |

---

## 4. DATASETS AND MACHINE LEARNING

### 4.1 Data files

`find` over the repository (excluding `.venv`, `node_modules`, `.git`) for `*.csv *.parquet *.json *.jsonl *.npz *.joblib *.sqlite *.db`:

| File | Bytes | Nature |
|---|---|---|
| `services/call_guard/call_guard/artifacts/clf-v1.joblib` | 112,387 | trained model artifact |
| `services/txn_guard/txn_guard/artifacts/gbm-v1.joblib` | 170,772 | trained model artifact |
| `services/demo_server/demo_server/data/benchmark_summary.json` | 19,449 | benchmark output bundled for the console |
| `web-console/src/lib/geo/india.topo.json` | 28,746 | map topology |
| `web-console/src/mocks/*.json` (11 files, e.g. `hero-run.json` 69,536) | 296 to 69,536 | test fixtures |
| `docs/demo/hero-run.marks.json` | 299 | video marks |

**There are no dataset files** (no CSV/Parquet/JSONL). No real dataset exists anywhere in the repository.

### 4.2 Data source: the simulator

All training and evaluation data is produced at run time by `services/sim_engine`. From `benchmark_summary.json` (`config`, seed 42, `meta.command` `python -m scam_bench.run_benchmark --seed 42`): 1,500 citizens, 14 days (5 warm-up), 5 scam campaigns x 25 victims, benign call rate 0.05. Scored transactions: 22,416 = 21,736 benign + 680 scam (prevalence 3.034%). Calls: 163 scam calls (163 alerted), 1,076 benign calls (0 alerted). Rails: UPI, IMPS, NEFT; scam flows use UPI and IMPS only. Roles of scam transactions: `victim_transfer` 366, `mule_forward` 314. Ground-truth classes: transaction-level scam vs benign, with campaign id and role (`sim_engine/labels.py`). Compose default `SIM_CITIZENS` is 2,000.

Synthetic vs real: **100% synthetic**. Calls are templated transcripts (`sim_engine/calls.py`), plus hand-authored scam/benign lines (`call_guard/authored.py`) and hand-written hard negatives (`hardneg.py`).

### 4.3 Models

| Component | Version | Architecture | Trained? |
|---|---|---|---|
| call-guard rules | `rules-v1` | hand-weighted regex cue families (authority, legal, isolation, move-funds/safe-account, verification asks, urgency; en/hi and transliterations) combined by noisy-OR (`rules.py`) | untrained |
| call-guard classifier | `clf-v1` | `TfidfVectorizer(ngram_range=(1,2), max_features=8000)` -> `LogisticRegression(C=1.0)` -> `IsotonicRegression`; final score `max(rules, calibrated_clf)` | yes (`python -m call_guard.train`, seeds 1000/2000/3000, authored weight 3.0) |
| txn-guard | `gbm-v1` | `HistGradientBoostingClassifier(max_iter=250, learning_rate=0.06, max_leaf_nodes=15, min_samples_leaf=30)` with monotone constraints, + isotonic calibrator, + policy overlays (`policy.py`), fallback `rules-fallback-v1` | yes (`python -m txn_guard.train`, train seeds 101-106, calibration 201-203, held-out eval 9001-9004) |
| txn-guard reasons | n/a | occlusion attribution (feature reset to baseline) | derived |
| graph-twin | `graph-v1` | incremental graph, component-based ring detection with thresholds, max-flow harm model, greedy and exhaustive takedown | untrained heuristics |
| geo-intel | `geo-poisson-v1` | per-city Poisson z-score against baseline windows, `min_count` guard | untrained statistic |
| citizen-shield | n/a | policy wrapper around call-guard score (thresholds 0.35/0.7, borders, language text tables) | no model of its own |

Txn-guard features (`features.FEATURE_NAMES`, 25): `amount_log, amount_inr, amount_zscore, amount_vs_typical_log, history_len, payee_age_days, payee_age_young, new_payee, velocity_count_1h, velocity_amount_1h, velocity_count_24h, velocity_amount_24h, rail, hour_ist, night, active_call_risk, device_novel, payee_in_antibody, future_dated, payee_recently_new, payee_repeat_large_1h, payee_established, payee_max_prior_log, antibody_id_prefix, antibody_expires_ts`; `MODEL_FEATURES` removes `POLICY_ONLY_FEATURES` (rail, absolute amount, two payee-relationship features) from the booster.

Artifact integrity: `ARTIFACT_SHA256` pins in `call_guard/artifact_pin.py` and `txn_guard/artifact_pin.py`; my `shasum -a 256` of both joblib files matched the pins (first 16 hex compared: `f28772674f11b814...`, `f1b91ca8e52628f4...`). Loading also checks scikit-learn version, feature names and a smoke test; otherwise it runs rules-only fallback.

### 4.4 Evaluation metrics (taken from the repository's own `docs/benchmark_report.md` and `benchmark_summary.json`; I did NOT re-run the benchmark)

| Metric (seed 42, hold_verify operating point) | Value | 95% Wilson CI |
|---|---|---|
| Precision | 100.0% (562 TP, 0 FP) | [99.3, 100] |
| Recall, all scam txns | 82.65% (118 FN) | [79.6, 85.3] |
| F1 | 90.5% | none |
| Held-benign rate (FPR) | 0.000% of 21,736 | [0.000, 0.018] |
| Step-up-or-hold flagged FPR | 0.097% | [0.063, 0.148] |
| Flagged recall | 87.2% | [84.5, 89.5] |
| Victim-transfer recall | 100% (366/366) | [99.0, 100] |
| Mule-forward recall | 62.4% (196/314) | [56.9, 67.6] |
| Recall UPI / IMPS / NEFT | 79.5% / 92.3% / n/a (no NEFT scams) | per report |
| Step-up-or-hold FPR UPI / IMPS / NEFT | 0.016% / 0.37% / 0.83% | per report |
| Lead time (first hold before 10th victim) | 5 of 5 campaigns, +3h58m to +8h34m | few campaigns, wide uncertainty |
| Scoring latency | p50 5.3 ms, p99 15.3 ms (n=2,242, in-process) | no network |
| Ablations (recall hold) | no_call_signal 81.6%, degraded_call_signal 82.2%, no_antibody 79.7% | per report |
| Antibody effect | victim-transfer recall 94.5% without, 100% with (seed 42); hero 40/40 seeds held vs 1/40 step-up without | simulator-structure dependent |

Noted drift: `benchmark_report.md` "Git commit" says `87b9a12` while `benchmark_summary.json` `meta.git_commit` says `2a8da35` (both are real commits in the history). README says the JSON equals the report run "except meta", consistent with them being separate runs of the same seed; I could not verify numerically because I did not re-run it.

### 4.5 Limitations of those metrics (repo's own limitations list, confirmed against code)

- Synthetic and generator-shared: train and evaluation worlds come from the same code, only seeds differ; template-bound call text (the classifier sees the simulator's templates).
- Prevalence 3.03% (calibration at about 1%) versus a real base rate far lower; the report itself shows precision falling to 47% (step-up or hold) at 0.1% prevalence.
- NEFT has no scam transactions; NEFT thresholds are hand-scaled (x5) and only unit-tested on hand-written scenarios.
- Call signal idealised in the headline run (alerts on every scam call, none on benign); see `degraded_call_signal`.
- The antibody gain and the 100% "held" shares depend on the simulator structure (3 to 6 mules reused over about 25 victims; oracle confirmation after a fixed 60 s); no analyst error or poisoning is modelled.
- Hero scenario is constructed (one hand-written evasive script by design).
- No counterfeit-currency, voice, video or spoofing-signature model exists, although the brief names them.
- Models are artifacts of Python/scikit-learn 1.9.1 (pickle-based joblib); load is gated by SHA pin + version check.

---

## 5. BACKEND AND DATABASE

### 5.1 Gateway (`services/gateway`)

Only routes exposed to clients; all `/api/<prefix>/{path}` for methods GET, POST, PUT, PATCH, DELETE.

| Method | Path | Roles | Purpose |
|---|---|---|---|
| POST | `/auth/login` | none | username/password -> HS256 JWT (30 min); 429 after failed-attempt limit (10 per window, per IP and per user, Redis) |
| GET | `/healthz`, `/readyz` | none | probes (`libs/svckit/health.py`; readyz always true) |
| any | `/api/txn`, `/api/txn/{path}` | analyst, officer, admin | proxy to `GATEWAY_UPSTREAM_TXN` |
| any | `/api/graph[/...]` | officer, analyst, admin | proxy to graph-twin |
| any | `/api/geo[/...]` | officer, analyst, admin | proxy to geo-intel |
| any | `/api/ledger[/...]` | officer, admin | proxy to evidence-ledger |
| any | `/api/citizen[/...]` | citizen, admin | proxy to citizen-shield |

Behaviour: strips client `X-Principal-*`, sets `X-Principal-Role/-Sub` from the verified token, propagates `X-Request-Id`, 1 MiB request cap and 5 MiB response cap, path traversal rejection, per-role limits (citizen 30, analyst 300, officer 300, admin 600 per minute, Redis fixed window, **fails open** if Redis errors). No upstream for `hub`, `call`, `sim`; `upstream_not_configured` -> 502 when an env var is missing.

### 5.2 Demo-server (`services/demo_server`, what the console actually uses)

| Method | Path | Roles | Purpose |
|---|---|---|---|
| GET | `/api/health` | none | liveness `{status, simulated}` |
| POST | `/api/auth/demo-login` | none (optional `DEMO_LOGIN_CODE`) | token for requested role |
| POST | `/api/demo/start` | officer, admin | start run (seed, speed, mode, pacing) |
| POST | `/api/demo/control` | officer, admin | pause/resume/stop/seek |
| GET | `/api/demo/state` | any authenticated | run state |
| GET | `/api/demo/runs` | any | recent runs |
| WS | `/api/stream?token=` | any valid token (origin checked) | live event stream |
| GET | `/api/holds?state=open|all` | officer, admin, analyst | hold views |
| POST | `/api/holds/{txn_ref}/resolve` | analyst, admin | resolve a hold |
| GET | `/api/antibodies?state=` | officer, admin, analyst | antibody views |
| GET | `/api/ledger/head` | officer, admin, analyst | head + public key + key id |
| GET | `/api/ledger/entries` | officer, admin, analyst | entries page |
| GET | `/api/ledger/checkpoints/latest`, `/api/ledger/checkpoints` | officer, admin, analyst | checkpoints |
| POST | `/api/cases` | officer, admin | create case |
| GET | `/api/packages/{case_id}` | officer, admin | signed package ZIP |
| POST | `/api/verify` | officer, admin | pinned verification |
| GET | `/api/ring/{case_id}/graph` | officer, admin, analyst | ring snapshot |
| POST | `/api/ring/{case_id}/simulate` | officer, admin, analyst | takedown (`k` xor `remove`) |
| GET | `/api/ring/{case_id}/best-takedown?k=` | officer, admin, analyst | greedy vs exhaustive |
| GET | `/api/geo/hotspots` | officer, admin, analyst | hotspots |
| GET | `/api/benchmark/summary` | any | bundled benchmark JSON (ETag) |
| POST | `/api/citizen/assess` | any | citizen verdict |
| GET | `/{path}` | none | static console + SPA fallback |

There is **no** `/api/citizen/report`, no tamper route, no analyst-note route, no user/admin routes.

### 5.3 Other services (reachable only on the internal network in compose; role headers trusted when `TRUST_GATEWAY_HEADERS=1` or a per-service gateway secret matches)

**txn-guard** (`txn_guard/api.py`)

| Method | Path | Roles | Purpose |
|---|---|---|---|
| GET | `/metrics` | `METRICS_TOKEN` or staff via gateway trust | gauges/counters |
| POST | `/admin/antibodies/bootstrap` | admin | reload exact set from hub |
| GET | `/holds` | analyst, officer, admin | open holds |
| GET | `/holds/{txn_id}` | staff, citizen (own only; 404 otherwise) | one hold |
| POST | `/holds/{txn_id}/resolve` | analyst, admin | release / confirm_block |
| POST | `/holds/{txn_id}/verify` | citizen (own step-up only) | citizen confirms own transfer |
| GET | `/healthz`, `/readyz` | none | probes |

**antibody-hub** (`antibody_hub/api.py`)

| Method | Path | Roles | Purpose |
|---|---|---|---|
| POST | `/antibodies` | analyst, admin | submit antibody (kind, key_hash, source_bank, evidence_ref) |
| GET/HEAD | `/antibodies/bloom?bank_id=` | bank (own), admin | Bloom snapshot, ETag |
| GET | `/antibodies/exact?bank_id=&since=` | bank (own), admin | exact set page |
| GET | `/antibodies` | officer, analyst, admin | list |
| GET | `/antibodies/{ab_id}` | officer, analyst, admin | one |
| POST | `/antibodies/{ab_id}/revoke` | analyst, admin | revoke |
| DELETE | `/antibodies/{ab_id}` | admin | delete |
| POST | `/admin/outbox/unpark` | admin | retry parked outbox |
| POST / DELETE | `/protected`, `/protected/{key_hash}` | admin | protected-merchant allowlist |
| GET | `/metrics`, `/healthz`, `/readyz` | token / staff / none | ops |

**evidence-ledger** (`evidence_ledger/api.py`; no write endpoint for entries by design)

| Method | Path | Roles | Purpose |
|---|---|---|---|
| GET | `/head`, `/entries`, `/entries/{seq}`, `/checkpoints`, `/checkpoints/latest`, `/keys` | officer, admin, analyst | chain reads |
| GET | `/verify?from_seq=&to_seq=` | officer, admin | server-side bounded verify (span cap 5000) |
| POST | `/cases` | officer, admin | create immutable case (201, 200 if identical, 409 if different) |
| GET | `/cases/{case_id}`, `/cases/{case_id}/exports` | officer, admin | case, export history |
| GET | `/packages/{case_id}` | officer, admin | signed ZIP |
| GET | `/metrics`, `/healthz`, `/readyz` | token / staff / none | ops |

**graph-twin**: `GET /stats`, `GET /rings`, `GET /rings/{ring_id}`, `GET /rings/{ring_id}/graph`, `GET /rings/{ring_id}/best-takedown` (officer, analyst, admin); `POST /rings/{ring_id}/simulate` (officer, admin); `/metrics`.

**geo-intel**: `GET /hotspots?window=&at=`, `GET /points?since=&before=&limit=`, `GET /cities` (officer, analyst, admin); `/metrics` (admin or token).

**citizen-shield**: `POST /assess`, `POST /report`, `GET /languages` (citizen, admin; per-principal and per-peer in-process limiter; message never logged); `/metrics` (admin or `CITIZEN_METRICS_TOKEN`).

**call-guard**: `POST /score` `{message<=4000, lang?}` -> `{score, reasons, model_version}`; `GET /metrics`. **No authentication of any kind** (relies on network isolation).

**sim-engine**: `POST /scenarios/hero` (202; 409 if a run is active). **No authentication.**

### 5.4 Database tables (SQLAlchemy Core `Table` definitions)

| Service | Tables | Notes |
|---|---|---|
| evidence-ledger | `ledger_entries` (PK `seq`; unique `entry_hash`, `idem_key`), `entry_refs` (PK `seq, ref`; index on `ref, seq`), `checkpoints` (PK `checkpoint_id`; unique `seq`), `cases` (PK `case_id`), `dlq_events` | Triggers on all five: Postgres `ledger_append_only()` raising on UPDATE/DELETE/TRUNCATE; SQLite `BEFORE UPDATE/DELETE` and replace-guard triggers (`store.py:_trigger_ddl`). A DB superuser can still drop triggers (README states "detectable, not prevented"). |
| antibody-hub | `antibodies` (unique `(kind,key_hash,generation)`, partial unique active index), `protected_hashes`, `audit_outbox`, `corroborations` (PK `antibody_id, bank_id, actor`) | no foreign keys |
| graph-twin | `gt_txns`, `gt_decisions`, `gt_nodes`, `gt_edges` (PK `src,dst`), `gt_antibodies`, `gt_rings`, `gt_audit_outbox` | no foreign keys |
| geo-intel | `gi_complaints` (autoincrement `seq`, unique `complaint_id`, indexes on ts and city/ts) | |
| citizen-shield | `cs_reports` (PK `report_id`, index on `created_at`) | |
| gateway, txn-guard, call-guard | none (Redis only) | |

Relationships: **no SQL foreign keys anywhere** (grep `ForeignKey`: none). Links are by value (case refs, hashes, `evidence_ref`). Cross-service rule: no service reads another's database (spec section 2).

### 5.5 Migrations

**No Alembic or any versioned migration tool** (grep `alembic`: no hits). Schema is created idempotently with `metadata.create_all` at service start (`*_CREATE_SCHEMA=1` default `"1"`, serialised by a Postgres advisory lock) and via one-shot `python -m <pkg>.migrate` modules (`antibody_hub`, `evidence_ledger`, `graph_twin`, `geo_intel`, `citizen_shield`). There is no mechanism to alter an existing table, so the first schema change will need a real migration tool. Service `*_DATABASE_URL` default is `sqlite://` (in-memory): an unset variable silently gives an ephemeral store.

### 5.6 Redis and Kafka usage

| Redis key pattern | Owner | Purpose |
|---|---|---|
| `gw:rl:{role}:{sub}:{window}`, `gw:rl:login:ip:*`, `gw:rl:login:user:*` | gateway | rate limits |
| `callguard:session:*`, `ledger_pending:*` | call-guard | session scores, pending audit |
| `txnguard:h:*` | txn-guard | payer history, velocity |
| `txnguard:ab:e:{kind}:{hash}`, `txnguard:ab:t:*` | txn-guard | antibody cache |
| `txnguard:hold:{txn_id}`, `txnguard:holds:open`, `txnguard:holds:audit`, `txnguard:overdue_total` | txn-guard | holds |
| `idem:*` (TTL 7 d) | svckit | idempotency claims |

Kafka topics and producers/consumers: `txn.events` (sim -> txn-guard x2, graph-twin), `call.events` (sim -> call-guard), `call.risk` (call-guard -> txn-guard), `txn.decisions` (txn-guard -> graph-twin), `antibody.published` (hub -> txn-guard, graph-twin), `ledger.append` (all -> ledger), `complaints` (citizen-shield -> geo-intel); `.dlq` for each. Topics are created by `topics-init` with 3 partitions, replication 1.

### 5.7 External services

Required in the stack: Redpanda (Kafka), Postgres, Redis. Optional: Prometheus, Grafana. **Not present: Neo4j, Qdrant, PostGIS features, any LLM API, any telecom/bank/CERT-In/MHA integration, any SMS/WhatsApp provider, any object store for evidence.**

### 5.8 Missing APIs, unsupported fields and incomplete integrations

| Gap | Evidence |
|---|---|
| Console talks to demo-server only; gateway routes (`/api/txn`, `/api/ledger`...) and login (`/auth/login`) are never called by the console | `web-console/src/lib/api/endpoints.ts` base `/api`, `/auth/demo-login`, `/demo/*`, `/ring/*` |
| No citizen report route in demo-server; console "Report this" is local only | `demo_server/api.py` route list; `VerdictCard.tsx` |
| Analyst reason note is mandatory in UI but not in `ResolveBody` (`action`, `decision_seq` only); not persisted or audited | `Analyst.tsx` `setNotes`; `web-console/README.md` |
| No tamper demo, no analyst-note, no complaint evidence upload endpoint | route lists above |
| ~~Gateway has no hub route; a `confirm_block` never reaches the hub in the compose flow~~ **Resolved in code:** txn-guard submits confirmed mules to the hub itself (`TXN_HUB_SUBMIT`, `HUB_URL`, `HUB_GATEWAY_SECRET`); gateway deliberately has no hub route. Container run still unverified | `txn_guard/hub_submit*`, `tests/e2e/audit/tests/test_confirm_to_hub.py` |
| Citizen cannot use `POST /holds/{id}/verify` through the gateway: it requires role `citizen` in txn-guard, but gateway prefix `txn` allows only analyst/officer/admin | `gateway/main.py ROUTE_ROLES["txn"]` vs `txn_guard/api.py:267` |
| Gateway `txn` upstream is bank A only (`GATEWAY_UPSTREAM_TXN: http://txn-guard-bank-a:8000`); bank B holds are not reachable through the gateway | `infra/docker-compose.yml` |
| Gateway does not inject per-service secrets; services trust `X-Principal-*` via `TRUST_GATEWAY_HEADERS=1` | compose env, `architecture.md` section 2 |
| `CITIZEN_REPORT_KEY` required by citizen-shield (>= 16 bytes) else the service refuses to start; `/report` answers 503 when the app is built without a key | `citizen_shield/api.py:create_service_app` |
| Gateway ledger prefix is narrower (officer, admin) than the ledger service READERS (adds analyst), so the analyst console's ledger reads would be 403 through the gateway | `ROUTE_ROLES["ledger"]` vs `READERS` |
| No PII tokenisation at the gateway edge (spec lists it) | README "The gateway does not tokenise" |
| No Postgres-backed user store, no password change, no user listing | `gateway/auth.py` |

---

## 6. FRONTEND STATUS

### 6.1 Routes (`web-console/src/app/App.tsx`, `nav.ts`)

Without a token the app shows only `Splash` (role picker -> `POST /api/auth/demo-login`). With a token, inside `Shell`:

| Route | Page | Function | Backend calls |
|---|---|---|---|
| `/` | `Landing.tsx` (Live Interception player) | plays the hero run: phone, bank A/B panes, map, ledger chain, timeline, finale card, quick "confirm block" | `POST /demo/start`, `POST /demo/control`, `GET /demo/state`, `POST /holds/{ref}/resolve`, WebSocket `/api/stream` |
| `/command` | `Command.tsx` | KPI strip, incident feed, India hotspot map, ring graph with takedown simulator, antibody ticker, benchmark mini | `GET /holds`, `/antibodies`, `/geo/hotspots`, `/ring/current/graph`, `POST /ring/current/simulate`, `GET /ring/current/best-takedown`, `GET /benchmark/summary` |
| `/analyst` | `Analyst.tsx` | hold queue, detail, countdown, resolve dialog with mandatory reason note, keyboard shortcuts, read-only banner for non-analyst roles | `GET /holds?state=all`, `POST /holds/{ref}/resolve` |
| `/evidence` | `Evidence.tsx` | ledger chain view, entry inspector, create case, download package, run verification, "limits" panel | `GET /ledger/head`, `/ledger/entries` (raw text), `/ledger/checkpoints`, `POST /cases`, `GET /packages/{id}`, `POST /verify` |
| `/citizen` | `Citizen.tsx` | message checker, 3 samples, language switch (en, hi, ta, bn) | `POST /citizen/assess` |
| `/insights` | `Insights.tsx` | benchmark charts, prevalence-adjusted precision computed in browser | `GET /benchmark/summary` |
| `/about` | `About.tsx` | architecture diagram and limits | none (static) |
| `*` | `NotFound.tsx` | 404 | none |

Also: `CommandPalette`, `ConnectionStatus`, `SimulationPill` (labels everything as simulated), theme (light/dark), motion preferences.

### 6.2 Hardcoded values, mocks and static dictionaries

- **Mocks**: `web-console/src/mocks/*.json` + `fixture.ts` are imported **only from test files** (`grep -rln "mocks/"` over `src`, `e2e`, `scripts` returned 16 files, all `*.test.ts(x)`). They are not in the production bundle path.
- Static dictionaries: `src/lib/i18n/citizen.ts` (UI strings, 4 languages), `src/lib/reasons.ts` (reason code to text; `reasons.test.ts` cross-checks codes against `call_guard/model.py`), `src/components/player/notes.ts` (narration notes), `src/lib/about/architecture.ts`.
- Hero-specific constants: the player and `state/evidence.ts` use a fixed `HERO_CASE` id; `demo_server/engine.py` is built around one shared hero mule.
- The India map is bundled (`india.topo.json`), no external tiles.

### 6.3 Non-functional or local-only behaviour

| Item | Behaviour | Evidence |
|---|---|---|
| Citizen "Report this" | shows acknowledgement, **sends nothing**; the text next to it says so | `Citizen.tsx:241`, `VerdictCard.tsx:113-123`, `web-console/README.md` |
| Analyst reason note | validated (length, no digit runs/emails) and kept in component state; not sent, not in ledger | `ResolveDialog.tsx`, `Analyst.tsx:127-137` |
| Tamper demo | omitted (no route) | `web-console/README.md` |
| Roles | `officer` can start runs; `citizen` and others see read-only banners where relevant | `Analyst.tsx:207` |
| WebSocket auth | JWT passed as a URL query string (`/api/stream?token=`) | `src/lib/ws/stream.ts:194`, `demo_server/api.py:304` |
| Console never exercises gateway login, RBAC proxy, hub, call-guard HTTP, citizen-shield service, geo-intel service | no such calls in `endpoints.ts` | |

### 6.4 Accessibility and responsiveness evidence

Evidence is from the repository, not from my own browser run: `web-console/e2e/pages.spec.ts` contains an axe-core check "no serious or critical violations on every page" run for both themes, a mobile citizen screenshot set (390x844) exists under `docs/screenshots/`, and `web-console/test-results/.last-run.json` (git-ignored, undated, left from an earlier run, not from mine) says `"status": "passed"`. **I did not execute Playwright** (not executed: needs a running demo server on a dedicated port; last reported by the build reports and the README which states "Playwright e2e and screenshots in this delivery pass: not re-run").

### 6.5 Bundle sizes (my `vite build --outDir /tmp/dist-report-build --emptyOutDir`, succeeded in 421 ms, 2,616 modules)

| Chunk | Raw | gzip |
|---|---|---|
| `index-*.js` (app, React, router, Radix) | 377.96 kB | 123.27 kB |
| `motion-*.js` (framer-motion) | 145.02 kB | 47.92 kB |
| `IndiaMap-*.js` | 36.51 kB | 13.78 kB |
| `Evidence-*.js` / `Citizen-*.js` / `Command-*.js` / `Analyst-*.js` / `Insights-*.js` / `About-*.js` | 30.89 / 30.49 / 28.16 / 26.57 / 24.97 / 18.37 kB | 10.03 / 9.74 / 9.44 / 8.68 / 7.67 / 7.08 kB |
| `index-*.css` | 58.29 kB | 11.59 kB |
| fonts (Inter, JetBrains Mono, woff + woff2) | 21 to 31 kB each (12 files) | n/a |

README bundle table (about 123 KB index, 47.9 KB motion) matches my build.

---

## 7. TESTING AND DEPLOYMENT

### 7.1 What I executed

All commands were run in this session at commit `6ba4072`. `make test-all` itself was not invoked; I ran the identical per-package command it uses (`python -m pytest -W error -q`, with `-rs` added to list skip reasons) in dependency order, one package at a time, using `/Users/aman/lastproject/.venv/bin/python`.

| Package | Command | Passed | Failed | Skipped | Not executed | Time | Notes |
|---|---|---|---|---|---|---|---|
| contracts | `pytest -W error -q` | 44 | 0 | 0 | 0 | 0.1 s | |
| libs/svckit | same | 179 | 0 | 0 | 0 | 5.8 s | |
| services/sim_engine | same | 56 | 0 | 0 | 0 | 3.5 s | |
| services/call_guard | same | 141 | 0 | 0 | 0 | 27.4 s | |
| services/txn_guard | same | 340 | 0 | 0 | 0 | 94.9 s | |
| services/antibody_hub | same | 160 | 0 | 5 | 0 | 3.2 s | 5 skipped: `HUB_TEST_POSTGRES_URL not set` |
| services/evidence_ledger | same | 184 | 0 | 7 | 0 | 11.1 s | 7 skipped: `LEDGER_TEST_POSTGRES_URL not set` |
| services/graph_twin | same | 122 | 0 | 3 | 0 | 45.4 s | 3 skipped: `GRAPH_TEST_POSTGRES_URL not set` |
| services/geo_intel | same | 64 | 0 | 1 | 0 | 0.5 s | 1 skipped: `GEO_TEST_POSTGRES_URL not set` |
| services/citizen_shield | same | 71 | 0 | 0 | 0 | 2.1 s | |
| services/gateway | same | 60 | 0 | 0 | 0 | 12.5 s | |
| tests/e2e/bench | same | 38 | 0 | 0 | 0 | 86.8 s | |
| tests/e2e/audit | same | 27 | 0 | 0 | 0 | 11.3 s | |
| services/demo_server | same | 183 | 0 | 0 | 0 | 35.7 s | |
| **Python subtotal (default run)** | | **1,669** | **0** | **16** | 0 | | |
| Opt-in Postgres suites, run separately against a disposable local PostgreSQL (Homebrew `initdb`/`pg_ctl`, temp dir, each script's own high port (hub default 54329, graph default 54349; ledger script not read) or 19050 for the ad-hoc geo cluster, `-A trust`, stopped and deleted afterwards) | `scripts/with_postgres.sh` in antibody_hub, evidence_ledger, graph_twin; geo_intel run with an ad-hoc temp cluster on port 19050 (no script exists for it) | 16 (5 + 7 + 3 + 1) | 0 | 0 | 0 | | these are the same 16 tests that were skipped above, now executed against real Postgres; they are not additional tests |
| web-console unit tests | `npx vitest run` | 172 (26 files) | 0 | 0 | 0 | 6.1 s | jsdom |
| Playwright e2e (`web-console/e2e`, 10 test declarations) | `npm run test:e2e` | 0 | 0 | 0 | **not executed**: needs a running demo server on a dedicated port; the stale `.last-run.json` says passed but is not my run | | |

**Totals: 1,857 distinct tests passed (1,669 Python default run + 16 Python opt-in Postgres + 172 vitest), 0 failed, 0 skipped after the opt-in run (16 skipped in the default run, then executed and passed), 10 Playwright test declarations not executed.**

These counts equal the README table counts for every package.

### 7.2 Other checks

| Command | Result |
|---|---|
| `cd web-console && npx tsc --noEmit` | exit 0, no output |
| `cd web-console && npx eslint .` | exit 0, no output |
| `cd web-console && npx vite build --outDir /tmp/dist-report-build --emptyOutDir` | exit 0, built in 421 ms (section 6.5); `web-console/dist` untouched |
| `.venv/bin/python -m ruff check .` at repo root | "All checks passed!" |
| `ruff check .` and `ruff format --check .` in each of the 14 Python packages | all report clean (contracts, bench and audit were re-run with absolute interpreter paths after a relative-path slip in my first loop) |
| `python scripts/check_links.py` | "19 files, ~107 links, 0 broken", exit 0 |
| `python scripts/check_dockerfiles.py` | 10 Dockerfiles, 0 problems, exit 0 |
| `docker compose --env-file infra/.env.example -f infra/docker-compose.yml --profile core --profile stack config -q` | exit 0; also exit 0 with `--profile observability` added |
| `python scripts/verify_package.py 1` | `RESULT: PASS (pinned)`, `PASS_PINNED`, 12 entries, 1 checkpoint verified (in-process, simulator data) |
| `docker info` | fails: cannot connect to the Docker API (no daemon) |

### 7.3 Not verified (could not be completed)

| Item | Reason |
|---|---|
| `docker build` of any image, `docker compose up`, container healthchecks, Redpanda/Redis/Postgres wiring in containers, `make stack-hero`, Prometheus scrape with `http_headers`, Grafana | no Docker daemon; **verification could not be completed** |
| Whether the multi-service flow produces an antibody after `confirm_block` | not exercised; static reading suggests no such path (section 3.2) |
| Playwright e2e, axe results, screenshots | not executed by me (see 7.1) |
| `make install` from an empty machine | no fresh-machine run; existing `.venv` and `node_modules` used |
| GitHub Actions `ci.yml` | cannot run here; static reading in 9 |
| Kafka-backed paths (`KafkaBus`) | covered only by unit tests with fake/in-memory buses; no broker available |
| Load/latency under real Redis/Postgres/Kafka | none exists; latency figures are in-process |
| `make demo` live HTTP smoke | not repeated by me (a demo server already occupies 8080 and must be left alone); `demo_server` suite covers the app in-process |

### 7.4 Dependency and environment requirements

- Python >= 3.11 (all `pyproject.toml`), editable installs in dependency order (`Makefile: PY_PKGS`): contracts, svckit, sim_engine, call_guard, txn_guard, antibody_hub, evidence_ledger, graph_twin, geo_intel, citizen_shield, gateway, bench, audit, demo_server. Optional extras: `[postgres]` (psycopg2-binary), `[train]` (sim-engine), `[dev]`. Node 20+ (README), `npm ci`.
- Environment variable NAMES (defaults in parentheses; values deliberately not copied):

| Service | Variables |
|---|---|
| gateway | `GATEWAY_JWT_SECRET` (required, >= 32 bytes), `GATEWAY_DEMO_USERS`, `GATEWAY_DEMO_PASSWORD`, `GATEWAY_DEMO_PASSWORD_<ROLE>`, `GATEWAY_REDIS_URL` (redis://redis:6379/0), `GATEWAY_UPSTREAM_{TXN,GRAPH,LEDGER,CITIZEN,GEO}`, `GATEWAY_MAX_BODY_BYTES` (1 MiB), `GATEWAY_MAX_RESPONSE_BYTES` (5 MiB), `PORT` |
| demo-server | `DEMO_JWT_SECRET` (random per process if unset), `DEMO_LOGIN_CODE`, `DEMO_STATIC_DIR`, `DEMO_CORS_ORIGINS`, `DEMO_WORK_DIR`, `DEMO_TRUST_PROXY`, `DEMO_HISTORY_SIZE`, `DEMO_CLIENT_QUEUE`, `DEMO_MAX_WS_CLIENTS`, `HOST` (default `0.0.0.0`), `PORT` (8080), `LOG_LEVEL` |
| txn-guard | `TXN_BANK_ID`, `KNOWN_BANKS`, `TXN_EVENTS_TOPIC`, `TXN_ACCEPT_UNATTRIBUTED`, `KAFKA_BOOTSTRAP`, `REDIS_URL`, `HUB_URL`, `HUB_GATEWAY_SECRET`, `GATEWAY_SHARED_SECRET`, `TRUST_GATEWAY_HEADERS`, `HOLD_DEADLINE_S`, `ANTIBODY_BLOOM`, `ANTIBODY_CACHE_CAPACITY`, `ANTIBODY_BOOTSTRAP_DEADLINE_S`, `ANTIBODY_REBOOTSTRAP_INTERVAL_S`, `AUDIT_DRAIN_INTERVAL_S`, `LEDGER_SWEEP_BATCH`, `METRICS_TOKEN` |
| call-guard | `KAFKA_BOOTSTRAP`, `REDIS_URL`, `LEDGER_DRAIN_INTERVAL_S`, `LEDGER_FALLBACK_MAX` |
| antibody-hub | `HUB_DATABASE_URL` (sqlite://), `HUB_BANKS`, `HUB_GATEWAY_SECRET`, `HUB_METRICS_TOKEN`, `HUB_CREATE_SCHEMA`, `HUB_DRAIN_INTERVAL_S`, `HUB_OUTBOX_RETENTION_DAYS`, `HUB_PARK_AFTER_S`, `HUB_PARK_ATTEMPTS`, `ANTIBODY_TTL_DAYS`, `KAFKA_BOOTSTRAP`, `TRUST_GATEWAY_HEADERS` |
| evidence-ledger | `LEDGER_DATABASE_URL` (sqlite://), `LEDGER_SIGNING_KEY_FILE`, `LEDGER_RETIRED_PUBKEYS`, `LEDGER_GATEWAY_SECRET`, `LEDGER_METRICS_TOKEN`, `LEDGER_CREATE_SCHEMA`, `LEDGER_CHECKPOINT_EVERY` (100), `LEDGER_CHECKPOINT_INTERVAL_S` (60), `LEDGER_MAINTENANCE_INTERVAL_S`, `LEDGER_MAX_BODY_BYTES`, `LEDGER_MAX_PACKAGE_SPAN`, `LEDGER_MAX_VERIFY_SPAN`, `KAFKA_BOOTSTRAP`, `TRUST_GATEWAY_HEADERS` |
| graph-twin | `GRAPH_DATABASE_URL` (sqlite://), `GRAPH_GATEWAY_SECRET`, `GRAPH_METRICS_TOKEN`, `GRAPH_CREATE_SCHEMA`, `KAFKA_BOOTSTRAP`, `TRUST_GATEWAY_HEADERS` |
| geo-intel | `GEO_DATABASE_URL` (sqlite://), `GEO_GATEWAY_SECRET`, `GEO_METRICS_TOKEN`, `GEO_CREATE_SCHEMA`, `GEO_RETENTION_DAYS` (compose 30), `KAFKA_BOOTSTRAP`, `TRUST_GATEWAY_HEADERS` |
| citizen-shield | `CITIZEN_REPORT_KEY` (required, >= 16 bytes), `CITIZEN_DATABASE_URL` (sqlite://), `CALL_GUARD_URL`, `CITIZEN_GATEWAY_SECRET`, `CITIZEN_METRICS_TOKEN`, `CITIZEN_CREATE_SCHEMA`, `KAFKA_BOOTSTRAP`, `TRUST_GATEWAY_HEADERS` |
| sim-engine | `SIM_SEED` (1), `SIM_SPEED` (60), `SIM_CITIZENS` (2000), `SIM_BANKS` (4; compose 2), `KAFKA_BOOTSTRAP` |
| compose `.env` | names in `infra/.env.example`: `GATEWAY_JWT_SECRET, GATEWAY_DEMO_PASSWORD, HUB_GATEWAY_SECRET, POSTGRES_PASSWORD, CITIZEN_REPORT_KEY, DEMO_JWT_SECRET, METRICS_TOKEN, GRAFANA_ADMIN_PASSWORD, LEDGER_RETIRED_PUBKEYS, SIM_SEED, SIM_SPEED, SIM_CITIZENS, HOLD_DEADLINE_S, LOG_LEVEL` (example values are demo-only placeholders and were not copied) |

### 7.5 Docker / compose review (static)

- Profiles: `core` (redpanda, topics-init, postgres, redis), `stack` (ledger-keygen, call-guard, txn-guard bank A/B, antibody-hub, evidence-ledger, graph-twin, geo-intel, citizen-shield, gateway, sim-engine, demo-server), `observability` (prometheus, grafana). `core` must always accompany `stack`.
- Hardening: `no-new-privileges`, `cap_drop: [ALL]`, uid 10001 (checked by `check_dockerfiles.py`), memory/cpu limits, json-file log rotation, all published ports bound to `127.0.0.1`, required secrets use `${VAR:?message}` so compose refuses to start with them empty (confirmed by prior build report; my `config -q` with the example env succeeded).
- Healthchecks via `infra/docker/healthcheck.py` (`/healthz`).
- Ledger key: generated by `ledger-keygen` one-shot into a named volume, 0600, never overwritten.
- Two txn-guard instances with distinct `TXN_BANK_ID`, Redis DB and consumer groups; sim-engine `SIM_BANKS=2` aligned with them (commit `c3ce1b0`).
- **Known not-run**: everything in this section is static. The Prometheus config uses `http_headers` token scraping that was never executed.
- Migrations: see 5.5 (create_all only).
- CI: `.github/workflows/ci.yml` exists (not mentioned in README). Not run here.

---

## 8. PROGRESS SUMMARY

Status: Complete = implemented and tested in this run within the simulator scope; Partial = implemented with material gaps; Missing = not in the repository.

| Feature | Status | Evidence | Remaining work |
|---|---|---|---|
| Synthetic data simulator and hero scenario | Complete (simulator scope) | `sim_engine/`; 56 tests passed; benchmark config in JSON | add NEFT scam flows, richer scam types, real-data adapters |
| Call scoring (rules + clf-v1) | Complete (simulator scope) | `call_guard/`; 141 tests passed; 163/163 scam calls alerted, 0/1076 benign (simulator) | train on real transcripts, voice/spoofing/video-metadata signals, non-template evaluation |
| Transaction scoring and holds (gbm-v1) | Complete (simulator scope) | `txn_guard/`; 340 tests passed; benchmark recall 82.65%, held-benign 0.000% | real-data calibration, NEFT scam data, latency under Redis/Kafka |
| Antibody sharing (hub + caches) | Partial | `antibody_hub/`; 160 + 5 Postgres tests passed; demo shows cross-bank block | confirm-to-hub submission implemented and tested in-process (not in containers); shared `HUB_GATEWAY_SECRET` lets any holder assert any principal; poisoning/false confirmation not modelled |
| Evidence ledger, packages, offline verify | Complete (tamper-evidence scope) | `evidence_ledger/`; 184 + 7 tests passed; `verify_package.py` PASS_PINNED | key management (HSM/KMS), external anchoring, legal review of certificate template; no admissibility claim |
| Graph twin (rings, takedown) | Complete (simulator scope) | `graph_twin/`; 122 + 3 tests passed | scale testing, optional graph DB, real account linkage |
| Geospatial intelligence | Partial | `geo_intel/`; 64 + 1 tests passed; 27-city static table, Poisson z | real geocoding, polygons/PostGIS or equivalent, counterfeit seizure layer, patrol prioritisation |
| Citizen shield service | Partial | `citizen_shield/`; 71 tests passed; 4 languages | 8 more languages, WhatsApp/IVR/app channels, evidence/complaint capture, wire console to `/report` |
| Gateway authN/RBAC/rate limit | Partial | `gateway/`; 60 tests passed | real user store, hub/call routes, per-service secrets, MFA/refresh, per-bank txn routing |
| Demo server | Complete (demo scope) | `demo_server/`; 183 tests passed | persistence, real auth, loopback default bind |
| Console pages (7 routes) | Complete (demo scope) | tsc, eslint clean; 172 vitest passed; build ok | Playwright re-run, connect to gateway API, report and note wiring |
| Docker stack | Partial (unverified) | compose + 10 Dockerfiles; `config -q` and static checker passed | build and run stack end to end; fix antibody path and gateway wiring |
| Docs, deck, demo video | Complete (with noted gaps) | `docs/`, `deck/`, `demo/`; links check 0 broken | narration audio absent; deck not opened in PowerPoint (README) |
| Benchmark harness | Complete | `tests/e2e/bench`; 38 passed; report present | not re-run by me; commit-hash drift between report and JSON |
| Audit end-to-end | Complete | `tests/e2e/audit`; 27 passed | none identified |
| LLM-based explanations / conversational AI | **Missing** | grep for LLM libraries: none; spec asks for "LLM with guardrails" | decide whether needed; if so design guardrails and PII policy |
| Fraud DNA (Qdrant / vector similarity) | **Missing** | grep `qdrant`: none | define fingerprinting; add vector store if wanted |
| Neo4j network intelligence | **Missing** (replaced by NetworkX + SQL) | `graph_twin/store.py` docstring; compose header | graph DB only if scale needs it |
| PostGIS geospatial | **Missing** (image only, no features) | compose `postgis/postgis:16-3.4`; no `ST_*` in code | use PostGIS features or switch to plain postgres image |
| Investigator dashboard | Partial | `/analyst`, `/command`, `/evidence` exist; no case management, no notes persistence | case workflow, saved queries, assignments, persisted notes |
| Admin dashboard and user/role management | **Missing** | no admin route/page/API | user store, role admin UI, audit of admin actions |
| Complaint registration with evidence upload | **Missing** | `/report` takes enum + hash + city only | complaint form, attachments, storage, chain-of-custody |
| Counterfeit currency detection | **Missing (dropped by decision)** | spec section 1, `challenge-statement.md` | out of scope unless reinstated |
| Alerts to telecom/MHA/victims | **Missing** | only bus events and console toasts | integration adapters (SMS, webhook, email) |
| Multi-channel (WhatsApp, IVR, mobile app) | **Missing** | no adapter code | channel adapters |
| Production deployment | **Missing** | nothing deployed; no k8s manifests (spec Phase 6) | environment, TLS, secrets, monitoring |
| Observability (OpenTelemetry, dashboards) | Partial | Prometheus config and `/metrics`; no OTel, no Grafana dashboards provisioned | tracing, dashboards, alerts |

---

## 9. KNOWN ISSUES AND NEXT STEPS

### 9.1 Critical

| # | Issue | Justification and evidence |
|---|---|---|
| C1 | **Nothing is deployed and the multi-container stack has never been built or started** | No daemon in the build environment (my `docker info` fails). Every compose-level claim (wiring, healthchecks, Postgres triggers on real Postgres under load, Kafka consumers) rests on unit tests with in-memory/fake buses and static `config -q`. Any production claim is unsupported. |
| C2 | **No real authentication; demo-login is open** | `POST /api/auth/demo-login` issues a token for any role (including `admin`/`officer`) to anyone who can reach the server, unless `DEMO_LOGIN_CODE` is set. The server defaults to `HOST=0.0.0.0` (`demo_server/__main__.py:19`) and `make demo` does not override it, so on a shared network any host can become `officer`. The gateway user store is in-memory demo users. |
| C3 | **Results are simulator-only** | Every metric is from the same generators that trained the models; no real data. Must not be presented as real-world performance (the repo itself says so). |

### 9.2 High

| # | Issue | Evidence |
|---|---|---|
| H1 (reduced) | ~~No path from analyst `confirm_block` to a hub antibody~~ **Implemented** (durable submission outbox in txn-guard, background submitter, 43 unit tests + 5 e2e tests). Remaining: not exercised in containers/Kafka/Postgres; shared hub secret model; holds created before the change skip with `invalid_key_hash` | `services/txn_guard`, `tests/e2e/audit/tests/test_confirm_to_hub.py` |
| H2 | Console is not wired to the gateway: different base paths, login and role model; the stack's gateway is exercised only by its own tests | `endpoints.ts` vs `gateway/main.py` |
| H3 | Gateway-to-service trust: services run with `TRUST_GATEWAY_HEADERS=1`, no per-service secret; any container on the `scam` network can send `X-Principal-Role: admin` | compose env, `architecture.md` section 2. call-guard and sim-engine have no auth at all |
| H4 | Gateway RBAC inconsistencies: citizens cannot reach `/holds/{id}/verify`; ledger prefix excludes analyst; only bank A's txn-guard is routed | `ROUTE_ROLES`, compose |
| H5 | Per-process ledger signing key in the demo-server (lost on restart, so packages from earlier runs cannot be verified later); keys in compose are file-based with no rotation procedure beyond `LEDGER_RETIRED_PUBKEYS` | `demo_server/evidence.py`, README limitations |
| H6 | Citizen channel is not a complaint system: no evidence capture, 4 of 12 languages, console report button is cosmetic | sections 2 and 6.3 |
| H7 | Admin/user management and investigator case workflow absent | section 2 |
| H8 | Fail-open rate limiting: gateway limiter allows requests when Redis errors or times out (0.5 s); citizen-shield and demo-server limiters are per-process memory, not shared across replicas | `gateway/main.py:guarded`, `ratelimit.py` docstring, `citizen_shield/ratelimit.py` |

### 9.3 Medium

| # | Issue | Evidence |
|---|---|---|
| M1 | JWT in the WebSocket URL query string (logged by proxies, visible in browser history/referrers if mishandled) | `stream.ts:194`; demo-server redacts its own logs (`logredact.py`) but intermediaries do not |
| M2 | No TLS anywhere; compose publishes plain HTTP on 127.0.0.1 only | compose |
| M3 | No Python lock file, `>=` bounds only; resolved versions are very recent (FastAPI 0.142, SQLAlchemy 2.1, scikit-learn 1.9.1). The joblib artifacts are pinned to a scikit-learn version and fall back to rules-only on mismatch | `pyproject.toml`; `artifact_pin.py` |
| M4 | No migration tool; default DB URL `sqlite://` is silently ephemeral | section 5.5 |
| M5 | `.github/workflows/ci.yml` installs packages in `find` order and `pip install -e "$d[dev]"` per directory; local packages (`scam-contracts`, `svckit`, `sim-engine`...) are not on PyPI and the install order is not the dependency order, so it very likely fails; it also never runs the console, `-W error` or `docker build`. Unverified (cannot run here) | `ci.yml` |
| M6 | Documentation drift: README/docs say `main` holds the project but the local `main` ref is 150 commits stale; benchmark report commit hash differs from JSON `meta.git_commit`; spec still lists Neo4j, PostGIS, transformer classifier, LLM, k8s, OpenTelemetry, WhatsApp adapter, gateway PII tokenisation and Postgres user store, none of which exist | git refs; spec section 2 |
| M7 | Duplicate citizen assessment logic: `demo_server/citizen.py` and `citizen_shield/policy.py/assess.py` (policy shared but orchestration duplicated); geo logic also duplicated in demo-server; risk of divergence | file comparison |
| M8 | Bounded lockout DoS: login failure limiter keys on username (documented trade-off in `gateway/main.py`) | docstring |
| M9 | Held transfers' deadline handling flags overdue but never auto-resolves; no escalation or notification | `holds.py` docstring |

### 9.4 Low

| # | Issue |
|---|---|
| L1 | Local build artifacts present in the tree but git-ignored (`__pycache__`, `web-console/test-results`, `dist`); `.superpowers/` ledger of the build process is ignored and not part of the delivery |
| L2 | License is "TBD"; all rights reserved |
| L3 | Demo video has no audio; deck not opened in PowerPoint (README) |
| L4 | Bloom filter and `script`/`device` antibody kinds exist in contracts and hub, but only `mule_account` is exercised in the benchmark and demo |
| L5 | Many static reason strings are English-first; localisation exists only for citizen verdicts |
| L6 | Console token and language kept in `sessionStorage` (acceptable for demo) |

### 9.5 Recommended next development phase (ordered)

1. **Make one real stack run.** On a machine with Docker: `make stack-config`, `make stack-up`, fix whatever breaks, run `make stack-hero`; capture logs. Gate: all healthchecks green and hero produces ledger entries.
2. **Close the antibody gap in the stack.** Add an authenticated analyst-confirm to hub step (txn-guard resolve calls hub `POST /antibodies`, or a gateway `hub` route with `analyst` role), including audit entry; add a stack-level test (compose-free two-service test first).
3. **Unify the API surface.** Make the console talk to the gateway (real `/auth/login`, `/api/txn|graph|geo|ledger|citizen`), or define demo-server as the only console backend and delete the unused paths. Fix gateway RBAC mismatches (citizen verify, ledger analyst reads, bank B routing).
4. **Replace demo auth.** Persistent user store (Postgres), password rotation, per-service shared secrets (`*_GATEWAY_SECRET` already supported in code), admin user-management API and page, bind demo-server to 127.0.0.1 by default, remove token-in-URL for WebSocket (subprotocol or ticket).
5. **Complaint and evidence intake.** Complaint model with narrative, attachments with hashing and chain-of-custody into the ledger, persistent investigator notes (send the reason note to the API and ledger), console `Report` wired to `/report`.
6. **Persistence and migrations.** Adopt Alembic, remove silent `sqlite://` defaults in service entrypoints, persist demo state or document it as ephemeral.
7. **Data and model realism.** Add NEFT scam flows and non-template call text to the simulator, hold-out text families for call-guard, report degraded-signal as the headline; if real data is ever available keep the evaluation harness and replace the generator.
8. **Scope decisions on absent items.** Decide explicitly whether Qdrant/"Fraud DNA", LLM explanations, PostGIS and Neo4j are required; if not, change the spec and the compose image (use plain Postgres) so docs match reality; if yes, schedule them with a PII policy.
9. **CI and reproducibility.** Fix `ci.yml` (dependency-order installs, `-W error`, console steps, compose config), add Python constraints/lock file, run Playwright in CI against a dedicated port.
10. **Observability and ops.** Provision Grafana dashboards, add tracing, document key rotation and backup of the ledger database and signing key.

---

## Appendix: commands run

Exit status as observed. Per-package pytest used `/Users/aman/lastproject/.venv/bin/python -m pytest -W error -q -rs` in the package directory.

| # | Command | Exit |
|---|---|---|
| 1 | `git rev-parse --short HEAD`; `git branch --show-current`; `git status --short` (before and after) | 0, clean |
| 2 | pytest in `contracts` | 0 (44 passed) |
| 3 | pytest in `libs/svckit` | 0 (179 passed) |
| 4 | pytest in `services/sim_engine` | 0 (56 passed) |
| 5 | pytest in `services/call_guard` | 0 (141 passed) |
| 6 | pytest in `services/txn_guard` | 0 (340 passed) |
| 7 | pytest in `services/antibody_hub` | 0 (160 passed, 5 skipped) |
| 8 | pytest in `services/evidence_ledger` | 0 (184 passed, 7 skipped) |
| 9 | pytest in `services/graph_twin` | 0 (122 passed, 3 skipped) |
| 10 | pytest in `services/geo_intel` | 0 (64 passed, 1 skipped) |
| 11 | pytest in `services/citizen_shield` | 0 (71 passed) |
| 12 | pytest in `services/gateway` | 0 (60 passed) |
| 13 | pytest in `tests/e2e/bench` | 0 (38 passed) |
| 14 | pytest in `tests/e2e/audit` | 0 (27 passed) |
| 15 | pytest in `services/demo_server` | 0 (183 passed) |
| 16 | `npx tsc --noEmit` (web-console) | 0 |
| 17 | `npx eslint .` (web-console) | 0 |
| 18 | `npx vitest run` (web-console) | 0 (172 passed) |
| 19 | `npx vite build --outDir /tmp/dist-report-build --emptyOutDir` | 0 |
| 20 | `ruff check .` (repo root) | 0 |
| 21 | `ruff check .` and `ruff format --check .` per package (14) | clean ("All checks passed!", files already formatted) |
| 22 | `python scripts/check_links.py` | 0 |
| 23 | `python scripts/check_dockerfiles.py` | 0 |
| 24 | `docker compose --env-file infra/.env.example -f infra/docker-compose.yml --profile core --profile stack config -q` | 0 |
| 25 | same with `--profile observability` added | 0 |
| 26 | `docker info` | failed: no daemon |
| 27 | `bash scripts/with_postgres.sh` in `services/antibody_hub`, `services/evidence_ledger`, `services/graph_twin` (disposable local PG, torn down) | 5 / 7 / 3 passed |
| 28 | geo-intel opt-in test against a temp PG cluster on port 19050 (initdb, pg_ctl, createdb, pytest `tests/test_postgres.py`, stop, delete) | 1 passed |
| 29 | `python scripts/verify_package.py 1` | PASS (pinned) |
| 30 | `shasum -a 256` of both joblib artifacts compared to `artifact_pin.py` prefixes | match |
| 31 | `find`/`ls`/`grep`/`git log`/`wc` read-only inspection commands | 0 |

## Appendix: files inspected

Root and docs: `README.md`, `Makefile`, `PROJECT_PROGRESS_REPORT.md` (written), `.github/workflows/ci.yml`, `docs/challenge-statement.md`, `docs/architecture.md`, `docs/benchmark_report.md`, `docs/audit-trail.md` (listed, skimmed), `docs/judging-map.md`, `docs/demo-script.md`, `docs/superpowers/specs/2026-10-07-scam-immune-system-design.md`, `docs/superpowers/plans/` (listed), `.superpowers/sdd/2026-10-07-scam-immune-system/delivery-report.md` (tail; ledger dir listed).

Contracts and libs: `contracts/scam_contracts/{topics,models}.py`, `libs/svckit/svckit/{health,idempotency}.py`.

Gateway: `services/gateway/gateway/{main,auth,rbac,ratelimit}.py`.

call-guard: `api.py`, `model.py`, `train.py`, `artifact_pin.py`, `rules.py` (symbol list), `session.py` (key prefixes).

txn-guard: `api.py`, `model.py` (docstring), `features.py`, `decision.py`, `train.py` (grep), `holds.py` (grep), `antibody_cache.py` (grep), `artifact_pin.py`.

antibody-hub: `api.py`, `store.py`. evidence-ledger: `api.py`, `store.py`, `package.py`, `verify.py` (docstring). graph-twin: `store.py`, `model.py`, `api.py` (routes), `README.md`. geo-intel: `api.py`, `compute.py`, `store.py`, `migrate.py`, `table.py`. citizen-shield: `api.py`, `policy.py`, `store.py`, `ratelimit.py`. sim-engine: `api.py`, `scam.py` (symbols), `replay.py` (grep). demo-server: `api.py`, `auth.py`, `config.py`, `citizen.py`, `geo.py`, `engine.py` (resolve/confirm sections), `__main__.py`, `README.md` (grep), `data/benchmark_summary.json`.

Infra and scripts: `infra/docker-compose.yml`, `infra/.env.example` (variable names only), `infra/postgres/init.sql`, `infra/prometheus.yml`, `scripts/{test_all.sh,verify_package.py,check_links.py,check_dockerfiles.py}`, `services/*/scripts/with_postgres.sh`, `services/*/Dockerfile` (FROM lines).

Web console: `package.json`, `src/app/{App.tsx,nav.ts}`, `src/lib/api/endpoints.ts`, `src/lib/api/client.ts` (grep), `src/lib/ws/stream.ts` (grep), `src/lib/i18n/citizen.ts`, `src/routes/*.tsx` (API usage grep), `src/components/citizen/VerdictCard.tsx`, `src/components/analyst/ResolveDialog.tsx`, `src/state/{evidence,playback,live,session}.ts`, `src/mocks/` (import graph), `e2e/{pages,smoke}.spec.ts`, `README.md` (API contract and bundle sections), `test-results/.last-run.json`.
