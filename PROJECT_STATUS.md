# Project status

Last updated: **2026-10-15** (Phase 0). Branch `build/implementation`, HEAD `91ebfeb`.
`origin/main` and `origin/build/implementation` are at `8e4dee0`; the 10 Phase B commits
(`a34754b`..`91ebfeb`) are **committed locally and not pushed**, pending the owner's approval.

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
| GitHub Actions CI | Workflow exists; **run status on GitHub not checked** (`gh` not installed) |
| Languages | English, Hindi, Hinglish evaluated (speech); simulator message check has en/hi/ta/bn texts; no 12-language claim |

## 2. Test results

No test was re-run during the Phase 0 documentation work (documentation only). The results below
were obtained earlier, against code that is unchanged since (HEAD `91ebfeb`, no code edits in
Phase 0).

| Date | Suite | Result | Run by |
|---|---|---|---|
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

Simulator services not touched since step B1 (sim_engine, call_guard, txn_guard, antibody_hub,
evidence_ledger, graph_twin, geo_intel, citizen_shield, contracts, svckit, e2e) were last run in
that `make test-all`.

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

## 4. External setup still required

Clerk keys; Tesseract (OCR); Qdrant + an embedding runtime (sentence-transformers/torch or
fastembed); optionally Neo4j and PostGIS; a Docker daemon for the stack; `gh` or the GitHub web UI
to check CI. Ollama runs only while started (`OLLAMA_HOST=127.0.0.1:11434 ollama serve`).

## 5. Next step

Awaiting the owner's review of Phase 0 and approval to (a) push the Phase B commits and (b) start
the next phase. Recommended next phase: audio-evidence transcription and the message fixes (target
phase 6) together with the grounding fixes; see [docs/DEVELOPMENT_ROADMAP.md](docs/DEVELOPMENT_ROADMAP.md).
