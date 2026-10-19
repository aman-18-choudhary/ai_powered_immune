# Phase 9 report: complaint network

Date: 2026-10-18. Branch `phase9-network` (local, **not pushed**), based on `b8d9dd7` (the pushed
accuracy work). The three backend commits made before the accuracy work (`98f82d6`, `a81f268`,
`8f4c086` on `build/implementation`) were cherry-picked onto this branch as `8d41f72`, `b517428`
and `725c523`. The five unfinished frontend files were recovered from the backup tarball without
changes, then completed.

> **Read this first.** Every test record in this phase is SYNTHETIC. No real complaint was used or
> fabricated to fill the graph. A shared identifier is a **lead**, not proof that the same person
> is involved. A **confirmed** link means an investigator judged two complaints to be part of the
> same case. It is **not** a finding that the identifier's owner, or anyone else, committed a
> crime. No precision or recall on real complaints is known.

## 1. Scope (as approved)

* The `complaint_entities` table, with migration 10 and a backfill. Identifiers are normalised.
* **Observed** links: two complaints share an exact normalised identifier.
* **Inferred** links: only the registrable domain of two different URLs matches. These are always
  labelled inferred.
* Investigator **confirm/reject** reviews, audited.
* Staff-only APIs. Identifier search is POST only.
* The Related Complaints panel, a network graph and a cluster view.
* SQL and Python only, with no Neo4j. Graph depth and size are capped.
* The case-matching opt-out is enforced server-side on every network endpoint.

## 2. Commits (`b8d9dd7..HEAD`)

| Commit | Change |
|---|---|
| `8d41f72` | Identifiers come from structured fields only, are normalised and masked. Adds the `complaint_entities` and `link_reviews` tables, migration 10 with a chunked, idempotent backfill, and writes identifiers in the same transaction as the complaint |
| `b517428` | Network API: related, graph, clusters, POST search, link review. Staff only. Every query uses one opt-out/deletion filter |
| `725c523` | The disposable test Postgres uses UTF8, like the official image |
| `f3f0f03` | Test: an opted-out complaint cannot bridge, be counted, appear in a cluster or search total, or be reviewed |
| `81e6d12` | Console: Related Complaints panel on the staff complaint page. Confirm/reject dialogs state that confirming is not a finding of crime. The profile text no longer says matching "is not built yet" |
| `630c81a` | Console: `/investigator/network` with the graph, legend, evidence list, clusters, POST search and nav entry. Link reviews get plain names in the audit log |
| `e2d1bfe` | Console tests: 12 vitest tests and a real-server Playwright spec with synthetic complaints |
| `d3d58f2` | Docs: API, tables, migration 10 recovery, opt-out, status |
| `cf2230f` | Test fix outside Phase 9: the demo-server rate-limit test depended on the wall clock (section 12) |
| `398c4fe` | Playwright: axe and 360 px overflow checks for the network page and the related panel |
| (this commit) | Network spec reuses sessions (6 sign-ins) so the full suite stays under the local sign-in limit; this report |

## 3. Data model and migration

* **`complaint_entities`:** one row per identifier per complaint. Columns: type, normalised value,
  SHA-256 `value_hash`, masked `display`, `source_field`. It is unique per complaint, type and hash.
  It is derived data and can be rebuilt from `complaints`.
* **`link_reviews`:** append-only. Each row is (`complaint_low < complaint_high`, `seq`, decision,
  basis, reviewer, note). The latest `seq` is the current decision; earlier reviews are kept.
* **Links are not stored.** Observed and inferred links are computed at query time, so they never go
  stale, and an opt-out takes effect on the next request.
* **Migration 10** is additive.
  * The backfill runs in chunks of 500 and never writes the same row twice.
  * It runs when version 10 is not recorded, or when the entities table is empty while complaints
    exist, so a dropped table repairs itself at start-up.
  * Tests cover four cases: a fresh database, an idempotent re-run, a dropped table, and a
    pre-versioning database. The whole complaints suite also passed on a disposable Postgres 14.
* **Rollback:**
  * The previous code ignores both tables, so redeploying it is enough.
  * `complaint_entities` can be dropped; it is rebuilt.
  * `link_reviews` holds investigator decisions that cannot be rebuilt. The audit log keeps each
    decision with the complaint ids, but not the note. Back it up before dropping it.

