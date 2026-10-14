# Implementation plan (Digital Fraud Detection and Cybercrime Intelligence)

Date: 2026-10-14. Built on `PROJECT_GAP_ANALYSIS.md` (written before steps B1/B2a and the live
microphone work; rows R2.x, R3.x, R5.5, R7.1-R7.3 and R10.1-R10.13 have backend implementations
since, see the `services/complaints` README) and on an inspection of the repository today.

## Where things stand (inspected, 2026-10-14)

| Area | Backend | Frontend | Notes |
|---|---|---|---|
| Identity, roles (CITIZEN/INVESTIGATOR/ADMIN), allowlist, bootstrap, lapse | `services/complaints` (Clerk RS256 verifier, demo HS256 verifier), tested | **none** (console has demo role cards only) | Live Clerk unverified: no keys. No local sign-up path |
| Complaints CRUD, status history, pagination, filters, ownership | done, tested | **none** | Bank details (bank name, account, IFSC) missing |
| Evidence upload, validation, download | done, tested | **none** | No owner delete; no versioned migrations (create_all + one ad-hoc ALTER) |
| Analysis pipeline v2 (rules, call-guard ML, category model, URL heuristics, Ollama optional) | done, tested | **none** | OCR, audio-file transcription: unavailable |
| Alerts (read/unread), stats, admin users/roles/audit/health | done, tested | **none** | Profile/privacy settings endpoints missing |
| Live microphone (consent, chunked local Whisper) | done, tested | done | Incremental, not streaming |
| Fraud DNA (Qdrant), network (Neo4j), hotspots (PostGIS) | **missing** for complaints | **none** | Servers not installed locally |
| Reports/exports for complaints, investigation notes | **missing** | **none** | |
| Complaints API reachable from the console on :8080 | **not mounted** in demo_server | | |

## Dependencies

Identity and the console's API wiring (A) block every dashboard. Persisted complaints and
analyses (A, B) feed Fraud DNA and the graph (D), which feed hotspots, alerts and reports (E).
Frontend pages are built alongside each backend phase so every feature meets the acceptance
criteria (frontend workflow + endpoint + persistence + auth + tests).

## Phases (in order; one at a time, each verified before the next)

**Phase A: identity, RBAC, database, complaints, evidence (now).**
1. Backend: versioned migrations (`schema_migrations`), bank-detail columns, profile + privacy
   settings (`GET/PATCH /me`), owner evidence delete, mount the complaints API into demo_server at
   `/api/complaints`, and a local identity provider for machines without Clerk keys (email +
   one-time code shown in the server terminal; dev only, off in Clerk mode, labelled).
2. Frontend: Clerk sign-in when `VITE_CLERK_PUBLISHABLE_KEY` is set, local sign-in otherwise;
   Citizen / Investigator entry; citizen dashboard (stats, history, empty state, new complaint form
   with optional fields, complaint detail with status history, evidence manager, alerts, safety and
   official-reporting resources, profile/privacy); investigator queue with filters and status
   updates; admin users/roles/audit/health. Existing simulator pages stay, labelled SIMULATION.

**Phase B: analysis surfaced end to end.** Analysis UI on complaints and a stand-alone checker,
rule/ML/LLM provenance shown, failure states; category model metrics page.

**Phase C: audio files, OCR, URLs.** Whisper transcription of uploaded audio evidence (reuse
`live_audio` engine), Tesseract OCR when installed (reported unavailable otherwise), URL analysis
in the checker.

**Phase D: Fraud DNA and network intelligence.** Privacy-filtered embeddings (cached multilingual
MiniLM, TF-IDF fallback) in Qdrant (local mode or server; unavailable reported, never invented);
entity extraction to a graph (Neo4j when configured; SQL fallback for observed shared
identifiers), observed vs inferred vs confirmed links, authorised lookups.

**Phase E: geospatial, alerts, reports.** Hotspots from stored coordinates (SQL grid; PostGIS when
the extension exists), date/category/location filters, rule-driven deduplicated alerts,
investigation notes, complaint and investigation report exports (PDF/JSON) with access control.

**Phase F: frontend completion and end-to-end verification**, then
`PROJECT_IMPLEMENTATION_REPORT.md`.

## Blocked by external configuration (will be reported, not faked)

Live Clerk keys; Ollama + Qwen2.5 3B; Qdrant/Neo4j servers (Qdrant local mode may be used);
PostGIS extension (Homebrew Postgres 14 has none); Docker daemon; Tesseract.
