# Project gap analysis: requirements brief ("Digital Suraksha") vs repository ("ScamShield Immune")

Audit only (Step A). No code, data, config, database or other document was modified; no server was started; no network or LLM call was made. Audit date: 2026-10-13. Working tree branch at audit time: `build/implementation` (clean; `main` and `origin/build/implementation` also exist). Repository: `/Users/aman/lastproject`.

## 0. Summary

Status counts over the 112 requirement rows in section 2:

| Status | Rows |
|---|---|
| COMPLETE AND VERIFIED | 22 |
| IMPLEMENTED BUT UNVERIFIED | 3 |
| PARTIALLY IMPLEMENTED | 32 |
| MISSING | 55 |
| BLOCKED BY CONFIGURATION/EXTERNAL SERVICE | 0 |

No row is labelled BLOCKED because every externally dependent capability (Clerk, Qdrant, Neo4j, PostGIS, Ollama) also has no code in the repository, so MISSING is the accurate status; section 3 states which of those would additionally be blocked by services or credentials that are not available on this machine.

**Conclusion.** The repository is a well-tested, simulator-only prototype of a bank-side and citizen-side scam interception system. It contains a real rules plus TF-IDF/logistic-regression text scorer for digital-arrest style scam language, a HistGradientBoosting transaction scorer, a cross-bank antibody hub, an in-memory graph model with ring detection, a city-level geo aggregator, a signed evidence ledger, a role-gated gateway and a Vite/React console. Roughly 1,640 Python tests and 172 console tests pass in this audit. It is not the system described in the brief. There is no complaint model, no complaint API, no user database, no registration, no Clerk (or any external identity) integration, no investigator email allowlist, no file upload, no URL/OCR/audio analysis, no Ollama integration, and no Qdrant, Neo4j or PostGIS usage; the console has no citizen or investigator complaint dashboards and no admin dashboard. What does exist and is reusable is the analysis core (text scorer with explicit degraded behaviour), RBAC plumbing, hardening patterns, SQLAlchemy persistence, the console shell and the test harness. Most of the brief is therefore new work layered on the existing repository, and several external services cannot be exercised on this machine (no Docker daemon, no Neo4j, Qdrant, Ollama, PostGIS, Clerk credentials).

## 1. Method and evidence

Everything below was re-verified from code in this audit, not taken from `PROJECT_PROGRESS_REPORT.md`. Commands run (read-only):

* `grep -rn` over `services`, `libs`, `contracts`, `web-console/src`, `infra`, `docs` for clerk, qdrant, neo4j, ollama, supabase, postgis, upload/multipart/ocr/whisper, anthropic/openai, email, allowlist.
* Test suites, run from each package with the repo venv (`python -m pytest -W error -q -p no:cacheprovider`) and `npx vitest run` in `web-console`, all after the above reads. The working tree was still clean afterwards (`git status --short` empty).

| Suite | Result in this audit |
|---|---|
| services/gateway | 60 passed |
| services/citizen_shield | 71 passed |
| services/call_guard | 141 passed |
| services/txn_guard | 376 passed |
| services/antibody_hub | 160 passed, 5 skipped (Postgres: `HUB_TEST_POSTGRES_URL` unset) |
| services/evidence_ledger | 184 passed, 7 skipped (Postgres) |
| services/graph_twin | 122 passed, 3 skipped (Postgres-gated) |
| services/geo_intel | 64 passed, 1 skipped (Postgres) |
| services/demo_server | 184 passed |
| services/sim_engine | 56 passed |
| libs/svckit | 180 passed |
| contracts | 44 passed |
| web-console (vitest) | 26 files, 172 tests passed |

Python total: 1,642 passed, 16 skipped, 0 failed. All 16 skips are Postgres-gated integration tests; no Postgres server is running, so Postgres persistence is not verified here.

## 2. Requirement matrix

Status vocabulary: COMPLETE AND VERIFIED | IMPLEMENTED BUT UNVERIFIED | PARTIALLY IMPLEMENTED | MISSING | BLOCKED BY CONFIGURATION/EXTERNAL SERVICE. "Verified" means a passing test or executed behaviour was seen in this audit.

Facts used repeatedly below:

* Roles in code are `officer`, `analyst`, `citizen`, `admin` (`services/gateway/gateway/auth.py:ROLES`, `services/demo_server/demo_server/config.py:21`). There is no `INVESTIGATOR` role name; officer/analyst are the nearest analogues.
* Gateway login (`POST /auth/login`, `services/gateway/gateway/main.py:191`) checks scrypt-hashed passwords from an in-memory `InMemoryUserStore` seeded from env (demo users). It issues HS256 JWTs. There is no user table, no sign-up.
* The demo server (`services/demo_server/demo_server/api.py:239`, `POST /api/auth/demo-login`) hands a token for any requested role to any caller (optionally gated by `DEMO_LOGIN_CODE`). Its own docstring says this is a simulation convenience, not an authentication scheme.
* The only complaint-like write path is `POST /report` in `services/citizen_shield/citizen_shield/api.py:307`, which accepts only `category in {scam_call, held_transfer, cash_out}`, an optional 64-hex message hash, `lang`, optional `city_code` and `state_code`. It stores a keyed hash id, category, coarse location and an emitted flag (`cs_reports`, `store.py:35`). It deliberately keeps no text, no principal, no IP.

### R1 Purpose