## 4. Identifiers and normalisation

Identifiers come only from these structured fields: `suspect_phone`, `upi_id`, `suspect_email`,
`suspicious_url` (plus its registrable domain), `bank_account`, and `social_platform` +
`social_account`. Free text, transcripts and evidence are never parsed for identifiers.

Normalisation reuses the complaint form's validators:

| Identifier | Normalised as |
|---|---|
| Phone | `+<country><number>`; a bare Indian mobile gets +91 |
| UPI ID, email | Casefolded |
| URL | Scheme and host lower-cased; default port and fragment dropped; path and query kept as typed |
| Bank account | Digits only. The IFSC is compared and shown (same / different / not given), but is not part of the identifier |
| Social account | `platform:account`. The same handle on another platform does not match |

All responses show masked values.

## 5. Link types and evidence

| Type | Rule | Shown as |
|---|---|---|
| observed | same type and same normalised value on both complaints | solid line, "Observed", evidence "Same normalised value in this complaint's <field> field" |
| inferred | registrable domain matches, URLs differ | dashed line, "Inferred", evidence "...a shared domain with a different link is inferred, not proof"; never joins a cluster |
| confirmed | latest review of the pair is confirm | thick arc, "Confirmed", reviewer, date, optional note, original basis, "not a finding that ... anyone ... committed a crime" |
| rejected | latest review is reject | dotted arc, "Rejected", reviewer, date, note |

Every edge the API returns has evidence:

* **Identifier edges** carry an `evidence` text and the source field.
* **Review edges** carry the reviewer, date, note and original basis.
* **Reviews need a shared identifier.** The API refuses a review where no identifier is shared
  (409 `link_no_relation`), and identifier fields cannot change after a complaint is created. So
  there is no "confirmed" edge without underlying shared data.
* **The UI repeats the evidence in text.** It lists every connection with its evidence below the
  graph, so nothing depends on colour or on hovering.

## 6. API and authorisation

The network endpoints are:

* `GET /complaints/{id}/related`
* `GET /complaints/{id}/graph?depth=1|2`
* `GET /graph/clusters`
* `POST /entities/search`
* `POST /complaint-links/review`

How they behave:

* **Roles:** INVESTIGATOR and ADMIN only. Citizens get 403 and anonymous callers 401; both are
  tested in the API tests and in the browser.
* **Identifiers never go in a URL.** Search is POST only; the planned `GET /entities/{type}/{value}`
  was replaced for that reason. Invalid search values are a 422 that does not echo the value.
* **Limits:**
  * pages of up to 50;
  * graph caps of 40 complaints, 60 identifiers and 250 edges, with a `truncated` flag;
  * row-scan caps on related, graph and clusters.
* **Rate limit:** the same buckets as other reads and writes.

## 7. Privacy review (opt-out and deletion)

**The single filter.** Every network query joins the complaint to its owner and applies
`deleted_at IS NULL AND users.allow_case_matching`. I reviewed `network_store.py` path by path:

| Path | Result |
|---|---|
| Related | the opted-out/deleted complaint is never listed; an opted-out centre returns `linking: "disabled"` and no items |
| Graph | expansion goes only through matchable complaints, so an excluded complaint cannot bridge two others; entity `complaint_count` counts shown complaints only; review edges only among shown nodes |
| Clusters | shared-identifier groups count only matchable complaints; an excluded complaint cannot join or connect a cluster |
| Search | `total` and items both filtered (an excluded complaint is not counted) |
| Review | refused for deleted (`link_complaint_deleted`) or opted-out (`link_matching_disabled`) complaints; existing review edges with them are hidden |
| Toggle | applied from the next request (no stored links to clean up), including for existing complaints |

**Tests.** The bridge test (`f3f0f03`) builds A–(UPI)–B–(phone)–C with B opted out. It asserts:

* no path from A to C;
* no cluster joined through B;
* no count includes B;
* no review edge with B;
* the search total excludes B;
* a new review involving B returns 409.

The browser test checks the same through the UI with a real server.

**Logs and audit.** Identifiers never reach the logs; a test checks this. Audit rows carry complaint
ids and decisions, never identifier values or the note.

**One residual point.** `complaint_entities.value_norm` stores the normalised identifier in
plaintext. This is the same data the complaint row already holds, so it adds no new exposure. If
complaint fields are ever encrypted at rest, this column must be too.

