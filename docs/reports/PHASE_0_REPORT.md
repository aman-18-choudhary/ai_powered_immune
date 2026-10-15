# Phase 0 report: requirements, architecture and design

Date: **2026-10-15**. Branch `build/implementation`, HEAD `91ebfeb` (unchanged by this phase).

## 1. Phase objective

Record the requirements, architecture, data design, API contract, UI/UX plan, roadmap and
technology decisions for the AI-powered Digital Public Safety Intelligence platform, **describing
the system as actually built** and marking what is verified, unverified, planned, blocked or a
known limitation. Documentation only.

## 2. Scope of work

In scope: new Phase 0 documents, additive updates to `docs/architecture.md` and `README.md`,
historical-reference header notes on the two earlier planning files, `PROJECT_STATUS.md`, this
report. Out of scope (per the owner's instructions): any application code, dependency, data or
configuration change; deleting, moving or renaming files; staging, committing or pushing.

Before writing, the pending Phase B browser verification was completed (section 7).

## 3. Features implemented

None (documentation phase). The documents describe existing features; see
[PROJECT_STATUS.md](../../PROJECT_STATUS.md).

## 4. Files added and modified

| File | Change |
|---|---|
| `docs/PROJECT_REQUIREMENTS.md` | **New**: problem, objectives, users, functional requirements A-G with status, non-functional requirements, scope and exclusions, success criteria, dependencies, findings |
| `docs/DATABASE_DESIGN.md` | **New**: stores, ER and status diagrams, all 9 complaints tables, ownership rules, migrations 1-8, retention and privacy, planned structures |
| `docs/API_SPECIFICATION.md` | **New**: deployments, auth, error format, rate limits, all 29 complaints routes, 5 live-audio routes, 23 simulator routes, planned endpoints |
| `docs/UI_UX_PLAN.md` | **New**: real vs simulation experiences, existing screens with verification level, planned screens, visual language, state patterns, accessibility, UX limitations |
| `docs/DEVELOPMENT_ROADMAP.md` | **New**: mapping of earlier work to phases 0-14, phase status, recommended order, phase gate |
| `docs/TECHNOLOGY_DECISIONS.md` | **New**: environment, options compared, choices and status |
| `docs/reports/PHASE_0_REPORT.md` | **New**: this report |
| `PROJECT_STATUS.md` | **New**: feature status, test results with dates and who ran them, limitations, external setup |
| `docs/architecture.md` | **Updated (additive)**: update note under the introduction; six decision-log rows; new sections 10-14 (real-input components, analysis flow, live-audio flow, identity and access, security limitations) with Mermaid diagrams. Sections 1-9 unchanged |
| `README.md` | **Updated (additive)**: an update note after the existing simulator notice; a Documentation table |
| `PROJECT_GAP_ANALYSIS.md` | **Updated (header note only)**: marked historical (2026-10-13), content unchanged |
| `PROJECT_IMPLEMENTATION_PLAN.md` | **Updated (header note only)**: marked superseded by the roadmap, content unchanged |

No file was deleted, moved or renamed. No application code changed.

## 5. Architecture or database changes

None. The documents record the existing architecture and the planned additions (complaint links,
investigation notes, vector collection, geographic aggregation, report exports, evidence-access
audit, retention job).

## 6. API endpoints added or changed

None. 57 implemented routes are documented (29 complaints, 5 live audio, 23 simulator) and 9
planned endpoints are listed as planned.

## 7. Tests executed and exact results

**During Phase 0 (2026-10-15):**

| Check | Result |
|---|---|
| Microphone consent flow, scripted headless Chromium against the running server (temporary script, deleted) | Microphone requests: 0 on page load, 0 after sign-in, 0 after a forced click on the disabled Start, **exactly 1** after ticking consent and pressing Start. Start disabled before consent. Denied permission: "The microphone could not be started." Granted (fake device): "Microphone on"; after Stop the track state went `live` → `ended` and the page showed "Microphone off" |
| Built bundle scan | One `getUserMedia` call site; no `permissions.query` or `enumerateDevices` |
| Browser pane microphone notice | Also shown on `/api/health` (plain JSON, no scripts), so it is stale pane state for the origin, not a request from the app |
| Check page by hand (test citizen, test scam message + test link) | CRITICAL 100/100 labelled uncalibrated; category digital arrest 0.58 with interpretation and top 3; rule, URL and ML indicators with evidence spans; LLM text shown separately and labelled. Two findings recorded (LLM role inversion; inaccurate audio-transcription message) |
| `python scripts/check_links.py` plus the root-level files | See section 7a |
| Endpoint / table / migration consistency (temporary script comparing the docs with the route decorators, `schema.py` and `migrations.py`) | **PASS**: routes 29/29, 5/5, 23/23 both directions; all 9 tables and every column present in the design doc; migrations 1-8 match |
| Environment variables named in the docs | All found in code or config except `OLLAMA_HOST`, which is Ollama's own variable used in a run command |
| Commit hashes named in the docs | All 10 exist |

**Not re-run during Phase 0:** the unit and integration suites. Their latest results (earlier on
2026-10-15 and on 2026-10-14, against the unchanged HEAD) are listed in
[PROJECT_STATUS.md](../../PROJECT_STATUS.md#2-test-results).

### 7a. Link check

`scripts/check_links.py` checks `README.md`, `docs/*.md` and service READMEs by default; the root
files and this report were also passed to it explicitly. Output after the final edits:

```
$ .venv/bin/python scripts/check_links.py
27 files, ~132 links, 0 broken            (exit 0)
$ .venv/bin/python scripts/check_links.py README.md PROJECT_STATUS.md PROJECT_GAP_ANALYSIS.md \
    PROJECT_IMPLEMENTATION_PLAN.md docs/*.md docs/reports/PHASE_0_REPORT.md
17 files, ~137 links, 0 broken            (exit 0)
```

The checker verifies relative file targets, not `#anchor` fragments; the two anchors used
(`#2-test-results`, `#3-known-limitations-and-open-findings`) were checked by hand against the
headings. **Mermaid syntax was not validated by a parser** (no Mermaid CLI is installed, and
installing one would be a dependency change); the diagrams follow the syntax of the existing
diagrams in `architecture.md`.

## 8. Frontend build and backend start-up status

Not rebuilt in Phase 0 (no code change). The demo server from earlier on 2026-10-15 was running on
`http://localhost:8080` (local sign-in, complaints Ollama enabled) and Ollama on
`127.0.0.1:11434` throughout.

## 9. Security and privacy checks

* No secrets, keys, tokens, real email addresses or other personal data were added to any
  document (checked with a search for email addresses, Clerk key prefixes, private-key headers and
  `password=`; the only address found is the reserved test address on the `example.test` domain
  mentioned below). The approved investigator address stays only in the existing
  `infra/.env.example` configuration.
* Test sign-ins used `citizen.test@example.test`; the test complaint and its analysis exist only in
  the gitignored local database `.data/complaints/`.
* Security findings recorded (not fixed): LLM grounding gap; ungrounded live-audio explainer;
  inaccurate transcription message; evidence storage (unencrypted, unscanned, downloads not
  audited); development sign-in (terminal codes, per-process limits, demo-server and compose fall
  back to local mode, no production guard); no retention enforcement; `0.0.0.0` default bind;
  generic microphone-denial message.

## 10. External services required

Clerk keys (blocked); Tesseract (OCR, blocked); Qdrant and an embedding runtime (planned); Neo4j and
PostGIS (optional); a Docker daemon (Compose untested); `gh` or the GitHub UI (CI status unchecked).
Ollama is installed and runs only while started.

## 11. Features that remain incomplete

Uploaded audio transcription, OCR, Fraud DNA, complaint network intelligence, complaint hotspots and
map, investigation notes, report export, per-case audit view, complaint edit, configuration view,
retention, evidence encryption and scanning, production auth guard, WhatsApp/IVR, further languages.

## 12. Known bugs and limitations

See [PROJECT_STATUS.md](../../PROJECT_STATUS.md#3-known-limitations-and-open-findings) (11 items).

## 13. Git commit hashes

None created in Phase 0 (no staging or commits, as instructed). Existing local-only commits:
`a34754b`..`91ebfeb` (Phase B, 10 commits).

## 14. Git push status

Nothing pushed. `origin/main` and `origin/build/implementation` remain at `8e4dee0`. Phase B and
the Phase 0 documentation await approval.

## 15. Commands to verify

```bash
cd /Users/aman/lastproject && git status --short && git diff --stat
```

```bash
cd /Users/aman/lastproject && .venv/bin/python scripts/check_links.py README.md PROJECT_STATUS.md PROJECT_GAP_ANALYSIS.md PROJECT_IMPLEMENTATION_PLAN.md docs/*.md docs/reports/PHASE_0_REPORT.md
```

Mermaid diagrams render on GitHub or in any Mermaid-aware Markdown viewer.

## 16. Recommended next phase

1. Approve pushing the 10 Phase B commits and committing the Phase 0 documentation (separately).
2. Next implementation step: target **phase 6** (transcribe uploaded audio evidence with the
   installed faster-whisper engine; correct the capability message), together with the grounding
   fixes for findings 1 and 2. Rationale in [DEVELOPMENT_ROADMAP.md](../DEVELOPMENT_ROADMAP.md).

## Addendum (2026-10-16): commit, CI repair and push

* The Phase 0 documentation was committed as `22741e8`.
* Before pushing, CI was checked on GitHub's public Actions page: **all 29 earlier runs had
  failed** (the `test` job, exit code 4 after about 18 s; step logs need a GitHub sign-in, which was
  not used). The owner chose to repair CI first.
* Reproducing CI locally on Python 3.11 (fresh venv, clean clone) showed the install step resolved
  local package names from PyPI (some exist there as unrelated projects), and, once fixed, revealed
  two concurrency bugs that only appear on Python 3.11 (the version of CI and the Docker images)
  plus one flaky test. Fixed in separate commits: `2bb5687` (graph-twin single flight), `79263f3`
  (txn-guard duplicate hub submission), `440de07` (demo-server seek test), `7b97731` (CI workflow).
* Verification: all 16 Python packages pass the CI steps on Python 3.11 locally; `scripts/test_all.sh`
  passes 17/17 packages on Python 3.13; GitHub Actions runs #30 and #31 on `7b97731` succeeded
  (jobs `test` and `static`).
* Pushed as a normal fast-forward (no force): `origin/main` and `origin/build/implementation`
  moved from `8e4dee0` to `7b97731`, which includes the 10 Phase B commits.
* Status documents updated accordingly ([PROJECT_STATUS.md](../../PROJECT_STATUS.md)).
