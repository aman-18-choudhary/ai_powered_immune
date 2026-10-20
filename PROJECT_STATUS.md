# Project status

Last updated: **2026-10-19**. GitHub `main` and `build/implementation` carry Phase 9 (complaint
network), the CI fix (`873430f`, `e84c490`: demo-server tests now follow the engine's ledger
timing contract; root cause in [docs/reports/PHASE_8_REPORT.md](docs/reports/PHASE_8_REPORT.md))
and Phase 8 (Fraud DNA, `06ce8d2`). Branch `phase10-geo` adds Phase 10 (complaint hotspots); see
[docs/reports/PHASE_10_REPORT.md](docs/reports/PHASE_10_REPORT.md).

## Accuracy first (2026-10-18)

The owner set accuracy, evidence and transparency as the top priority. See
[docs/ACCURACY_AUDIT.md](docs/ACCURACY_AUDIT.md) (failure modes found by running real pipelines on
SYNTHETIC probes) and [docs/reports/ACCURACY_BATCH_1_REPORT.md](docs/reports/ACCURACY_BATCH_1_REPORT.md).

* Fixed: risk scores no longer use the category guess (no MEDIUM+ without evidence: 11 → 0 on the
  frozen set); every scored point has a listed reason; HIGH/CRITICAL need a rule-based indicator;
  citizen alerts need a rule-based indicator; no "safe" verdict anywhere; results carry an
  `assessment` and evidence wording.
* Measured on 20 SYNTHETIC probes only (not real-world accuracy): false positives 6/8 → 1/8,
  scam recall at MEDIUM+ 10/10 → 2/10 (the removed "detections" had no evidence).
* Review (2026-10-18): every LOW result states it is not a safety finding; older analyses get a
  derived assessment; both citizen checkers score explained points only and agree on all 20 cases.
* Batch 2 (rules-v2, links in text): genuine bank OTP warnings no longer flagged; evidence-backed
  rules for UPI PIN-to-receive, advance fees, guaranteed returns, sextortion, account-block threats
  and reported scam encounters; offline checks for links written in the text (never fetched,
  marked not verified). On the SYNTHETIC sets (written by the rule author; regression evidence
  only): v1 10/10 scams and 0/8 legitimate flagged, v2 36/36 and 0/28. See
  [docs/reports/ACCURACY_BATCH_2_REPORT.md](docs/reports/ACCURACY_BATCH_2_REPORT.md).
* Open: transcript confidence and WER (fix 7); Live Protection false positives on conversations
  about scams (fix 9); native-speaker review of hi/ta/bn texts; SAFE_ACCOUNT_TRANSFER breadth
  ("secure account"); no real-world accuracy measurement.
* EXPERIMENTAL until validated on real data: risk bands, category model, script classifier, URL
  heuristics, Hindi/Hinglish transcription, Live Protection levels, LLM explanations, simulator
  numbers, complaint network.

Labels: **Verified** (tests run and passed; "by hand" where checked in a browser), **Unverified**
(code exists; only an agent ran the test, or not exercised), **Planned**, **Blocked** (external
dependency missing), **Limitation**.

## 1. Feature status

### Identity and access
| Feature | Status |
|---|---|
| Roles CITIZEN / INVESTIGATOR / ADMIN from the database only | Verified |
| Investigator access only for verified emails in `INVESTIGATOR_ALLOWLIST`, re-checked per request; friendly refusal | Verified (tests); refusal and allowlisted flow in the browser: agent Playwright only |
| Admin via `ADMIN_BOOTSTRAP_EMAILS` or an existing admin; last-admin guard; no self-promotion | Verified (tests) |
| Clerk sign-in | **Blocked** (no keys); verifier tested with local RSA keys only |
| Local development sign-in (email + code printed to the terminal) | Verified by hand 2026-10-14 and 2026-10-15; **dev-only, see limitations** |

