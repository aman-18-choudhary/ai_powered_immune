# complaints

Citizen complaints service for the ScamShield Immune stack: complaint intake with a verified
identity, three roles (CITIZEN, INVESTIGATOR, ADMIN), evidence file uploads, a minimal and honest
text analysis, alerts and an admin API. FastAPI, SQLAlchemy Core (SQLite in tests, Postgres in the
stack), plain Postgres only.

**Status of the pieces (read this first)**

| Piece | Status |
|---|---|
| API, RBAC, storage, validation, uploads, migrations, profile, local sign-in | implemented; 364 tests on SQLite (+1 Postgres-only skipped), the same suite (365) passed against a disposable Postgres 14 (`scripts/with_postgres.sh`) on 2026-10-14 |
| Local sign-in (email + one-time code in the server terminal) | implemented and tested; **development only**, `demo`/`local` modes, absent in `clerk` mode |
| Clerk token verification | implemented and tested ONLY with locally generated RSA keys and an injected JWKS. **Live Clerk verification has not been exercised: blocked by configuration** (no Clerk instance or keys exist for this repository) |
| Clerk Backend API email lookup | defined, OFF by default, tested only against a mock transport |
| Analysis (pipeline v2) | call-guard text scorer + category classifier trained on SYNTHETIC data only + offline URL/format heuristics + rule-based explanation; optional local-Ollama explanation (not exercised live here). Score is an UNCALIBRATED heuristic. Audio transcript, screenshot OCR, document text: `unavailable` (listed as unanalysed attachments) |
| Malware scanning of uploads | **not implemented** |
| Qdrant, Neo4j, PostGIS, Ollama, Redis | not used; reported as `not_configured` by `/admin/health` |

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
| `DELETE /complaints/{id}` | owner (CITIZEN) | soft delete, only while SUBMITTED, audited |
| `POST /complaints/{id}/evidence` | owner (CITIZEN) | multipart, one file in field `file` |
| `GET /complaints/{id}/evidence`, `.../{eid}/download` | owner, staff | `Content-Disposition: attachment`, `nosniff` |
| `DELETE /complaints/{id}/evidence/{eid}` | owner while SUBMITTED, ADMIN any time | marks the row `deleted`, removes the file, audited (`evidence_delete`, no file name); an admin removal alerts the owner. `200 {id, status: "deleted", file_removed}` (`file_removed: false` = the row is deleted but the file could not be removed, logged). Foreign or unknown ids: 404; INVESTIGATOR: 403; owner after review started: 409 `evidence_not_deletable` |
| `POST /complaints/{id}/analysis`, `GET .../analysis` | owner, staff | new version per run (pipeline v2) |
| `POST /analyze` | any authenticated user | stateless analysis of `text` (10-5000) plus optional `suspicious_url`, `suspect_phone`, `upi_id`, `suspect_email`, `amount`, `payment_method`, `city`, `state`, `social_platform`; nothing stored, content not logged, `persisted: false`; own rate bucket |
| `GET /alerts`, `POST /alerts/{id}/read`, `GET /me/stats` | any (own data) | stats are computed from the DB, zeros when empty |
| `GET /admin/users`, `GET /admin/audit`, `GET /admin/health` | ADMIN | |
| `PUT /admin/users/{id}/role`, `PUT /admin/users/{id}/active` | ADMIN | roles granted here have source `admin`; optimistic `expected_role`; last active admin protected; audited |

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
| read own complaints / evidence / analysis | yes | n/a | n/a |
| read all complaints / evidence / analysis | no (404) | yes | yes |
| upload evidence | own only | no | no |
| remove evidence | own, SUBMITTED only | no | any |
| own profile and privacy (`/me`) | yes | yes | yes |
| change complaint status | no | yes | yes |
| delete complaint | own, SUBMITTED only | no | no |
| run analysis | own | any | any |
| alerts, stats | own | own | own |
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
  same bytes again creates a new record.

## Bank details and privacy settings

* `bank_name`, `bank_account`, `ifsc` are optional and NULL when absent. They are returned to the
  owner and to staff only (like every complaint field; this service masks no complaint field in
  list views). Invalid values are a 422 that does not echo the value.
* `users.allow_case_matching` (default true, `PATCH /me`): when false, the user's complaints are to
  be excluded from future similarity / network matching shown to other parties. **It is stored
  now and not yet enforced: no such matching exists in this service yet; the Fraud DNA / network
  phase (Phase D) will honour it.**

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

## Analysis pipeline v2

`pipeline_version` is `complaints-analysis-v2+<text model>+<category model>`. Previous (v1) rows stay
valid; API fields were only added. Each stage reports its own status in `capabilities`
(`available | unavailable | not_applicable | skipped_no_input`; the LLM stage can also be
`timeout | error`) with a machine reason in `capability_reasons`. A failing stage never blocks
another and is never turned into LOW or BENIGN.

