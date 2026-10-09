# evidence-ledger

A tamper-evident, hash-chained audit log for the scam immune system, plus signed, court-oriented
evidence packages that can be verified offline with the Python standard library alone.

Services (txn-guard, antibody-hub, ...) publish `LedgerEntryIn` events on `ledger.append`. The
ledger appends each one to a single chain, signs periodic checkpoints, and exports per-case
packages. It never receives raw identifiers: entries carry hashes, opaque references and an
optional small PII-free `payload`.

## What is stored

* `LedgerEntryIn` = `service, actor, event_type, payload_hash, model_version?, payload?, case_refs[]`
  (`payload` <= 4 KiB, `case_refs` <= 10 opaque refs `^[A-Za-z0-9._:-]{1,64}$`).
* When `payload` is present the ledger checks `sha256(canonical_json(payload)) == payload_hash`
  and rejects mismatches. Without a payload only the hash is kept ("no payload retained").
* Canonical JSON (`scam_contracts.canonical`, copied byte-for-byte into `verify.py` and pinned by a
  parity test): UTF-8, keys sorted, separators `(",", ":")`, `ensure_ascii=False`, no NaN/Infinity.
  Floats are Python-`repr` canonical only; prefer strings or scaled integers for cross-language use.

## The chain

```
entry_hash = sha256( prev_hash_ascii || canonical_json(entry without entry_hash) )   (hex)
prev_hash of seq 1 = "0" * 64        seq starts at 1, no gaps
```

The hashed entry is `seq, ts, service, actor, event_type, payload_hash, prev_hash, model_version,
payload, case_refs`. `ts` is the ledger's **receipt** time (UTC, `YYYY-MM-DDTHH:MM:SS.ffffffZ`).
**Chain order is receipt order, not event order**: the topic is unordered across replicas and
emitters are at-least-once, so an event can be recorded after a later event that was emitted
afterwards. Event times, where they matter, belong inside the payload.

`append` is idempotent on `(service, event_type, payload_hash)`: a repeat returns the existing
entry and writes nothing (counted as a duplicate). Appends are serialised (process lock on
SQLite, `pg_advisory_xact_lock` on Postgres); the idempotency lookup, chain insert, case-ref rows
and any checkpoint are ONE transaction, so `seq` is gap-free under concurrency and two replicas
racing the same event produce exactly one row (the unique constraint is the backstop).

### Immutability and what it does not give you

`ledger_entries`, `entry_refs`, `checkpoints` and `cases` have triggers that RAISE on UPDATE and
DELETE (and TRUNCATE on Postgres); tests prove it for both dialects. The service's DB role should
be `INSERT, SELECT` only (create the schema with `python -m evidence_ledger.migrate` as a separate
owner role and run replicas with `LEDGER_CREATE_SCHEMA=0`).
**A database superuser can drop the triggers and edit rows.** That is not prevented, it is made
detectable by the hash chain and the signed checkpoints, and by packages you have already exported.

### Signed checkpoints

Every `LEDGER_CHECKPOINT_EVERY` entries (default 100, inside the append transaction) and every
`LEDGER_CHECKPOINT_INTERVAL_S` seconds (default 60, only if there are unsigned entries; the oldest
unsigned entry must be that old) the service signs
`{checkpoint_id, seq, entry_hash, count, ts, key_id}` (Ed25519 over canonical JSON) into the
append-only `checkpoints` table. `GET /checkpoints/latest`, `GET /checkpoints?from_seq=`.
A package export also signs a checkpoint at the head if none covers the case yet (so `GET
/packages` can write one checkpoint row).

### Threat model

