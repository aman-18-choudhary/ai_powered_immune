# API specification

Phase 0 deliverable, written 2026-10-15; updated 2026-10-16 for Phase 6 (audio evidence
transcription, transcript endpoint, transcription statuses, live-audio explanation fields). Every
implemented route below was listed from the route decorators in the code at HEAD `91ebfeb` (`services/complaints/complaints/api.py`,
`services/live_audio/live_audio/api.py`, `services/demo_server/demo_server/api.py`). Planned
endpoints are in section 6 and do not exist yet. Request and response details are summarised; the
Pydantic models in `services/complaints/complaints/validation.py` are authoritative.

## 1. Base paths and deployments

| Deployment | Base path | Notes |
|---|---|---|
| Local single process (`make demo`, `python -m demo_server`) | `http://localhost:8080` | Complaints mounted at `/api/complaints`, live audio at `/api/live-audio`, simulator at `/api/*`, console at `/` |
| Docker stack | gateway `/api/complaints/*` → complaints service | Compose untested here (no Docker daemon) |
| Standalone complaints service | `/` (paths below without the `/api/complaints` prefix) | |

## 2. Authentication

* **Header:** `Authorization: Bearer <token>`.
* **Clerk mode** (`COMPLAINTS_AUTH_MODE=clerk`, the service default): RS256 Clerk session JWTs
  verified against the JWKS (`iss`, `exp`/`nbf`/`iat` with 30 s skew, `sub`, `azp`, optional `aud`).
  Tested only with locally generated keys; **live Clerk is blocked (no keys)**.
* **Local / demo mode** (development only): HS256 tokens from the local sign-in (below) or the
  simulator demo login, signed with the server's demo secret. Any `role` claim is ignored.
* **Roles** come only from the `users` table, re-evaluated on every request against
  `INVESTIGATOR_ALLOWLIST` and `ADMIN_BOOTSTRAP_EMAILS`.

Role shorthand in the tables: **any** = any signed-in active user; **citizen** = role CITIZEN;
**staff** = INVESTIGATOR or ADMIN; **admin** = ADMIN. Ownership scoping (citizens see only their own
records; foreign ids return 404) applies in addition to the role.

## 3. Errors

| Case | Status | Body |
|---|---|---|
| Domain error | 4xx | `{"error": "<code>", "message": "<friendly text>"}` (for example `investigator_not_approved`, `invalid_transition`, `complaint_closed`, `evidence_limit_reached`, `forbidden_role`) |
| Validation | 422 | `{"error": "validation_error", "detail": [{"loc": [...], "msg": "...", "type": "..."}]}`; submitted values are never echoed |
| Not found / foreign record | 404 | `{"error": "not_found", ...}` |
| Rate limited | 429 | `{"error": "rate_limited", ...}` with `Retry-After` |
| Database or identity provider unavailable | 503 | `{"error": "service_unavailable"}` |
| Unexpected | 500 | `{"error": "internal_error"}` |

Default per-user rate limits (per minute, per process): write 30, upload 20, analysis 10,
analyze 10, read 240.

## 4. Complaints API (`/api/complaints`)

### Sign-in and session
| Method and path | Auth | Purpose |
|---|---|---|
| `GET /auth/config` | none | Sign-in methods: `{auth_mode, clerk: {enabled, publishable_key}, local: {enabled, dev_only, delivery, code_ttl_s}}` |
| `POST /local-auth/start` | none; **local/demo mode only, 404 in clerk mode** | Body `{email}`. Sends a 6-digit code to the server terminal (dev stand-in for email). 202; response never contains the code or reveals whether the account exists |
| `POST /local-auth/verify` | none; local/demo mode only | Body `{email, code, name?}` → `{token}` (HS256, 8 h, `email_verified: true`, no role) |
| `GET /auth/session` | any | Current user, effective role, capabilities, lapse messages (`investigator_access: "lapsed"` etc.) |
| `POST /auth/entry` | any | Body `{entry: "citizen" \| "investigator"}`. Investigator only for a verified allowlisted email; otherwise 403 `investigator_not_approved` with a friendly message |

### Complaints
| Method and path | Auth | Purpose |
|---|---|---|
| `POST /complaints` | citizen | Create. Required: `category`, `title`, `description`. Optional: `incident_at`, `amount`, `currency`, `payment_method`, `upi_id`, `utr_reference`, `bank_name`, `bank_account`, `ifsc`, `suspect_phone`, `suspect_email`, `suspicious_url`, `social_platform`, `social_account`, `location_text`, `city`, `state`, `latitude`, `longitude`. Optional `Idempotency-Key` header. 201 with the complaint (id, status `SUBMITTED`, server `reported_at`) |
| `GET /complaints` | any (scoped) | List with `limit` (≤100), `offset`, filters `status`, `category`, `city`, `state`, `incident_from/to`, `created_from/to`, `risk_level` (LOW/MEDIUM/HIGH/CRITICAL, exact), `has_evidence`, `q`, `sort` → `{items, total, limit, offset}` |
| `GET /complaints/{id}` | any (scoped) | Detail with status history, evidence count, latest analysis summary |
| `PATCH /complaints/{id}/status` | staff | Body `{status, note?}`; allowed transitions only (see [DATABASE_DESIGN.md](DATABASE_DESIGN.md)); writes history and notifies the owner |
| `DELETE /complaints/{id}` | citizen (owner) | Soft delete; 204 |