| ID | Requirement | Status | Evidence | APIs / models | Minimal remaining work |
|---|---|---|---|---|---|
| R1.1 | Digital-arrest scam detection | COMPLETE AND VERIFIED | `call_guard/rules.py` (cues DIGITAL_ARREST_PHRASE, SAFE_ACCOUNT_TRANSFER, ISOLATION_DEMAND etc.), `call_guard/model.py` clf-v1; call_guard 141 passed incl. `test_independent_eval.py`; citizen_shield 71 passed. Simulator-calibrated only (`model.py` docstring) | `POST /score` (call_guard), `POST /assess` (citizen_shield), `POST /api/citizen/assess` (demo_server) | None for the existing capability; real-data validation is out of scope |
| R1.2 | Other frauds: UPI, phishing, OTP, bank, investment, job, social media | PARTIALLY IMPLEMENTED | Only an OTP/credential-ask cue (`CREDENTIAL_ASK`, `rules.py:560`) and a transaction anomaly scorer (`txn_guard/features.py`, `model.py`, simulator UPI/IMPS/NEFT). No phishing/URL, investment, job or social-media logic anywhere (grep) | `Transaction`/`CallEvent` in `scam_contracts/models.py` | Add per-category cue sets and a category label (see R4.8, R8.7) |
| R1.3 | Citizens report incidents | PARTIALLY IMPLEMENTED | `POST /report` exists and is tested (citizen_shield tests), but captures category enum + coarse location only; not wired in the console (`grep "/report" web-console/src` empty) | `cs_reports`; `Complaint` event | Full complaint model and API (R3) |
| R1.4 | Citizens analyse fraud | COMPLETE AND VERIFIED | `citizen_shield/assess.py:assess`; `tests/test_assess.py`, `test_api.py`; console `routes/Citizen.tsx` + `Citizen.test.tsx` pass | `POST /api/citizen/assess` via gateway prefix `citizen` | None for pasted text |
| R1.5 | Citizens understand risk | COMPLETE AND VERIFIED | Verdict has label, reasons with localised text (en/hi/ta/bn, `texts.py`), advice steps; `components/citizen/VerdictCard.tsx` | same | Add category and 0-100 mapping if brief's output format is wanted (R4.8) |
| R1.6 | Citizens track complaints | MISSING | No complaint store with status, no list/get endpoint; `/report` returns an id only | none | Complaint table with status lifecycle, `GET /complaints`, `GET /complaints/{id}` |
| R1.7 | Investigators discover connections between complaints | PARTIALLY IMPLEMENTED | `graph_twin` links accounts/devices from transactions and decisions (tables `nodes`, `edges`, `rings`); graph_twin 122 passed. Links are over simulated transactions, not complaints | `/rings`, `/rings/{id}/graph` | Entity extraction and link model over complaints (R6) |
| R1.8 | Investigators see recurring patterns | PARTIALLY IMPLEMENTED | Antibody hub fingerprints and corroboration (`antibody_hub/store.py`), geo hotspots (`geo_intel/compute.py`); tests pass. Not complaint-driven | `/antibodies`, `/hotspots` | Feed complaint entities into hub/geo-style aggregation |
| R1.9 | Investigators see networks | PARTIALLY IMPLEMENTED | `graph_twin/rings.py`, `graph.py` (networkx, in memory, SQLAlchemy persistence); tests pass | `/rings` | As R1.7 |

### R2 Roles and authentication

| ID | Requirement | Status | Evidence | APIs / models | Minimal remaining work |
|---|---|---|---|---|---|
| R2.1 | Three roles CITIZEN / INVESTIGATOR / ADMIN with permissions | PARTIALLY IMPLEMENTED | Four roles exist (`ROLES`), route matrix `gateway/main.py:42-48`: txn/graph/geo -> officer/analyst/admin, ledger -> officer/admin, citizen -> citizen/admin. Tested: `gateway/tests/test_proxy.py:test_rbac_matrix`, `demo_server/tests/test_auth.py:test_every_endpoint_by_role` | JWT claim `role` | Decide mapping (INVESTIGATOR = analyst+officer?) and add a persisted role per user |
| R2.2 | Use existing Clerk integration if it works | MISSING | No match for "clerk" in any `.py`, `.ts(x)`, `package.json`, `pyproject.toml`, `.env.example`, compose file. SDK not installed in the venv. No Clerk integration exists to evaluate | none | Owner supplies Clerk instance (see section 3); implement JWKS verification (WP-2) |
| R2.3 | Secure sign-in / sign-up | PARTIALLY IMPLEMENTED | Sign-in: gateway `/auth/login`, scrypt, per-IP/user lockout (`gateway/ratelimit.py`), `gateway/tests/test_auth.py` passes. Sign-up: none. Demo server login is passwordless | `/auth/login` | Registration flow (via Clerk or local) and a users table |
| R2.4 | Separate Citizen and Investigator entry options in UI | PARTIALLY IMPLEMENTED | `app/Splash.tsx` shows four role cards (Officer, Analyst, Citizen, Admin) calling `endpoints.login(role)` which posts `{role}` to `/auth/demo-login`. Any visitor can pick any role | `src/lib/api/endpoints.ts:26` | Separate citizen and investigator entries with real sign-in |
| R2.5 | Investigator sign-in only for approved email via ENV allowlist | MISSING | grep for allowlist/email/`INVESTIGATOR_*` in services finds nothing; no email concept in any auth code | none | `INVESTIGATOR_EMAIL_ALLOWLIST` env + check at identity-mapping time (WP-2) |
| R2.6 | Eligibility enforced on the backend; unapproved rejected with friendly message | MISSING | As R2.5 | none | Backend check returning 403 with a stable friendly error code; console renders it |
| R2.7 | Role never trusted from registration input | PARTIALLY IMPLEMENTED | Gateway role comes from the user store, not the request (verified, `test_auth.py`). Demo-login does trust the requested role by design (`api.py:239-247`, `_Body.role`) | `/auth/login`, `/api/auth/demo-login` | Keep demo-login behind a flag off by default in any non-demo mode; derive role server-side only |
| R2.8 | Citizen registration never grants INVESTIGATOR/ADMIN | MISSING | There is no registration at all | none | Test with registration once built (WP-2) |
| R2.9 | ADMIN only via secure configured bootstrap or authorised process | PARTIALLY IMPLEMENTED | Admin exists only as a demo user whose password comes from env/generated (`auth.py:demo_users_from_env`); no bootstrap-by-email, no role-change process | env `GATEWAY_DEMO_USERS`, `GATEWAY_DEMO_PASSWORD*` | `ADMIN_BOOTSTRAP_EMAIL` env + admin-only role-change endpoint with audit (WP-2, WP-7) |
| R2.10 | Verify Clerk identity, map to internal DB user | MISSING | No JWKS/RS256 code, no user table. `PyJWT 2.15.1` and `cryptography 50.0.2` are installed in the venv, so verification is feasible | none | WP-2 |
| R2.11 | RBAC enforced on every protected endpoint | COMPLETE AND VERIFIED | Existing endpoints only: gateway `require_role` per prefix; downstream services trust gateway headers only with a shared secret (`citizen_shield/api.py:_trusted`); the RBAC matrix tests pass. Caveat: complaint, user and admin endpoints do not exist yet | `gateway/rbac.py:require_role` | Reuse for new routes and add them to the matrix test |
| R2.12a | Citizens cannot reach investigator/admin endpoints | COMPLETE AND VERIFIED | `test_rbac_matrix` passes (citizen gets 403 on txn/graph/geo/ledger) | gateway prefixes | None |
| R2.12b | Citizens cannot read others' complaints | MISSING | No complaint read path exists, so there is nothing to protect yet; no ownership column anywhere | none | Owner-scoped queries with 404 on foreign id (WP-3) |
| R2.13 | No secret keys exposed | IMPLEMENTED BUT UNVERIFIED | `infra/.env.example` holds placeholders only; `demo_server/logredact.py`, `libs/svckit/svckit/pii.py`; no Clerk keys exist. Finding: `gateway/auth.py:106` logs a generated demo password at WARNING when no password env is set | env names: `GATEWAY_JWT_SECRET`, `DEMO_JWT_SECRET`, `HUB_GATEWAY_SECRET`, `POSTGRES_PASSWORD`, `CITIZEN_REPORT_KEY`, `METRICS_TOKEN` | Stop logging generated passwords (write once to a 0600 file or fail); add a secret-scan test |
| R2.14 | No passwords stored in app tables | IMPLEMENTED BUT UNVERIFIED | No user/password table in any `Table(...)` definition (listed in the audit); scrypt hashes are held in memory only | none | Keep true when adding `users` (store Clerk subject, never a password) |
| R2.15 | No auto-promotion by merely submitting an approved email | MISSING | Feature absent; nothing to violate or test | none | Require verified identity claim (email_verified from the token) before allowlist match; negative test |