| Attack | Detected? |
|---|---|
| Edit any field of any entry (actor, payload, ts, ...) | Yes: `entry_hash` mismatch at that seq (or the next seq's `prev_hash` if the attacker re-hashes the entry) |
| Delete or reorder entries in the middle | Yes: seq gap / order, reported at the first affected seq |
| Append a malformed forged entry | Yes, at its seq |
| Delete the tail | Only with checkpoints: a signed checkpoint beyond the last entry exposes it (reported at last+1). Without a surviving checkpoint (or a previously exported package) nothing remembers the tail existed |
| Rewrite the whole chain from some point and re-hash | Yes if a signed checkpoint at or after that point survives elsewhere: it cannot be reproduced without the signing key |
| Rewrite the whole chain AND hold the signing key (or delete all checkpoints and have no external copy) | **No.** Whoever has the key and DB write access can rebuild a consistent history. Keep the key off the database host, ship checkpoints/head hashes to an external append-only store, and keep exported packages |
| Append a well-formed forged entry after the last checkpoint | **No** until the next checkpoint is signed; the receipt-order chain cannot say who inserted a valid-looking entry (access control and the DB role do) |
| Dishonest or wrong clock | **No.** `ts` is the ledger host's clock; the chain proves order of receipt, not when things happened. Use NTP and treat `ts` as receipt time |
| A compromised emitting service | **No.** The ledger records what it was told |
| Entry content that was false when recorded | **No.** Integrity is not truthfulness |

`first_bad_seq` semantics (see `verify.py` docstring): the seq of the first entry, scanning in
order, at which a check fails; a recomputed-hash mismatch is reported AT the altered entry, a
re-hashed alteration surfaces at the NEXT entry, a missing entry is reported as the missing seq, a
checkpoint mismatch at the checkpoint's seq (tamper at or before it), a truncated tail at last+1.

## Keys

* `LEDGER_SIGNING_KEY_FILE`: Ed25519 PEM (PKCS#8); the file must be mode 0600 (group/other bits
  refused). `LEDGER_SIGNING_KEY_B64` (base64 of the 32-byte seed) is for development only.
  Generate a dev key with `python -m evidence_ledger.keygen [PATH]` (0600, never overwrites,
  prints only the path and public key id).
* `key_id` = first 16 hex of `sha256(raw public key)`. `GET /keys` lists the current and retired
  public keys.
* Rotation: start the service with the new key and list the old public keys (hex or base64 raw
  32 bytes) in `LEDGER_RETIRED_PUBKEYS`; old checkpoints and packages stay verifiable.
* The private key is held only inside `Signer` (no repr, no pickling) and is never logged.
  Use a secret manager or an HSM/KMS-backed signer in production (not provided here).
* A package's embedded public keys are **self-asserted**. A court expert must obtain the signing
  `key_id` from a source independent of the package (published by the operator, notarised, in the
  certificate) and pass it as `--trusted-key-id`.

## Endpoints (behind the gateway)

Headers `X-Principal-Role` / `X-Principal-Sub` are trusted only when `LEDGER_GATEWAY_SECRET` is set
and matches `X-Gateway-Secret` (constant-time bytes compare; non-ASCII -> 401) or
`TRUST_GATEWAY_HEADERS=1`; otherwise 401. The subject must be a pseudonymous token
(`^[A-Za-z0-9._:/-]{1,100}(@bank_id)?$`), it becomes the `actor` of export audit entries.

| Endpoint | Roles | Notes |
|---|---|---|
| `GET /entries?from_seq=&limit=` | officer, admin, analyst | limit 1..1000, `next_from_seq` |
| `GET /entries/{seq}` | same | |
| `GET /head` | same | `{seq, entry_hash, count}` |
| `GET /verify?from_seq=&to_seq=` | officer, admin | server-side `verify_chain` over <= `LEDGER_MAX_VERIFY_SPAN` (5000) entries with the signed checkpoints in range (all later ones when the span reaches the head, which detects tail truncation) |
| `GET /checkpoints/latest`, `GET /checkpoints?from_seq=&limit=` | readers | |
| `GET /keys` | readers | current + retired public keys |
| `POST /cases` | officer, admin | `{case_id, title (<=120, PII-guarded), case_refs[<=20], seqs[<=500]}`; 201 new, 200 identical repeat, 409 different content (cases are immutable) |
| `GET /cases/{id}` | officer, admin | |
| `GET /packages/{case_id}` | officer, admin | the zip; `Content-Disposition`, `ETag` = sha256; 413 if the span exceeds `LEDGER_MAX_PACKAGE_SPAN` (5000): narrow the case |
| `/healthz`, `/readyz`, `/metrics` | metrics: `LEDGER_METRICS_TOKEN` or staff role | counters `appended, duplicates, rejected, dlq, checkpoints`, gauge `head_seq` |

There is no HTTP write path for entries. 422 bodies contain `loc/msg/type` only, never the
submitted value; the access log is method, route template, status, request id and duration (no
query strings, headers or bodies; the entrypoint runs uvicorn with `access_log=False`).

## Consumer

`run_ledger_consumer` reads `ledger.append` (group `evidence-ledger`) through `svckit.consume`.
Malformed JSON, payload-hash mismatches, and identifier-looking content go to `ledger.append.dlq`
and are counted (`rejected`, `dlq`); duplicates are counted (`duplicates`). Entries the ledger
rejects are forwarded to the DLQ as a redacted envelope `{reason, payload_hash}`; messages that
cannot even be parsed are forwarded as received, so restrict that topic's ACL. Transient database
errors are retried (3 attempts, backoff) then dead-lettered. The consumer needs no dedupe memory
of its own (the store is idempotent). The consumer task is restarted if the bus connection fails.

### PII guard

Payload strings and keys, `case_refs`, `service`/`actor`/`event_type` and case titles go through
`svckit.pii` (NFKC, separators collapsed, 9+ digits, e-mail/UPI `@`, `+`/`0091`, PAN, IFSC).
Platform-minted lowercase hex ids of 16+ characters containing a letter (sha256 digests,
`txn_<16 hex>`) are exempt. **Known limits:** spelled-out numbers, encoded values, fragments of 8
digits or fewer, and identifiers glued into a long hex-looking token are not detected. The guard
is a safety net; emitters must not put identifiers in payloads. A raw identifier that does reach
a stored payload is immutable and ends up in every package that includes the entry.

## Evidence packages

`GET /packages/{case_id}` (or `evidence_ledger.package.build`) returns a ZIP that is **byte-for-byte
deterministic** for the same ledger state: stored (uncompressed), fixed member order, 1980-01-01
timestamps, sorted JSON, no randomness. Members:

| Member | Content |
|---|---|
| `entries.json` | the case and the contiguous chain **segment** from the first selected seq to the covering checkpoint's seq, every entry in full, plus `selected_seqs` (the case's entries; the rest is proof material) |
| `chain_proof.json` | segment bounds, anchor (`prev_hash` of the first entry = the preceding entry's `entry_hash`), the signed checkpoint(s) at the end of the segment, all public keys |
| `explanations.md` | timeline of the selected entries rendered ONLY from stored fields: event type, actor, decision, reason codes with a one-line meaning from a static dictionary, model version, receipt time; entries without payload say "No payload retained (hash only)"; other entries listed in a context table |
| `manifest.json` | `case_id`, `generated_from_head_seq`, selected `{seq, entry_hash}`, `sha256` of every other member, `key_id`, Ed25519 `signature_b64` over `canonical_json(manifest without signature)` |
| `certificate_section63_template.md` | template for counsel (below) |
| `verify.py` | the standalone verifier (stdlib only) |

Why a segment: selected seqs may be non-contiguous. Including every entry from the first selected
seq to a signed checkpoint lets the whole segment be recomputed, and the checkpoint signature then
authenticates all of it (a change to any entry changes every later hash). Consequences: (1) a case
spanning more than `LEDGER_MAX_PACKAGE_SPAN` entries is refused with 413; (2) **the package
contains every entry in the span, including entries of other cases** (PII-free by design, but not
confidential from the recipient). Narrow cases, or use a future "redacted proof" format.

`generated_from_head_seq` is the highest seq that is not a `package.exported` entry; counting the
export audit entries themselves would change the bytes of every repeat export.

Each export appends `package.exported` (`actor` = principal, payload `{case_id, package_sha256,
from_seq, to_seq, exported_by}`) to the ledger itself, before the package is returned. It is
idempotent: re-exporting the identical package by the same principal writes nothing; a different
principal's first export of it is recorded.

### Section 63 template: honest scope

The certificate is a **template for counsel to complete**. It is not legal advice and not a
statement that any record is admissible under Section 63 of the Bharatiya Sakshya Adhiniyam, 2023.
Whether the requirements are met depends on counsel's reading of the Act and its Schedule form
and on the operator's actual controls (access control, DB roles, key custody, clock source,
retention, who certifies). This service supplies integrity evidence, not compliance.

