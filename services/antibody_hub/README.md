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
| `POST /antibodies` | analyst, admin | body `kind, key_hash, source_bank, evidence_ref?, extend?`. 201 new; 200 idempotent repeat (same id, no event, expiry untouched); `extend:true` pushes expiry out (one audit entry + updated event); protected hash 409 `PROTECTED` |
| `DELETE /antibodies/{id}?reason=` | analyst, admin | reason required; idempotent; publishes tombstone (`revoked=true`) |
| `GET /antibodies?state=active\|all&limit=` | officer, analyst, admin | limit 1..1000, soonest expiry first |
| `GET /antibodies/{id}` | officer, analyst, admin | 404 if missing |
| `GET /antibodies/bloom?bank_id=` | bank, admin | Bloom snapshot `{version,n,m,k,fp_rate,count,bits(base64),generated_at}`; `ETag`/`If-None-Match` -> 304. `bank_id` must be in `HUB_BANKS` (else 403); role `bank` must be that bank |
| `GET /antibodies/exact?bank_id=&since=&limit=` | bank, admin | active hashes, cursor paginated (`next_cursor`) |
| `POST /protected` | admin | add allowlisted hash; any active antibody for it is revoked with a tombstone |
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

## Guidance for txn-guard (Task 11)

Bloom snapshots use fp_rate 1e-6 with 2x capacity headroom (effective rate far lower). A Bloom
filter never misses a member but can false-positive: a Bloom hit that would BLOCK a payment should
be verified against the exact active set (bootstrap with `/antibodies/exact`, keep it current from
the event stream, honouring tombstones); use Bloom alone only as a pre-filter or hold trigger.
Refresh snapshots with `If-None-Match`; events give seconds-level propagation between refreshes.