## 8. Wording: leads, not proof; confirmed is not guilt

Wording shown wherever links appear:

* **"Leads, not proof."** "Shared identifiers are leads, not proof that the same person is
  involved." It appears on the network page, the related panel, search results and the API
  responses.
* **Confirmed is not guilt.** "A confirmed link means an investigator judged the complaints to be
  part of the same case. It is not a finding that the identifier's owner, or anyone else,
  committed a crime." It appears in the related panel, on every confirmed edge, and in the network
  page notice.

Wording in specific places:

* **Confirm dialog:** "...not a finding that anyone committed a crime."
* **Audit log:** "Complaint link confirmed (same case; not a finding of crime)".

## 9. User interface

* **Related Complaints panel** on the staff complaint page:
  * linked complaints, each with a type badge (icon and text);
  * the shared identifiers (masked), with the field on each side and the IFSC comparison;
  * the latest review and its history count;
  * Confirm/Reject dialogs with an optional note (≤ 500 characters);
  * pagination, a truncation notice and the opt-out message.
* **Network page** (`/investigator/network`, staff nav entry "Network"):
  * a complaint ID form, or a pick from clusters or search;
  * a deterministic radial SVG graph with no new graph library and a depth 1/2 toggle;
  * nodes that can be selected by keyboard, with a details panel;
  * a legend, and a text list of every connection with its evidence;
  * truncation and opt-out notices;
  * a cluster list with pagination;
  * identifier search by type, value, and platform for social accounts.
* **Accessibility:** axe finds no serious or critical violations, and nothing overflows at 360 px, in
  dark and light themes.
* **Review edges:** these are drawn as arcs so they never hide the identifier between two complaints.
  This was found during a visual check and fixed.

## 10. Audit trail

* Each review appends a `link_reviews` row and an `admin_audit` row (`link_confirm` /
  `link_reject`). The audit row records the previous decision, both complaint ids, the decision,
  basis, review id, sequence and whether a note was given.
* The admin Audit log shows these entries with plain names.
* Tests cover the API (confirm then reject: the latest wins, history is kept, audited), the
  console unit tests, and the browser (an admin sees the confirmed review in the audit log).

## 11. Tests added

| Where | Count | What |
|---|---|---|
| `services/complaints/tests/test_network_entities.py` | 17 | normalisation per type (phone formats, URL details, UPI/email case, bank account and IFSC, social platform), masking, invalid input without echo |
| `services/complaints/tests/test_network_migration.py` | 5 | tables on a fresh DB, idempotent backfill, dropped-table repair, pre-versioning DB, same-transaction write |
| `services/complaints/tests/test_network_api.py` | 25 | 403/401/404, no link without shared identifier, observed UPI (the synthetic two-complaints-linked / unrelated-not-linked check), inferred vs observed URLs, bank/IFSC, social platform, pagination, reviews and audit, invalid reviews, opt-out everywhere with immediate toggle, soft delete, graph depth, inferred and review edges, caps, clusters, POST-only search, no identifiers in logs, health, capabilities, the opt-out bridge test |
| `web-console/src/routes/account/network.test.tsx` | 12 | related panel evidence and wording, confirm request body and dialog text, opt-out message, citizens never call the API, graph edge evidence (inferred and confirmed text), keyboard selection, depth 2, opt-out and truncation notices, clusters, POST-only search and social platform, layout determinism |
| `pages.test.tsx`, `gates.test.tsx` | +1, updated | audit labels; the nav list gains the staff-only "Network" entry (citizens are still asserted not to get it) |
| `web-console/e2e/network.spec.ts` | 3 | real server, SYNTHETIC citizens: two share a UPI ID (typed differently) and are linked; an unrelated one and an opted-out one are absent from the panel, graph and search; citizen 403 on page and API; confirm with note; audit log; axe + 360 px in both themes |

## 12. Validation run (2026-10-18)