### Complaints and evidence
| Feature | Status |
|---|---|
| Complaint with only category, title, description; optional fields stored as NULL | Verified (tests; by hand 2026-10-14) |
| Optional bank, payment, suspect, social and location fields | Verified (tests) |
| Owner adds or corrects optional details while SUBMITTED (identifiers re-extracted, audited, in the timeline) | Verified (tests; agent Playwright) |
| Ownership (foreign ids 404) | Verified (tests) |
| Status workflow with history and owner alerts | Verified (tests); investigator UI: agent Playwright only |
| Evidence upload with content sniffing and size limits; honest per-file result | Verified (tests); upload in the browser: agent Playwright only |
| Evidence download / owner removal | Verified (tests) |
| Complaint edit after submission | Planned |

### Analysis
| Feature | Status |
|---|---|
| Rules + call-guard classifier + category model; score 0-100 labelled uncalibrated | Verified (tests; `/check` by hand 2026-10-15: CRITICAL 100 on a test message) |
| call-guard clf-v1 held-out metrics (synthetic): 0.999 recall on its own generators but ROC-AUC 0.59 / recall 0.17 on independent hand-written messages; rules carry the detections | Measured 2026-10-19 ([report](docs/reports/PHASE_5_CLF_METRICS.md)); shown on the Models page |
| Provenance per item (rule / ml / llm / static_guidance) with evidence spans | Verified (tests; by hand) |
| Failure semantics (INSUFFICIENT_INPUT, PARTIAL, UNAVAILABLE; no silent LOW) | Verified (tests) |
| Stateless `/analyze` and the Check page | Verified (by hand) |
| Analysis history; alert de-duplication | Verified (tests) |
| `/models` page | Unverified by hand (tests pass) |
| Local LLM explanations (Ollama qwen2.5:0.5b), grounded by the shared `svckit.llm_grounding` checks | Verified live 2026-10-15 (controller); grounding fix of Phase 6 **agent-run only** (tests; live `OLLAMA_LIVE_TESTS=1` 2026-10-16: 3 runs, 1 accepted, 2 rejected for `role_inversion`); **quality weak**: most small-model answers are rejected, so the rule explanation is usually the only one shown |
| URL analysis | Partial: offline heuristics only, by design |
| Uploaded audio evidence transcription (local faster-whisper, WAV/MP3/OGG/M4A, limits, stored once, timed indicators, precise statuses) | **Unverified** (agent-run only, 2026-10-16): mocked-engine tests, real Whisper opt-in tests (English WAV + M4A, Hindi, Hinglish; 4 passed), Playwright e2e with real Whisper (agent run, own server); Postgres suite. Phase 6, **not pushed** |
| Accurate capability messages (`/analyze`, `/models`, `/admin/health`, evidence states) | **Unverified** (agent-run tests and Playwright) |
| Screenshot OCR (local Tesseract 5.5, eng + hin; text scored by the same rules, indicators point to the image; stored once per file; text viewable by owner and staff) | Verified 2026-10-19 (tests incl. real Tesseract on a synthetic screenshot; agent Playwright against a real server). OCR quality on real screenshots not measured |

### Live Protection
| Feature | Status |
|---|---|
| Consent-gated microphone; Start/Pause/Resume/Stop | Verified: scripted Chromium 2026-10-15 (0 requests before consent, 1 after Start, track ended on Stop) |
| Local Whisper transcription with timestamps (en, hi, Hinglish) | Verified 2026-10-14 (real-Whisper tests) |
| Incremental risk, indicators linked to segments, warnings | Verified (tests) |
| Live LLM explanation (off unless `OLLAMA_URL`), grounded with the shared checks; label and rejection note in the console | **Unverified** (agent-run tests; live Ollama 2026-10-16: 3 runs, 1 accepted, 2 rejected `repeats_scam_instruction`) |
| Streaming recognition | Limitation: incremental 4 s chunks |

### Intelligence
| Feature | Status |
|---|---|
| Fraud DNA (similar complaints): cached multilingual MiniLM offline, Qdrant embedded, redacted text, staff-only API and panel, opt-out/deletion excluded at index and query time | Verified (tests incl. a real-model offline test; agent Playwright against a real server); **EXPERIMENTAL**: thresholds from SYNTHETIC data (held-out precision 0.82 / recall 0.52); weak on Hinglish |
| Complaint network: identifiers from structured fields, observed (exact) and inferred (same domain) links, investigator confirm/reject with audit, related panel, graph (depth ≤ 2, size caps), clusters, POST-only identifier search; staff only; opted-out and deleted complaints excluded everywhere | Verified (tests incl. Postgres; agent Playwright against a real server with SYNTHETIC complaints); **EXPERIMENTAL**: a shared identifier is a lead, not proof |
| Complaint hotspots and map (grid cells from optional coordinates, places without coordinates, period comparison, staff map without any tile service) | Verified (tests; agent Playwright against a real server); counts exclude opted-out and deleted complaints |
| Ring graph, takedown simulation, city hotspots | Verified, **simulator data only** (not complaint intelligence) |

