# Project requirements

Phase 0 deliverable. Written 2026-10-15 against the repository as it exists on branch
`build/implementation` (HEAD `91ebfeb`). It records the target requirements for the AI-powered
Digital Public Safety Intelligence platform and, for each one, the status established by
inspecting the code. Current status in one place: [PROJECT_STATUS.md](../PROJECT_STATUS.md).

Status labels used in this document and across the Phase 0 set:

| Label | Meaning |
|---|---|
| **Verified** | Implemented, with automated tests that were run and passed, and (where stated) checked by hand |
| **Unverified** | Code exists, but it has not been independently exercised (for example only an agent ran the test, or it needs a service that is not available here) |
| **Partial** | Some of the requirement exists; named parts are missing |
| **Planned** | Not implemented; scheduled in [DEVELOPMENT_ROADMAP.md](DEVELOPMENT_ROADMAP.md) |
| **Blocked** | Needs credentials, an external service or software that is not available in this environment |
| **Limitation** | A known weakness of something that exists |

## 1. Problem statement

Digital-arrest scams (callers impersonating the CBI, ED, Customs or police who keep a victim on a
call and demand transfers to a "safe account"), UPI and banking fraud, phishing links, OTP theft
and investment, job and social-media scams cause large losses in India (figures quoted from the
challenge brief in [challenge-statement.md](challenge-statement.md); not independently verified).
Reporting is reactive: complaints arrive after money has moved, sit in separate queues, and the
links between complaints (a shared UPI ID, phone number, domain or script) are found late, if at
all.

## 2. Objectives

1. Let citizens check a suspicious message, link or conversation and get an explained risk
   indication before they act.
2. Let citizens register complaints, with whatever details they have, attach evidence and follow
   the status.
3. Give consent-based, on-device-microphone Live Protection that flags scam cues in a conversation
   as it happens (incremental, a few seconds behind speech).
4. Give authorised investigators a queue of complaints with explained analysis and, later, cross-
   complaint intelligence (similar cases, shared identifiers, geographic patterns).
5. Keep every decision explainable and every consequential action human-reviewed and audited.
6. Keep the existing simulator (call → hold → antibody → second bank blocks → signed evidence) as
   a clearly labelled demonstration of the federated "scam immune system" idea.

## 3. Target users

| User | Role in the system | How they get the role |
|---|---|---|
| Citizen | `CITIZEN` | Signs in (Clerk, or local development sign-in); default for every new account |
| Investigator | `INVESTIGATOR` | Verified email listed in the server-side `INVESTIGATOR_ALLOWLIST` (entry flow), or granted by an admin |
| Administrator | `ADMIN` | Verified email in `ADMIN_BOOTSTRAP_EMAILS`, or granted by an existing admin |
| Simulator viewer | demo roles `officer`, `analyst`, `citizen`, `admin` (simulator only) | Demo login on the simulator; these roles never grant anything in the complaints service |

Banks and law-enforcement systems are represented only inside the simulator (two simulated banks).
No real external system is integrated.

## 4. Functional requirements and current status

Module letters follow the target specification. "Where" names the implementing code.

### Module A: fraud detection (text, links, screenshots, audio, complaints)