### R3 Complaint registration

| ID | Requirement | Status | Evidence | APIs / models | Minimal remaining work |
|---|---|---|---|---|---|
| R3.1 | Mandatory: category, title, description, incident datetime, reported datetime | MISSING | `ReportBody` (`citizen_shield/api.py:86`) has none of title/description/datetimes; category enum is 3 simulator kinds | none | `Complaint` model + `POST /complaints`; reported datetime server-set |
| R3.2 | Optional amount and payment method | MISSING | No complaint field; `Transaction` contract in `scam_contracts/models.py` has amount/rail for bank events only | none | Nullable columns, Decimal amount |
| R3.3 | Optional UPI ID, UTR/reference, suspect phone, suspect email, URL | MISSING | No such fields in any request model | none | Nullable columns with format validation; store normalised and hashed forms for matching |
| R3.4 | Optional location / city / state | PARTIALLY IMPLEMENTED | `city_code`/`state_code` on `/report`, validated against a fixed enum (`scam_contracts/geo.py`, 27 cities / 17 states), tested in citizen_shield; city-level only, no free text, no coordinates | `cs_reports.city_code/state_code` | Free-text city/state with normalisation; optional lat/lon only if PostGIS is adopted |
| R3.5 | Optional social media platform / account | MISSING | None | none | Nullable columns |
| R3.6 | Optional evidence attachments (images/PDF/docs/audio) | MISSING | No `UploadFile`/multipart, no `python-multipart` in venv, no upload code (grep) | none | WP-4 |
| R3.7 | Optional fields labelled optional in UI; missing values stay null | MISSING | No form; no complaint table | none | Console form with "(optional)" labels; test nulls round-trip |
| R3.8 | File validation: size, MIME, permissions | MISSING | None | none | Size cap, magic-byte MIME sniffing against an allowlist, per-owner access check |
| R3.9 | Secure file storage | MISSING | None (the ledger stores signed JSON packages, not user files) | none | Server-side storage dir outside web root, random names, evidence metadata table with sha256 |
| R3.10 | Never pretend an upload succeeded | MISSING | None | none | Response reports per-file status; partial failure surfaced in UI |

### R4 Analysis

