# Project status

Last updated: **2026-10-16** (after Phase 0 and the CI repair). `main` and
`build/implementation` are both at `7b97731` on GitHub, which includes the 10 Phase B commits,
the Phase 0 documentation and the CI repair. **CI passes on GitHub for the first time**
(runs #30 and #31, jobs `test` and `static`, 2026-10-16).

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
| Ownership (foreign ids 404) | Verified (tests) |
| Status workflow with history and owner alerts | Verified (tests); investigator UI: agent Playwright only |
| Evidence upload with content sniffing and size limits; honest per-file result | Verified (tests); upload in the browser: agent Playwright only |
| Evidence download / owner removal | Verified (tests) |
| Complaint edit after submission | Planned |

### Analysis (Phase B, unpushed)
| Feature | Status |
|---|---|
| Rules + call-guard classifier + category model; score 0-100 labelled uncalibrated | Verified (tests; `/check` by hand 2026-10-15: CRITICAL 100 on a test message) |
| Provenance per item (rule / ml / llm / static_guidance) with evidence spans | Verified (tests; by hand) |
| Failure semantics (INSUFFICIENT_INPUT, PARTIAL, UNAVAILABLE; no silent LOW) | Verified (tests) |
| Stateless `/analyze` and the Check page | Verified (by hand) |
| Analysis history; alert de-duplication | Verified (tests) |
| `/models` page | Unverified by hand (tests pass) |
| Local LLM explanations (Ollama qwen2.5:0.5b) | Verified live (2 tests, 2026-10-15); **quality weak**, see limitation 1 |
| URL analysis | Partial: offline heuristics only, by design |
| Uploaded audio transcription | Planned (engine installed; not wired) |
| Screenshot OCR | **Blocked** (no OCR engine) |

### Live Protection
| Feature | Status |
|---|---|
| Consent-gated microphone; Start/Pause/Resume/Stop | Verified: scripted Chromium 2026-10-15 (0 requests before consent, 1 after Start, track ended on Stop) |
| Local Whisper transcription with timestamps (en, hi, Hinglish) | Verified 2026-10-14 (real-Whisper tests) |
| Incremental risk, indicators linked to segments, warnings | Verified (tests) |
| Streaming recognition | Limitation: incremental 4 s chunks |

### Intelligence
| Feature | Status |
|---|---|
| Fraud DNA (similar complaints, Qdrant) | Planned (Qdrant, embedding libraries not installed) |
| Complaint network (shared identifiers, typed links) | Planned |
| Complaint hotspots and map | Planned |
| Ring graph, takedown simulation, city hotspots | Verified, **simulator data only** (not complaint intelligence) |

### Investigator and admin
| Feature | Status |
|---|---|
| Queue with search, filters, sort, pagination | Verified (tests); UI agent Playwright only |
| Admin users, roles, activation, audit, service health | Verified (tests); UI agent Playwright only |
| Investigation notes, case report export, per-case audit view, configuration view | Planned |

### Platform
| Feature | Status |
|---|---|
| Single-process run (`make demo`, :8080) | Verified (running 2026-10-15) |
| Docker Compose stack | **Unverified** (no Docker daemon; config only) |
| GitHub Actions CI | Verified: passing on GitHub since `7b97731` (2026-10-16). All 29 earlier runs had failed (the install step resolved local package names from PyPI); fixed in `7b97731`. Runs Python 3.11, all 16 Python packages with `-W error`, Dockerfile, link and compose checks. The console is not tested in CI |
| Python 3.11 compatibility (CI and Docker images) | Verified 2026-10-16 after fixing two concurrency bugs that only showed on 3.11 (graph-twin single flight, txn-guard duplicate hub submission) |
| Languages | English, Hindi, Hinglish evaluated (speech); simulator message check has en/hi/ta/bn texts; no 12-language claim |

## 2. Test results

The 2026-10-16 rows were run against the code now on GitHub (`7b97731`). Earlier rows were run
before the CI repair; the repair changed only `graph_twin/service.py`, `txn_guard/holds.py`, a
demo-server test and the CI workflow, and all of those packages were re-run on 2026-10-16.

| Date | Suite | Result | Run by |
|---|---|---|---|
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

1. **LLM grounding gap**: a qwen2.5:0.5b explanation said "the caller is under 'digital arrest'"
   and passed the checks (2026-10-15). Labelled "may be imprecise"; never changes the score.
2. **Live-audio LLM explainer is ungrounded** (default `qwen2.5:3b`; off unless `OLLAMA_URL` set).
3. **Inaccurate message**: complaint analysis says "no transcription engine installed" although
   faster-whisper is installed (it is not wired to complaint evidence yet).
4. **Evidence storage**: local disk, random names, not encrypted, not malware-scanned; downloads not
   audited.
5. **Development sign-in**: prints email and code to the terminal; per-process, per-IP rate limits;
   demo-server falls back to local mode when Clerk is unset, and the compose `demo-server` service
   does not set `COMPLAINTS_AUTH_MODE`; no production guard.
6. **No retention enforcement.**
7. `python -m demo_server` binds `0.0.0.0` unless `HOST` is set.
8. Microphone denial message is generic ("could not be started").
9. Alerts show raw status codes (for example `UNDER_REVIEW`).
10. The console defaults to the dark theme (light is a toggle).
11. All model training data is synthetic; metrics are not real-world performance.
12. On Python 3.11, `txn_guard` test `test_confirm_block_records_pending_in_the_same_cas[redis]`
    takes about 60 s (on 3.13 it is instant). It passes; the wait happens during test teardown with
    a fake hub that never answers. The store's own shutdown was measured as instant outside pytest;
    the cause was not isolated.
13. CI does not run the console tests (vitest, Playwright) or the Postgres-backed tests.

Fixed on 2026-10-16 (no longer open): CI never passing; the graph-twin single-flight race and the
txn-guard duplicate hub submission on Python 3.11; the flaky demo-server seek test.

## 4. External setup still required

Clerk keys; Tesseract (OCR); Qdrant + an embedding runtime (sentence-transformers/torch or
fastembed); optionally Neo4j and PostGIS; a Docker daemon for the stack. Ollama runs only while
started (`OLLAMA_HOST=127.0.0.1:11434 ollama serve`). CI status can be read without signing in at
the repository's Actions page or the public API; step logs need a GitHub sign-in.

## 5. Next step

Awaiting the owner's approval of the next phase. Recommended: audio-evidence transcription and the
transcription-message fix (target phase 6) together with the LLM grounding fixes (findings 1 and
2); see [docs/DEVELOPMENT_ROADMAP.md](docs/DEVELOPMENT_ROADMAP.md).