| Suite | Result |
|---|---|
| `scripts/test_all.sh`, Python 3.13 (on `d3d58f2`) | 16/17 packages. The failure was `demo_server` `test_relogin_does_not_reset_citizen_bucket` (see below) |
| Same suite after the fix (on `398c4fe`) | **17/17 packages** (complaints 601 passed, 7 skipped; demo_server 213; console 284) |
| CI test job, Python 3.11, fresh clone (on `cf2230f`) | **16/16 packages**, ruff clean in each, `pip check` clean (complaints 596 passed, 12 skipped: PyAV and optional STT not installed, as on GitHub) |
| CI static job (Dockerfile and link checks), fresh clone | 11 Dockerfiles 0 problems; 28 files 0 broken links |
| Complaints on a disposable Postgres 14 (`with_postgres.sh`) | 602 passed, 6 skipped, 0 failed |
| Console vitest | 284 passed |
| Console `tsc --noEmit`, eslint, `vite build` | clean; main chunk 103.6 KB gzip |
| Playwright, all specs, fresh real demo server from this branch (real faster-whisper; fake mic device) | **26/26** on the final spec (complaints 10, network 3, pages 9, smoke 2, live audio 2) |
| Frozen SYNTHETIC evaluations v1 and v2 | identical to the recorded batch 2 results (only `code_revision` differs) |

**The `demo_server` failure, investigated rather than assumed flaky:**

* **Cause:** the limiter uses fixed 60 s windows aligned to the wall clock. On the real clock, the
  test's 35 requests can straddle a minute boundary. The count then restarts and the expected 429
  never comes.
* **Reproduction:** deterministic, using a clock started 0.15 s before a boundary (no 429) versus
  mid-window (429).
* **Fix (`cf2230f`):** the test now pins the clock inside one window. The assertion is unchanged,
  and a limiter that never refuses still fails it (mutation check).
* **Scope:** the defect existed before Phase 9 and is unrelated to it. It *may* explain the
  unidentified CI failure of run #35, but that is not proven.

**Playwright and the local sign-in limit.** Local sign-in allows 20 code requests per IP per 15
minutes. The first version of the network spec signed in 9 times. With the other specs in one run,
that exceeded the limit: a 429 on `/local-auth/start`, which is the limiter working as intended. The
spec now reuses sessions and signs in 6 times. A full run uses 19 of the 20, which leaves no
headroom: a new spec that signs in will need a fresh server, or a test-only limit.

**Not run:** a Docker build of the stack (no Docker daemon), and GitHub CI (nothing pushed).

## 13. Limitations

* **No real-world precision or recall.** The rules are exact matches (plus the domain rule).
  Identifiers typed wrongly by a complainant, or given only in the description, are missed by design.
* **The registrable domain** uses a small authored suffix list, not the Public Suffix List. Shared
  hosting (for example `*.blogspot.com`) can make unrelated sites look like one domain. This is
  one reason domain matches are only ever inferred and never cluster.
* **Common identifiers.** A widely shared value, such as a payment aggregator's UPI handle, links
  unrelated complaints. Size caps keep the graph usable, but no "too common" weighting exists.
* **Clusters** are computed per request over at most 20,000 identifier rows (with a `truncated` flag).
  A larger deployment would need a stored or incremental computation.
* **Graph layout** is a simple radial layout. Large or dense graphs, near the 40-complaint cap, will
  overlap. The text list remains complete.
* **Reviews are shared, not per-investigator.** They are investigator-only (no separate approval
  workflow), and a later review replaces the displayed decision.

## 14. Security notes

* Staff-only routes use the existing role guard; investigators are allowlisted and re-checked each
  request.
* No identifier appears in a URL, log line or audit row.
* All SQL is SQLAlchemy Core with bound parameters.
* Inputs are bounded: id length ≤ 64, value ≤ 2048, note ≤ 500, limit and offset caps.

## 15. Docs updated

`PROJECT_STATUS.md`, `docs/API_SPECIFICATION.md`, `docs/DATABASE_DESIGN.md`,
`docs/DEVELOPMENT_ROADMAP.md`, `docs/UI_UX_PLAN.md`, `services/complaints/README.md` (API, RBAC,
opt-out now enforced, normalisation, link types).

## 16. Final state

* **Final validation:** sections 12 and 16 list the final runs. The Python suites ran on `398c4fe`.
  The final commit changes only the e2e spec and this report; the full Playwright run above used
  that final spec.
* **Not pushed.** `main` and `build/implementation` on GitHub stay at `b8d9dd7`. Local
  `build/implementation` still points to the old Phase 9 backup (`f18f432`) and is not part of
  this push.
