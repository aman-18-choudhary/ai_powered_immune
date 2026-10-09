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
  Payload numbers (and numeric keys) with a run of 9 or more digits are rejected by design (see
  the PII guard); amounts of 9+ digits (for example Rs 10 crore in rupees) must be omitted or sent
  in a different shape.
* Canonical JSON (`scam_contracts.canonical`, copied byte-for-byte into `verify.py` and pinned by a
  parity test): UTF-8, keys sorted, separators `(",", ":")`, `ensure_ascii=False`, no NaN/Infinity.
  Floats are Python-`repr` canonical only; prefer strings or scaled integers for cross-language use.

## The chain

```
chain format 2:
entry_hash = sha256( prev_hash_ascii || canonical_json({seq, ts, service, actor, event_type,
                     payload_hash, payload_present, model_version, case_refs}) )   (hex)
prev_hash of seq 1 = "0" * 64        seq starts at 1, no gaps
```

The payload BODY is not part of the hash: `payload_hash` and the `payload_present` flag are. When
a payload body is present, `sha256(canonical_json(payload))` must equal `payload_hash` (checked
by `verify_chain` and `verify.py`). That is what lets an export **withhold** a payload (redaction)
and still verify: a redacted entry has `payload` null, `payload_present` true and its original
`payload_hash`. Redaction proves nothing about the withheld content except its hash: an expert can
ask for the withheld payloads and check each against `payload_hash`; nobody can substitute
different content without breaking the hash. `chain_format_version` and `package_format_version`
are both 2; the verifier refuses other versions.

`ts` is the ledger's **receipt** time (UTC, `YYYY-MM-DDTHH:MM:SS.ffffffZ`), clamped to
`max(previous ts, now)` so the receipt timeline never runs backwards even if the clock does.
**Chain order is receipt order, not event order**: the topic is unordered across replicas and
emitters are at-least-once, so an event can be recorded after a later event that was emitted
afterwards. Event times, where they matter, belong inside the payload. The clock itself is not
authenticated (see the threat model).

`append` is idempotent on the EXACT entry: `idem_key = sha256(canonical_json({service, event_type,
payload_hash, actor, model_version, sorted case_refs, payload_present}))` is unique. Replaying the
identical entry any number of times writes one row; an entry that differs in actor, model version,
case refs or payload presence is a distinct entry (a hash-only entry followed by the same entry
carrying its payload gives two records). Appends are serialised (process lock on SQLite,
`pg_advisory_xact_lock` on Postgres); the idempotency lookup, chain insert, case-ref rows and any
checkpoint are ONE transaction, so `seq` is gap-free under concurrency and two replicas racing the
same entry produce exactly one row (the unique key is the backstop).

### Immutability and what it does not give you

`ledger_entries`, `entry_refs`, `checkpoints` and `cases` have triggers that RAISE on UPDATE and
DELETE (and TRUNCATE on Postgres); tests prove it for both dialects. SQLite also has BEFORE INSERT triggers that refuse
any insert colliding with an existing key (so `INSERT OR REPLACE` cannot overwrite a row);
Postgres relies on the unique constraints plus the UPDATE trigger (which also fires for
`ON CONFLICT DO UPDATE`), all tested. The service's DB role should
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
| An emitter sends an entry the ledger refuses to store | Not lost: a hash-only quarantine entry records that it happened (see Consumer); its content is not kept |
| Redacted context entries in a package | Their content is not shown, only bound by `payload_hash`; withheld payloads can be requested and checked against it |
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
| `GET /cases/{id}/exports` | officer, admin | the case's `package.exported` history |
| `GET /packages/{case_id}` | officer, admin | the zip; `Content-Disposition`, `ETag` = sha256; 413 if the span exceeds `LEDGER_MAX_PACKAGE_SPAN` (5000): narrow the case |
| `/healthz`, `/readyz`, `/metrics` | metrics: `LEDGER_METRICS_TOKEN` or staff role | counters `appended, duplicates, rejected, dlq, checkpoints`, gauge `head_seq` |

There is no HTTP write path for entries. 422 bodies contain `loc/msg/type` only, never the
submitted value; the access log is method, route template, status, request id and duration (no
query strings, headers or bodies; the entrypoint runs uvicorn with `access_log=False`).

## Consumer

`run_ledger_consumer` reads `ledger.append` (group `evidence-ledger`) through `svckit.consume`.

