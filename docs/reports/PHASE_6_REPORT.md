# Phase 6 report: audio evidence transcription, accurate capability messages, LLM grounding

Dates: **2026-10-16** (implementation and most tests) and **2026-10-17** (documentation, CI
reproduction). Branch `build/implementation`, from `cb783a3`. All work and every test below was
done by the Phase 6 implementation agent; the owner has not yet verified it by hand.

## 1. Phase objective

Transcribe uploaded audio evidence with the faster-whisper engine already used by Live Protection
and analyse the transcript like the complaint text; replace the inaccurate "no transcription engine
installed" message with precise per-file and per-capability states (finding 3); fix the LLM
role-confusion gap (finding 1) with a shared grounding module, and ground the Live Protection
explainer with it (finding 2); show all of this in the console.

## 2. Scope of work

In scope (as approved): A) audio evidence transcription with limits, persistence (migration 9),
transcript endpoint, analysis integration and a shared engine in the demo server; B) accurate
capability messages; C) shared grounding in `libs/svckit`; D) grounded Live Protection explainer;
E) console (evidence list, transcript viewer, analysis panel, Check page, Live monitor, Models
page). Out of scope and untouched: OCR, Fraud DNA, network, geo, any other service, CI workflow,
Docker files, the owner's server on :8080 and the Ollama server (only used read-only by the opt-in
tests through its HTTP API).

## 3. Features implemented

* **Audio evidence transcription** (`services/complaints/complaints/transcribe.py`): WAV decoded
  with the stdlib `wave` module (numpy mixing / linear resampling to 16 kHz mono PCM16); MP3,
  OGG/Opus, M4A and float WAV with PyAV (ships with faster-whisper; without it
  `decode_unavailable`). Transcribed by live-audio's `SttEngine` (`engine_from_env`), language
  `auto` with the same English vs Hindi+Urdu decision and romanised-Hindi prompt, in a worker
  thread, one at a time per process. Limits (env): 180 s per file (longer → `truncated`), 2 new
  files per run (`not_attempted_limit`), 120 s budget per run (`timeout`, `budget_exhausted`),
  plus the existing upload caps. Runs synchronously in `POST /complaints/{id}/analysis`; each file
  is transcribed once and the stored transcript reused; a per-complaint lock prevents duplicate
  work in one process. Optional extra `complaints[stt] = live-audio[stt] + numpy`, imported lazily.
* **Analysis**: transcripts (bounded to 4,000 characters) scored by the same call-guard scorer
  (rules + clf-v1); `transcript_score` in the formula `max(text, transcript, category prior) + URL`;
  transcript indicators carry `{type: evidence_transcript, field: evidence_transcript,
  evidence_id, start_s, end_s, snippet}`; per-file provenance stages; never LOW while a recording
  is untranscribed; untranscribed audio listed per file with the precise reason.
* **Precise states**: `transcribed`, `truncated`, `engine_not_configured`,
  `model_not_available_offline`, `timeout`, `decode_failed`, `decode_unavailable`,
  `unsupported_format`, `budget_exhausted`, `not_attempted_limit`, plus `engine_error` (engine
  raised) and `not_attempted` (no run yet). Old analysis rows with the inaccurate reason read as
  `not_implemented_at_analysis_time`. `POST /analyze` says audio is analysed only when attached to a
  complaint and that no OCR engine is installed. `/admin/health` and `/models` report the STT
  engine truthfully without loading it (`ready`, `available_offline`,
  `model_not_available_offline`, `engine_not_configured`, `configured`).
* **Shared grounding** (`libs/svckit/svckit/llm_grounding.py`): per-sentence role-inversion
  detection that ignores quotes, case and punctuation (caller / scammer / sender / officer under
  arrest, placed under (digital) arrest, investigated, threatened, the victim, asked to pay;
  "claims to be under arrest"; "you are from the CBI"; safety steps attributed to the caller),
  plus checks found while testing against the live model: `prompt_echo`, the scammer's claim stated
  as fact ("You are under digital arrest." → `unsupported_claim`), `repeats_scam_instruction`
  ("Do not tell anyone about this call."). Every sentence of the answer is checked, not only the
  three shown. Used by the complaints explainer (behaviour and existing tests kept) and Live
  Protection.
