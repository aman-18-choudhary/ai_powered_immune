# complaints

Citizen complaints service for the ScamShield Immune stack: complaint intake with a verified
identity, three roles (CITIZEN, INVESTIGATOR, ADMIN), evidence file uploads, a minimal and honest
text analysis, alerts and an admin API. FastAPI, SQLAlchemy Core (SQLite in tests, Postgres in the
stack), plain Postgres only.

**Status of the pieces (read this first)**

| Piece | Status |
|---|---|
| API, RBAC, storage, validation, uploads | implemented; 175 tests on SQLite, the same suite passed against a disposable Postgres 14 (`scripts/with_postgres.sh`) |
| Clerk token verification | implemented and tested ONLY with locally generated RSA keys and an injected JWKS. **Live Clerk verification has not been exercised: blocked by configuration** (no Clerk instance or keys exist for this repository) |
| Clerk Backend API email lookup | defined, OFF by default, tested only against a mock transport |
| Analysis | text-only, in-process call-guard scorer; the score is an UNCALIBRATED heuristic ordering. No category classifier, no LLM explanation, no evidence-file analysis (all reported as `unavailable`) |
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
* Removing an address from the allowlist does not demote an existing investigator; use the admin
  API.

### Clerk configuration the service needs

Create a Clerk JWT template (or session-token customisation) that adds these claims:
`email` (primary email), `email_verified` (boolean) and optionally `name`. The exact template
shortcodes depend on the Clerk dashboard version and were not verified here. If the claims are
absent the identity is still valid but `email_verified` is false, so investigator/admin eligibility
fails closed. Set `CLERK_ISSUER`, `CLERK_JWKS_URL` (both https) and, recommended,
`CLERK_AUTHORIZED_PARTIES` (the console's origin; a token without a matching `azp` is rejected).

`COMPLAINTS_AUTH_MODE=demo` accepts HS256 tokens (for example from the gateway demo login) signed
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
| `POST /auth/entry` | any | `{entry: citizen | investigator}` |
| `POST /complaints` | CITIZEN | required: `category`, `title`, `description` (10-5000). Optional fields stay NULL when absent or blank. `Idempotency-Key` header (24 h, per user) |
| `GET /complaints` | any | citizen: own only; staff: all non-deleted. Filters `status category city state incident_from/to created_from/to risk_level has_evidence q`, `sort`, `limit` (1-100), `offset` |
| `GET /complaints/{id}` | owner, INVESTIGATOR, ADMIN | others get 404, identical to a missing id; includes status history, latest analysis summary, evidence list |
| `PATCH /complaints/{id}/status` | INVESTIGATOR, ADMIN | valid transitions only, history row, alert to the owner |
| `DELETE /complaints/{id}` | owner (CITIZEN) | soft delete, only while SUBMITTED, audited |
| `POST /complaints/{id}/evidence` | owner (CITIZEN) | multipart, one file in field `file` |
| `GET /complaints/{id}/evidence`, `.../{eid}/download` | owner, staff | `Content-Disposition: attachment`, `nosniff` |
| `POST /complaints/{id}/analysis`, `GET .../analysis` | owner, staff | new version per run |
| `GET /alerts`, `POST /alerts/{id}/read`, `GET /me/stats` | any (own data) | stats are computed from the DB, zeros when empty |
| `GET /admin/users`, `GET /admin/audit`, `GET /admin/health` | ADMIN | |
| `PUT /admin/users/{id}/role`, `PUT /admin/users/{id}/active` | ADMIN | optimistic `expected_role`; last active admin protected; audited |

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
| change complaint status | no | yes | yes |
| delete complaint | own, SUBMITTED only | no | no |
| run analysis | own | any | any |
| alerts, stats | own | own | own |
| users, roles, audit, health | no | no | yes |

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

## Analysis (WP-2 scope)

`POST /complaints/{id}/analysis` scores title + description with the in-process call-guard scorer
(rules + TF-IDF/logistic classifier trained on simulator and authored data). `risk_score` is
`round(score * 100)`; levels LOW < 35, MEDIUM 35-69, HIGH >= 70. `score_semantics` is
`uncalibrated_heuristic`: it is an ordering of texts, not a likelihood, and it was built for call
scripts, so a victim-voiced narrative can score lower than the caller's words quoted in it.

* `indicators`: the scorer's reason codes with its human text. `classification`, `confidence`,
  `explanation`: null (no classifier, no LLM). `capabilities` says so per component.
* `status`: COMPLETED when the scorer ran; UNAVAILABLE (no score, no level; never LOW) when it
  cannot be loaded or fails; PARTIAL is reserved for a configured optional step failing.
* `recommended_actions`: static guidance keyed by category and level, labelled
  `source: static_guidance`. It is authored text that needs legal and domain review before
  production use.
* Optional complaint fields and uploaded files are not used and never count as fraud evidence.
* HIGH results create one alert for the complaint owner per analysis version; nothing is
  fabricated for investigators.

## Environment variables (names only; placeholders are in `infra/.env.example`)

| Variable | Purpose |
|---|---|
| `COMPLAINTS_DATABASE_URL` | SQLAlchemy URL (required) |
| `COMPLAINTS_EVIDENCE_DIR` | evidence directory (required) |
| `COMPLAINTS_AUTH_MODE` | `clerk` (default) or `demo` |
| `CLERK_ISSUER`, `CLERK_JWKS_URL` | required in clerk mode (https) |
| `CLERK_AUTHORIZED_PARTIES`, `CLERK_AUDIENCE` | optional `azp` / `aud` checks |
| `CLERK_BACKEND_LOOKUP`, `CLERK_SECRET_KEY` | optional primary-email lookup (off; never logged) |
| `COMPLAINTS_DEMO_JWT_SECRET` (falls back to `GATEWAY_JWT_SECRET`) | demo mode only |
| `INVESTIGATOR_ALLOWLIST` | verified emails allowed to enter as investigator |
| `ADMIN_BOOTSTRAP_EMAILS` | verified emails promoted to ADMIN at first sign-in |
| `COMPLAINTS_CORS_ORIGINS` | explicit origins (`*` is refused) |
| `COMPLAINTS_MAX_IMAGE_BYTES`, `COMPLAINTS_MAX_AUDIO_BYTES`, `COMPLAINTS_MAX_EVIDENCE_PER_COMPLAINT`, `COMPLAINTS_MAX_JSON_BYTES` | limits |
| `COMPLAINTS_RATE_WRITE_PER_MIN`, `..._UPLOAD_...`, `..._ANALYSIS_...`, `..._READ_...` | per-user limits |
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
| privilege escalation by claim or body | role only from DB; allowlist and bootstrap need a verified email; audit rows | whoever controls the allowlist/env or an ADMIN account controls roles |
| IDOR on complaints/evidence | owner check in every handler; others get 404 | none known |
| upload abuse | sniffing, caps, count cap, atomic store, rate limit | no malware scan; content of valid-looking files is untrusted |
| resource exhaustion | JSON cap 64 KiB, streamed upload cap, bounded pagination and filters, in-process per-user rate limits | rate limits are per process, not shared |
| information leak via errors/logs | no echo in 422, 503 without detail, access log has method/route/status/request id/user hash only, tokens/emails/filenames/queries never logged | `q` and filters travel in the URL and reach any proxy that logs URLs |
| DB outage | 503 `service_unavailable`; no secrets in the message | no retry/queue |
| JWKS outage | fail closed (503); stale keys used up to 24 h after a failed refresh | |
| SQL injection | Core with bound parameters; LIKE input escaped | |

## Honest limits

* Live Clerk verification not exercised; the Clerk template claim names are documented from the
  design, not from a running instance.
* Uncalibrated score, text only, no category classifier, no explanations.
* No malware scanning; no data-retention or erasure job; evidence stays on one volume (no
  replication).
* The in-process rate limiter and idempotency cache of the JWKS lookup are per process.
* Create-only schema (no in-place migrations); `python -m complaints.migrate` runs `create_all`
  behind a Postgres advisory lock.
* No `/metrics` endpoint (not scraped by Prometheus).
* `latitude`/`longitude` are accepted and stored but not used by any analysis here.

## Run and test

```
pip install -e "services/complaints[dev]"          # also: pip install -e services/call_guard
cd services/complaints && python -m pytest -W error -q
bash scripts/with_postgres.sh                      # whole suite on a disposable local Postgres
python -m complaints.migrate                       # one-shot schema creation (needs the env)
python -m complaints                               # uvicorn, access log off
```
