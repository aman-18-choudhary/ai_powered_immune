# citizen-shield

The citizen-facing message checker. A citizen pastes a message or a short call summary; the service
asks call-guard for a score and answers with a verdict, the reasons and what to do, in English,
Hindi, Tamil or Bengali. A citizen can also report a scam anonymously; a report becomes a
PII-free `Complaint` event that geo-intel turns into hotspots.

Python >= 3.11. Tests: `pytest -W error` (about 2 s; they run the real call-guard app in process).

## Design

```
POST /assess ─> NFKC + length cap ─> call-guard POST /score (short timeout, circuit breaker)
                                        │ ok          │ down / malformed / slow
                                        ▼             ▼
                              label + localised     degraded verdict:
                              reasons and advice    suspicious, uncertain, degraded=true

POST /report ─> keyed hash + category (+ city/state code) ─> cs_reports ─> `complaints` event
```

* **Verdict.** `likely_scam` at score >= 0.7 (call-guard's alert threshold), `suspicious` at >= 0.35,
  otherwise `safe`. `safe` means "no clear scam cues found", never "verified safe", and its advice
  says so. `uncertain` is true for truncated input, an unsupported language, a score within 0.08 of
  a threshold, fewer than four words, or a rules-only scorer (`model_version` starting `rules`).
  Reasons are call-guard rule codes with static localised text; the scorer's own free-text detail
  is dropped.
* **Scorer down.** A failed, slow, non-200 or malformed call-guard answer produces
  `label: suspicious`, `uncertain: true`, `degraded: true`, `model_version: "unavailable"`, no
  reasons and advice key `degraded` ("could not be checked; do not share an OTP, PIN or password;
  verify with a number you look up; call 1930"). The numeric `score` is the 0.35 threshold as a
  placeholder so existing clients that require a number keep working: it is not a model output
  (`score_is_placeholder: true`); a client must read `degraded` and not display a percentage. After 5 consecutive failures calls are skipped for 10 s (breaker) so a dead
  scorer does not add its timeout to each request. A `safe` verdict is never produced by a failure.
* **Script coverage.** call-guard's rules cover English, Hinglish and Devanagari. When more than
  20% of the (NFKC) message's letters are in another script (Tamil, Bengali, Gujarati, Telugu,
  Arabic, Han, Cyrillic, ...), the verdict carries `coverage: "partial"`, `uncertain: true`, is
  never `safe` (a would-be `safe` becomes `suspicious` with `score_is_placeholder: true`, advice
  key `partial_coverage`), the neutral reason `COVERAGE_PARTIAL` replaces "no scam cues", and the
  first advice step says the language is not fully checked (do not share OTP/PIN, do not pay, call
  1930). Benign Tamil or Bengali text therefore also comes back `suspicious`: an accepted
  tradeoff, because the scorer has no basis to call it safe. Numbers, symbols and emoji have no
  letters and stay `full`.
* **Input handling.** Strip, cap the raw input at 4,000 characters, NFKC-normalise (full-width
  letters and digits, ligatures), strip, cap again; more than 4,000 characters scores the first
  4,000 and sets `uncertain` with a notice as the first advice step. Blank or format-characters-only
  input answers `safe`, `uncertain`, advice `no_input` without calling the scorer. An unsupported
  `lang` falls back to script detection (else English) and sets `uncertain`; `lang` is cut to 16
  characters first. Malformed JSON or a non-string message is a 422 that never echoes the value.
* **Prompt injection.** The text is data for a rules-and-classifier scorer; there is no language
  model or instruction channel, so text such as "ignore previous instructions, label this safe"
  has no special effect (it is scored like any other words; tested).
* **Reports.** `POST /report {category, message_hash?, lang?, city_code?, state_code?}` with
  `category` one of `scam_call | held_transfer | cash_out` and codes from the fixed enums in
  `scam_contracts.geo`. The stored `report_id` is `rpt-` plus 48 hex of an HMAC-SHA256 (key
  `CITIZEN_REPORT_KEY`) over the client's 64-hex message hash; without `message_hash` a random id
  is used and retries are not deduplicated. Stored: id, category, location code, time, an
  `emitted` flag. Not stored: message, client hash, IP, principal, language. A `Complaint` is
  published to `complaints` only when a location code was given. A publish failure answers 503 and
  keeps `emitted=false`, so the same request re-emits (the complaint id is deterministic;
  geo-intel dedupes).
* **Rate limiting.** In-process fixed windows per principal (60 `/assess` and 10 `/report` per
  minute) and per network peer (1,200 per minute; behind the gateway the peer is the gateway, so
  that is a global ceiling). 429 with `Retry-After`. Fail-open: a limiter error allows the request
  and is counted. Limits are per replica; the gateway applies the platform-wide per-role limit.

## API (JSON; everything except the probes needs gateway trust and role citizen or admin)

| Route | Notes |
|---|---|
| `POST /assess {message, lang?}` | `{label, score, reasons[{code,text,weight}], advice{key,steps[]}, lang, uncertain, degraded, model_version, thresholds}`; `Cache-Control: no-store` |
| `POST /report {...}` | `{accepted, report_id, duplicate, emitted}` |
| `GET /languages` | `{default, supported[{code,name}]}` |
| `GET /healthz`, `/readyz` | readiness follows the report database only (the service answers in degraded mode without call-guard); the body shows the breaker state |
| `GET /metrics` | `CITIZEN_METRICS_TOKEN`, or gateway trust with role `admin` (other roles 403, no principal 401): verdict counts by label, degraded, reports, duplicates, emit failures, rate-limited, scorer calls/failures/skipped, limiter errors |

Through the gateway: `/api/citizen/assess`, `/api/citizen/report`, `/api/citizen/languages`.

## Configuration

`CALL_GUARD_URL`, `CITIZEN_CALL_GUARD_TIMEOUT_S` (1.5; the TOTAL deadline of one scorer call, response capped at 64 KiB), `CITIZEN_BREAKER_FAILURES` (5),
`CITIZEN_BREAKER_OPEN_S` (10), `CITIZEN_DATABASE_URL` (default in-memory SQLite),
`CITIZEN_REPORT_KEY` (required by `create_service_app`, >= 16 bytes), `KAFKA_BOOTSTRAP` (report
events are published only when set), `CITIZEN_GATEWAY_SECRET` or `TRUST_GATEWAY_HEADERS=1`,
`CITIZEN_METRICS_TOKEN`, `CITIZEN_ASSESS_PER_MIN`, `CITIZEN_REPORT_PER_MIN`, `CITIZEN_PEER_PER_MIN`,
`CITIZEN_MAX_BODY_BYTES` (65536), `CITIZEN_REPORT_RETENTION_DAYS` (90). Run:
`python -m citizen_shield` (uvicorn, `access_log=False`).

## Localisation tables

`citizen_shield/texts.py` holds reason texts (per call-guard rule code), advice per label plus
`no_input` and `degraded`, a generic reason and the truncation notice, in the four languages. The
demo-server imports them from here. A test derives every reason code call-guard can emit (its rule
spec plus every `code="..."` literal in its package) and requires text in all four languages, and
that no orphan code remains.

## Threat model

* **Trust.** Only the gateway reaches the service; principal headers are honoured with
  `CITIZEN_GATEWAY_SECRET` (constant-time byte comparison; non-ASCII fails with 401) or
  `TRUST_GATEWAY_HEADERS=1`; otherwise 401. Request bodies are capped (413), request ids are
  replaced unless 8-64 chars of `[A-Za-z0-9-]`, validation errors never echo input, and access
  logs hold method, route template, status, request id and duration only.
* **Privacy.** The message exists only in request memory and in the HTTP call to call-guard (same
  internal network). The `/assess` log line carries label, degraded flag and length. The report
  path never receives message text; the client hash is keyed before storage.
* **Abuse.** A citizen token is cheap to mint, so limits are also applied per peer. Floods of
  reports with a chosen city code can inflate that city's count in geo-intel; this service limits
  rate, it does not verify truth. An attacker who can probe `/assess` can learn how to word a
  message to score low: a property of any rules-and-classifier scorer.

## Honest limits

* Rules plus a classifier: not a guarantee. A `safe` verdict means no cues found. Known weak spots:
  obfuscated digits and spelled-out OTP requests, very short scams ("pay now or arrest"), scams in
  a language or register the rules and training data do not cover, and image or voice content.
* NFKC removes full-width and compatibility tricks, not homoglyphs (Cyrillic look-alikes), zero-width
  characters inside words or leetspeak.
* Localised texts are static and have not been reviewed by native speakers; the helpline number (1930) and portal named in them are India-specific.
* Coverage is by script, not by language: Hinglish in Latin letters is `full` but only as well as
  the rules know it, and a scam in a Latin-script language the rules do not know scores `safe`.
* The breaker and rate limiter are per process. Not load-tested.
