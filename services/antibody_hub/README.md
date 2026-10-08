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
  `source_bank` must be listed in `HUB_BANKS` when that is set.

## Endpoints (behind the gateway)

Headers `X-Principal-Role/-Sub/-Bank` are trusted only when `HUB_GATEWAY_SECRET` is set and
matches `X-Gateway-Secret` (constant time) or `TRUST_GATEWAY_HEADERS=1`; otherwise 401.

| Endpoint | Roles | Notes |
|---|---|---|
| `POST /antibodies` | analyst, admin | body `kind, key_hash, source_bank, evidence_ref?, extend?` (`evidence_ref` is an opaque case reference `^[A-Za-z0-9._:-]{1,64}$`). 201 new; 200 idempotent repeat (same id, no event, expiry untouched); `extend:true` pushes expiry out (one audit entry + updated event); protected hash 409 `PROTECTED` |
| `DELETE /antibodies/{id}?reason=` | analyst, admin | reason required (free text); idempotent; publishes tombstone (`revoked=true`) |
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

## Free text, validation errors and logs

`reason` and `note` (max 200 chars) are rejected with 422 if they contain 9+ digits (separators
allowed), an e-mail / UPI handle (`x@y`), a `+NN` phone prefix, a 10-digit mobile number or an
IFSC-shaped token; `evidence_ref` must be an opaque reference and also passes that check. 422
bodies contain only `loc`/`msg`/`type`, never the submitted value. Nothing submitted is logged.

## Cross-bank behaviour and trust model

* A second bank re-submitting an existing antibody gets 200 with only
  `{antibody_id, active, expires_at}` (the first bank's `confirmed_by` / `source_bank` are not
  disclosed). Its corroboration is stored (`corroborations`) and audited in the ledger
  (`antibody.corroborated`); no new bus event.
* Prototype trust model: any analyst/admin may revoke any antibody (false-positive handling must
  not wait on the originating bank). The actor and `revoked_by_bank` are recorded, and a revoke by a
  bank other than `source_bank` is audited as `antibody.revoked.cross_bank`.
* Recoverability (plan Review Focus 3): a wrongful block is bounded by the 14-day TTL and cleared
  by revoke (tombstone reaches banks in seconds); a wrongful revoke is undone by re-submission,
  which creates a new generation and event. Allowlisted (`protected`) hashes can never be blocked.

## Operations

* Outbox: sent rows are purged after `HUB_OUTBOX_RETENTION_DAYS` (default 7) by the periodic sweep.
  A row whose publish keeps failing is retried each drain; after `HUB_OUTBOX_MAX_ATTEMPTS` (10) it
  is **parked** (gauge `antibody_hub_outbox_parked`, alert on > 0). Rows for other keys keep
  flowing; later rows for the parked row's key (key_hash on the antibody topic) are held back so
  per-key order is preserved until an operator resolves it.
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