| Capability | Meaning |
|---|---|
| `text_risk_scorer` | call-guard rules + classifier on title and description (built for digital-arrest scripts) |
| `category_classifier` | TF-IDF + logistic regression, text only, abstains to `UNDETERMINED`; fallback `classification: null` |
| `url_heuristics` | offline lexical checks: scheme, IP host, punycode/IDN, many subdomains, static suspicious-TLD list, shorteners, brand look-alike (small static list), credentials in URL, long query, odd port. `skipped_no_input` without a URL |
| `url_fetch_reputation`, `contact_reputation_lookup` | always `unavailable` (no network lookups by design) |
| `entity_format_checks` | phone (E.164-like, placeholders), UPI id format and known-handle list, email syntax. Weight 0: unusual values are data-quality notes, not evidence |
| `geographic_context` | presence recorded as context; not scored (hotspot comparison is not part of this service) |
| `audio_transcription`, `screenshot_ocr`, `document_text_extraction`, `evidence_content_analysis` | `unavailable` (no engine installed); files are listed under `unanalysed_attachments` by type and count |
| `llm_explanation` | `unavailable` (`not_configured`), `available`, `timeout`, `error` |

**Risk score** (`score_semantics: "uncalibrated_heuristic"`):
`risk = min(100, max(text_score, category_prior) + round(25 * noisy_or(url_indicator_weights)))`.
`text_score = round(100 * call-guard score)`. `category_prior` applies only when the classifier
answered (DIGITAL_ARREST 70, OTP_SCAM 60, UPI/PHISHING/INVESTMENT 55, BANK/JOB 50,
SOCIAL_MEDIA 45, OTHER 0); an abstention contributes nothing. Terms of unavailable stages are left
out, and if the text scorer did not run a result that would be LOW is withheld
(`risk_score`/`risk_level` null). The full formula is in `limitations` and
`score_components`. Bands: LOW 0-34, MEDIUM 35-69, HIGH 70-89, CRITICAL 90-100. The legacy
`analyses.risk_level` column only allows LOW/MEDIUM/HIGH, so CRITICAL is stored as HIGH (list
filters and the HIGH alert treat it as HIGH) and the exact level is kept in `detail` and returned by
the API.

* `confidence` is the classifier's maximum class score when it is available (also for
  `UNDETERMINED`), labelled `confidence_semantics: model_confidence_uncalibrated`; otherwise null.
* `status`: COMPLETED when every applicable stage ran; PARTIAL when some applicable stage was
  unavailable (named in `unavailable_capabilities`, for example uploaded audio or screenshots, a
  configured Ollama that failed, or a missing model); UNAVAILABLE only when nothing could be scored.
* `explanation` is always the deterministic rule-based text (`explanation_source: rules`).
  `llm_explanation` is additional and never changes the score, level or classification.
* `recommended_actions` are static, reviewed-in-principle guidance keyed by category and level
  (`source: static_guidance`): 1930 helpline, cybercrime.gov.in, bank/UPI dispute, never share an OTP.
  **They need legal and domain review (`review_required`) before production use.**
* Optional complaint fields and files are never fraud evidence when absent. A new `analyses.detail`
  JSON column holds the v2 extras; migration 2 adds it to an existing table (additive, idempotent).
* HIGH results (including CRITICAL) create one alert for the owner per analysis version.

### Ollama (optional local explanation)

```
ollama pull qwen2.5:3b          # owner action; no model is downloaded by this repository
export OLLAMA_URL=http://localhost:11434
```
Calls `POST /api/generate` (non-streaming, temperature 0.2, timeout `OLLAMA_TIMEOUT_S`, default 20 s,
at most 2 concurrent). The prompt holds only the computed indicators and the description with phone
numbers, URLs, UPI ids, emails and long digit runs replaced by placeholders. Output over 1,200
characters, or containing a link, address, number-like token or a likelihood claim, is discarded
and `llm_explanation` is null with the capability set to `unavailable|timeout|error`. `/admin/health`
shows `ollama: not_configured | reachable | unreachable` using a cached (30 s) `GET /api/tags`.
**Not exercised against a live Ollama here** (not installed); tests use a fake HTTP transport.

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
| `ADMIN_BOOTSTRAP_EMAILS` | verified emails promoted to ADMIN at first sign-in |
| `COMPLAINTS_CORS_ORIGINS` | explicit origins (`*` is refused) |
| `COMPLAINTS_MAX_IMAGE_BYTES`, `COMPLAINTS_MAX_AUDIO_BYTES`, `COMPLAINTS_MAX_EVIDENCE_PER_COMPLAINT`, `COMPLAINTS_MAX_JSON_BYTES` | limits |
| `COMPLAINTS_RATE_WRITE_PER_MIN`, `..._UPLOAD_...`, `..._ANALYSIS_...`, `..._READ_...` | per-user limits |
| `COMPLAINTS_RATE_ANALYZE_PER_MIN` | per-user limit for `POST /analyze` (default 10) |
| `OLLAMA_URL`, `OLLAMA_MODEL` (default `qwen2.5:3b`), `OLLAMA_TIMEOUT_S` (default 20) | optional local LLM explanation |
| `CATEGORY_MIN_CONFIDENCE` | overrides the artifact's abstention threshold (0-1) |
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
* Uncalibrated score. The category model was trained on synthetic template data and never validated on real complaints. Audio, screenshots and PDFs are not analysed. URL checks are offline lexical heuristics.
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
cd services/complaints && python -m pytest -W error -q
bash scripts/with_postgres.sh                      # whole suite on a disposable local Postgres
python -m complaints.migrate                       # apply pending migrations (needs COMPLAINTS_DATABASE_URL)
python -m complaints                               # uvicorn, access log off
```