| ID | Requirement | Status | Evidence | APIs / models | Minimal remaining work |
|---|---|---|---|---|---|
| R4.1 | Real backend pipeline over complaint description | COMPLETE AND VERIFIED | `citizen_shield/assess.py` normalises, calls call_guard `/score` over HTTP (`guard_client.py`), labels, localises. Tests pass | `POST /assess` | Call from the new complaint service (WP-3) |
| R4.2 | Audio transcript analysis (if implemented) | MISSING | No speech-to-text anywhere; `call_guard` consumes already-transcribed chunks | none | Optional; needs a local ASR model (not present). Mark capability "unavailable" in output |
| R4.3 | Screenshot OCR (if implemented) | MISSING | No OCR library in venv (`pip list`); no code | none | Optional; needs Tesseract or similar installed locally |
| R4.4 | URL analysis (if implemented) | MISSING | No URL parsing/reputation code in call_guard/citizen_shield | none | Offline lexical URL checks (homograph, shorteners, suspicious TLD) are feasible without network |
| R4.5 | Transaction / suspect info | PARTIALLY IMPLEMENTED | `txn_guard` scores bank-side transactions using payer history and call risk; 376 tests pass. It cannot score a citizen-reported UPI/UTR without payer history | `txn_guard` `/score`-style API | Separate complaint-side heuristics (amount, payee reuse across complaints) |
| R4.6 | Geographic info | PARTIALLY IMPLEMENTED | `geo_intel` city-level hotspots (`compute.py`); 64 passed; not used in per-complaint analysis | `/hotspots` | Add location prior as a labelled, low-weight indicator |
| R4.7 | Missing inputs do not block unrelated analysis | PARTIALLY IMPLEMENTED | Text-only scoring works with every other input absent (verified). Blank text returns label `safe`, score 0, `uncertain: true`, advice key `no_input` (`assess.py:63`), which a complaint flow must not display as "safe" | `assess` | Complaint analyser composes independent steps, each returning `skipped` with a reason |
| R4.8 | Output: risk 0-100, level, classification, indicators, confidence, explanation, timestamp, safety actions | PARTIALLY IMPLEMENTED | Present: score 0-1 float, label (`safe`/`suspicious`/`likely_scam`), reasons[] with weights and text, advice steps, `uncertain` boolean, `model_version`, `degraded`, `coverage`. Absent: 0-100 scale, fraud category/classification, numeric confidence, timestamp | `Verdict.to_json` | Map to 0-100, add timestamp and category; keep `uncertain` and `degraded` |
| R4.9 | Use existing rule-based classifier and trained ML model | COMPLETE AND VERIFIED | call_guard rules-v1 plus clf-v1 (`clf-v1.joblib`, SHA-256 pinned in `artifact_pin.py`); txn_guard `gbm-v1.joblib`; suites pass | as above | None |
| R4.10 | Local Ollama / Qwen2.5 3B if functional | MISSING | No Ollama code; `ollama` binary not found; `~/.ollama` has only `config.json` and `cache` (no models). Only mention in repo is a test data string | none | Client behind health-checked interface with `unavailable` fallback (WP-6); model install is an owner action |
| R4.11 | No paid cloud LLM APIs | COMPLETE AND VERIFIED | grep for anthropic/openai/generativelanguage across `services`, `libs`, `contracts` Python and TOML: no matches | none | Keep it that way; add a dependency-denylist test |
| R4.12 | Failed model must not silently become BENIGN | COMPLETE AND VERIFIED | `citizen_shield/tests/test_assess.py:test_scorer_down_degrades_to_suspicious_never_safe`, `test_fix1.py:test_unexpected_transport_errors_degrade_instead_of_500` pass; call_guard falls back to `rules-v1` loudly when the artifact pin fails | `Verdict.degraded`, `score_is_placeholder` | None |
| R4.13 | Unavailable capabilities clearly identified | PARTIALLY IMPLEMENTED | `degraded`, `coverage: partial`, `model_version` exist; no per-capability status list (OCR, URL, audio, LLM) because those do not exist | `Verdict` | `capabilities: {ocr: unavailable, ...}` field in analysis result |
| R4.14 | Score not described as a calibrated probability unless validated | IMPLEMENTED BUT UNVERIFIED | `call_guard/model.py` docstring: "NOT a real-world probability"; README banner "no number below is a real-world performance claim". No test asserts UI wording; console wording not audited line by line | docs, `Insights` | Add a console test that the result card carries the caveat |

### R5 Citizen dashboard

| ID | Requirement | Status | Evidence | APIs / models | Minimal remaining work |
|---|---|---|---|---|---|
| R5.1 | Real-API-backed stats | MISSING | `routes/Citizen.tsx` is an assess page only; Command/Insights show simulator metrics | none | `GET /complaints/stats` and a dashboard page |
| R5.2 | Complaint history / status | MISSING | No endpoint or page | none | WP-3, WP-5 |
| R5.3 | Complaint details and analysis | MISSING | None | none | WP-3, WP-5 |
| R5.4 | Indicators / explanations | PARTIALLY IMPLEMENTED | `VerdictCard.tsx` shows reasons for a pasted message (Citizen.test.tsx passes); not attached to a stored complaint | `/assess` | Render stored analysis |
| R5.5 | Alerts with read/unread | MISSING | No alert model for citizens (txn_guard holds and ledger alerts are bank-side) | none | `alerts` table, `PATCH /alerts/{id}/read` |
| R5.6 | New complaint | MISSING | No form; backend `/report` not used by console | none | WP-5 |
| R5.7 | Suspicious-content analysis | COMPLETE AND VERIFIED | `routes/Citizen.tsx` calling `/api/citizen/assess`; vitest 172 pass; backend 71 pass | `/api/citizen/assess` | None |
| R5.8 | Evidence management | MISSING | None | none | WP-4, WP-5 |
| R5.9 | Safety tips and official reporting resources | PARTIALLY IMPLEMENTED | Per-label advice steps (`texts.py:ADVICE`, 4 languages). The helpline "1930" appears only as an advisory-clause regex in `rules.py:83`, not as a resource shown to citizens (grep of console and services) | `Verdict.advice` | Static resources page; owner must confirm official contact details before they are shipped |
| R5.10 | Profile / settings | MISSING | None | none | Profile page keyed to internal user |
| R5.11 | No hardcoded counts, fake alerts, sample data presented as live | PARTIALLY IMPLEMENTED | Console contains `src/mocks/*.json` fixtures for offline/demo mode; all shown data is simulator data. The existing UI states it is a simulator (README banner); each new page must not reuse these | `src/mocks/` | Keep new complaint pages free of mocks; test empty/real responses |
| R5.12 | Helpful empty states with "submit first complaint" button | MISSING | `EmptyState` component exists (`components/common/Panel.tsx:34`) and is used on Command pages, none for complaints | `EmptyState` | Reuse component on new pages |

### R6 Investigator dashboard

