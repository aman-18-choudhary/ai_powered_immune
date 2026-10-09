# The audit trail, end to end

This page explains what the platform records about a scam case, what is deliberately not
recorded, and how an outside expert can check the record without trusting our software. It
describes the design and what the tests demonstrate. It makes no claim about legal
admissibility.

## The story it tells (the hero case)

Victim A (bank A) is phoned by a templated "digital arrest" caller and pays a mule account.
Victim B (bank B, another state) gets an off-template call that call-guard does not flag, and 90
seconds later pays the same, seasoned mule. The ledger records, in receipt order:

1. `callrisk.alert` (call-guard): A's call crossed the 0.7 threshold; reason codes, model version.
2. `hold.created` (txn-guard, bank A): A's transfer to the mule was held; reason codes include the
   active call risk.
3. `antibody.created` (antibody-hub): an analyst confirmed the mule; the hub published a shared
   threat marker.
4. `hold.resolved` (txn-guard): the analyst confirmed the block (pseudonymous principal, role).
5. `hold.created` (txn-guard, bank B): B's first transfer to the same account was held with
   reason `ANTIBODY_MATCH`, although nothing local to B looked wrong.

`tests/e2e/audit` runs exactly this on simulated data (seeds 1 to 3) through the real services
and the real ledger, exports the case, and checks that B's blocked transfer can be reconstructed
from the exported package alone. Per run: 11 to 12 ledger entries (call alert 1-2, holds created
5-6, one hold upgrade, antibody created / extended / revoked, one resolution), exactly one entry
per state change, none quarantined, package about 50 KB.

## What is recorded, what is only hashed, what is never recorded

* **Retained in the entry (PII-free payload, at most 4 KiB):** opaque ids, decision, decision
  sequence, score to 4 decimals, reason codes (codes only, not the explanatory text), model
  version, rail, an amount *bucket* (`<1k`, `1k-10k`, `10k-100k`, `100k-1m`, `>=1m`), deadlines
  and resolution times, the acting role and pseudonymous principal.
* **Hashed, with the body kept only if the emitter attached it:** every entry carries
  `payload_hash = sha256(canonical_json(payload))`. The chain binds the hash, so a package may
  withhold a payload (other cases' entries are always withheld) and still verify.
* **Never recorded:** amounts, account numbers, phone numbers, names, device ids, payer tokens,
  call text, free-text notes and reasons. Emitters reject such payloads (`svckit.ledger`) and the
  ledger re-checks and quarantines anything that slips through (a hash-only entry records that an
  event was refused).
* **Joining keys (case refs):** `txn_<16 hex>`, `payee_ref:<16 hex>` (truncated keyed hash of the
  payee account: the same mule account across banks), `call_ref:<16 hex>` (truncated hash of the
  call id), the antibody id. These are what link A's call, A's hold, the antibody and B's hold into
  one case without naming anyone.

## Audit never gets in the way of a fraud action

* **call-guard:** the alert is published first and the consumer never awaits the ledger. Alerts
  and audit entries are both at-least-once; the audit entry is stored atomically with the
  crossing marker and delivered from a background outbox (concurrency cap, per-entry timeout,
  circuit breaker, periodic sweeper). A ledger outage only defers audit entries; the only way to
  lose one is to lose the session store.
* **txn-guard:** a hold change only schedules its audit drain; the decision path never waits for
  the ledger (same cap, timeout and breaker; the sweeper retries in bounded batches).
* **antibody-hub:** the audit entry is written in the same transaction as the change, built so
  that it cannot fail the action.
* **Refusals:** when the PII guard refuses a payload (for example a numeric transaction id or a
  digits-only principal subject) the emitter substitutes opaque references (`txn_ref`: the id or
  `txn_` + sha256 prefix; `redacted_<hash>` for actors and bank slugs) and, if the payload is still
  refused, a fixed-shape placeholder entry (`audit: payload_refused`). The chain therefore always
  records that the change happened, without the content. Cases are created with the same
  `txn_ref` values.

## Where each piece comes from

| service | events | note |
|---|---|---|
| call-guard | `callrisk.alert` | published with the alert in one idempotent step |
| txn-guard | `hold.created`, `hold.upgraded`, `hold.resolved`, `hold.overdue` | transactional outbox, at-least-once |
| antibody-hub | `antibody.created / extended / expired / revoked / revoked.cross_bank / corroborated`, `protected.added / removed` | same DB transaction as the change |
| evidence-ledger | chain, signed checkpoints, cases, packages, `package.exported` | receipt-ordered, idempotent on the full entry |

## How an expert verifies a package offline

1. Obtain the package (zip) and, **separately and from a source you trust**, the ledger's public
   key (hex, base64 or PEM).
2. Extract it and run, with any Python 3.8 or newer and nothing installed:
   `python -I -S verify.py <package-dir> --trusted-pubkey <key>`.
3. Exit 0 means: every entry's hash and chain link recompute, retained payloads match their
   hashes, withheld ones are marked, the checkpoint signature and the manifest signature verify
   under the pinned key, and no file was altered. Exit 2 means the integrity checks passed but the
   signer was not pinned (authenticity not established). Exit 1 is a failure with the reason.
4. Read `explanations.md` (rendered mechanically from the stored data, nothing inferred) next to
   `entries.json`. The `verify.py` source is short and shipped inside the package so it can itself
   be reviewed.

## Honest limits

* **Integrity is not truthfulness.** The ledger records what services told it. A compromised
  service can emit false entries; a compromised ledger host that also holds the signing key can
  rebuild a consistent history. Keep the key off the database host and keep exported packages and
  checkpoints somewhere independent.
* **Time is receipt time** from the ledger host's clock, not event time. Chain order is the order
  of receipt. Event times that matter (deadlines, resolution) are inside the payload.
* **No external timestamping or anchoring** is implemented.
* **Packages disclose the envelope** (service, event type, case refs, hashes) of other cases'
  entries inside the covered span, though not their payloads.
* **`payee_ref` is linkable by design.** It lets banks and analysts see that two holds concern
  the same account. Anyone with the federation key can test a guessed account against it.
* **The PII guard is a safety net**, with documented false-reject (about 0.05% for 16-hex ids) and
  residual risks; amounts of nine or more digits cannot be sent at all.
* `payee_ref` and the antibody's `key_hash_prefix` overlap on purpose: the ref joins cases, the
  prefix lets a reader match an antibody to a payee hash they hold.
* **Decisions are model output.** Scores are risk scores calibrated on simulated and authored
  data, not real-world probabilities; the record shows what the system decided and why (reason
  codes), not that the decision was right.
* The Section 63 certificate in the package is a template for the person who operates the
  system; completing, signing and relying on it is a matter for counsel and the court.