| ID | Requirement | Status | Where / evidence |
|---|---|---|---|
| A1 | Analyse pasted text, chat and call notes | **Verified** | `POST /api/complaints/analyze`; complaints tests; checked by hand in the browser 2026-10-15 |
| A2 | Analyse complaint descriptions and persist the result | **Verified** | `POST /complaints/{id}/analysis`, `analyses` table; tests; checked by hand 2026-10-14 |
| A3 | Indicators: impersonation, arrest threats, urgency, money demands, secrecy, OTP requests, payment instructions | **Verified** | `call_guard` rules reused by complaints and live audio; rule tests |
| A4 | Risk score 0-100, level, category, indicators, evidence, explanation, actions | **Verified** | Analysis output; score labelled an uncalibrated heuristic everywhere |
| A5 | Distinguish indicators from confirmed fraud | **Verified** | Wording in API and UI ("indicators, not a confirmation of fraud"); no "confirmed" state set by AI |
| A6 | Suspicious URL analysis | **Partial** | Offline lexical heuristics only (scheme, TLD list, brand-in-host, path words). No page fetch or reputation lookup, by design |
| A7 | Screenshot analysis (OCR) | **Blocked** | No OCR engine installed (Tesseract absent); reported `unavailable` |
| A8 | Audio recording analysis (uploaded files) | **Planned** | faster-whisper is installed and used by Live Protection, but uploaded audio evidence is not transcribed. The complaints service currently reports this as "no transcription engine installed", which is inaccurate (see section 9) |
| A9 | Call metadata from lawful integrations | **Planned / out of current scope** | No telecom integration exists |
| A10 | Provenance: rule vs ML vs LLM output kept separate | **Verified** | Every indicator/explanation/action carries `source` and `method` (Phase B, unpushed) |
| A11 | Never return a misleading LOW on failure or missing input | **Verified** | `INSUFFICIENT_INPUT`, `PARTIAL`, `UNAVAILABLE` statuses; failure-path tests |

### Module B: Live Protection

| ID | Requirement | Status | Where / evidence |
|---|---|---|---|
| B1 | Explicit consent before microphone access | **Verified** | Consent checkbox gates Start; scripted Chromium check 2026-10-15: 0 microphone requests before consent, exactly 1 after Start |
| B2 | Start, pause, resume, stop | **Verified** | `web-console` live monitor; vitest; Stop ends the media track (checked 2026-10-15) |
| B3 | Incremental audio processing and STT with timestamps | **Verified** | `services/live_audio`, ~4 s 16 kHz WAV chunks, local faster-whisper; 104 tests + 4 real-Whisper tests (2026-10-14) |
| B4 | Timestamped indicators, updating risk, warnings at thresholds | **Verified** | Same suite |
| B5 | Permission, processing and service-error states | **Verified** (generic denial text) | Denied permission shows "The microphone could not be started."; it does not distinguish "denied" from "no device" |
| B6 | Never claim access to ordinary cellular-call audio | **Verified** | UI tells users to use speakerphone near the device's microphone |
| B7 | True streaming recognition | **Limitation** | Chunked, near-real-time, stated in the UI and API (`mode: incremental_chunked`) |

### Module C: fraud network intelligence

| ID | Requirement | Status | Where / evidence |
|---|---|---|---|
| C1 | Graph of complaints and observed entities (phones, UPI IDs, accounts, URLs/domains, social accounts) | **Planned** | Complaints store the identifiers; no complaint graph yet |
| C2 | Shared identifiers, candidate clusters, evidence per relationship, observed vs inferred vs confirmed | **Planned** | |
| C3 | Ring detection and takedown simulation | **Verified, simulator only** | `services/graph_twin` and demo-server `ring` module operate on simulated transactions, not complaints |
| C4 | Neo4j | **Planned (optional)** | Not installed; NetworkX/SQL first per decision |

### Module D: geospatial intelligence

| ID | Requirement | Status | Where / evidence |
|---|---|---|---|
| D1 | Store valid complaint coordinates; tolerate missing/invalid ones | **Verified** | `complaints.latitude/longitude` optional with range checks |
| D2 | Map, filters (date, category, risk, location), clusters, time comparison from persisted complaints | **Planned** | |
| D3 | City-level hotspots | **Verified, simulator only** | `services/geo_intel`, demo `/api/geo/hotspots` on simulated data |
| D4 | PostGIS | **Planned (optional) / Blocked locally** | Local Postgres 14 has no PostGIS extension |

### Module E: Citizen Fraud Shield