| ID | Requirement | Status | Evidence | APIs / models | Minimal remaining work |
|---|---|---|---|---|---|
| R6.1 | Authorised complaint listing and filters | MISSING | No complaint store | none | `GET /complaints` (investigator scope) with filters |
| R6.2 | Details and evidence access per permissions | MISSING | None | none | Evidence download endpoint with role check and access audit |
| R6.3 | Risk analysis and indicators | PARTIALLY IMPLEMENTED | Analyst console shows hold detail with reasons for held transactions (`components/analyst/HoldDetail.tsx`, `Analyst.test.tsx` passes) | `/api/holds` | Complaint analysis view |
| R6.4 | Fraud DNA similarity search via Qdrant | MISSING | No Qdrant code or client (`qdrant-client` not in venv). Closest feature: antibody hub exact keyed-hash match plus Bloom filter, which is not similarity | none | WP-6: embedding + `qdrant-client` `:memory:` |
| R6.5 | Related-complaint / entity relationship analysis via Neo4j | MISSING | No Neo4j driver or code; `docs/architecture.md:109,269` records the decision to use in-memory networkx instead | none | See R6.5b and section 3 recommendation |
| R6.5b | Same capability without Neo4j (functional equivalent) | PARTIALLY IMPLEMENTED | `graph_twin` (networkx, `GraphStore` swap surface in `store.py`), ring detection and takedown simulation; tests pass; operates on transactions, not complaints | `/rings*` | Entity-link query over complaint identifiers using SQL joins on shared normalised identifiers |
| R6.6 | Geographic hotspots via PostGIS | MISSING | `geo_intel` uses a static 27-city table and SQLAlchemy; no geometry types; compose image is `postgis/postgis:16-3.4` but no `CREATE EXTENSION` exists (`infra/postgres/init.sql`); local Homebrew PG14 has no PostGIS files | `/hotspots`, `/points`, `/cities` | City-level hotspots over complaints need no PostGIS; PostGIS only for point/radius queries |
| R6.7 | Investigation status and reports | PARTIALLY IMPLEMENTED | Evidence ledger cases and signed export packages (`evidence_ledger/package.py`, `/cases`, `/packages/{id}`); 184 passed. Cases are created from holds, not complaints | `/cases` | Link cases to complaint ids; status field |
| R6.8 | Alerts and stats | PARTIALLY IMPLEMENTED | Command centre metrics for simulator run; not complaint-based | demo_server `/api/demo/state` | Complaint stats endpoint |
| R6.9 | Real queries; never invent links | PARTIALLY IMPLEMENTED | Existing graph is built from observed transactions only; no complaint links exist to invent | graph_twin | Enforce at the new layer with tests |
| R6.10 | Distinguish observed shared identifiers vs inferred relationships vs confirmed findings; similarity is not proof | MISSING | No such three-way typing in graph_twin edges or API (grep for inferred/observed/confirmed finds only takedown notes) | none | `link_type in {observed_shared_identifier, inferred_similarity, confirmed}` with UI labels and the "similarity is not proof" notice |

### R7 Admin dashboard

| ID | Requirement | Status | Evidence | APIs / models | Minimal remaining work |
|---|---|---|---|---|---|
| R7.1 | User listing | MISSING | No users table | none | `GET /admin/users` after WP-2 |
| R7.2 | Role management | MISSING | None | none | `PATCH /admin/users/{id}/role` |
| R7.3 | Activation / deactivation (only if supported by auth architecture) | MISSING | No `is_active` concept (grep); supportable once an internal users table exists, and Clerk-side bans would need to be mirrored | none | `is_active` column checked on every request |
| R7.4 | System / integration health | PARTIALLY IMPLEMENTED | `/healthz`, `/readyz`, `/metrics` on each service (`svckit/health.py`), Prometheus config in `infra/prometheus.yml`; no aggregated admin page and no integration (Qdrant/Neo4j/Ollama) probes | per-service endpoints | `GET /admin/health` aggregating adapter probes |
| R7.5 | Audit information | PARTIALLY IMPLEMENTED | Evidence ledger hash-chained signed log for antibody/hold decisions (verified by 184 tests); no audit for logins, role changes or evidence access | `/ledger/entries`, `/verify` | Append auth/role/evidence-access events to the same ledger or an `audit_log` table |
| R7.6 | Backend-enforced admin permissions | COMPLETE AND VERIFIED | `require_role` plus matrix tests (admin-only paths in demo_server `test_every_endpoint_by_role`) for existing endpoints | `gateway/rbac.py` | Apply to new admin routes |
| R7.7 | Sensitive role changes audited | MISSING | No role changes exist | none | Part of WP-7 |

### R8 Data and ML

| ID | Requirement | Status | Evidence | APIs / models | Minimal remaining work |
|---|---|---|---|---|---|
| R8.1 | Dataset inspection | PARTIALLY IMPLEMENTED | Only generated data: `sim_engine/calls.py` scripted scam/benign call chunks (EN/HI/TA/BN), hand-authored scam/benign lines (`call_guard/authored.py`, 522 lines), hard negatives (`hardneg.py`), simulated transactions (`txn_guard/simdata.py`). No CSV/parquet/JSONL data files in the repo (find). No complaint corpus, no category labels | n/a | Section 5 |
| R8.2 | Preprocessing | COMPLETE AND VERIFIED | `citizen_shield/policy.py:normalise_for_scoring` (truncation, script coverage), `call_guard` min-words filter; tests pass | n/a | None |
| R8.3 | Artifacts | COMPLETE AND VERIFIED | `clf-v1.joblib` and `gbm-v1.joblib`; loaded only if SHA-256 pin and sklearn version match and a smoke test passes (`model.py`); tests pass | n/a | None |
| R8.4 | Evaluation | COMPLETE AND VERIFIED | Reliability tables/ECE stored in artifacts; `call_guard/tests/test_independent_eval.py` and `txn_guard/tests/test_eval.py` pass; `txn_guard/README.md` reports held-out precision, recall, ECE | n/a | None |
| R8.5 | Text-only complaints analysable | COMPLETE AND VERIFIED | Same as R4.1 | `/assess` | None |
| R8.6 | Missing phone/URL/transaction/location supported | PARTIALLY IMPLEMENTED | Text path needs none of them (verified); txn_guard cold start yields neutral features (`features.py` docstring; `test_features.py` passes); phone/URL are not features anywhere | n/a | Features for these must default to "unknown", not zero or fraud |
| R8.7 | Category-appropriate features | MISSING | Models are binary scam/benign for call language and transaction anomaly; no UPI/phishing/investment/job/social-media features or labels | n/a | Section 5 |
| R8.8 | Missing data not treated as fraud evidence | PARTIALLY IMPLEMENTED | Cold-start neutral features (verified); but `is_blank` returns label `safe` (not "unknown") and short texts are only flagged `uncertain` | n/a | Return `insufficient_information` rather than `safe` for empty complaints |
| R8.9 | Train/test separation without leakage | COMPLETE AND VERIFIED | `train.py` disjoint seeds (train 1000, calibration 2000, report 3000) and 60/20/20 split of authored benign lines; frozen independent eval set `tests/data/independent_eval.py`; tests pass. Caveat: train and eval share generator families (README "Simulator only") | n/a | None, but not real-world evidence |
| R8.10 | Suitable metrics on a held-out test set | COMPLETE AND VERIFIED | Precision, recall, FPR, ECE on held-out seeds (txn_guard README; `docs/benchmark_report.md`) | n/a | Re-do for any new category model |
| R8.11 | Synthetic data labelled, not presented as real performance | COMPLETE AND VERIFIED | README banner lines 8-9 and "Simulator only" limitation; `model.py` docstring | docs | Carry the same labelling into complaint-category metrics |