### Verifying a package (for a court expert)

1. Obtain `verify.py` from a source you trust (it is also in the package and hashed in the
   manifest; if the package is hostile, its own copy is not evidence of anything). It is ~400 lines,
   stdlib only, readable end to end.
2. Obtain the operator's signing `key_id` independently of the package.
3. `python -S -I verify.py <extracted dir or .zip> --trusted-key-id <key_id>`; no network, no
   packages needed. Exit 0 = PASS, 1 = FAIL with a reason. `--json` for machine output.
4. It refuses on: unexpected/missing members, any member hash differing from the manifest,
   invalid manifest signature, key not the pinned one, broken chain segment, invalid or
   non-matching checkpoint, no signed checkpoint at the end of the segment, or a manifest entry
   list that differs from the verified chain.

`verify.py` implements Ed25519 verification (RFC 8032, pure Python, cofactorless, canonical-S
check) and is tested against the RFC 8032 vectors and against the `cryptography` library.

## Operations

Env: `LEDGER_DATABASE_URL`, `LEDGER_SIGNING_KEY_FILE`, `KAFKA_BOOTSTRAP`, `LEDGER_GATEWAY_SECRET`,
`LEDGER_METRICS_TOKEN`, `LEDGER_CHECKPOINT_EVERY`, `LEDGER_CHECKPOINT_INTERVAL_S`,
`LEDGER_MAINTENANCE_INTERVAL_S` (10), `LEDGER_MAX_PACKAGE_SPAN`, `LEDGER_MAX_VERIFY_SPAN`,
`LEDGER_CREATE_SCHEMA` (1). `python -m evidence_ledger.migrate` creates tables and triggers (safe
to run twice; advisory-locked on Postgres). SQLite is for tests and single-process dev only.

Throughput (single machine, this repo's tests, small entries with payloads; not a benchmark):
about 1,000 appends/s on SQLite (file DB, 1 or 8 threads) and about 1,500 appends/s on Postgres 14
(8 threads, local). All appends serialise on one lock by design, so the ledger scales with one
writer's speed, not with replicas.

## Known limits

* Receipt-order chain; no consensus, no external timestamping (RFC 3161) and no external
  anchoring of the head hash; add both for stronger claims.
* The signing key lives in the service process; a compromised service can sign anything.
* A GET on `/packages` may write a checkpoint row and the export audit entry.
* The ledger grows without bound (retention/archival of an immutable chain is an operations
  decision not implemented here). The consumer DLQ is not replayed automatically.
* Cases are immutable once created; add refs via a new case id.
* The standalone verifier duplicates `canonical_json` (3 lines) on purpose; a parity test pins it
  to `scam_contracts.canonical`.