| ID | Requirement | Status | Where / evidence |
|---|---|---|---|
| E1 | Registration and sign-in | **Verified (local dev sign-in) / Blocked (Clerk)** | Local email + code sign-in checked by hand; Clerk only tested with local keys and a mocked frontend module |
| E2 | Message and URL analysis | **Verified** | `/check` page |
| E3 | Screenshot, document and audio uploads | **Verified (storage only)** | Evidence upload with type sniffing and size limits; contents not analysed |
| E4 | Complaint registration with optional details | **Verified** | Required: category, title, description; all else optional and stored as NULL |
| E5 | Complaint history, alerts, status tracking | **Verified** | Citizen dashboard; alerts with read state |
| E6 | Safety education and official resources (1930, cybercrime.gov.in) | **Verified** | Dashboard section |
| E7 | WhatsApp / IVR | **Planned** | Not configured; nothing claims it exists |
| E8 | Languages | **Partial** | Evaluated: English, Hindi, Hinglish (speech). Simulator message check has en/hi/ta/bn templates. No 12-language claim |

### Module F: investigator command centre

| ID | Requirement | Status | Where / evidence |
|---|---|---|---|
| F1 | Complaint queue, search, filters, risk prioritisation | **Verified** (Playwright run by agent only) | `/investigator`; `GET /complaints` with filters and sort |
| F2 | Complaint and evidence review, AI explanations | **Verified** | Shared detail page with staff fields |
| F3 | Status updates with notes | **Verified** | `PATCH /complaints/{id}/status` with transition rules and history |
| F4 | Investigation notes separate from status history | **Planned** | |
| F5 | Fraud DNA similarity, network graph, geo intelligence on complaints | **Implemented** (Phases 8, 9, 10; staff only; opt-out enforced) | Synthetic evaluation only for Fraud DNA |
| F6 | Case reports and exports | **Planned** | The simulator's signed evidence packages are a separate, simulator-only feature |
| F7 | Per-case audit history | **Partial** | Status history per complaint exists; admin audit covers role and evidence actions |

### Module G: administration

| ID | Requirement | Status | Where / evidence |
|---|---|---|---|
| G1 | User list, role assignment/removal, activation | **Verified** | `/admin/users`, role and active endpoints; last-admin guard |
| G2 | Investigator access control | **Verified** | Allowlist entry flow, per-request re-evaluation, lapse handling |
| G3 | Service health and integration status | **Verified** | `/admin/health` reports configured/unavailable honestly |
| G4 | Audit of administrative actions | **Verified** | `admin_audit` table and `/admin/audit` |
| G5 | Authorised configuration management | **Planned** | Configuration is environment-only today |

## 5. Non-functional requirements

| Area | Requirement | Status |
|---|---|---|
| Security | Backend-enforced authorisation on every protected endpoint; roles never taken from the client | **Verified** (tests) |
| Security | No secrets, tokens, transcripts or personal data in logs | **Verified** for complaints/live audio log paths by tests; the dev sign-in deliberately prints email + code to the terminal (**Limitation**) |
| Privacy | Raw audio never stored; transcripts in memory only | **Verified** (live audio) |
| Privacy | Evidence encrypted at rest, malware-scanned, retention enforced | **Not implemented** (see section 9) |
| Reliability | External services (Ollama, Whisper, Clerk JWKS) time out and fail closed or report unavailable | **Verified** by tests with fakes; Ollama also live-tested 2026-10-15 |
| Explainability | Every score shown with its sources; LLM text labelled and grounded | **Partial**: grounding checks miss some errors (section 9) |
| Accessibility | Keyboard, focus, ARIA live regions, risk not by colour alone, 360 px | **Unverified** independently (axe checks run by agent in Playwright) |
| Performance | Live-audio chunk processing ~0.7-1.3 s per 4 s chunk on this Mac (Whisper base, int8) | **Verified** 2026-10-14 |
| Portability | SQLite locally, Postgres in Docker | **Verified** (complaints suite on a disposable Postgres 14, run by agent) |
| Deployability | Docker Compose stack | **Unverified**: no Docker daemon here; Dockerfiles only statically checked |

## 6. Scope and exclusions

In scope: modules A-G as above, on synthetic and user-entered data.

Excluded:
* Counterfeit currency detection (dropped by decision; may return as a separate future module).
* Automatic interception of ordinary cellular calls (no lawful, platform-supported route exists in
  a browser).
* Paid cloud AI APIs (the free Gemini API was considered and rejected on privacy grounds; see
  [TECHNOLOGY_DECISIONS.md](TECHNOLOGY_DECISIONS.md)).