**What the ledger does when it cannot store an entry.** It never drops one. An entry that fails
validation (identifier-looking content, payload-hash mismatch, malformed fields) is replaced by a
durable, hash-only quarantine entry in the chain: `service` = the original service if it is a clean
name, else `unknown`; `actor` = `quarantine`; `event_type` = `ledger.entry_quarantined.pii`,
`.hash_mismatch` or `.invalid`; `payload_hash` = the original entry's hash (a constant when even
that is malformed, so malformed ones of one service collapse into one entry); no payload, no refs,
no free text. It is idempotent on the original payload_hash (replays write one entry), is logged
at WARNING without any payload, shows up in packages as an ordinary hash-only entry ("entry
withheld by the ledger (reason)") and is counted durably from the chain on `/metrics`
(`evidence_ledger_quarantined_total{reason=...}`). The original event is thus recorded as having
happened, with its identity hash, and the content is not stored.

Messages that are not a `LedgerEntryIn` at all (or whose handling keeps failing after 3 attempts)
go to `ledger.append.dlq` and are counted durably in the append-only `dlq_events` table (id,
receipt time, reason, **sha256 of the raw bytes only**; the bytes are never stored in the
database; `evidence_ledger_dlq_events_total`). `svckit.consume` forwards the raw bytes to the DLQ
topic, so restrict that topic's ACL. Duplicates are counted (`duplicates`). The consumer needs no
dedupe memory of its own (the store is idempotent) and is restarted if the bus connection fails.

### PII guard

Payload strings, keys and numbers, `case_refs`, `service`/`actor`/`event_type`, `model_version`
and case titles go through `svckit.pii` (NFKC, separators collapsed, 9+ digits, e-mail/UPI `@`,
`+`/`0091`, PAN, IFSC). Platform-minted opaque ids are accepted **only as whole strings** of an
exact shape: an optional lowercase `prefix_` (2-8 letters) or case-ref namespace `prefix_ref:`, then
exactly 16, 24, 32 or 64 lowercase hex characters with at least one letter a-f whose longest digit run is at most 15, 21,
22 or 25 respectively. Everything else (including an id embedded in longer text, and every
free-text field) gets the strict rule. A whole-string UTC timestamp
`YYYY-MM-DDTHH:MM:SSZ` (valid ranges only, optional fractional seconds) is also accepted, since its
14 digits would otherwise trip the 9-digit rule. Numbers (and numeric keys) with a run of 9+ digits are
rejected: amounts of 9+ digits (for example Rs 10 crore in rupees) must be omitted or sent in a
different shape; decision sequence numbers, scores and rupee amounts below 9 digits are fine.

Measured false-reject rate on uniformly random lowercase hex ids (1,000,000 per length):

| id length | rejected |
|---|---|
| 16 hex (`txn_` + 16) | 0.051% (0.054% is the floor: all-digit ids look like card numbers) |
| 24 hex | 0.0053% |
| 32 hex | 0.0089% |
| 64 hex (sha256) | 0.0074% |

(The earlier "about 1%" claim and the 5-14% of a flat 9-digit rule were wrong or too costly; the
per-length caps are the smallest that keep the rate under 0.01% where that is possible.) The
test suite re-measures 200,000 ids per length.

**Residual risk, stated plainly:** a shape rule cannot tell a 15-digit number plus one hex letter
in a 16-character string from the 0.5% of random 16-hex ids that have a 15-digit run, nor a
phone-sized digit run padded with hex letters to an id length. Those pass. `a1234567890123456`,
`1234567890123456a`, `12345678a12345678a` and an id glued into text are rejected. Spelled-out
numbers, encoded values and fragments of 8 digits or fewer are not detected either. The guard is
a safety net; emitters must not put identifiers in payloads or refs. A raw identifier that does
reach a stored payload is immutable and ends up in every package that selects the entry.

## Emitter conventions (Task 13)

Services build entries with `svckit.ledger.build_ledger_entry` / `emit_ledger`, which compute
`payload_hash` with the shared canonical helper and refuse (`LedgerPayloadError`, message never
echoes the value) payloads with forbidden key names (`phone, mobile, account, account_number,
name, email, address, upi, vpa, pan, aadhaar, otp, password, token_secret`, case-insensitive
substring), PII-looking values, more than 4 KiB, or more than 10 refs. The ledger still re-checks
everything (a refused entry is quarantined). Conventions:

| ref | definition | joins |
|---|---|---|
| `txn_ref(txn_id)` | the transaction id if guard-safe (`txn_<16 hex>`), else `txn_` + 16 hex of its sha256 (`svckit.ledger.txn_ref`); create cases with this value | txn-guard holds of one transfer |
| `payee_ref:<16 hex>` | first 16 hex of the keyed payee hash (`key_hash` in the hub); the next 16-hex block if that one is all digits | the same (mule) account across banks: A's and B's holds and the antibody lifecycle |
| `call_ref:<16 hex>` | first 16 hex of `sha256(call_id)` (same all-digit fallback) | call-guard's `callrisk.alert` and the holds that call influenced |
| `<antibody_id>` | sha256 hex | antibody lifecycle |

`payee_ref` identifies a mule account across banks (that is its purpose); it is a truncated keyed
hash, so it reveals nothing about the account number, but anyone holding the federation key can
test a guessed account against it. Operational notes: emitters deliver from outboxes in bounded batches (txn-guard 200 holds, call-guard
50 entries per sweep cycle), so a sweep is O(batch) and a large backlog drains over several cycles;
env: `LEDGER_EMIT_TIMEOUT_S`, `LEDGER_DRAIN_INTERVAL_S`, `LEDGER_DRAIN_CONCURRENCY`,
`LEDGER_BREAKER_COOLDOWN_S`. `txn_ref` is not injective for a bank-supplied id that itself equals
another id's `txn_ref`.

When the guard refuses an emitter's payload the emitter sends a fixed-shape placeholder (`audit:
payload_refused`, a `redacted_<16 hex>` actor) instead of failing its business action; the redundant
`payee_ref` + `key_hash_prefix` pair in antibody payloads is intentional (the ref joins cases, the
prefix lets a reader match the antibody to a payee hash they already hold).
Payload timestamps are `YYYY-MM-DDTHH:MM:SSZ` strings (the PII
guard exempts that exact whole-string shape and `prefix_ref:`-namespaced 16-hex ids; nothing
else with 9+ digits passes). Payloads must be deterministic (no wall clock read at send time):
the ledger's idempotency key makes an exact repeat a no-op, anything that varies between retries
becomes a second entry. Chain order is receipt order; emitters need no cross-event ordering.
The full flow is exercised in `tests/e2e/audit` (see `docs/audit-trail.md`).

## Evidence packages

`GET /packages/{case_id}` (or `evidence_ledger.package.build`) returns a ZIP that is **byte-for-byte
deterministic** for the same ledger state: stored (uncompressed), fixed member order, 1980-01-01
timestamps, sorted JSON, no randomness. Members:

| Member | Content |
|---|---|
| `entries.json` | the case and the contiguous chain **segment** from the first selected seq to the covering checkpoint's seq, plus `selected_seqs`. Selected entries carry their full payload; every other entry in the segment is **redacted** (payload omitted, `payload_present` true, `payload_hash` kept) and exists only for chain continuity |
| `chain_proof.json` | segment bounds, anchor (`prev_hash` of the first entry = the preceding entry's `entry_hash`), the signed checkpoint(s) at the end of the segment, all public keys |
| `explanations.md` | timeline of the selected entries rendered ONLY from stored fields: event type, actor, decision, reason codes with a one-line meaning from a static dictionary, model version, receipt time; entries without payload say "No payload retained (hash only)"; redacted context entries are listed in a table |
| `manifest.json` | `case_id`, `generated_from_head_seq`, format versions, selected `{seq, entry_hash}`, `redacted_seqs`, `sha256` of every other member, `key_id`, Ed25519 `signature_b64` over `canonical_json(manifest without signature)` |
| `certificate_section63_template.md` | template for counsel (below) |
| `verify.py` | the standalone verifier (stdlib only) |

Why a segment: selected seqs may be non-contiguous. Including every entry from the first selected
seq to a signed checkpoint lets the whole segment be recomputed, and the checkpoint signature then
authenticates all of it. A case spanning more than `LEDGER_MAX_PACKAGE_SPAN` entries is refused
with 413. **Other cases' payloads are not in the package** (they are redacted), but their
envelope fields are: seq, receipt time, service, actor, event type, model version, `case_refs`
and `payload_hash`. Tests scan the zip bytes for sentinel text placed in other cases' payloads.

`generated_from_head_seq` is the highest seq that is not a `package.exported` entry at build time.
Package bytes are a function of the ledger state, so they change after unrelated new traffic
whenever the case's covering checkpoint or that value changes, and then stabilise: repeated
exports with no new non-audit traffic are byte-identical (counting the export audit entries
themselves would change the bytes of every repeat export).

Each export appends `package.exported` (`actor` = principal, `case_refs=[case_id]`, payload
`{case_id, package_sha256, from_seq, to_seq, exported_by}`) to the ledger itself, before the
package is returned. It is idempotent: re-exporting the identical package by the same principal
writes nothing; a different principal's first export of it is recorded. Export entries are never
selected into the case's own package; `GET /cases/{id}/exports` lists the case's export history.

### Section 63 template: honest scope

The certificate is a **template for counsel to complete**. It is not legal advice and not a
statement that any record is admissible under Section 63 of the Bharatiya Sakshya Adhiniyam, 2023.
Whether the requirements are met depends on counsel's reading of the Act and its Schedule form
and on the operator's actual controls (access control, DB roles, key custody, clock source,
retention, who certifies). This service supplies integrity evidence, not compliance.

### Verifying a package (for a court expert)

1. Obtain `verify.py` from a source you trust (it is also in the package and hashed in the
   manifest; if the package is hostile its own copy is no evidence). It is ~600 lines, stdlib
   only, readable end to end, and runs on **Python 3.8 or newer** (tested on 3.9, 3.10, 3.11,
   3.13; older interpreters get a clear message and exit 1).
2. Obtain the agency's Ed25519 **public key** independently of the package (published by the
   operator, notarised, referenced in the certificate). `key_id` is only a 64-bit label, not a
   security pin: pin the full key.
3. `python -S -I verify.py <extracted dir or .zip> --trusted-pubkey <hex | base64 | PEM | file>`
   (repeat the flag for a rotated-out key that signed older checkpoints; `--trusted-key-id` pins by
   label only). No network, no packages.
4. Exit codes: **0** = integrity OK and the signer (and every checkpoint signer) is pinned;
   **2** = integrity OK but UNPINNED, final line `INTEGRITY OK - UNPINNED: AUTHENTICITY NOT
   ESTABLISHED; ...` (anyone can build a self-consistent package with their own key); **1** =
   failure with a `FAIL:` reason. `--allow-unpinned` turns 2 into 0 for demos/tests but still
   prints the UNPINNED line. `--json` for machine output.
5. It refuses on: unexpected, duplicate, nested, non-regular or oversized members (64 MiB each,
   256 MiB total, compression ratio 100, JSON nesting 64), any member hash differing from the
   manifest, invalid manifest signature, signer or checkpoint key not pinned, broken chain
   segment, unknown entry fields, invalid or non-matching checkpoint, no signed checkpoint at the
   end of the segment, a selected entry with its payload withheld, a `redacted_seqs` list that
   differs from the entries, unsupported format versions, small-order keys, non-canonical
   encodings. Hostile input yields a `FAIL:` line, never a traceback. The redacted entries it
   found are listed in the output.

`verify.py` implements Ed25519 verification (RFC 8032, pure Python, cofactorless, canonical-S
check) and is tested against the RFC 8032 vectors and against the `cryptography` library.

## Operations

Env: `LEDGER_DATABASE_URL`, `LEDGER_SIGNING_KEY_FILE`, `KAFKA_BOOTSTRAP`, `LEDGER_GATEWAY_SECRET`,
`LEDGER_METRICS_TOKEN`, `LEDGER_CHECKPOINT_EVERY`, `LEDGER_CHECKPOINT_INTERVAL_S`,
`LEDGER_MAINTENANCE_INTERVAL_S` (10), `LEDGER_MAX_PACKAGE_SPAN`, `LEDGER_MAX_VERIFY_SPAN`,
`LEDGER_CREATE_SCHEMA` (1), `LEDGER_MAX_BODY_BYTES` (65536; POST bodies over it get 413, also
when streamed without Content-Length). `python -m evidence_ledger.migrate` creates tables and triggers (safe
to run twice; advisory-locked on Postgres). **Postgres is the supported production database.** SQLite is for tests and single-process dev
only: its serialisation is a process lock, so several processes sharing one file get IntegrityError
noise on seq races.

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
* The PII shape rule has a small false-reject rate (0.05% for 16-hex ids) and the residual risk
  described above. Quarantined entries keep the event's identity hash but not its content.
* The standalone verifier duplicates `canonical_json` (3 lines) on purpose; a parity test pins it
  to `scam_contracts.canonical`.