* **Live Protection explainer**: findings-only prompt (third-person labels, one redacted excerpt per
  finding in a separate block, fixed steps; no transcript, score or level), temperature 0.1, 120
  tokens, timeout; rejected / failed answers suppressed (`llm_status`, `llm_reason`); accepted text
  labelled `llm_label` "LLM-generated (model), may be imprecise". Default model `qwen2.5:0.5b`
  (env); off unless `OLLAMA_URL` is set. `live_audio` declares `svckit`.
* **Demo server**: the mounted complaints service receives the live-audio engine object (one model
  in memory); `HF_HUB_OFFLINE` handling unchanged.
* **Console**: per-file transcription status with the precise reason, timestamped transcript
  viewer (on demand, `aria-expanded`), audio indicators with time offsets and an "Audio evidence"
  section in the analysis panel, accurate Check page wording, STT panel on the Models page, LLM
  label and rejection note in the Live monitor.

## 4. Files added and modified

Added: `libs/svckit/svckit/llm_grounding.py`, `libs/svckit/tests/test_llm_grounding.py`,
`services/complaints/complaints/transcribe.py`, `services/complaints/tests/{stt_fakes.py,
test_transcription.py, test_audio_decode.py, test_stt_live.py}`,
`services/live_audio/tests/test_ollama_live.py`,
`web-console/src/components/complaints/TranscriptViewer.tsx`,
`web-console/src/lib/complaints/transcription.ts`,
`web-console/src/routes/account/audio-evidence.test.tsx`, this report.

Modified: complaints `analysis.py`, `api.py`, `config.py`, `explain.py`, `migrations.py`,
`provenance.py`, `schema.py`, `store.py`, `pyproject.toml`, tests `conftest.py`,
`test_analyze_inputs.py`, `test_llm_grounding.py`, `test_migrations.py`, `test_ollama_live.py`;
live_audio `config.py`, `explain.py`, `service.py`, `session.py`, `pyproject.toml`, tests
`test_api.py`, `test_explain.py`; demo_server `api.py`, `complaints_mount.py`,
`tests/test_live_audio.py`; console `AnalysisResultView.tsx`, `EvidenceManager.tsx`,
`LiveMonitor.tsx` (+ test), `lib/complaints/{api,types}.ts`, `lib/i18n/liveAudio.ts`,
`lib/liveAudio/api.ts`, `routes/account/{CheckMessage,Models}.tsx`, `analysis.test.tsx`,
`e2e/complaints.spec.ts`; docs: `PROJECT_STATUS.md`, `docs/DEVELOPMENT_ROADMAP.md`,
`docs/DATABASE_DESIGN.md`, `docs/API_SPECIFICATION.md`, `services/complaints/README.md`,
`services/live_audio/README.md`. 47 files in the code commits (+3,799 / −322 lines).

## 5. Architecture or database changes

* Migration 9 `evidence_transcripts`: id, evidence_id (FK, unique), complaint_id (FK, indexed),
  status (CHECK: transcribed, truncated, decode_failed, unsupported_format), engine, model,
  language, duration_s, transcribed_s, truncated, segments JSON `[{start_s, end_s, text}]`,
  error_reason, created_at. Only definitive outcomes are stored; transient ones live in the
  analysis `detail.audio_transcription`. Deleted with the evidence (same transaction).
* Data flow: analysis endpoint → `Transcriber.prepare` (load stored transcripts → decode →
  engine in a thread → store) → pipeline (text scoring, transcript scoring, category, URL,
  entities, LLM) → stored analysis.
* The LLM output checks moved from `complaints/explain.py` to `svckit/llm_grounding.py`
  (complaints re-exports the old names).

## 6. API endpoints added or changed

