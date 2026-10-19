# complaints

Citizen complaints service for the ScamShield Immune stack: complaint intake with a verified
identity, three roles (CITIZEN, INVESTIGATOR, ADMIN), evidence file uploads, a minimal and honest
text analysis, alerts and an admin API. FastAPI, SQLAlchemy Core (SQLite in tests, Postgres in the
stack), plain Postgres only.

**Status of the pieces (read this first)**

| Piece | Status |
|---|---|
| API, RBAC, storage, validation, uploads, migrations, profile, local sign-in | implemented; on 2026-10-16 (Phase 6): 493 tests pass on SQLite (7 skipped: 1 Postgres-only, 2 opt-in live Ollama, 4 opt-in real speech-to-text), the same suite on a disposable Postgres 14 (`scripts/with_postgres.sh`): 494 passed, 6 skipped (the opt-in live tests) |
| Local sign-in (email + one-time code in the server terminal) | implemented and tested; **development only**, `demo`/`local` modes, absent in `clerk` mode |
| Clerk token verification | implemented and tested ONLY with locally generated RSA keys and an injected JWKS. **Live Clerk verification has not been exercised: blocked by configuration** (no Clerk instance or keys exist for this repository) |
| Clerk Backend API email lookup | defined, OFF by default, tested only against a mock transport |
| Analysis (pipeline v2) | call-guard text scorer + category classifier trained on SYNTHETIC data only + offline URL/format heuristics + rule-based explanation, with per-item provenance (rule / ml / llm / static_guidance). Optional GROUNDED local-Ollama wording (default `qwen2.5:0.5b`; checks in the shared `svckit.llm_grounding`); exercised live on 2026-10-15 and 2026-10-16 only through the opt-in `OLLAMA_LIVE_TESTS=1` test (a small model: its text is usually rejected by the grounding checks and is labelled "may be imprecise" when shown). Score is an UNCALIBRATED heuristic. **Uploaded audio evidence is transcribed locally** (faster-whisper through live-audio's engine, optional extra `stt`; see "Audio evidence transcription") and scored like the description. Screenshot OCR and document text: `unavailable` (listed as unanalysed attachments; no OCR engine installed) |
| Malware scanning of uploads | **not implemented** |
| Qdrant | embedded (local) mode for Fraud DNA only (`qdrant-client`, no server) |
| Neo4j, PostGIS, Redis | not used; reported as `not_configured` by `/admin/health` |
| Ollama | optional, local hosts only, explanation wording only; `/models` and `/admin/health` report its state |

## Auth flow

```
Browser (Clerk sign-in)                  complaints service
  |  Clerk session JWT (RS256)                 |
  |------ Authorization: Bearer <jwt> -------->|  1. alg must be RS256 (none/HS* rejected) and kid known
  |                                            |  2. signature vs JWKS (TTL cache, rate-limited refresh)
  |                                            |  3. iss, exp/nbf/iat (30 s skew), sub, azp (+aud if set)
  |                                            |  4. email / email_verified from custom claims
  |                                            |  5. upsert internal user (CITIZEN) ; admin bootstrap
  |                                            |  6. role comes from the users table ONLY
  |<-------- 200 {user, capabilities} ---------|
  |  POST /auth/entry {"entry":"investigator"} |  verified email in INVESTIGATOR_ALLOWLIST -> INVESTIGATOR
  |<-------- 200 or 403 investigator_not_approved
```

* Roles are never read from the request body, query, headers or token claims. A `role` claim or a
  `role` key in a body is ignored.
* Registering as a citizen never grants INVESTIGATOR/ADMIN. `POST /auth/entry {"entry":"citizen"}`
  never changes a role.
* INVESTIGATOR: only through `POST /auth/entry` with `entry=investigator` when the **verified**
  identity (signature, issuer, expiry, azp, `email_verified` true) has an email in
  `INVESTIGATOR_ALLOWLIST` (casefolded, exact match, constant-time per entry), or through the admin
  API. Anyone else gets 403 `investigator_not_approved` with a friendly message that does not reveal
  the list. Enforced on the backend; the console cannot bypass it.
* ADMIN: only through `ADMIN_BOOTSTRAP_EMAILS` (verified email, applied at user upsert once per
  user and audited as `admin_bootstrap`; a later deliberate demotion is not undone) or by an
  existing ADMIN through `PUT /admin/users/{id}/role`. Granting INVESTIGATOR/ADMIN through the API
  requires the target to have a verified email.
* **Access is re-evaluated on every request.** `users.role_source` records how an
  INVESTIGATOR/ADMIN role was granted: `allowlist` (entry flow), `bootstrap` (env) or `admin`
  (admin API). An `allowlist` investigator is effective only while the current verified token
  email (verified, case/whitespace-insensitive) is still in `INVESTIGATOR_ALLOWLIST`; a
  `bootstrap` admin only while it is still in `ADMIN_BOOTSTRAP_EMAILS`. Otherwise that request is
  treated as CITIZEN, `/auth/session` returns `investigator_access: "lapsed"` (or
  `admin_access: "lapsed"`) with a friendly message, and the lapse is audited once
  (`investigator_access_lapsed` / `admin_access_lapsed`; `..._restored` when the email returns).
  The stored role is not rewritten and nothing is cached beyond the request, so allowlist removal
  (after the service picks up the new env) or an unverified/changed email takes effect on the next
  request. Roles granted through the admin API are unaffected by list changes; an admin can
  confirm an existing role (`PUT .../role` with the same role) to make it independent of the
  lists. Rows from before `role_source` existed are derived (INVESTIGATOR -> allowlist; ADMIN ->
  bootstrap only if bootstrapped and never changed by an admin).
* Lock-out guard: a lapsed bootstrap admin keeps ADMIN while no other active admin granted by an
  admin exists (`admin_access: "lapsed_retained"`, audit and ERROR log
  `last_admin_bootstrap_lapsed`). Appoint another administrator through the API to end this.

### Local sign-in (development only, machines without Clerk keys)

With `COMPLAINTS_AUTH_MODE=local` (or `demo`) the service offers an email + one-time-code sign-in
so the console can be used without a Clerk instance:

```
POST /local-auth/start  {"email": "..."}                 -> 202 (same answer for every address)
     server stderr: [LOCAL DEV MAIL] sign-in code for <email>: 123456
POST /local-auth/verify {"email": "...", "code": "123456", "name"?: "..."}
                                                          -> 200 {token, token_type, expires_in}
```

* The printed line is the local stand-in for sending an email (no mail server, no paid API).
  Whoever can read the server terminal can sign in as any address: it exists for a developer's
  own machine and is labelled dev-only everywhere. It is not available in `clerk` mode (404).
* The code is 6 random digits; only a salted HMAC of it is kept, in memory, for 10 minutes; at
  most 5 wrong attempts (then a new code is needed); a new request replaces the previous code; one
  use. Rate limits (15-minute windows): 5 code requests per email, 20 per client address, 30
  verifications per client address (`429` with `Retry-After`). Wrong, expired, used and
  never-issued codes all get the same `400 invalid_code`.
* Responses never contain the code and never reveal whether an account exists. The email, the
  code and its hash are never logged (the stderr line is the only place the code appears).
* The token is HS256 (8 h) signed with `COMPLAINTS_DEMO_JWT_SECRET` (in the demo server: its
  `DEMO_JWT_SECRET`): `sub` = `local:` + a hash of the email, `email`, `email_verified: true` (the
  code proved access to the "mailbox"), optional `name`, no role. It is verified like any demo
  token, so the role is still decided here: CITIZEN by default, INVESTIGATOR only through
  `POST /auth/entry` with an email in `INVESTIGATOR_ALLOWLIST`, ADMIN only through
  `ADMIN_BOOTSTRAP_EMAILS` or an existing admin.
* `GET /auth/config` (public) tells the console which methods exist:
  `{"auth_mode", "clerk": {"enabled", "publishable_key"}, "local": {"enabled", "dev_only",
  "delivery", "code_ttl_s"}}`. `publishable_key` comes from `CLERK_PUBLISHABLE_KEY`, which must look
  like a publishable key (`pk_test_...`/`pk_live_...`, otherwise the service refuses to start so a
  secret key is never sent to browsers).

### Clerk configuration the service needs

Create a Clerk JWT template (or session-token customisation) that adds these claims:
`email` (primary email), `email_verified` (boolean) and optionally `name`. The exact template
shortcodes depend on the Clerk dashboard version and were not verified here. If the claims are
absent the identity is still valid but `email_verified` is false, so investigator/admin eligibility
fails closed. Set `CLERK_ISSUER`, `CLERK_JWKS_URL` (both https) and, recommended,
`CLERK_AUTHORIZED_PARTIES` (the console's origin; a token without a matching `azp` is rejected).

`COMPLAINTS_AUTH_MODE=demo` (and `local`, which is the same plus the local sign-in above) accepts HS256 tokens (for example from the gateway demo login) signed
with `COMPLAINTS_DEMO_JWT_SECRET`/`GATEWAY_JWT_SECRET` (>= 32 bytes). The `role` claim is ignored,
the subject is prefixed `demo:`, and demo users are CITIZEN unless the token itself carries a
verified `email` that matches the allowlist (a token minted by whoever holds the secret). Never use
demo mode in production.

The default mode is `clerk`. If `COMPLAINTS_DATABASE_URL`, `COMPLAINTS_EVIDENCE_DIR`,
`CLERK_ISSUER` or `CLERK_JWKS_URL` is missing (or a demo secret is short) the process exits with a
configuration error; there is no insecure fallback.

## API

| Method and path | Roles | Notes |
|---|---|---|
| `GET /healthz`, `GET /readyz` | none | readiness pings the DB and the evidence dir |
| `GET /auth/session` | any | upserts the user, returns `{user, capabilities}` |
| `POST /auth/entry` | any | `{entry: "citizen"}` or `{entry: "investigator"}` |
| `GET /auth/config` | none | sign-in methods available (Clerk publishable key, local sign-in on/off) |
| `POST /local-auth/start`, `POST /local-auth/verify` | none | development-only email + code sign-in; `demo`/`local` modes only (404 in `clerk` mode) |
| `GET /me` | any (own) | `id, email, email_verified, display_name, role` (effective), `created_at`, `privacy: {allow_case_matching}` |
| `PATCH /me` | any (own) | only `display_name` (1-100; `null` returns to the name from sign-in) and `privacy.allow_case_matching`; any other key (`role`, `email`, ...) is a 422 and nothing changes; audited as `profile_update` without the name |
| `POST /complaints` | CITIZEN | required: `category`, `title`, `description` (10-5000). Optional fields stay NULL when absent or blank, including the bank details `bank_name` (<= 100), `bank_account` (suspect/beneficiary account, 6-18 digits after removing spaces) and `ifsc` (`^[A-Z]{4}0[A-Z0-9]{6}$`, upper-cased). `Idempotency-Key` header (24 h, per user) |
| `GET /complaints` | any | citizen: own only; staff: all non-deleted. Filters `status category city state incident_from/to created_from/to risk_level has_evidence q`, `sort`, `limit` (1-100), `offset`. `q` matches title/description and, exactly, a bank account number (spaces ignored) or IFSC |
| `GET /complaints/{id}` | owner, INVESTIGATOR, ADMIN | others get 404, identical to a missing id; includes status history, latest analysis summary, evidence list |
| `PATCH /complaints/{id}/status` | INVESTIGATOR, ADMIN | valid transitions only, history row, alert to the owner |
| `PATCH /complaints/{id}` | owner (CITIZEN) | add or correct optional details while SUBMITTED (409 `complaint_not_editable` after); `null` clears; category/title/description fixed; identifiers re-extracted; audited (`complaint_edit`, field names only) |
| `DELETE /complaints/{id}` | owner (CITIZEN) | soft delete, only while SUBMITTED, audited |
| `POST /complaints/{id}/evidence` | owner (CITIZEN) | multipart, one file in field `file` |
| `GET /complaints/{id}/evidence`, `.../{eid}/download` | owner, staff | `Content-Disposition: attachment`, `nosniff`. Evidence items carry `kind` and, for audio, `transcription` (status, precise reason, source `stored` / `last_analysis` / `none`) |
| `GET /complaints/{id}/evidence/{eid}/transcript` | owner, staff | the stored transcript (`segments: [{start_s, end_s, text}]`, engine, model, language, durations, truncated); foreign ids 404; `not_audio_evidence` / `transcript_not_available` (404, with the file's transcription state) |
| `DELETE /complaints/{id}/evidence/{eid}` | owner while SUBMITTED, ADMIN any time | marks the row `deleted`, removes the file, audited (`evidence_delete`, no file name); an admin removal alerts the owner. `200 {id, status: "deleted", file_removed}` (`file_removed: false` = the row is deleted but the file could not be removed, logged). Foreign or unknown ids: 404; INVESTIGATOR: 403; owner after review started: 409 `evidence_not_deletable` |
| `POST /complaints/{id}/analysis`, `GET .../analysis` | owner, staff | new version per run (pipeline v2); POST first transcribes stored audio evidence that has no transcript (synchronous, bounded; see below) |
| `GET /complaints/{id}/analyses` | owner, staff | every version, newest first, `limit`/`offset`; summary + pipeline and model versions per version |
| `POST /analyze` | any authenticated user | stateless analysis of `text` (10-5000) plus optional `url`, `upi_id`, `phone`, `email`, `amount`, `payment_method` (the older names `suspicious_url`, `suspect_phone`, `suspect_email` still work; giving both names with different values is a 422 on the short name), `city`, `state`, `social_platform`; same pipeline and provenance; `input_capabilities` per input (`analysed`, `format_checked`, `context_only`, `not_provided`, `insufficient_input`; `audio` is `not_accepted_here`: recordings are analysed only when attached to a complaint; `screenshot` is `unavailable`: no OCR engine is installed); nothing stored, content not logged, `persisted: false`; own rate bucket |
| `GET /models` | any authenticated user | `stt`: speech-to-text state for audio evidence (`ready`, `available_offline`, `model_not_available_offline`, `engine_not_configured`, `configured`), limits, decoders. category-v1 and call-guard clf-v1: version, pinned artifact SHA-256, training data (100% synthetic), held-out metrics read from the shipped metrics file (clf-v1 ships none: `metrics: null` with the reason), the leaky random split separately (`is_headline: false`), limitations (category: from `MODEL_CARD.md`), runtime status; `llm`: Ollama `not_configured` / `unavailable` (reason) / `ready` with the model name. Never contacts a non-local host |
| `GET /alerts`, `POST /alerts/{id}/read`, `GET /me/stats` | any (own data) | stats are computed from the DB, zeros when empty |
| `GET /admin/users`, `GET /admin/audit`, `GET /admin/health`, `GET /admin/config` | ADMIN | health includes `stt`, `ocr`, `dna`, `qdrant`; config shows the effective non-secret settings |
| `PUT /admin/users/{id}/role`, `PUT /admin/users/{id}/active` | ADMIN | roles granted here have source `admin`; optimistic `expected_role`; last active admin protected; audited |
| `GET /complaints/{id}/related` | INVESTIGATOR, ADMIN | complaints sharing an exact normalised identifier (`observed`) or only a registrable domain (`inferred`), each with the shared identifiers (masked), the field on both sides and the latest review; `limit` (1-50), `offset`; `linking: "disabled"` and no items when the complaint's owner opted out |
| `GET /complaints/{id}/graph?depth=1\|2` | INVESTIGATOR, ADMIN | complaint and identifier nodes, identifier edges with their evidence text, review edges among shown complaints; caps 40 complaints, 60 identifiers, 250 edges, `truncated` flag |
| `GET /graph/clusters` | INVESTIGATOR, ADMIN | connected groups over exact identifiers only (domains never join clusters); `limit`, `offset` |
| `POST /entities/search` | INVESTIGATOR, ADMIN | body `{type, value, platform?, limit?, offset?}`; the identifier is never in a URL; invalid values are a 422 that does not echo them |
| `POST /complaint-links/review` | INVESTIGATOR, ADMIN | body `{complaint_a, complaint_b, decision: confirmed\|rejected, note?}`; 201; appends a review (history kept); 409 `link_no_relation` / `link_complaint_deleted` / `link_matching_disabled`; audited as `link_confirm` / `link_reject` with complaint ids, never identifier values or the note |

Status transitions: SUBMITTED to UNDER_REVIEW or REJECTED; UNDER_REVIEW to INVESTIGATING, RESOLVED
or REJECTED; INVESTIGATING to UNDER_REVIEW, RESOLVED or REJECTED; RESOLVED to INVESTIGATING;
REJECTED to UNDER_REVIEW. Transitions are compare-and-set; of two concurrent changes one gets 409.
Investigator notes are visible to the complainant.

Errors are JSON `{error, message}`; validation errors are `{error: "validation_error", detail:
[{loc, msg, type}]}` and never echo submitted values or unknown key names.

## RBAC matrix

| Capability | CITIZEN | INVESTIGATOR | ADMIN |
|---|---|---|---|
| create complaint | yes | no | no |
| read own complaints / evidence / transcripts / analysis | yes | n/a | n/a |
| read all complaints / evidence / transcripts / analysis | no (404) | yes | yes |
| upload evidence | own only | no | no |
| remove evidence | own, SUBMITTED only | no | any |
| own profile and privacy (`/me`) | yes | yes | yes |
| change complaint status | no | yes | yes |
| delete complaint | own, SUBMITTED only | no | no |
| run analysis | own | any | any |
| alerts, stats | own | own | own |
| complaint network (related, graph, clusters, identifier search, link review) | no (403) | yes | yes |
| Fraud DNA (similar complaints), hotspots, investigation notes, case timeline | no (403) | yes | yes |
| case report download | own complaint (owner version) | yes (staff version) | yes (staff version) |
| users, roles, audit, health | no | no | yes |

INVESTIGATOR (allowlist source) and ADMIN (bootstrap source) apply only while the verified email is
still in the corresponding list; see Auth flow.

An inactive user gets 403 `account_inactive` on every endpoint. Users are deactivated, never
deleted.

## Evidence uploads

* Allowed: PNG, JPEG, WebP, PDF (cap 10 MiB, `COMPLAINTS_MAX_IMAGE_BYTES`) and MP3, WAV, OGG,
  M4A (cap 25 MiB, `COMPLAINTS_MAX_AUDIO_BYTES`); 10 files per complaint. The type is decided
  from the leading bytes; the extension must agree if it is one of the known ones; the client
  Content-Type is ignored.
* The body is parsed incrementally and the cap is enforced while streaming; memory use does not
  grow with file size. A request body over the route cap is refused before it is read.
* The file is written to a temp name, fsynced, renamed atomically, re-read and hash-verified;
  only then is a `stored` row inserted. Any failure returns an error, leaves no `stored` row and
  removes the file (a crash between rename and insert can leave an unreferenced file; a startup
  sweep only removes old temp files).
* Storage key is random; the client filename is sanitised and stored for display only. Files are
  mode 0600 in a 0700 directory (`COMPLAINTS_EVIDENCE_DIR`) that nothing serves statically.
* Re-sending the same bytes for a complaint returns the existing record (retry safe).
* **No malware or content scanning is performed.** A file that has a valid PNG/PDF header can
  still be malicious; downloads are attachments with `nosniff` and a fixed type, which limits but
  does not remove that risk. Soft-deleted complaints keep their files; there is no retention job.
* Removal (`DELETE .../evidence/{eid}`): the row becomes `deleted` (with `deleted_at`) and stays
  for the audit trail; the file is unlinked. Deleted evidence is excluded from lists, detail,
  downloads, counts, the `has_evidence` filter, the per-complaint cap and analysis; uploading the
  same bytes again creates a new record. An audio file's transcript row is deleted in the same
  transaction.

## Audio evidence transcription (Phase 6)

`POST /complaints/{id}/analysis` transcribes every stored audio file that has no transcript yet,
then analyses the complaint (`complaints/transcribe.py`).

* **Engine:** live-audio's speech-to-text engine (`live_audio.stt.engine_from_env`: faster-whisper,
  `LIVE_STT_ENGINE` / `LIVE_STT_MODEL`, default `base`, CPU int8, `local_files_only`: a model that
  is not cached is `model_not_available_offline`, nothing is downloaded). Language `auto` with the
  same English / Hindi+Urdu decision and romanised-Hindi prompt as Live Protection. Installed with
  the optional extra `pip install -e "services/complaints[stt]"` (= `live-audio[stt]` + numpy) and
  imported lazily: without it the service runs and reports `engine_not_configured`. The demo
  server passes its live-audio engine object, so one model is in memory.
* **Decoding** to 16 kHz mono PCM16: WAV with the stdlib `wave` module (integer PCM, numpy mixing
  and linear resampling); MP3, OGG/Opus, M4A (and float WAV) with PyAV, which ships with
  faster-whisper. Without PyAV those are `decode_unavailable`.
* **Limits** (per analysis run): `COMPLAINTS_STT_MAX_FILES_PER_RUN` (default 2) new files, the rest
  `not_attempted_limit` (the next run continues); `COMPLAINTS_STT_MAX_SECONDS_PER_FILE` (default
  180 s) of audio per file, longer files `truncated`; `COMPLAINTS_STT_RUN_BUDGET_S` (default 120 s)
  for all transcription in the run (`timeout`, `budget_exhausted`); the upload size caps above. The
  engine call runs in a worker thread, one at a time per process (its own semaphore), so the event
  loop is not blocked. A live chunk and an evidence file can be transcribed at the same time.
  `COMPLAINTS_STT_ENABLED=0` turns evidence transcription off (`engine_not_configured`,
  `disabled_by_config`).
* **Once per file:** definitive outcomes (`transcribed`, `truncated`, `decode_failed`,
  `unsupported_format`) are stored in `evidence_transcripts` (migration 9) and reused by every
  later analysis (`reused: true`); transient ones are reported for that run only and retried. Two
  analyses of the same complaint in one process do not transcribe a file twice (a per-complaint
  lock; the unique `evidence_id` covers other processes).
* **Analysis:** the transcripts (segments joined, at most 4,000 characters) are scored by the same
  call-guard scorer (rules + clf-v1) as the description; `transcript_score` enters the risk formula
  as `max(text_score, transcript_score)`. Indicators found in a transcript have
  `stage: audio_transcript` and evidence `{type: "evidence_transcript", evidence_id, start_s,
  end_s, snippet}` (the first segment, or pair of adjacent segments, where the rules find the
  cue). The category model reads the description together with the transcripts (bounded;
  `category_model.evidence.audio_transcripts_included`) since 2026-10-17. Provenance has one `audio_transcription`
  stage per file. A recording that could not be transcribed makes the run `PARTIAL`, is listed in
  `unanalysed_attachments` with the precise reason, and a result that would be LOW is withheld.
* **Statuses:** see the table in [docs/API_SPECIFICATION.md](../../docs/API_SPECIFICATION.md).
  `/admin/health` and `/models` report the engine truthfully without loading it (`available_offline`
  means the model is in the local cache).
* **Privacy:** transcript text, file names and evidence ids are never logged (status codes, counts
  and durations only; tested with `caplog`). Transcripts have the same access as the evidence and
  are deleted with it. Not encrypted at rest (like the files).
* **Measured (real Whisper `base`, CPU, 2026-10-16, `LIVE_STT_TESTS=1`):** English (WAV and M4A)
  near verbatim, CRITICAL 100; Hindi (Devanagari read by Lekha) romanised and partly garbled
  ("Mekh Police se boh raha hoon. Aap Digital Arrest me hai..."), CRITICAL 100; Hinglish (Rishi)
  "Main CBI office a bowl raha hoon. Aap digital arrest me ho...", CRITICAL 100. Synthetic TTS is
  clean speech; real phone recordings will be worse.

## Bank details and privacy settings

* `bank_name`, `bank_account`, `ifsc` are optional and NULL when absent. They are returned to the
  owner and to staff only (like every complaint field; this service masks no complaint field in
  list views). Invalid values are a 422 that does not echo the value.
* `users.allow_case_matching` (default true, `PATCH /me`): when false, the user's complaints are
  excluded, server-side and from the next request on, from every complaint-network result:
  related complaints, graphs (as nodes and as bridges between other complaints), clusters,
  identifier search results and counts, and new link reviews (409 `link_matching_disabled`). Staff
  can still read the complaint itself. Fraud DNA honours it the same way (not indexed, not returned,
  not counted).

## Complaint network (Phase 9)

Identifiers come only from the structured fields (`suspect_phone`, `upi_id`, `suspect_email`,
`suspicious_url` plus its registrable domain, `bank_account`, `social_platform` + `social_account`),
never from free text. They are normalised (`complaints/network.py`: phone to `+<country><number>` (+91 for a bare Indian mobile),
UPI and email casefolded, URL scheme and host lower-cased, default port and fragment dropped, path and query kept as typed, bank account
digits only, social `platform:account`), stored in `complaint_entities` in the same transaction as
the complaint, and compared by a SHA-256 of type and value. Responses show masked values only.

* **observed**: two complaints give the same normalised identifier.
* **inferred**: only the registrable domain of two different links matches. Always labelled
  inferred; never joins a cluster. The registrable domain uses a small authored suffix list, not
  the Public Suffix List, so shared hosting can make unrelated sites share a "domain".
* **confirmed / rejected**: the latest investigator review of the pair (`link_reviews`, every
  review kept). A review is possible only where a shared identifier exists. Confirming says the
  complaints appear to be part of one case; it is **not** a finding that anyone committed a crime.

Every query applies the same filter: the complaint is not deleted and its owner allows case
matching. Opted-out or deleted complaints cannot appear, be counted, or connect other complaints.
Shared identifiers are leads, not proof that the same person is involved.

Since Phase 11 a complainant can change optional details (and so identifiers) while the complaint
is SUBMITTED. Links are always computed from the current identifiers; a review made earlier stays
in the history but is only shown where a shared identifier still exists.

## Investigator case tools (Phase 12)

Investigation notes (`GET/POST /complaints/{id}/notes`, staff, append-only, never shown to the
complainant, audited without the text), the case timeline (`GET /complaints/{id}/timeline`, staff,
built from the stored status history, evidence, transcripts, analyses, link reviews and notes) and
the case report (`GET /complaints/{id}/report`, Markdown attachment; the owner's version leaves out
notes, link reviews and user ids; every export audited as `case_report_export`).

## Screenshot text (OCR, Phase 6)

Image evidence (PNG, JPEG, WebP) is read by the local `tesseract` program (5.x; `eng` and `hin`
when their data files are installed) when a complaint analysis runs: at most 3 new files per run,
20 s per file, 45 s per run; the text (at most 5000 characters) is stored once in `evidence_ocr`
and reused. The text is scored per image by the same call-guard rules as the description;
indicators carry `stage: "screenshot_text"` and point to the image, and the category model reads
it with the description. OCR can misread: each result has Tesseract's mean word confidence and is
labelled machine-read. Without tesseract every image reports `engine_unavailable` and the
analysis never returns LOW while a screenshot could not be read. Variable:
`COMPLAINTS_OCR_ENABLED` (default 1). `GET /complaints/{id}/evidence/{eid}/ocr` returns the text
(owner and staff).

## Complaint hotspots (Phase 10)

`GET /geo/complaints/hotspots` (staff) counts complaints per latitude/longitude grid cell from the
optional coordinates complainants give (no geocoding, no map service). Only cell centres and
bounds are returned, never a complaint's coordinates; complaints without coordinates are counted
by `state`/`city`. Filters: `from`, `to`, `category`, `risk_level` (latest analysis or
`not_scored`), `cell` (0.1/0.25/0.5/1.0), `bbox`, `min_count`; `compare=true` with `from` adds the
equal period before. Deleted and opted-out complaints are not counted. Bounds: 50,000 complaints
read, 500 cells, 100 places, each with a `truncated` flag.

## Fraud DNA (Phase 8)

`GET /complaints/{id}/similar` (staff): complaints whose **descriptions** are worded alike, from
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384 dimensions, cached revision
`e8f8c211`, loaded with `local_files_only=True`; nothing is downloaded). Install the optional extra
`pip install -e "services/complaints[dna]"`; without it the endpoint answers `status:
"unavailable"`, `reason: "engine_not_installed"`.

* Text: title + description; links, email/UPI addresses, IFSC and 8+ digit numbers are replaced by
  placeholders first (identifiers are the network's job, Phase 9); fewer than 20 letters left =
  `insufficient_text`.
* Index: Qdrant embedded mode, `COMPLAINTS_DNA_DIR` (memory when unset), payload = complaint id +
  text hash. A background task loads the model and syncs at start-up; create, delete and
  case-matching changes mark the index stale and the next query syncs first.
* Results: cosine >= 0.58 (`moderate`), >= 0.62 (`strong`), chosen by a fixed rule on the dev
  families of the SYNTHETIC set `evaluation/datasets/fraud_dna_v1.jsonl`; held-out test families:
  precision 0.82 / recall 0.52 (0.91 / 0.35 for strong). Each item explains itself (shared
  rule indicators, category) and says separately if an identifier is shared.
* Privacy: only matchable complaints are indexed, and every candidate is re-checked in SQL at
  request time. Similar wording is **not** proof that the same person or crime is involved.
* Variables: `COMPLAINTS_DNA_ENABLED` (default 1), `COMPLAINTS_DNA_MODEL`, `COMPLAINTS_DNA_DIR`.

## Schema migrations

Versioned and additive (`complaints/migrations.py`, table `schema_migrations(version, name,
applied_at)`). They run at startup and with `python -m complaints.migrate`, in one transaction
behind a Postgres advisory lock (SQLite and Postgres). Every step inspects the live schema and only
adds what is missing, so a database created before versioning (with or without `analyses.detail`)
upgrades in place, a re-run is a no-op, and applied steps are re-checked on start (a column dropped
by hand is restored). Data is never dropped.

| Version | Change |
|---|---|
| 1 `baseline` | `create_all` of the current tables |
| 2 `analyses_detail_column` | `analyses.detail` (previously an ad-hoc ALTER) |
| 3 `complaint_bank_details` | `complaints.bank_name`, `bank_account`, `ifsc`, index on `bank_account` |
| 4 `user_profile_privacy` | `users.allow_case_matching` (default true), `users.display_name_set` |
| 5 `evidence_deleted_status` | evidence status `deleted` allowed (SQLite: table rebuilt with all rows; Postgres: CHECK swapped), `complaint_evidence.deleted_at` |
| 6 `analyses_critical_level` | `analyses.risk_level` allows CRITICAL (same rebuild / swap); rows that stored CRITICAL as HIGH (exact level in `detail.risk_level`) are backfilled, only when the CHECK had to be widened |
| 7 `analyses_insufficient_input` | `analyses.status` allows INSUFFICIENT_INPUT (Postgres also widens the column 16 -> 24) |
| 8 `alerts_risk_level` | `alerts.risk_level` (nullable) for one risk alert per complaint and level |
| 9 `evidence_transcripts` | table `evidence_transcripts` (one row per audio evidence file; definitive transcription outcomes only) + index |

## Analysis pipeline v2

`pipeline_version` is `complaints-analysis-v2+<text model>+<category model>`. Previous (v1) rows stay
valid; API fields were only added. Each stage reports its own status in `capabilities`
(`available | unavailable | not_applicable | skipped_no_input | skipped_insufficient_input`; the
LLM stage can also be `timeout | error | rejected`) with a machine reason in `capability_reasons`.
A failing stage never blocks another and is never turned into LOW or BENIGN.

| Capability | Meaning |
|---|---|
| `text_risk_scorer` | call-guard rules (`rules-v1`) + script classifier (`clf-v1`) on title and description (built for digital-arrest scripts) |
| `category_classifier` | TF-IDF + logistic regression, text only, abstains to `UNDETERMINED`; fallback `classification: null` |
| `url_heuristics` | offline lexical checks: scheme, IP host, punycode/IDN, many subdomains, static suspicious-TLD list, shorteners, brand look-alike (small static list), credentials in URL, long query, odd port. `skipped_no_input` without a URL |
| `url_fetch_reputation`, `contact_reputation_lookup` | always `unavailable` (no network lookups by design) |
| `entity_format_checks` | phone (E.164-like, placeholders), UPI id format and known-handle list, email syntax. Weight 0: unusual values are data-quality notes, not evidence |
| `geographic_context` | presence recorded as context; not scored (hotspot comparison is not part of this service) |
| `audio_transcription` | `available` (every audio file transcribed), `unavailable` (reason: the failing per-file status or `multiple_reasons`), `skipped_no_input` (`no_audio_evidence`); per-file detail in `audio_transcription[]` |
| `audio_transcript_scoring` | call-guard scoring of the transcripts: `available`, `unavailable`, `skipped_no_input` (no transcript, or `no_speech_in_transcripts`) |
| `screenshot_ocr`, `document_text_extraction`, `evidence_content_analysis` | `unavailable` (no OCR engine installed; document text not implemented; only audio content is analysed, via transcription); images and PDFs are listed under `unanalysed_attachments` by type and count |
| `llm_explanation` | `unavailable` (`not_configured`, `non_local_host`, `unreachable`), `available`, `timeout`, `error`, `rejected` (grounding checks), `skipped_no_input` (`no_findings_to_rephrase`) |

**Risk score** (`score_semantics: "uncalibrated_heuristic"`, 0-100, never a probability):
`risk = min(100, max(text_score, transcript_score) + round(25 *
noisy_or(url_indicator_weights)))`. `text_score = round(100 * call-guard score)` of the title and
description, counting only scored points with a listed reason (`require_explained`; points without
a reason are ignored and recorded in `score_components.unexplained_scores_ignored`);
`transcript_score` the same for the audio transcripts (when any were transcribed). **The predicted
category is a model guess and is not part of the score** (until 2026-10-18 a category prior of
45-70 was added, which made legitimate texts HIGH with no indicator; see
`docs/ACCURACY_AUDIT.md`). HIGH and CRITICAL need at least one rule-based indicator; a score from
the ML classifier alone is capped at MEDIUM. Terms of unavailable stages are left out.
`assessment` says what the result can claim: `indicators_found`, `model_prediction_only`,
`no_indicators_found` (with a limitation that this is not a finding of safety),
`insufficient_evidence` or `not_assessed`; it is also in the complaint's `latest_analysis`. Citizen
`HIGH_RISK_ANALYSIS` alerts require a rule-based indicator. The full formula is in `limitations` and `score_components`. Bands: LOW 0-34, MEDIUM 35-69,
HIGH 70-89, CRITICAL 90-100. Since migration 6 the exact level (including CRITICAL) is stored in
`analyses.risk_level`; the `risk_level` list filter is exact (`HIGH` no longer includes CRITICAL)
and `/me/stats` has `by_risk_level` (latest analysis per own complaint, plus `not_scored` and
`not_analysed`).

### Failure semantics (never a silent LOW)

| Situation | Result |
|---|---|
| title + description has fewer than 4 words (empty, whitespace, "help me") | `status: INSUFFICIENT_INPUT`, `risk_score`/`risk_level` null, the text scorer and category model are NOT run (`skipped_insufficient_input`); URL/format checks still reported |
| text scorer cannot be imported / loaded, or raises | `text_risk_scorer: unavailable` (`ImportError`..., `scoring_error`), `status: PARTIAL`; a result that would be LOW is withheld (null) |
| category model missing, SHA-256 pin mismatch, sklearn mismatch, failed smoke test, inference error | `category_classifier: unavailable` (`missing_artifact`, `pin_mismatch`, ..., `inference_error`), `status: PARTIAL`; a result that would be LOW is withheld (null) |
| an audio evidence file was not transcribed (any status other than `transcribed` / `truncated`) | `audio_transcription: unavailable` with the reason, `status: PARTIAL`, the file listed in `unanalysed_attachments`; a result that would be LOW is withheld (null) |
| nothing could be scored | `status: UNAVAILABLE`, no level |
| Ollama timeout / garbage / ungrounded text | no LLM text, `llm_explanation: timeout | error | rejected` with the reason, `status: PARTIAL`; score, level and category unchanged |

A LOW level is given only when both core text stages (scorer and category model) ran and every
uploaded recording was transcribed and scored. Evidence that
reaches MEDIUM or above without them is still reported (not lowered, not hidden). Every path has a
test (`tests/test_failure_semantics.py`, `tests/test_llm_grounding.py`) asserting no LOW and no
BENIGN.

### Provenance

Every indicator, explanation part (`explanation_parts`) and recommended action has `source` in a
fixed vocabulary, a `method` and an `evidence` pointer (`complaints/provenance.py`):

| `source` | What | `method` examples |
|---|---|---|
| `rule` | deterministic logic | `call_guard.rules`, `url_heuristics`, `entity_checks`, `risk_formula`, `explanation_template`, `pipeline_status`, `input_check` |
| `ml` | trained models (synthetic data only) | `call_guard.clf-v1` (`SCRIPT_CLASSIFIER_MATCH`), `category-v1` |
| `llm` | local Ollama, wording only | `ollama:qwen2.5:0.5b` |
| `static_guidance` | authored advice keyed by category and level | `static_guidance_table` |

`evidence` is one of: `text_span` (field `title`/`description`/`text`, `start`/`end` offsets into
that field, a snippet of at most 160 characters, `granularity: sentence`: the sentence in which the
call-guard rules found the cue; never the whole text), `field` (the input field, plus `host` for a
URL), `whole_text` (a model that scores the whole text), `evidence_transcript` (a cue in an audio
transcript: `evidence_id`, `start_s`, `end_s`, snippet), `keyed_by` (guidance), `generated` (LLM).
The old indicator `source` value (`call_guard`, `url_heuristics`) is kept as `stage`.
`provenance` on the analysis lists `stages` (stage, source, method, status `ran | unavailable |
skipped | not_configured | rejected | timeout | error`, reason, version), `ran`, `unavailable`,
`skipped` and `model_versions` (`call_guard_rules`, `call_guard_classifier`, `category`, `llm`).
Rows stored before provenance get one derived from `pipeline_version` and `capabilities`
(`derived_from_legacy: true`, indicator evidence `not_recorded`); an unrecognised pipeline is marked
`legacy: true`.

* `confidence` is the category model's top-class score when it ran (also for `UNDETERMINED`),
  `confidence_semantics: model_confidence_uncalibrated`; `confidence_interpretation` says in plain
  words what it is (top-class score of a model trained on 100% synthetic data, uncalibrated, not
  the chance of being right; UNDETERMINED below the abstention threshold). `category_model` has
  `top_categories` (top 3 with scores) and `abstention_threshold`.
* `status`: COMPLETED when every applicable stage ran; PARTIAL when some applicable stage was
  unavailable (named in `unavailable_capabilities`); UNAVAILABLE when nothing could be scored;
  INSUFFICIENT_INPUT for too-short text.
* `explanation` is always the deterministic rule-based text (`explanation_source: rules`; the rule
  parts of `explanation_parts` joined). `llm_explanation` is additional, separate, labelled, and
  never changes the score, level or classification.
* `recommended_actions` are static, reviewed-in-principle guidance keyed by category and level
  (`source: static_guidance`): 1930 helpline, cybercrime.gov.in, bank/UPI dispute, never share an OTP.
  **They need legal and domain review (`review_required`) before production use.**
* Optional complaint fields and files are never fraud evidence when absent.
* HIGH and CRITICAL results alert the owner once per complaint and level: re-running the analysis
  (by the owner or staff) does not repeat the alert; a new level (HIGH then CRITICAL) alerts once
  more. `alerts.risk_level` records it.

### Ollama (optional, local, grounded)

```
ollama pull qwen2.5:0.5b        # owner action (fits an 8 GB machine); qwen2.5:3b preferred when hardware allows
export COMPLAINTS_OLLAMA_URL=http://127.0.0.1:11434   # or the shared OLLAMA_URL; `make demo` sets it when a local Ollama answers
```
Only local hosts are contacted (loopback, `*.localhost`, private/link-local IPs, single-label
compose names); anything else is `unavailable (non_local_host)` and is never called. Calls
`POST /api/generate` (non-streaming, temperature 0.1, `num_predict` 120, timeout `OLLAMA_TIMEOUT_S`
default 8 s, at most 2 concurrent). **Grounding:** the prompt lists only the computed findings (up to
5, strongest first: plain text + sanitised evidence snippet), the suggested category and the first 3
static steps; the complaint text is not sent. The LLM is not called when there are no findings. The
answer is checked by the shared `svckit.llm_grounding.review_output` (also used by Live
Protection): links/addresses/numbers not in the prompt are stripped, every complete sentence is
checked (at most 3 are shown; repeats and list echoes dropped), and it is REJECTED (`rejected` +
reason) when empty or too long (`output_too_long`, > 700 chars), not English (`wrong_language`), a
copy of the prompt's quoted evidence (`prompt_echo`), claiming a likelihood (`likelihood_claim`),
certainty, safety or evidence, or telling the reader as a fact that they are under arrest
(`unsupported_claim`, e.g. "the caller has evidence of a financial transaction", "You are under
digital arrest."), turning the roles around (`role_inversion`: per sentence, ignoring quotes, case
and punctuation, the caller / scammer / sender / officer described as under arrest, placed under
(digital) arrest, investigated, threatened, the victim or asked to pay; "claims to be under
arrest"; "you are from the CBI"; the safety steps attributed to the caller. Regression: "the caller
is under 'digital arrest'", produced by qwen2.5:0.5b on 2026-10-15), repeating a scam instruction as
advice (`repeats_scam_instruction`, "Do not tell anyone about this call."), naming a scam cue not
among the findings (`ungrounded_indicator`) or referring to none of them (`not_grounded`).
Attributed or negated wording passes ("the caller says you are under digital arrest", "the caller
claims the victim is ...", "the victim was told ...", "you are not under digital arrest"). The rule explanation stays
primary; the console shows accepted text separately as "LLM-generated (model), may be imprecise".
These checks are heuristics: an accepted text can still be clumsy or imprecise (see the Phase B
report for live outputs). `/admin/health` shows `ollama: not_configured | reachable | unreachable |
non_local_host`; `/models` shows `ready` only when the model is pulled. Opt-in live test:
`OLLAMA_LIVE_TESTS=1 python -m pytest -s tests/test_ollama_live.py`.

## ML: category classifier (SYNTHETIC data only)

* Dataset: `data/complaints_synthetic_v1.csv` (3,680 template-generated rows, 9 categories, English,
  romanised Hindi, Devanagari; see `data/README.md`). Regenerate: `python -m complaints.ml.synth`.
* Train: `python -m complaints.ml.train` (seeded). Split by scenario family 60/20/20 (no template
  overlap); C and the abstention threshold chosen on validation; the artifact
  (`complaints/ml/artifacts/category-v1.joblib`, 0.4 MB, SHA-256 pinned, sklearn version checked,
  smoke tested at load) is fitted on train only. Details: `complaints/ml/MODEL_CARD.md`.

Held-out test metrics (**SYNTHETIC data, not real-world performance**), from
`category-v1.metrics.json`:

| Metric | Value |
|---|---|
| macro-F1 / accuracy (argmax, n=727) | 0.715 / 0.714 |
| abstention rate at min confidence 0.25 / accuracy on non-abstained | 0.391 / 0.815 |
| macro-F1 with fresh typo noise | 0.683 |
| accuracy en / hi / dv | 0.716 / 0.761 / 0.571 |
| hard negatives answered as a fraud category | 0.159 |
| leaky random-split macro-F1 (NOT the headline) | 0.985 |

Weakest class: SOCIAL_MEDIA_FRAUD (F1 0.50). Per-class tables and the confusion matrix are in the
metrics file.

## Environment variables (names only; placeholders are in `infra/.env.example`)

| Variable | Purpose |
|---|---|
| `COMPLAINTS_DATABASE_URL` | SQLAlchemy URL (required) |
| `COMPLAINTS_EVIDENCE_DIR` | evidence directory (required) |
| `COMPLAINTS_AUTH_MODE` | `clerk` (default), `demo`, or `local` (= demo + the dev-only email code sign-in, which `demo` also enables) |
| `CLERK_PUBLISHABLE_KEY` | optional `pk_...` browser key returned by `GET /auth/config` |
| `CLERK_ISSUER`, `CLERK_JWKS_URL` | required in clerk mode (https) |
| `CLERK_AUTHORIZED_PARTIES`, `CLERK_AUDIENCE` | optional `azp` / `aud` checks |
| `CLERK_BACKEND_LOOKUP`, `CLERK_SECRET_KEY` | optional primary-email lookup (off; never logged) |
| `COMPLAINTS_DEMO_JWT_SECRET` (falls back to `GATEWAY_JWT_SECRET`) | demo/local modes only (signs and verifies local sign-in tokens) |
| `INVESTIGATOR_ALLOWLIST` | verified emails allowed to enter as investigator |
| `COMPLAINTS_DNA_ENABLED`, `COMPLAINTS_DNA_MODEL`, `COMPLAINTS_DNA_DIR` | Fraud DNA on/off (default on), embedding model id (must be in the local Hugging Face cache), Qdrant embedded index directory (memory when unset) |
| `ADMIN_BOOTSTRAP_EMAILS` | verified emails promoted to ADMIN at first sign-in |
| `COMPLAINTS_CORS_ORIGINS` | explicit origins (`*` is refused) |
| `COMPLAINTS_MAX_IMAGE_BYTES`, `COMPLAINTS_MAX_AUDIO_BYTES`, `COMPLAINTS_MAX_EVIDENCE_PER_COMPLAINT`, `COMPLAINTS_MAX_JSON_BYTES` | limits |
| `COMPLAINTS_RATE_WRITE_PER_MIN`, `..._UPLOAD_...`, `..._ANALYSIS_...`, `..._READ_...` | per-user limits |
| `COMPLAINTS_RATE_ANALYZE_PER_MIN` | per-user limit for `POST /analyze` (default 10) |
| `COMPLAINTS_OLLAMA_URL` (wins) or `OLLAMA_URL`, `OLLAMA_MODEL` (default `qwen2.5:0.5b`), `OLLAMA_TIMEOUT_S` (default 8) | optional local, grounded LLM wording |
| `COMPLAINTS_STT_ENABLED` (default 1), `COMPLAINTS_STT_MAX_FILES_PER_RUN` (2, 0-10), `COMPLAINTS_STT_MAX_SECONDS_PER_FILE` (180, 5-1800), `COMPLAINTS_STT_RUN_BUDGET_S` (120, 5-900) | audio evidence transcription and its limits |
| `LIVE_STT_ENGINE` (default `faster_whisper`; `none` disables), `LIVE_STT_MODEL` (default `base`), `LIVE_STT_BEAM`, `LIVE_STT_CPU_THREADS`, `HF_HUB_OFFLINE` | the speech-to-text engine (shared with live-audio; see its README) |
| `CATEGORY_MIN_CONFIDENCE` | overrides the artifact's abstention threshold (0-1) |
| `COMPLAINTS_LOCAL_AUTH_OUTBOX` | **TEST ONLY.** A file path: local sign-in codes are also appended there as JSON lines `{"email", "code"}` (file mode 0600) so automated end-to-end tests can sign in. Refused (`ConfigError`) unless the mode is `local`/`demo`; never set it outside a test run |
| `PORT`, `LOG_LEVEL` | listen port, log level |

## Gateway

`/api/complaints/**` is routed by the gateway (roles citizen, investigator, admin). For this prefix
the gateway forwards `Authorization` and passes an unverified RS256-shaped token through as an
`external` principal (rate limited by IP); the service verifies it. The service never reads
`X-Principal-*` headers. The browser may also call the service directly (CORS allowlist).
INVESTIGATOR is not mapped onto the analyst/officer roles of the intelligence services.

## Threat model

| Threat | Mitigation | Limit |
|---|---|---|
| forged / replayed / confused tokens | RS256 pinned, `none` and HS* rejected, key by `kid`, iss/exp/nbf/iat/azp checked | a stolen valid token works until it expires; no revocation list |
| privilege escalation by claim or body | role only from DB; allowlist and bootstrap need a verified email; allowlist removal takes effect on the next request (effective role re-checked per request); audit rows | whoever controls the allowlist/env or an ADMIN account controls roles; admin-granted roles persist until an admin changes them |
| IDOR on complaints/evidence | owner check in every handler; others get 404 | none known |
| upload abuse | sniffing, caps, count cap, atomic store, rate limit | no malware scan; content of valid-looking files is untrusted |
| resource exhaustion | JSON cap 64 KiB, streamed upload cap, bounded pagination and filters, in-process per-user rate limits | rate limits are per process, not shared |
| information leak via errors/logs | no echo in 422, 503 without detail, access log has method/route/status/request id/user hash only, tokens/emails/filenames/queries never logged | `q` and filters travel in the URL and reach any proxy that logs URLs |
| DB outage | 503 `service_unavailable`; no secrets in the message | no retry/queue |
| JWKS outage | fail closed (503); stale keys used up to 24 h after a failed refresh | |
| SQL injection | Core with bound parameters; LIKE input escaped | |
| local sign-in abuse (demo/local modes) | off in clerk mode; salted code hash in memory, 10 min, 5 attempts, single use, per-email and per-IP limits, uniform answers | anyone who reads the server terminal can sign in as any address; limits are per process; development only |
| self-promotion through profile | `PATCH /me` accepts only `display_name` and `privacy`; anything else is a 422 | |

## Honest limits

* Live Clerk verification not exercised; the Clerk template claim names are documented from the
  design, not from a running instance.
* Uncalibrated score. The category model was trained on synthetic template data and never validated on real complaints. Screenshots and PDFs are not analysed. Audio is analysed only through a machine transcript (local Whisper `base` on CPU; measured only on synthetic TTS speech; Hindi is written romanised and partly garbled; at most 180 s per file by default; the category model does not read transcripts). URL checks are offline lexical heuristics.
* Evidence spans are sentence-level (where the rules found a cue), not exact token spans; the ML classifier cannot point to a span.
* LLM grounding checks are keyword/pattern heuristics; a small model's accepted text can still be imprecise. It never affects the score, level or category.
* Audio transcription runs inside the analysis request (bounded by the run budget, default 120 s); a slow first run can take that long. There is no background queue.
* No malware scanning; no data-retention or erasure job; evidence stays on one volume (no
  replication).
* The rate limiter and the (optional) backend email-lookup cache are per process.
* Migrations are additive only (no down-migrations, no data migrations so far).
* `allow_case_matching` is stored but nothing consumes it yet (Phase D).
* Local sign-in is a development convenience, not an identity provider: it proves only access to
  the server terminal.
* No `/metrics` endpoint (not scraped by Prometheus).
* `latitude`/`longitude` are accepted and stored but not used by any analysis here.

## Run and test

```
pip install -e "services/complaints[dev]"          # also: pip install -e services/call_guard
pip install -e "services/complaints[stt]"          # optional: audio evidence transcription
cd services/complaints && python -m pytest -W error -q
LIVE_STT_TESTS=1 HF_HUB_OFFLINE=1 python -m pytest -W error -q -s tests/test_stt_live.py   # real Whisper (macOS say)
OLLAMA_LIVE_TESTS=1 python -m pytest -W error -q -s tests/test_ollama_live.py              # live local Ollama
bash scripts/with_postgres.sh                      # whole suite on a disposable local Postgres
python -m complaints.migrate                       # apply pending migrations (needs COMPLAINTS_DATABASE_URL)
python -m complaints                               # uvicorn, access log off
```