### R9 Databases and stack

| ID | Requirement | Status | Evidence | APIs / models | Minimal remaining work |
|---|---|---|---|---|---|
| R9.1 | PostgreSQL (users, complaints, analyses, evidence metadata, alerts) | PARTIALLY IMPLEMENTED | SQLAlchemy Core stores work on SQLite and Postgres URLs (`citizen_shield/store.py`, hub, ledger, graph, geo). No `users`, `complaints`, `analyses`, `evidence`, `alerts` tables. Postgres integration tests exist but are skipped here (16 skips) | `cs_reports`, `gi_complaints`, ledger tables | New tables via new service; run the Postgres-gated tests against a local server |
| R9.2 | Supabase | MISSING | No reference in code, env example or compose; plain Postgres via SQLAlchemy is used | none | Recommendation in section 4: do not adopt |
| R9.3 | Qdrant | MISSING | No client, no code | none | WP-6 |
| R9.4 | Neo4j | MISSING | No driver, no code; architecture doc records it as removed | none | See section 4 |
| R9.5 | PostGIS | MISSING | Image only in compose; no extension, no geometry columns | none | See R6.6 |
| R9.6 | Redis (cache / rate limit) | COMPLETE AND VERIFIED | Gateway rate limit (`gateway/ratelimit.py`), call_guard sessions, txn_guard history/holds/antibody cache use Redis; tests pass against `fakeredis` (not a real server) | `redis` | Optionally run against the local Redis on 6379 (not done) |
| R9.7 | Ollama / Qwen2.5 3B | MISSING | See R4.10 | none | WP-6 |
| R9.8 | FastAPI | COMPLETE AND VERIFIED | All services are FastAPI 0.142.2; suites pass | n/a | None |
| R9.9 | Next.js | MISSING | `web-console/package.json`: Vite 8, React 18, react-router-dom 6, vitest; no `next` | n/a | Recommend not migrating (section 4) |

### R10 Quality gates (tests the owner wants)

| ID | Test wanted | Exists today? | Passes? | Status | Notes |
|---|---|---|---|---|---|
| R10.1 | Citizen registration and login | Login only: `gateway/tests/test_auth.py` | Yes (login); registration absent | PARTIALLY IMPLEMENTED | No registration code to test |
| R10.2 | Approved investigator login | No | n/a | MISSING | Needs R2.5 |
| R10.3 | Rejection of unauthorised investigator emails | No | n/a | MISSING | Needs R2.6 |
| R10.4 | Backend role enforcement | Yes: `gateway/tests/test_proxy.py:test_rbac_matrix`, `demo_server/tests/test_auth.py:test_every_endpoint_by_role` | Yes | COMPLETE AND VERIFIED | Extend matrix with new routes |
| R10.5 | Complaint with mandatory fields only | No | n/a | MISSING | |
| R10.6 | Complaint with optional fields | No | n/a | MISSING | |
| R10.7 | Text-only analysis | Yes: `citizen_shield/tests/test_assess.py`, `test_api.py` | Yes | COMPLETE AND VERIFIED | Pasted text, not a stored complaint |
| R10.8 | Missing optional fields | No | n/a | MISSING | |
| R10.9 | Ownership / privacy | Privacy yes (`demo_server/logredact`, `svckit/pii`, citizen_shield no-text storage tests); ownership no | Privacy tests yes | PARTIALLY IMPLEMENTED | |
| R10.10 | Analysis persistence | No (citizen_shield intentionally persists no text or verdict) | n/a | MISSING | |
| R10.11 | Qdrant / Neo4j / PostGIS integration | No. Existing Postgres-gated tests (hub, ledger, graph, geo) are skipped | 16 skipped | MISSING | |
| R10.12 | Empty dashboard states | Component exists; no complaint-dashboard test | n/a | MISSING | |
| R10.13 | File uploads | No | n/a | MISSING | |

## 3. Environment and external services

Checked read-only on this machine. Values of environment variables were never printed.

| Service | What was found | Determination | Implication |
|---|---|---|---|
| PostgreSQL | `/opt/homebrew/bin/psql`, `postgres`, `pg_ctl` present; Homebrew `postgresql@14 14.21`. Nothing listening on 5432. Compose pins `postgis/postgis:16-3.4`. Python `psycopg2-binary 2.9.13` and `SQLAlchemy 2.1.4` installed | Binaries available, no server running, not started in this audit | Postgres-backed tests (16 skips) are runnable by the owner by starting a local server and exporting `*_TEST_POSTGRES_URL`; status for Postgres persistence is IMPLEMENTED BUT UNVERIFIED, not blocked |
| PostGIS extension | `ls /opt/homebrew/share/postgresql@14/extension` contains no postgis files; `brew list` shows no postgis | Not available locally | PostGIS features are BLOCKED BY CONFIGURATION/EXTERNAL SERVICE (need `brew install postgis` or the compose image); today PostGIS usage is MISSING in code as well |
| Redis | `redis-server` binary present; Homebrew `redis 8.6.1`; a process is listening on localhost:6379 (not touched). Client `redis 8.1.0`, `fakeredis 2.39.0` in venv | Available | Rate limiting and history verified only against fakeredis |
| Neo4j | `neo4j` binary not found; not in Homebrew; no Python driver in venv | Not available | Neo4j work is BLOCKED BY CONFIGURATION/EXTERNAL SERVICE (and the code is MISSING) |
| Qdrant | Not in Homebrew; no listener; `qdrant-client` not installed | Not available | Local `:memory:` mode of `qdrant-client` would work after a pip install (not done here); a real server is BLOCKED |
| Ollama | `which ollama` empty; `~/.ollama` has `config.json` and `cache` only; no models | Not installed | LLM explanations BLOCKED until the owner installs Ollama and pulls Qwen2.5 3B; interface can be built and tested with an `unavailable` fallback |
| Clerk | No `CLERK_*` name in any `.env.example`, compose file or source; no SDK installed. `PyJWT 2.15.1` and `cryptography 50.0.2` are installed, which suffices for JWKS/RS256 verification | No integration exists; instance and keys unknown (could not be determined) | Real Clerk sign-in BLOCKED BY CONFIGURATION/EXTERNAL SERVICE; verification logic can be built and tested with locally generated RSA keys |
| Docker daemon | CLI `/usr/local/bin/docker` 29.4.3, context `desktop-linux`; `docker info` shows client info only (daemon known absent) | No daemon | Compose stack cannot be run; compose-defined services are unverifiable here |
| Node / Next.js | Node and npm present at `/usr/local/bin`; no `next` dependency anywhere | Node available, Next.js absent | Console tests runnable (172 pass); Next.js not part of the repo |
| Supabase | No reference in code, env example or compose | Not used; credentials unknown | Treated as MISSING by design; recommendation is to stay on plain Postgres |