### Evidence
| Method and path | Auth | Purpose |
|---|---|---|
| `POST /complaints/{id}/evidence` | citizen (owner) | Multipart, one file. Type sniffed from content (PNG, JPEG, WebP, PDF, MP3, WAV, OGG/Opus, M4A) and must agree with the extension; size limits apply. 201 only when the file is stored; otherwise a 4xx with a reason |
| `GET /complaints/{id}/evidence` | any (scoped) | Evidence list (deleted files excluded). Each item has `kind` (`audio`, `image`, `document`, `other`) and `transcription` (audio only, else null): `{status, reason, message, source, transcript_available, engine, model, language, duration_s, transcribed_s, truncated, segment_count, checked_at}`. `source`: `stored` (a transcript row), `last_analysis` (the outcome of the latest analysis run, e.g. `engine_not_configured`) or `none` (`status: not_attempted`, before any analysis). The same fields appear in the complaint detail and the upload response |
| `GET /complaints/{id}/evidence/{eid}/transcript` | any (scoped: owner and staff; foreign ids 404) | The stored transcript of one audio file: `{evidence_id, complaint_id, status (transcribed or truncated), engine, model, language, duration_s, transcribed_s, truncated, segments: [{start_s, end_s, text}], created_at, note}`. 404 `not_found` (unknown or foreign), 404 `not_audio_evidence`, 404 `transcript_not_available` with `message` and the file's `transcription` state *(Phase 6)* |
| `GET /complaints/{id}/evidence/{eid}/download` | any (scoped) | File download with safe headers. Not audited (finding) |
| `DELETE /complaints/{id}/evidence/{eid}` | owner while `SUBMITTED`, or admin | Marks deleted and removes the file; investigators 403, others 404 |

### Analysis
| Method and path | Auth | Purpose |
|---|---|---|
| `POST /complaints/{id}/analysis` | any (scoped) | Transcribes stored audio evidence that has no transcript yet (synchronously, within the limits below), runs the pipeline on the stored complaint and stores a new version. 201 |
| `GET /complaints/{id}/analysis` | any (scoped) | Latest analysis |
| `GET /complaints/{id}/analyses` | any (scoped) | Versions, newest first, paginated *(Phase B, unpushed)* |
| `POST /analyze` | any | Stateless check, nothing stored. Body `text` (required) plus optional `title`, `url`, `phone`, `email`, `upi_id`, `amount`, `payment_method`, `city`, `state`, `social_platform` (older names `suspicious_url`, `suspect_phone`, `suspect_email` accepted; giving both forms is a 422). Returns the analysis and `input_capabilities`; `audio` is `not_accepted_here` ("Audio recordings are analysed only when they are attached to a complaint ..."), `screenshot` is `unavailable` (no OCR engine installed) *(Phase B; wording Phase 6)* |
| `GET /models` | any | Model versions, artifact SHA, synthetic training data, held-out metrics, limitations, runtime status, Ollama status, and `stt`: `{status, engine, model, reason, local, evidence_transcription, limits, decoders, used_for}` with `status` `ready` (loaded), `available_offline` (in the local cache, loaded on first use), `model_not_available_offline`, `engine_not_configured` or `configured` (whisper.cpp, not probed) *(Phase B; stt Phase 6)* |

Analysis result fields: `status` (`COMPLETED`, `PARTIAL`, `UNAVAILABLE`, `INSUFFICIENT_INPUT`),
`risk_score` 0-100 (an **uncalibrated heuristic**, never a probability), `risk_level` (LOW, MEDIUM,
HIGH, CRITICAL, or null when not assessable), `classification`, `confidence`,
`confidence_interpretation`, top categories, `indicators[]` (each with `source` = `rule` | `ml` |
`llm` | `static_guidance`, `method`, weight and an evidence pointer), `explanation` parts with
source, `recommended_actions`, `llm_explanation` (labelled, grounded, may be rejected),
`provenance`, `unavailable_capabilities`, `model_version`, timestamps.

**Audio evidence (Phase 6).** `audio_transcription[]` lists every stored audio file of the
complaint: `{evidence_id, mime_type, status, reason, message, engine, model, language, duration_s,
transcribed_s, truncated, segment_count, reused}`. Per-file `status`:

