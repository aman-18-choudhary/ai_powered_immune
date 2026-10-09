# antibody-hub

Federated, privacy-preserving threat memory. When an analyst CONFIRMS that a payee account is a
mule (or a script / device fingerprint is malicious) the hub stores a **keyed hash**, broadcasts an
`Antibody` event on `antibody.published` (keyed by `key_hash`) and serves Bloom / exact snapshots,
so every other bank's txn-guard can block the same mule within seconds (Task 11 consumes both).

## Threat model and key handling

* Banks compute `key_hash = HMAC-SHA256(federation_key, kind NUL value)` themselves
  (`scam_contracts.hashing.keyed_hash`; `antibody_hub.hashing` re-exports it). The hub **never sees
  raw account numbers / phones and never holds the federation key**; it cannot brute-force the
  small account / phone space without that key. Key distribution and rotation among banks is out
  of scope (rotation = re-hash and re-submit; old hashes expire with the 14-day TTL).
* `key_hash` must match `^[0-9a-f]{64}$`; anything shaped like a raw identifier is rejected 422.
  Raw values are never persisted, published or logged (tests grep DB dump, bus, logs).
* The antibody event carries the full `key_hash` (the intended shared datum). Ledger entries carry
  only a deterministic `payload_hash` (binds antibody_id, event, generation, actor, expires_at).
* `confirmed_by` is the authenticated principal's sub, never a body field. If the principal
  carries `X-Principal-Bank`, `source_bank` must equal it (bank A cannot publish as bank B).
  `source_bank` must be listed in `HUB_BANKS` when that is set; when it is empty (default) only `^[a-z0-9_-]{2,32}$` is accepted (no free text).

## Endpoints (behind the gateway)

Headers `X-Principal-Role/-Sub/-Bank` are trusted only when `HUB_GATEWAY_SECRET` is set and
matches `X-Gateway-Secret` (constant time) or `TRUST_GATEWAY_HEADERS=1`; otherwise 401.

| Endpoint | Roles | Notes |
|---|---|---|
| `POST /antibodies` | analyst, admin | body `kind, key_hash, source_bank, evidence_ref?, extend?` (`evidence_ref` is an opaque case reference `^[A-Za-z0-9._:-]{1,64}$`). 201 new; 200 idempotent repeat (same id, no event, expiry untouched); `extend:true` pushes expiry out (one audit entry + updated event); protected hash 409 `PROTECTED` |
| `POST /antibodies/{id}/revoke` | analyst, admin | JSON `{reason}` required (free text); idempotent; publishes tombstone (`revoked=true`). `DELETE /antibodies/{id}` is removed and answers 400 (a query-string reason would reach access logs) |
| `POST /admin/outbox/unpark` | admin | re-queue parked outbox rows |
| `GET /antibodies?state=active\|all&limit=` | officer, analyst, admin | limit 1..1000, soonest expiry first |
| `GET /antibodies/{id}` | officer, analyst, admin | 404 if missing |
| `GET /antibodies/bloom?bank_id=` | bank, admin | Bloom snapshot `{version,n,m,k,fp_rate,count,bits(base64),generated_at}`; `ETag`/`If-None-Match` -> 304 (checked before any build; builds are cached by version); `HEAD` returns the ETag only. `bank_id` must be in `HUB_BANKS` (else 403); role `bank` must be that bank |
| `GET /antibodies/exact?bank_id=&since=&limit=` | bank, admin | active hashes, cursor paginated (`next_cursor`) |
| `POST /protected` | admin | add allowlisted hash (note: free text, see below); any active antibody for it is revoked with a tombstone. Idempotent: 201 new, 200 existing |
| `DELETE /protected/{key_hash}` | admin | remove an allowlist entry (idempotent, audited in the ledger) |
| `/healthz`, `/readyz`, `/metrics` | metrics: `HUB_METRICS_TOKEN` or staff role | |

## Semantics