Other listeners seen (not part of the project): 8080 (the demo server, left untouched), 5000 and 7000 (system), 6379 (Redis). Python 3.13.5 in the venv.

Python packages checked in the venv: present, `fastapi`, `httpx`, `psycopg2-binary`, `PyJWT`, `cryptography`, `redis`, `fakeredis`, `scikit-learn`, `SQLAlchemy`. Absent, `qdrant-client`, `neo4j`, any Clerk SDK, `python-multipart`, `alembic`, any OCR or speech package, `ollama`, `geoalchemy2`.

## 4. Architecture fit

The services follow a repeatable pattern: a FastAPI app factory with `svckit.health` probes, SQLAlchemy Core store working on SQLite and Postgres URLs, env-driven config, gateway-trusted headers via shared secret, a keyed-hash/PII policy, and a prefix-to-roles table in the gateway. The console is a React/Vite app with a `Shell`, route components, a typed API client and an `EmptyState` component.

### 4.1 Backward-compatible additions

| Addition | How it fits | Notes |
|---|---|---|
| New `services/complaints` FastAPI service (users, complaints, analyses, evidence metadata, alerts, audit) | Same app-factory, config, store and health patterns as `citizen_shield`; new gateway prefix `complaints` in `ROUTE_ROLES`; calls `citizen_shield` or `call_guard` `/score` over HTTP with the existing degraded semantics | Does not touch existing services or contracts; add new contract models in `scam_contracts` only if events are needed |
| Identity layer: JWKS-verified external token mapped to internal `users` row, roles from DB | Extends `gateway/auth.py` with an additional verifier next to HS256; keep HS256 demo path for the simulator | RS256 only for the new path; algorithm allowlist must stay explicit |
| Console pages: citizen dashboard, complaint form, complaint detail, investigator list/detail, admin pages | New routes in `App.tsx` and `nav.ts`, reuse `Panel`, `EmptyState`, `api` client, `VerdictCard` | Existing demo pages stay; no new framework |
| Adapters for Qdrant, Neo4j (if wanted), PostGIS (if wanted), Ollama behind a `Protocol` with `health()` and explicit `unavailable` results | Mirrors `GraphStore` swap surface and `Bus`/`UserStore` protocols already used | Each adapter returns `{"status": "unavailable", "reason": ...}` rather than raising into the analysis path |
| Evidence ledger reuse for audit | Append role-change and evidence-access events to the existing signed ledger via its outbox pattern | Avoids a second audit mechanism |
| Alembic or similar migrations | Not present today (stores call `create_all`) | Optional; flag as a decision |

### 4.2 Conflicts with "preserve existing UI/architecture" and recommendations

| Brief item | Conflict | Recommendation |
|---|---|---|
| Next.js | The console is Vite + React 18 + react-router, 26 test files, Playwright e2e; a migration would rewrite the build, routing and tests and break the demo server's static serving (`demo_server` serves `web-console/dist`) | Do NOT migrate. Keep Vite. If server-side rendering is truly needed later, treat it as a separate decision |
| Supabase | Repo uses plain Postgres via SQLAlchemy and a compose Postgres; Supabase adds a hosted dependency and its own auth, overlapping Clerk | Do NOT adopt. Use plain Postgres (local or managed) via SQLAlchemy; Clerk for identity |
| Neo4j | Repo explicitly dropped it (`docs/architecture.md`), graph_twin has a `GraphStore` swap surface; Neo4j cannot be run or tested here | Keep the SQL/networkx approach for complaint linking first; add a Neo4j adapter only if the owner supplies an instance and wants Cypher |
| PostGIS | Compose already uses a PostGIS image; geo is city-code-level by privacy design (location is "city-level or coarser by construction") | Keep city-level hotspots; add PostGIS only for optional point queries and keep geometry nullable; do not store precise coordinates without an owner privacy decision |
| Qdrant | New dependency, nothing to conflict with | Add behind an adapter; `qdrant-client` `:memory:` for tests |
| Ollama / Qwen2.5 3B | New; analysis must not depend on it | Optional explanation layer only; never changes the score; clearly labelled "generated explanation" |
| Roles CITIZEN/INVESTIGATOR/ADMIN vs officer/analyst/citizen/admin | Four existing roles used by gateway tests and console | Map INVESTIGATOR to {analyst, officer} for the new complaint routes rather than renaming existing roles |
| Existing simulator-first UI, demo-login role picker | Brief requires real sign-in and separate entry points | Keep the demo path available only when an env flag says so; add a real sign-in route beside it |
| Treating this repo as "Digital Suraksha" | Product name, README and docs are ScamShield Immune and simulator-bound | Owner decision (section 6) |

## 5. Proposed implementation plan (not implemented)

Sizes: S under half a day, M one to two days, L several days. Each package is backward compatible and testable without external services unless stated.