### Investigator and admin
| Feature | Status |
|---|---|
| Queue with search, filters, sort, pagination | Verified (tests); UI agent Playwright only |
| Admin users, roles, activation, audit, service health | Verified (tests); UI agent Playwright only |
| Investigation notes (staff only, append-only, audited), case timeline, Markdown case report (owner and staff versions, audited) | Verified (tests incl. Postgres; agent Playwright against a real server) |
| Configuration view | Planned |

### Platform
| Feature | Status |
|---|---|
| Single-process run (`make demo`, :8080) | Verified (running 2026-10-15) |
| Docker Compose stack | **Unverified** (no Docker daemon; config only) |
| GitHub Actions CI | Verified: passing on GitHub since `7b97731` (2026-10-16); Phase 6 commits reproduced locally on Python 3.11 (see test results), not yet run on GitHub. All 29 earlier runs had failed (the install step resolved local package names from PyPI); fixed in `7b97731`. Runs Python 3.11, all 16 Python packages with `-W error`, Dockerfile, link and compose checks. The console is not tested in CI |
| Python 3.11 compatibility (CI and Docker images) | Verified 2026-10-16 after fixing two concurrency bugs that only showed on 3.11 (graph-twin single flight, txn-guard duplicate hub submission) |
| Languages | English, Hindi, Hinglish evaluated (speech); simulator message check has en/hi/ta/bn texts; no 12-language claim |

## 2. Test results

Phase 6 rows (2026-10-16/17) were run on the Phase 6 code (`7a0593b`; the later commits change
documentation only) by the Phase 6 implementation agent ("agent" below), not by the owner. Earlier
rows: as recorded at the time.