* **New** `GET /complaints/{id}/evidence/{eid}/transcript` (owner and staff; foreign ids 404;
  `not_audio_evidence`, `transcript_not_available` with the file's state).
* Changed (additive): evidence items gain `kind` and `transcription`; analyses gain
  `audio_transcription[]`, capabilities `audio_transcription` (`available` / `unavailable` /
  `skipped_no_input`) and `audio_transcript_scoring`, `score_components.transcript_score`;
  `POST /analyze` `input_capabilities.audio` = `not_accepted_here`; `/models` and `/admin/health`
  gain `stt`; live-audio `explanation` gains `llm_reason`, `llm_model`, `llm_label` and the status
  `rejected`. Details: [API_SPECIFICATION.md](../API_SPECIFICATION.md).

## 7. Tests executed and exact results

Mocked tests use a scripted STT engine (`stt_fakes.FakeStt`), fake HTTP transports for Ollama, or
mocked `fetch` in the console; **real-service tests** are opt-in and named as such.

| Date | Check | Kind | Result |
|---|---|---|---|
| 2026-10-16 | svckit `pytest -W error` | unit (no network) | 296 passed (116 of them in the new `test_llm_grounding.py`) |
| 2026-10-16 | complaints `pytest -W error` (SQLite) | mocked STT / Ollama, real decoding, real call-guard where indicators are located | 493 passed, 7 skipped (1 Postgres-only, 2 live Ollama, 4 real STT) |
| 2026-10-16 | complaints on disposable Postgres 14.21 (`scripts/with_postgres.sh`) | as above | 494 passed, 6 skipped |
| 2026-10-16 | complaints PyAV decoding (MP3, OGG/Opus, M4A, float WAV encoded with PyAV) | real decoders | passed (in the 493; `importorskip("av")`, skipped in CI) |
| 2026-10-16 | live_audio `pytest -W error` | fake STT / fake Ollama | 115 passed, 5 skipped |
| 2026-10-16 | demo_server `pytest -W error` | scripted engine | 210 passed |
| 2026-10-16 | `bash scripts/test_all.sh` (Python 3.13) | all packages | **17/17 packages passed** (vitest 262) |
| 2026-10-16 | **real** Whisper through the evidence path (`LIVE_STT_TESTS=1 HF_HUB_OFFLINE=1`) | real faster-whisper base, macOS `say` | **4 passed** (below) |
| 2026-10-16 | **real** Ollama qwen2.5:0.5b, complaints and live explainers (`OLLAMA_LIVE_TESTS=1`), 3 runs each | real local model | all passed; decisions below |
| 2026-10-16 | web-console `npx tsc --noEmit` / `npx vitest run` / `npm run lint` / `npm run build` | mocked fetch | ok / **262 passed** (36 files) / clean / built, initial bundle **214.6 KB gzip** |
| 2026-10-16 | Playwright, all specs, own demo server `127.0.0.1:19401` (real Whisper, local sign-in, fresh data) | real server | **23 passed** (complaints 10 incl. the new audio-evidence test, pages 9, smoke 2, live-audio-denied 1, live-audio 1 with real Whisper). Earlier runs: 1st run 2 failures (an outdated assertion "Audio: unavailable" in `complaints.spec.ts`, fixed; the live-audio spec with an ad-hoc WAV whose chunk boundary split "You are under / digital arrest", fixed by using the documented script); 2nd run 2 failures (the new audio test added a complaint to the shared citizen, fixed by giving it its own citizen; **one smoke failure**, `verify-status` not found, which did not recur) |
| 2026-10-17 | CI `test` job reproduced (see 7a) | Python 3.11 | see 7a |
| 2026-10-17 | `python scripts/check_links.py` (+ new and root files) | docs | see 7b |

**Real Whisper outputs** (faster-whisper `base`, CPU, int8; description benign so `text_score`
0; the score comes from the transcript):

| Case | Transcript | Level | Transcript indicators (start–end s) |
|---|---|---|---|
| English, Samantha, WAV 44.1 kHz stereo (stdlib decode + resample), 13.4 s | "Hello, this is Officer Sharma calling from the CBI, a parcel in your name was seized with drugs. You are under digital arrest. Do not tell anyone about this call and do not disconnect. Transfer the money to the safe account now." | CRITICAL 100 | DIGITAL_ARREST_PHRASE 6.16–7.76, SAFE_ACCOUNT_TRANSFER 11.28–13.24, ISOLATION_DEMAND 7.76–11.28, AUTHORITY_IMPERSONATION 0.0–5.16, LEGAL_THREAT 0.0–5.16, URGENCY 11.28–13.24, classifier (whole transcript) |
| English, M4A/AAC via afconvert (PyAV decode), 13.6 s | same, punctuated with commas | CRITICAL 100 | DIGITAL_ARREST_PHRASE 5.12–11.04, ... |
| Hindi, Lekha (Devanagari text), 6.9 s, detected `hi` | "Mekh Police se boh raha hoon. Aap Digital Arrest me hai. Kisi ko mat baat baat aa ye. Aap vi pese kran sabhar ki je." | CRITICAL 100 | DIGITAL_ARREST_PHRASE, ISOLATION_DEMAND, AUTHORITY_IMPERSONATION (0.0–6.66, one segment) |
| Hinglish, Rishi, 7.7 s, detected `hi` | "Main CBI office a bowl raha hoon. Aap digital arrest me ho. Kisi ko mat batana. Abhi pese transfer karo." | CRITICAL 100 | DIGITAL_ARREST_PHRASE 2.0–7.0, ISOLATION_DEMAND 2.0–7.0, AUTHORITY_IMPERSONATION 0.0–2.0, URGENCY 2.0–7.0 |

Each case also verified that a second analysis reused the stored transcript.

**Live Ollama (qwen2.5:0.5b) answers and grounding decisions** (the raw answer is the one the
checks judged; final runs after the last check was added):

* Complaints, digital-arrest example, run 1: "The message suggests that the caller is under
  digital arrest and is demanding money ..." → **rejected `role_inversion`**; rule explanation
  used.
* Complaints, run 2: a numbered echo of the findings → **accepted** after tidying: "The text
  matches known digital-arrest call scripts. The caller demands the victim stay on the call, stay
  silent or stay in view. The caller says the victim is under 'digital arrest'."
* Complaints, run 3: "The message suggests that the caller is under digital arrest ..." →
  **rejected `role_inversion`**.
* Complaints, benign example (3 runs): no findings, LLM not called (`skipped_no_input`).
* Earlier runs during the phase also produced "The message is safe and confirmed." → rejected
  `unsupported_claim`.
* Live Protection, runs 1 and 3: "The caller says you are under 'digital arrest.' The caller claims
  to be police, CBI or another agency. Do not tell anyone about this call. Hang up the call. ..." →
  **rejected `repeats_scam_instruction`** (the scammer's isolation demand given as advice).
* Live Protection, run 2: "- The caller says you are under 'digital arrest'. - The caller claims to
  be police, CBI or another agency. - The caller tells you not to tell anyone or to stay on the
  call." → **accepted** as three sentences.
* Before the prompt and checks were revised the live model echoed the prompt verbatim, wrote "You
  are from the CBI", "The caller claims to be under 'digital arrest'", "You are under 'digital
  arrest' and should not disclose any information" and "The caller advises you to hang up the
  call"; each is now a regression case in `libs/svckit/tests/test_llm_grounding.py`.

### 7a. CI test job reproduced on Python 3.11

Run on 2026-10-17 from a clean `git clone` of `7a0593b` (the last code commit; the documentation
commits after it change Markdown only) into a scratch directory, with a fresh Python **3.11.14**
venv, installed exactly as `.github/workflows/ci.yml` does (external requirements computed from the
pyprojects with the `stt` group skipped, local packages `--no-deps -e`), then `pip check` ("No
broken requirements found."), then `ruff check .` and `python -m pytest -W error -q` in every
package. faster-whisper and PyAV were absent, as in CI. **Exit 0, every package passed:**

| Package | Result |
|---|---|
| contracts | 44 passed |
| libs/svckit | 296 passed |
| services/antibody_hub | 160 passed, 5 skipped |
| services/call_guard | 141 passed |
| services/citizen_shield | 71 passed |
| services/complaints | 488 passed, 12 skipped (the 7 opt-in / Postgres skips + 5 PyAV decoding tests skipped cleanly) |
| services/demo_server | 210 passed |
| services/evidence_ledger | 184 passed, 7 skipped |
| services/gateway | 75 passed |
| services/geo_intel | 64 passed, 1 skipped |
| services/graph_twin | 122 passed, 3 skipped |
| services/live_audio | 115 passed, 5 skipped |
| services/sim_engine | 56 passed |
| services/txn_guard | 376 passed |
| tests/e2e/audit | 32 passed |
| tests/e2e/bench | 38 passed |

`ruff check .` passed in every package. The `static` job's checks in the same clone:
`check_dockerfiles.py` 11 Dockerfiles, 0 problems; `check_links.py` 27 files, ~132 links, 0
broken. (The compose config check needs a Docker daemon, which is not available here.)

### 7b. Link check

On the final tree (2026-10-17):

```
$ .venv/bin/python scripts/check_links.py
27 files, ~134 links, 0 broken
$ .venv/bin/python scripts/check_links.py README.md PROJECT_STATUS.md docs/*.md \
    docs/reports/PHASE_6_REPORT.md services/complaints/README.md services/live_audio/README.md
17 files, ~137 links, 0 broken
```

Anchors (`#7-tests-executed-and-exact-results`, `#3-known-limitations-and-open-findings`) were
checked by hand against the headings.

## 8. Frontend build and backend start-up status

`npm run build` succeeded (Vite; initial bundle: `index` 101.5 KB + `motion` 47.3 KB +
`react-dom` 42.5 KB + CSS 12.1 KB + small chunks = 214.6 KB gzip). `dist/` is not tracked in git.
The demo server started on `127.0.0.1:19401` (my own process, fresh data under the scratch
directory, `HF_HUB_OFFLINE=1`, `LIVE_STT_ENGINE=faster_whisper`) for Playwright and was stopped
afterwards by PID. The owner's server on :8080 and Ollama on :11434 were not stopped or
reconfigured.

## 9. Security and privacy checks

* Ownership: transcripts have the evidence's scope; tests for owner, investigator and admin (200),
  another citizen (404, no segments in the body), foreign complaint id (404), non-audio (404),
  unknown id (404).
* Deletion: owner and admin evidence removal deletes the transcript row (tested).
* Logging: no transcript text, file names or evidence ids in the service logs (caplog test; the
  Playwright server log searched for transcript phrases: 0 matches).
* LLM: output untrusted; grounding failures suppressed with the rule explanation and `rejected` +
  reason recorded in provenance / the live view; the LLM never changes score, level or category.
* No model download at runtime (`local_files_only`); no paid or cloud API; Ollama local only.
* No secrets added; test e-mails use `example.org`.
* Not done: transcripts are not encrypted at rest (like evidence files); no malware scanning.

## 10. External services required

None new. Optional: the `stt` extra and a cached faster-whisper model (present on this machine:
`Systran/faster-whisper-base` and `-tiny`); a local Ollama for explanations. Still blocked: Clerk
keys, OCR engine, Docker daemon.

## 11. Features that remain incomplete

OCR of screenshots (no engine); document text extraction; background queue for long audio; speaker separation; evaluation on real recordings; a 3B
local model (not tested on this 8 GB machine). Everything outside Phase 6 in the roadmap.

## 12. Known bugs and limitations

* With qwen2.5:0.5b most LLM answers are rejected (1 of 3 accepted in each explainer's final runs);
  the checks are heuristics, so an accepted text can still be imprecise.
* Transcription is synchronous inside the analysis request (up to the 120 s budget). An evidence
  file and a live chunk may be transcribed at the same time on the shared model.
* Hindi with `base` is romanised and partly garbled; only synthetic TTS speech was tested.
* PyAV decoding and all real-STT tests are not run by CI (the `stt` extra is skipped there).
* Playwright `smoke.spec.ts` failed once in three runs (simulator verify step); not reproduced.
* See [PROJECT_STATUS.md](../../PROJECT_STATUS.md#3-known-limitations-and-open-findings).

## 13. Git commit hashes

`9dbad27` svckit grounding · `ed8004f` complaints uses it · `ae741ca` live-audio grounding ·
`aa0a224` complaints audio transcription · `890139f` demo-server shared engine · `5427c60` further
grounding checks · `b6e77c0` live prompt + live test · `bcc21e9` complaints opt-in real tests ·
`f2b1a2d` web console · `7a0593b` e2e · plus the documentation commit(s) that contain this report.

## 14. Git push status

**Not pushed, awaiting owner approval.** `origin/main` and `origin/build/implementation` remain at
`cb783a3`.

## 15. Commands to verify

```bash
cd /Users/aman/lastproject && git log --oneline cb783a3..HEAD
bash scripts/test_all.sh
cd services/complaints && ../../.venv/bin/python -m pytest -W error -q && PYTHON=../../.venv/bin/python bash scripts/with_postgres.sh
LIVE_STT_TESTS=1 HF_HUB_OFFLINE=1 ../../.venv/bin/python -m pytest -W error -q -s tests/test_stt_live.py
OLLAMA_LIVE_TESTS=1 ../../.venv/bin/python -m pytest -W error -q -s tests/test_ollama_live.py
cd ../live_audio && OLLAMA_LIVE_TESTS=1 ../../.venv/bin/python -m pytest -W error -q -s tests/test_ollama_live.py
cd ../../web-console && npx tsc --noEmit && npx vitest run && npm run lint && npm run build
# Playwright: start a demo server on a free port with COMPLAINTS_AUTH_MODE=local,
# COMPLAINTS_LOCAL_AUTH_OUTBOX=<file>, INVESTIGATOR_ALLOWLIST=investigator.e2e@example.org,
# ADMIN_BOOTSTRAP_EMAILS=admin.e2e@example.org, DEMO_STATIC_DIR=web-console/dist, then:
CONSOLE_URL=http://127.0.0.1:<port> COMPLAINTS_OUTBOX=<file> E2E_ADMIN_EMAIL=admin.e2e@example.org \
  LIVE_AUDIO_WAV=<16 kHz speech wav> npx playwright test
cd .. && .venv/bin/python scripts/check_links.py
```

By hand: upload a call recording to a complaint, run the analysis, open "Show transcript", check
the timed indicators; on `/check` read the attachments note; on `/models` the speech-to-text panel.

## 16. Recommended next phase

1. Owner review of Phase 6 (by hand: the evidence flow above and a live session with
   `OLLAMA_URL`), then approve the push.
2. Next: **Phase 9** (complaint network from shared identifiers; no new software), then Phase 8
   (Fraud DNA). Consider evaluating `qwen2.5:3b` where hardware allows, since 0.5b answers are
   mostly rejected.

## Addendum (2026-10-17): owner review fixes and push

Two findings from the owner's review were fixed before pushing (no other findings were changed in
this batch):

1. **An optional LLM failure no longer marks the analysis PARTIAL.** A rejected, timed-out or
   failed LLM explanation is still reported (`unavailable_capabilities`, provenance stage
   `rejected`/`timeout`/`error`, and a limitation saying the rule explanation is shown and the
   score, level and category are unaffected), but the status stays `COMPLETED` when every
   required stage ran.
2. **The category model reads audio transcripts.** It classifies the description together with
   the transcripts of audio evidence (bounded); `category_model.evidence.audio_transcripts_included`
   records how many were included.

Verification (controller, 2026-10-17): complaints 495 passed, 7 skipped (Python 3.13); complaints
on Python 3.11 in the CI-like environment 490 passed, 12 skipped; ruff check clean; real Whisper
(4) and live Ollama (2) tests passed; demo_server 210 passed. On the running server, the audio test
complaint re-analysed as `COMPLETED`, CRITICAL 100, category DIGITAL_ARREST 0.65 with one transcript
included (before: `PARTIAL`, OTHER 0.41), with the LLM text still rejected (`role_inversion`).

Tracked separately (not changed here): seven files in `services/complaints` are not
`ruff format`-clean (from the Phase 6 commits; CI runs `ruff check` only); the other findings in
[PROJECT_STATUS.md](../../PROJECT_STATUS.md#3-known-limitations-and-open-findings).