* TTL `ANTIBODY_TTL_DAYS` (default 14). A sweep in the lifespan (and every drain interval,
  `HUB_DRAIN_INTERVAL_S`, default 5) marks lapsed antibodies revoked-by-expiry (`system:expiry`) and
  publishes exactly ONE tombstone each. Expired and revoked entries are excluded from Bloom,
  exact and the default listing immediately, even before the sweep runs.
* One active antibody per (kind, key_hash); duplicates merge (concurrent POSTs create one record
  and one event). `antibody_id = sha256(kind:key_hash:generation)`; re-submitting after
  revoke/expiry creates generation+1 with a new id and a new event.
* Every state change and its outbox rows (Antibody event + `LedgerEntryIn`) commit in ONE DB
  transaction; the outbox is drained after each change, on idempotent re-entry and by the periodic
  sweep, in order, at-least-once (consumers dedupe on `antibody_id` + `revoked`; the ledger on
  `payload_hash`). A failing bus never fails the request.
* Storage: SQLAlchemy Core, `HUB_DATABASE_URL` (SQLite in tests, Postgres via the `postgres`
  extra). Env: `KAFKA_BOOTSTRAP` (else an in-memory bus, dev only).

## Ledger entries (Task 13)

Every state change enqueues one ledger entry in the same transaction as the change (service
`antibody-hub`, actor = the confirming/revoking principal, `system:expiry` for expiry,
`sub@bank` for corroborations). Payload: `antibody_id, event, generation, kind, key_hash_prefix`
(8 hex), `expires_at` (`YYYY-MM-DDTHH:MM:SSZ`), `actor_role` (gateway role, `system` for expiry),
`actor_bank?` (pseudonymous lowercase slug). `event` is one of `created, extended, expired,
revoked, revoked.cross_bank, corroborated`; `protected.added` / `protected.removed` carry `event,
key_hash_prefix, actor_role, at` (microsecond UTC). Case refs: `[antibody_id, payee_ref:<16 hex of
key_hash>]` (`device_ref:` / `script_ref:` for those kinds; `payee_ref:` only for protected
hashes), so an antibody's whole lifecycle joins the txn-guard holds on the same payee in one case.
An audit problem never fails an analyst action: the actor, `actor_role` and `actor_bank` are
replaced by `redacted_<16 hex>` if the PII guard refuses them, and a payload that is still
refused becomes `{antibody_id, event, generation, kind, audit: payload_refused}`, written in the
same transaction. `source_bank` / `HUB_BANKS` entries must match `^[a-z][a-z0-9_-]{1,31}$` and
pass the identifier guard (422 / startup error otherwise). Not in the ledger: the full `key_hash`, `evidence_ref`, revoke reason, protected-hash note, any
raw identifier. The outbox dedupe key is `sha256(actor | payload_hash)`, so two analysts
corroborating the same antibody remain two entries.

## Free text, validation errors and logs

`evidence_ref` is an opaque reference `^[A-Za-z0-9._:-]{1,64}$`; `reason` and `note` are free text
(max 200). All three go through ONE guard (`contains_identifier`): NFKC + casefold, whitespace and
separators collapsed, zero-width/format/control characters removed, then 422 if it finds 9+ digits
with up to 3 non-digits between any two (or 9+ digits once all punctuation is stripped), an `@`
followed by an alphanumeric (e-mail / UPI, spaces allowed), a `+` prefix or `0091`, a PAN
(`ABCDE1234F`) or an IFSC. Table-driven tests cover parentheses, double spaces, Aadhaar groups,
NBSP/tab/newline/NUL/zero-width separators, circled/superscript digits and full-width `＋`/`＠`.

**Known limits** (the guard is a safety net; analysts are trained not to paste identifiers):
spelled-out numbers, base64/hex-encoded values, and fragments of 8 digits or fewer split across
fields or requests are not detected; unusual scripts' digits are caught only if NFKC maps them.

422 bodies contain only `loc`/`msg`/`type`, never the submitted value. The hub logs no request
bodies and no query strings: the only access log is a minimal line (method, route template,
status, request id, duration); run uvicorn with `access_log=False` (the entrypoint does).

## Cross-bank behaviour and trust model