| WP | Scope | Size | Testable locally without external services | Blocked until owner supplies |
|---|---|---|---|---|
| WP-1 | Data model and `services/complaints` skeleton: `users`, `complaints` (mandatory and nullable optional fields), `analyses`, `evidence`, `alerts`, `audit_log`; owner-scoped CRUD; health probes; gateway prefix and RBAC matrix rows | M | SQLite via SQLAlchemy; `pytest -W error`; ownership and null round-trip tests; Postgres-gated twin of each test | A running Postgres only for the gated tests |
| WP-2 | Identity: verifier for Clerk-compatible JWT (RS256, JWKS, issuer/audience/exp checks, `email_verified` required), internal user mapping, `INVESTIGATOR_EMAIL_ALLOWLIST` and `ADMIN_BOOTSTRAP_EMAIL` env handling, friendly 403 for unapproved investigators, role never read from client input | M | Locally generated RSA keys and a fake JWKS fixture; tests for approved, unapproved, unverified-email, tampered-token and registration cannot escalate | Clerk instance (issuer URL, JWKS URL, publishable key name) and the console sign-in widget configuration |
| WP-3 | Complaint submission and analysis orchestration: `POST /complaints`, `GET /complaints`, `GET /complaints/{id}`; analysis composed of independent steps (text via call_guard, optional URL lexical checks, optional transaction heuristics) each returning `skipped`/`unavailable`; output mapped to 0-100, level, category, indicators, confidence, timestamp, actions; persisted | L | Fake scorer transport (pattern in `citizen_shield/tests`), `uncertain`/`degraded` propagation tests, never-BENIGN-on-failure test | Nothing |
| WP-4 | Evidence upload: multipart, size cap, magic-byte MIME allowlist, sha256, random storage names, owner/investigator-only download, per-file status response | M | tmp-dir storage; negative tests (oversize, spoofed MIME, traversal, foreign owner) | Nothing (needs `python-multipart` installed, an owner approval for a new dependency) |
| WP-5 | Console: citizen dashboard (stats, history, detail, alerts read/unread, new complaint, analyse content, evidence, tips/resources, profile), empty states with "submit first complaint" | L | vitest with mocked API client; no fixture data shown as live | Official resource list text confirmed by owner |
| WP-6 | Optional adapters: Qdrant "Fraud DNA" similarity (embedding method to be chosen; TF-IDF vectors are feasible offline), Ollama explanation client with timeout and `unavailable` fallback, optional Neo4j and PostGIS adapters; admin health aggregation | L | `qdrant-client` `:memory:` mode; stub HTTP transport for Ollama; contract tests for `unavailable` states | Real Qdrant, Neo4j, PostGIS servers; Ollama install and Qwen2.5 3B pull |
| WP-7 | Investigator and admin dashboards: filters, entity links typed observed/inferred/confirmed, hotspots (city-level SQL), case status, user list, role change, activation, audit view; role changes written to audit/ledger | L | SQLite, vitest, matrix tests | Nothing for the SQL versions |
| WP-8 | Complaint-category dataset and model: synthetic, clearly labelled generator for UPI, phishing, OTP, bank, investment, job, social-media texts with benign look-alikes; category classifier with held-out evaluation; model card text | L | Fully offline | Owner decision on dataset policy (section 6) |

Suggested order: WP-1, WP-2, WP-3, WP-4, WP-5, WP-7, WP-6, WP-8 (WP-8 can run in parallel earlier if the category output is needed in WP-3).

## 6. Risks and decisions needed from the owner

1. **Clerk instance and keys.** No integration exists. Decide whether to use Clerk (needs instance, issuer, JWKS URL, publishable key name; secret key must stay server-side) or a local identity provider. Without it, real sign-in cannot be verified end to end; only the verification logic can be tested.
2. **Is this repo the Digital Suraksha codebase?** The repo, docs and UI are branded ScamShield Immune and explicitly simulator-only. Adding complaints turns a simulator demo into a system that holds citizen data; confirm naming, branch strategy (current branch is `build/implementation`, not `main`) and whether the simulator demo must stay intact.
3. **Next.js vs the existing console.** Recommended: stay on Vite/React (section 4.2).
4. **Supabase vs plain Postgres.** Recommended: plain Postgres; confirm.
5. **Neo4j and PostGIS necessity.** Both unavailable locally. Confirm whether SQL-based linking and city-level hotspots are acceptable for the first release.
6. **Ollama model installation.** Confirm the owner will install Ollama and pull Qwen2.5 3B (about 2 GB); until then explanations are "unavailable".
7. **Datasets for category classification.** The repo has no real or labelled complaint data (no data files; only simulator call transcripts, authored scam/benign lines and simulated transactions). The simulator covers digital-arrest and mule-account flows only. A synthetic dataset for UPI fraud, phishing, OTP, bank fraud, investment, job and social-media fraud would need: template-plus-slot generators per category with multilingual variants (at least EN/HI as in the existing scorer), benign look-alikes (bank OTP notices, genuine job offers, real investment newsletters), noise and missing-field variation, disjoint seeds for train/calibration/test, a frozen independent evaluation set written separately from the generator, and a visible "synthetic, not real-world performance" label in docs, model card and UI. Alternatively the owner may supply anonymised real complaints under an explicit data-handling policy.
8. **Privacy and retention.** The current design stores no message text. Complaints necessarily store descriptions, phone numbers, UPI IDs and evidence files; decisions are needed on retention, encryption at rest, access logging and deletion on request.
9. **Score semantics.** The 0-100 score from rules plus a simulator-calibrated classifier must keep the "ranking score, not probability" caveat until validated on real data; the owner should accept that wording.
10. **Existing demo risks to resolve before any real deployment.** `POST /api/auth/demo-login` grants any role to any caller; the gateway logs generated demo passwords at WARNING (`gateway/auth.py:106`); both are acceptable in a simulator but not alongside real citizen data.
11. **Official resources.** Helpline numbers and portal URLs shown to citizens must be confirmed by the owner; the repo currently does not display them.
12. **Test coverage in CI.** Postgres-gated tests are skipped by default; any new Postgres-specific behaviour should be run against a real server before release.