* Any automatic accusation or "confirmed fraud" status set by AI.
* Real bank, telecom or police integrations.

## 7. Success criteria

A feature counts as complete only when its frontend workflow exists, its backend endpoint works,
its data persists where required, authorisation is enforced, success and failure paths are
handled, automated tests pass, and it has been verified against real services or is labelled
blocked. Project-level criteria:

1. A citizen can sign in, check a message, register a complaint with only the three required
   fields, attach evidence and see an explained analysis. (Met.)
2. An allowlisted investigator can review and progress complaints; a non-allowlisted email is
   refused with a clear message. (Met; investigator flow verified by an agent-run Playwright test.)
3. Live Protection works only after consent and stops cleanly. (Met.)
4. Cross-complaint intelligence (similar cases, shared identifiers, hotspots) is derived only from
   persisted complaints, with evidence for every link. (Met: Phases 8-10; similarity and
   shared identifiers are leads, not proof.)
5. No dashboard number, chart or result is hard-coded or fabricated; synthetic data is labelled.
   (Met for the complaint paths; simulator numbers are labelled SIMULATION.)

## 8. Assumptions and external dependencies

| Dependency | State on 2026-10-15 | Effect |
|---|---|---|
| Clerk | No keys configured | Production sign-in untested; local development sign-in used instead |
| Ollama + qwen2.5:0.5b | Installed and running at 127.0.0.1:11434 (started from a session, not a login service) | Optional explanations; quality is weak (section 9) |
| faster-whisper (tiny/base cached) | Installed in the venv | Live Protection STT works offline |
| Tesseract | Not installed | OCR blocked |
| Qdrant, sentence-transformers, torch | Installed 2026-10-19 (qdrant-client 1.19.1, sentence-transformers 6.1.0, torch 2.14.1 CPU); the cached multilingual MiniLM model is used offline | Fraud DNA (Phase 8) |
| Neo4j | Not installed | Optional |
| PostGIS | Not available in local Postgres 14 | Optional; the Docker image `postgis/postgis:16-3.4` has it but is untested here |
| Docker daemon | Not available | Compose stack untested |
| `gh` CLI | Not installed | CI status read from the public Actions page and API instead; passing since 2026-10-16 (`7b97731`) |

Assumptions: the project is a final-year prototype, not a production deployment; all model training
data is synthetic; the person running it locally is the developer.

## 9. Known findings to track (not fixed in Phase 0)

1. **LLM grounding gap.** On 2026-10-15 the local qwen2.5:0.5b explanation said "the caller is
   under 'digital arrest'" (the victim is the one threatened) and passed the grounding checks. The
   text is labelled "may be imprecise" and cannot change score, level or category, but the
   role-inversion check needs to catch this wording.
2. **Live-audio explainer is not grounded.** `services/live_audio` still uses its original Ollama
   explainer (default model `qwen2.5:3b`, no grounding checks). It is off unless `OLLAMA_URL` is
   set.
3. **Inaccurate capability message.** Complaint analysis reports audio transcription as
   "unavailable (no transcription engine installed)" although faster-whisper is installed; the
   accurate statement is "not connected to complaint evidence yet".
4. **Evidence storage.** Files are stored on local disk under random names, unencrypted, without
   malware scanning. A storage interface exists so object storage can be added.
5. **Development sign-in.** Prints the email and one-time code to the server terminal; rate limits
   are per process and per client IP. The standalone complaints service defaults to `clerk` mode,
   but demo-server switches to `local` mode whenever Clerk is not configured, and the
   `demo-server` service in `infra/docker-compose.yml` does not set `COMPLAINTS_AUTH_MODE`. There
   is no guard that refuses local mode in a production configuration.
6. **No retention enforcement.** Nothing deletes complaints, evidence or analyses on a schedule.
7. **Server binding.** `python -m demo_server` binds `0.0.0.0` unless `HOST` is set (the compose
   file publishes it on 127.0.0.1 only).
8. **Microphone denial message** does not distinguish "permission denied" from "no microphone".