* Antibody **events carry `source_bank` and `confirmed_by`** (the analyst's pseudonymous principal
  subject, not a name) to every subscribed bank, by design: they are part of the shared datum.
  `expires_at` also reveals `created_at` (= expires_at - TTL). Cross-bank `extend:true` is allowed
  by design; the audit trail records actor and bank.
* HTTP reads are scoped: an analyst bound to another bank (`X-Principal-Bank` != `source_bank`)
  gets only `{antibody_id, kind, key_hash, active, expires_at, revoked}` from `GET /antibodies`,
  `GET /antibodies/{id}`, repeat `POST` and revoke responses. Officers, admins and bank-less
  principals see the full record (incl. `evidence_ref`, `revoke_reason`, `revoked_by`).
* A second bank re-submitting an existing antibody gets 200 with that limited view. Its
  corroboration is stored in `corroborations` (one row per antibody x bank x actor) and audited as
  `antibody.corroborated`; no new bus event.
* Prototype trust model: any analyst/admin may revoke any antibody (false-positive handling must
  not wait on the originating bank). The actor and `revoked_by_bank` are recorded, and a revoke by a
  bank other than `source_bank` is audited as `antibody.revoked.cross_bank`.
* Recoverability (plan Review Focus 3): a wrongful block is bounded by the 14-day TTL and cleared
  by revoke (tombstone reaches banks in seconds); a wrongful revoke is undone by re-submission,
  which creates a new generation and event. Allowlisted (`protected`) hashes can never be blocked.

## Operations

* Outbox: sent rows are purged after `HUB_OUTBOX_RETENTION_DAYS` (default 7) by the periodic sweep.
  A failing row is retried each drain; it is **parked** only after `HUB_PARK_ATTEMPTS` (10)
  failures AND `HUB_PARK_AFTER_S` (900) seconds since its first failure, so a short broker blip
  parks nothing (gauge `antibody_hub_outbox_parked`, alert on > 0). Rows for other keys keep
  flowing, and held keys are filtered before the batch limit so a backlog cannot starve others;
  later rows for a parked row's key (key_hash on the antibody topic) are held back to preserve
  per-key order. `POST /admin/outbox/unpark` (admin) re-queues them
  (`antibody_hub_outbox_unparked_total`).
* Schema: `python -m antibody_hub.migrate` creates tables once per deploy (run replicas with
  `HUB_CREATE_SCHEMA=0`); with the default `1`, replicas serialise `create_all` on a Postgres
  advisory lock. Submit and protect on one hash serialise on `pg_advisory_xact_lock(hashtext(hash))`
  so a protected hash can never end up with an active antibody. No in-place migration of older
  tables (prototype).
* Opt-in Postgres tests: `PYTHON=.../python scripts/with_postgres.sh` (disposable local instance).

## Guidance for txn-guard (Task 11)

Bloom snapshots use fp_rate 1e-6 with 2x capacity headroom (effective rate far lower). A Bloom
filter never misses a member but can false-positive: a Bloom hit that would BLOCK a payment should
be verified against the exact active set (bootstrap with `/antibodies/exact`, keep it current from
the event stream, honouring tombstones); use Bloom alone only as a pre-filter or hold trigger.
Refresh snapshots with `If-None-Match`; events give seconds-level propagation between refreshes.

### Consuming the antibody topic

Delivery is at-least-once, and with several hub replicas events for one antibody may be reordered.
* Dedupe key: `(antibody_id, revoked, expires_at)`. An `extend:true` event repeats
  `(antibody_id, revoked=false)` with a later `expires_at`, so deduping on the shorter key would drop it.
* Merge rule per `antibody_id`: `revoked` is sticky (once any event says revoked, it stays revoked);
  otherwise the event with the greatest `expires_at` wins. No contract change is needed.
* Expiry tombstones and manual-revoke tombstones look identical on the topic (`revoked=true`);
  only the ledger `event_type` (`antibody.expired` vs `antibody.revoked[.cross_bank]`) differs.
* A re-submission after revoke has a new `antibody_id` (new generation), so it is not blocked by the
  sticky tombstone of the old one.