| Date | Suite | Result | Run by |
|---|---|---|---|
| 2026-10-16 | `scripts/test_all.sh`, Python 3.13 (16 Python packages + vitest) | 17/17 packages passed (complaints 493 passed / 7 skipped, live_audio 115 / 5, demo_server 210, svckit 296, vitest 262) | agent |
| 2026-10-17 | CI `test` job reproduced: Python 3.11, fresh venv, clean clone, `--no-deps` local installs, `pip check`, every package `ruff` + `pytest -W error`, plus Dockerfile and link checks | all 16 packages passed on Python 3.11.14 from a clean clone of `7a0593b` (complaints 488 passed / 12 skipped: PyAV tests skip cleanly without the `stt` extra); Dockerfile and link checks clean ([details](docs/reports/PHASE_6_REPORT.md#7-tests-executed-and-exact-results)) | agent |
| 2026-10-16 | complaints on a disposable Postgres 14 (`scripts/with_postgres.sh`) | 494 passed, 6 skipped (opt-in live tests) | agent |
| 2026-10-16 | complaints real Whisper (`LIVE_STT_TESTS=1`, faster-whisper base, macOS `say`) | 4 passed (English WAV, English M4A, Hindi, Hinglish; all CRITICAL) | agent |
| 2026-10-16 | live Ollama qwen2.5:0.5b, complaints and live-audio explainers (`OLLAMA_LIVE_TESTS=1`, 3 runs each) | all passed; grounding decisions quoted in the Phase 6 report | agent |
| 2026-10-16 | web-console tsc / vitest / eslint / build | ok / 262 passed / clean / built (initial bundle 214.6 KB gzip) | agent |
| 2026-10-16 | Playwright, all specs, own demo server on :19401 (real Whisper, local sign-in) | 23 passed (incl. live-audio with real Whisper and the new audio-evidence test); an earlier run had 1 intermittent smoke failure that did not recur | agent |
| 2026-10-16 | GitHub Actions CI runs #30 (`build/implementation`) and #31 (`main`) on `7b97731` | success (jobs `test` and `static`) | GitHub |
| 2026-10-16 | CI steps reproduced locally: Python 3.11, fresh venv, clean clone, all 16 Python packages (`ruff` + `pytest -W error`) after `pip check` | all passed | controller |
| 2026-10-16 | `scripts/test_all.sh` on Python 3.13 (16 Python packages + web-console vitest) | 17/17 packages passed (python suites as in CI; vitest 249) | controller |
| 2026-10-15 | complaints `pytest -W error` | 446 passed, 3 skipped | controller (me) |
| 2026-10-15 | complaints live Ollama (`OLLAMA_LIVE_TESTS=1`) | 2 passed | controller |
| 2026-10-15 | demo_server `pytest -W error` | 209 passed | controller |
| 2026-10-15 | ruff (complaints, demo_server) | clean | controller |
| 2026-10-15 | web-console tsc / vitest / eslint | ok / 249 passed / clean | controller |
| 2026-10-15 | Microphone consent flow (scripted headless Chromium) | passed (see above) | controller |
| 2026-10-15 | Check page in the browser | passed by hand | controller |
| 2026-10-15 | Playwright (complaints 9/9; smoke, pages, live-audio-denied 12 passed; live-audio spec skipped: needs a WAV and STT engine) | passed | agent only |
| 2026-10-15 | complaints on a disposable Postgres 14 | 447 passed, 2 skipped | agent only |
| 2026-10-14 | live_audio | 104 passed, 4 skipped | controller |
| 2026-10-14 | live_audio real Whisper (`LIVE_STT_TESTS=1`) | 4 passed | controller |
| 2026-10-14 | gateway | 75 passed | controller |
| Step B1 (after the 2026-10-13 23:45 commit `c5d9767`; exact run date not recorded) | `make test-all` (16 packages) | python 1,902 passed, 17 skipped; vitest 172 | controller; not re-run since |

Every Python package and the console unit tests were re-run on 2026-10-16 (rows above).
Playwright and the Postgres run are still agent-only results.

## 3. Known limitations and open findings

1. ~~LLM grounding gap~~ **Fixed in Phase 6; re-verified by the controller 2026-10-17** (the observed sentence and variants rejected, correct sentences pass; live qwen2.5:0.5b again wrote "The caller is under digital arrest…" and it was rejected) (tests incl. the observed sentence as a
   regression, and live runs where such answers were rejected). The checks remain keyword/pattern
   heuristics: an accepted text can still be imprecise; it is labelled and never changes the score.
2. ~~Live-audio LLM explainer is ungrounded~~ **Fixed in Phase 6; live test re-run by the
   controller 2026-10-17** (shared checks, findings-only prompt, default qwen2.5:0.5b; tests and
   live runs). Same heuristic caveat.
3. ~~Inaccurate transcription message~~ **Fixed in Phase 6; verified by the controller
   2026-10-17** (real Whisper tests; a recording uploaded on the live server was transcribed and
   shown with its transcript in the browser): audio evidence is transcribed and
   every state has a precise reason; old analysis rows read as `not_implemented_at_analysis_time`.
   Tests and Playwright (agent runs).
4. **Evidence storage**: local disk, random names, not malware-scanned. Since 2026-10-19 files are
   encrypted at rest (AES-256-GCM) when `COMPLAINTS_EVIDENCE_KEY` is set, which production requires;
   downloads are audited. Transcripts and OCR text in the database are not encrypted.
5. **Development sign-in**: prints email and code to the terminal; per-process, per-IP rate limits;
   demo-server falls back to local mode when Clerk is unset, and the compose `demo-server` service
   does not set `COMPLAINTS_AUTH_MODE`. **Production guard added 2026-10-19**: `COMPLAINTS_ENV=production`
   refuses demo/local sign-in, the test outbox and non-https CORS origins at start-up.
6. **Retention**: an erasure command for complaints deleted by their owners exists
   (`python -m complaints.retention --older-than-days N`, dry run unless `--apply`, 2026-10-19); the
   period and a schedule are policy decisions not yet made, so nothing runs it automatically.
7. ~~`python -m demo_server` binds `0.0.0.0` unless `HOST` is set~~ **Fixed 2026-10-19**: loopback by
   default; the container image sets `HOST=0.0.0.0` and compose publishes on 127.0.0.1 only.
8. ~~Microphone denial message is generic~~ Fixed earlier (specific denied / no device / in use /
   insecure page messages, browser-tested); the remaining fallback now says what to check (2026-10-19).
9. ~~Alerts show raw status codes~~ **Fixed 2026-10-19**: new alerts use words; older stored alerts
   are shown with words in the console.
10. ~~The console defaults to the dark theme~~ **Fixed 2026-10-19**: a stored choice wins, otherwise
    the system preference, light when there is none.
11. All model training data is synthetic; metrics are not real-world performance.
12. On Python 3.11, `txn_guard` test `test_confirm_block_records_pending_in_the_same_cas[redis]`
    takes about 60 s (on 3.13 it is instant). It passes; the wait happens during test teardown with
    a fake hub that never answers. The store's own shutdown was measured as instant outside pytest;
    the cause was not isolated.
13. CI now runs the console's type-check, lint, unit tests and build (job `console`, 2026-10-19).
    It does not run Playwright or the Postgres-backed tests. CI also
    skips the optional `stt` extras, so the PyAV decoding tests and all real-STT tests run locally
    only.
14. Audio evidence transcription runs inside `POST /complaints/{id}/analysis` (bounded by the
    120 s run budget; no background queue). An evidence file and a live chunk can be transcribed
    at the same time (separate one-at-a-time limits) on the shared model.
15. Evidence transcripts are stored as plain text (not encrypted), like the evidence files; Hindi
    transcription with `base` is romanised and partly garbled; only synthetic TTS audio was tested.
16. With qwen2.5:0.5b most LLM answers are rejected by the grounding checks (live runs:
    complaints 1 of 3 accepted, live audio 1 of 3); the 3B model was not tested (8 GB machine).
17. Playwright `smoke.spec.ts` (simulator verify step) failed once on 2026-10-16 and once on
    2026-10-19, both times while other heavy jobs ran on the same machine; it passed alone and in
    every quiet full run. Attributed to CPU contention, not proven.
18. Seven files in `services/complaints` are not `ruff format`-clean (introduced in Phase 6; CI
    runs `ruff check` only, which passes). Formatting cleanup is tracked separately.

Fixed on 2026-10-17 (controller-verified): (a) a rejected or failed optional LLM explanation no
longer turns an otherwise complete analysis into PARTIAL; it is still listed in
`unavailable_capabilities`, provenance and limitations; (b) the category model now reads audio
transcripts together with the description (`category_model.evidence.audio_transcripts_included`);
the live test complaint moved from OTHER 0.41 to DIGITAL_ARREST 0.65.

Fixed in Phase 6 (2026-10-16): findings 1-3 above. Fixed on 2026-10-16 (no longer open): CI never passing; the graph-twin single-flight race and the
txn-guard duplicate hub submission on Python 3.11; the flaky demo-server seek test.

## 4. External setup still required

Clerk keys; Tesseract for OCR on any other machine (`brew install tesseract`, plus `hin.traineddata` from tesseract-ocr/tessdata_fast; installed on this machine 2026-10-19, not in the Docker images); the optional `stt` extra and a cached faster-whisper model on any
machine that should transcribe audio evidence (`pip install -e "services/complaints[stt]"`, model
in the Hugging Face cache; nothing is downloaded at runtime); Qdrant + an embedding runtime (sentence-transformers/torch or
fastembed); optionally Neo4j and PostGIS; a Docker daemon for the stack. Ollama runs only while
started (`OLLAMA_HOST=127.0.0.1:11434 ollama serve`). CI status can be read without signing in at
the repository's Actions page or the public API; step logs need a GitHub sign-in.

## 5. Next step

Recommended next: Phase 12 investigator tools (investigation notes, case report export, per-case
audit view), then Phase 14 hardening (production auth guard, retention job, Docker build); see
[docs/DEVELOPMENT_ROADMAP.md](docs/DEVELOPMENT_ROADMAP.md).