| Status | Meaning | Stored |
|---|---|---|
| `transcribed` | transcribed locally (faster-whisper through live-audio's engine) | yes |
| `truncated` | transcribed up to the per-file limit (`COMPLAINTS_STT_MAX_SECONDS_PER_FILE`, default 180 s) | yes |
| `decode_failed` | the file could not be decoded | yes |
| `unsupported_format` | an audio type without a decoder path | yes |
| `engine_not_configured` | no engine for evidence (`stt` extra not installed, `LIVE_STT_ENGINE=none`, or `COMPLAINTS_STT_ENABLED=0`; `reason` says which) | no |
| `model_not_available_offline` | the model is not in the local cache (never downloaded at runtime) | no |
| `decode_unavailable` | MP3 / OGG / M4A without PyAV | no |
| `timeout` | did not finish within the run's budget (`COMPLAINTS_STT_RUN_BUDGET_S`, default 120 s) | no |
| `budget_exhausted` | the run's budget was used up before this file | no |
| `not_attempted_limit` | over the per-run file cap (`COMPLAINTS_STT_MAX_FILES_PER_RUN`, default 2); the next run continues | no |
| `engine_error` | the engine raised | no |

Capabilities: `audio_transcription` is `available` (every audio file transcribed), `unavailable`
(reason: the single failing status, or `multiple_reasons`) or `skipped_no_input`
(`no_audio_evidence`); `audio_transcript_scoring` reports the scoring of the transcripts.
Transcripts are scored by the same call-guard scorer as the description; their indicators have
`stage: "audio_transcript"` and `evidence: {type: "evidence_transcript", field:
"evidence_transcript", evidence_id, start_s, end_s, snippet, granularity: "segment"}` (start/end
null with a `note` when the classifier scored the whole transcript or a span was not located).
`score_components.transcript_score` is the transcripts' score; `risk = max(text, transcript,
category prior) + URL adjustment`. A result that would be LOW is withheld (null) while any audio
file is not transcribed. Untranscribed audio is listed in `unanalysed_attachments` per file with
`status` and the plain-language `reason`. Provenance has one `audio_transcription` stage per file
(`evidence_id`, `reused`). Rows stored before Phase 6 with the inaccurate reason
`no_transcription_engine_installed` are returned as `not_implemented_at_analysis_time`.

### Alerts, profile, statistics
| Method and path | Auth | Purpose |
|---|---|---|
| `GET /alerts` | any (own) | `limit`, `offset`, `unread` |
| `POST /alerts/{id}/read` | any (own) | Marks read |
| `GET /me` | any | Profile and privacy settings |
| `PATCH /me` | any | `{display_name?, privacy?: {allow_case_matching}}`; any other key (role, email) is a 422 |
| `GET /me/stats` | any | Own counts `by_status`, `by_category`, `by_risk_level`, and `unread_alerts` |

### Administration
| Method and path | Auth | Purpose |
|---|---|---|
| `GET /admin/users` | admin | Paginated, filters `role`, `is_active` |
| `PUT /admin/users/{id}/role` | admin | `{role, expected_role}`; INVESTIGATOR/ADMIN need a verified email; last-admin guard; audited |
| `PUT /admin/users/{id}/active` | admin | `{is_active}`; audited |
| `GET /admin/audit` | admin | Paginated audit log |
| `GET /admin/health` | admin | Database, identity keys (JWKS), evidence directory, call-guard scorer, category model, Ollama, `stt` (same object as in `/models`, without `used_for`), `dna` (Fraud DNA status), `qdrant` (embedded index: `local` / `not_started`), `graph` (`sql`), and `not_configured` for Neo4j, PostGIS, Redis |

### Complaint network (Phase 9, staff only)

| Method and path | Auth | Notes |
|---|---|---|
| `GET /complaints/{id}/related?limit&offset` | INVESTIGATOR, ADMIN | linked complaints with link type (`observed` / `inferred` / `confirmed` / `rejected`), the shared identifiers (masked) and the field on each side, the latest review; `linking: "disabled"` when the owner opted out |
| `GET /complaints/{id}/graph?depth=1\|2` | INVESTIGATOR, ADMIN | nodes and edges; every identifier edge has an `evidence` text; caps 40/60/250 with `truncated` |
| `GET /graph/clusters?limit&offset` | INVESTIGATOR, ADMIN | groups over exact identifiers only |
| `POST /entities/search` | INVESTIGATOR, ADMIN | `{type, value, platform?}` in the body. Replaces the planned `GET /entities/{type}/{value}/complaints`, which would have put an identifier in a URL |
| `POST /complaint-links/review` | INVESTIGATOR, ADMIN | `{complaint_a, complaint_b, decision, note?}`; 409 `link_no_relation`, `link_complaint_deleted`, `link_matching_disabled`; audited |

Citizens get 403 on all of them. Complaints that are deleted or whose owner turned off case
matching are excluded from every result and count. A confirmed link is not a finding of guilt.

### Complaint hotspots (Phase 10, staff only)

| Method and path | Auth | Notes |
|---|---|---|
| `GET /geo/complaints/hotspots?from&to&category&risk_level&cell&bbox&min_count&compare` | INVESTIGATOR, ADMIN | counts per lat/lon cell (`cell` 0.1, 0.25 (default), 0.5, 1.0 degrees): cell centre and bounds (never a complaint's own coordinates), `count`, `by_category`, `by_risk` (latest analysis; `not_scored`), first/last report time; `compare=true` (needs `from`) adds `previous_count` and `change` for the equal period before `from`; `places[]` counts complaints without coordinates by state and city; `totals`; `bbox` = south,west,north,east; invalid values 422 |

Deleted and opted-out complaints are not counted. Counts are of complaints, not offences.

### Fraud DNA (Phase 8, staff only)

| Method and path | Auth | Notes |
|---|---|---|
| `GET /complaints/{id}/similar?limit=1..50` | INVESTIGATOR, ADMIN | complaints whose title and description (identifiers redacted) are worded alike: `items[]` with `similarity` (cosine, not a probability), `band` (`strong` >= 0.62, `moderate` >= 0.58), `shared_indicators` (rule-based codes of both latest analyses), `same_category`, `identifier_link` (`observed` / `inferred` / null, from the network), `explanation`; `status` `ok` / `loading` / `unavailable` (`reason`: `engine_not_installed`, `model_not_available_offline`, `load_failed`, `index_unavailable`, `embedding_failed`, `disabled`) / `insufficient_text`; `matching: "disabled"` when the owner opted out. Rate bucket: read |

Unknown or deleted complaint: 404. Excluded (deleted or opted-out) complaints are never returned or
counted; candidates are re-checked against the database at request time. Similarity is not proof.

## 5. Live audio API (`/api/live-audio`, demo-server)

Auth: a demo-server token (any role); the session owner is the token subject.

| Method and path | Purpose |
|---|---|
| `GET /capabilities` | Mode `incremental_chunked`, chunk size, audio format (16 kHz mono PCM16 WAV), STT engine status, scorer status, LLM status, limits, privacy statement |
| `POST /sessions` | Start a session (language `auto`, `hi`, `en`); max 2 per user |
| `POST /sessions/{sid}/chunks?seq=N&start_ms=…` | Upload one WAV chunk; ordered by `seq`, idempotent per `seq`; 40 per minute |
| `GET /sessions/{sid}` | Transcript segments with timestamps, risk history, indicators linked to segments, warnings, actions, `explanation` `{rules, llm, llm_status, llm_reason, llm_model, llm_label, llm_at_s, llm_note}` (`llm_status` `rejected` + `llm_reason` when the grounding checks refused the wording; `llm_label` "LLM-generated (model), may be imprecise") |
| `POST /sessions/{sid}/end` | End and delete the session's in-memory data |

## 6. Simulator API (demo-server, simulated data only)

`POST /api/auth/demo-login`, `POST /api/demo/start`, `POST /api/demo/control`,
`GET /api/demo/state`, `GET /api/demo/runs`, `WS /api/stream`, `GET /api/holds`,
`POST /api/holds/{txn_ref}/resolve`, `GET /api/antibodies`, `GET /api/ledger/head`,
`GET /api/ledger/entries`, `GET /api/ledger/checkpoints`, `GET /api/ledger/checkpoints/latest`,
`POST /api/cases`, `GET /api/packages/{case_id}`, `POST /api/verify`,
`GET /api/ring/{case_id}/graph`, `POST /api/ring/{case_id}/simulate`,
`GET /api/ring/{case_id}/best-takedown`, `GET /api/geo/hotspots`, `GET /api/benchmark/summary`,
`POST /api/citizen/assess`, `GET /api/health`. Contract: the console/demo-server design spec in
`docs/superpowers/specs/2026-10-10-console-and-demo-server-design.md`. These routes operate on
simulator output and **are not complaint intelligence**.

## 7. Planned endpoints (not implemented)

| Endpoint (proposed) | Auth | Phase |
|---|---|---|
| OCR of screenshot evidence | owner, staff | 6 (blocked until an OCR engine is installed) |
| `GET/POST /complaints/{id}/notes` | staff | 12 |
| `GET /complaints/{id}/report` (export) | owner (own complaint), staff | 12 |
| `PATCH /complaints/{id}` (citizen edits while `SUBMITTED`) | owner | 11 |

Each will follow the same error format, ownership scoping and rate limiting.
