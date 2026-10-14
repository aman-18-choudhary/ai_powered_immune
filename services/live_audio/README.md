# live-audio: consent-based microphone fraud detection

Real audio, not simulated. The web console's **Live Interception -> Live microphone** mode
captures this device's microphone after an explicit consent step and the browser's own permission
prompt, sends short WAV chunks to the demo server, which transcribes them **locally** with Whisper
and analyses the transcript with the **existing call-guard scorer** (rules-v1 + the pinned clf-v1
classifier) plus a few transparent extra rules. The page shows the transcript, a risk score and
level, the indicators with the transcript segments that triggered them, timestamped warnings,
static safety advice and service status. Indicators are things heard in the speech: **they are not
a confirmation of fraud**.

## Mode: incremental chunked, not streaming

The browser cuts audio into chunks of `chunk_ms` (4 s by default) and uploads them in order; each
chunk is transcribed as a whole when it arrives (`mode: incremental_chunked`). This is
near-real-time incremental analysis, not streaming recognition: a word appears at the earliest one
chunk length after it was spoken, plus processing time.

Measured (Apple-silicon laptop, CPU, faster-whisper `base` int8, beam 1, VAD on):

| what | measured |
|---|---|
| server processing per 4 s chunk (STT + analysis), demo server log of the Playwright run | 0.85-0.99 s (last partial 1 s chunk 0.73 s) |
| HTTP round trip per chunk, real-STT pytest (TestClient) | 0.71-1.33 s (first includes warm-up) |
| model load (first session) | about 0.6 s, in a worker thread when a session is created |
| word-to-screen latency | up to about 4 s (chunk length) + about 1 s |

Whisper `tiny` is faster and noticeably worse (especially for Hindi); `small` and above are not
cached here and would be slower on CPU.

## Architecture

```
browser (web-console)                         demo server (/api/live-audio, demo auth)
getUserMedia (after consent + click)
 -> AudioContext + AudioWorklet (ScriptProcessor fallback)
 -> resample to 16 kHz mono Int16
 -> 4 s chunks -> WAV  --POST ?seq=N&start_ms=-->  strict WAV validation (in memory)
    ordered queue, retry/backoff on                -> + 0.5 s tail of the previous chunk
    network/5xx/429                                -> SttRunner (thread, 1 at a time, timeout)
                                                   -> faster-whisper (local, offline)
                                                   -> call-guard Scorer.score_text + extra rules
                                                   -> call-guard accumulate (decay) -> 0..100
                                                   <- segments, indicators, risk, warnings,
                                                      actions, explanation, status, errors
```

Modules: `wav.py` (validation), `stt.py` (engines + runner), `detect.py` (analysis, risk),
`session.py` (in-memory sessions, limiter), `service.py` (pipeline), `explain.py` (rules text +
optional Ollama), `guidance.py` (static advice), `api.py` (`build_router` for the demo server,
`create_app` standalone).

### Analysis and score

* Chunk text -> `call_guard.model.Scorer.score_text` (the same scorer object the demo server
  loads). Segment links: call-guard's `rules.analyse` per segment; a cue only the whole chunk
  explains (or the classifier) is linked to all of the chunk's segments.
* Extra live rules (`source: live_rules`), run on call-guard's advisory-filtered clauses (so
  "never share your OTP" does not fire): `OTP_PIN_REQUEST` 0.45, `MONEY_TRANSFER_INSTRUCTION`
  0.40, `UPI_BANK_FRAUD` 0.40, `REMOTE_ACCESS_REQUEST` 0.45, `SECRECY_DEMAND` 0.40,
  `INTIMIDATION` 0.30, `SPOKEN_LINK` 0.30 ("secure dash kyc dot in slash verify" ->
  `secure-kyc.in/verify`), `SPOKEN_PAYMENT_ID` 0.35 ("ramesh at the rate ybl" -> `ramesh@ybl`).
  English, romanised Hindi and Devanagari patterns. An extra rule is skipped on a segment where the
  overlapping call-guard code already fired (CREDENTIAL_ASK, ISOLATION_DEMAND, LEGAL_THREAT,
  SAFE_ACCOUNT_TRANSFER).
* Chunk score = noisy-OR(call-guard score, extra weights); session score = call-guard's
  `session.accumulate` (noisy-OR, half-life 300 s of audio time, classifier-only evidence capped).
  Score = round(100 x session score). **Uncalibrated heuristic** (`score_semantics`), not a
  probability. Levels: LOW 0-24, MEDIUM 25-49, HIGH 50-69, CRITICAL 70-100 (70 = call-guard's alert
  threshold). A warning is emitted when the level rises.
* Failures never read as low risk: STT unavailable/timeout -> `status.stt` says so, the risk is not
  updated and is flagged `stale` (`level: null` if nothing was assessed yet); scorer missing or
  raising -> `status.analysis: unavailable`, same. Silence -> zero segments (`analysis: no_speech`);
  no text is ever invented.
* Explanation: a deterministic rules text always; an Ollama text only when `OLLAMA_URL` is set and
  reachable, at most once per 20 s and only when the level changes, built from indicator codes,
  labels and redacted excerpts (no transcript), never changing score or level. Not exercised
  against a live Ollama here (not installed); tested with a fake transport.

## Engines and install

```
pip install -e "services/live_audio[stt,dev]"   # faster-whisper (CTranslate2); no model download
export HF_HUB_OFFLINE=1                          # models load with local_files_only=True anyway
LIVE_STT_ENGINE=faster_whisper LIVE_STT_MODEL=base make demo-server
```

| env | default | meaning |
|---|---|---|
| `LIVE_STT_ENGINE` | `faster_whisper` | `faster_whisper`, `whisper_cpp` (needs `WHISPER_CPP_URL`, a local whisper.cpp `server`; tested only with a fake transport) or `none` |
| `LIVE_STT_MODEL` | `base` | a model already in the Hugging Face cache (`Systran/faster-whisper-<name>`); missing -> `model_not_available` |
| `LIVE_STT_TIMEOUT_S` / `LIVE_STT_CONCURRENCY` | 30 / 1 | per chunk (waiting included) / parallel transcriptions |
| `LIVE_STT_BEAM`, `LIVE_STT_CPU_THREADS` | 1, 0 | decoding beam, CTranslate2 threads (0 = auto) |
| `LIVE_CHUNK_MS`, `LIVE_CHUNKS_PER_MINUTE` | 4000, 40 | chunk length told to the browser, per-user chunk rate |
| `OLLAMA_URL`, `OLLAMA_MODEL`, `OLLAMA_TIMEOUT_S` | unset, `qwen2.5:3b`, 20 | optional explanation |

If the model is not cached or faster-whisper is not installed, capabilities report STT
`unavailable` with the reason, and chunks return `status.stt: unavailable` (no risk update).
The demo-server Docker image installs live_audio without the `[stt]` extra (no model inside).

## Languages and quality (measured, synthetic voices)

`language`: `auto` (restricted to English or Hindi: Whisper's `p(en)` vs `p(hi)+p(ur)`), `hi`, `en`.
**Deviation:** the multilingual `base`/`tiny` models write Hindi speech in Urdu (Perso-Arabic)
script or garbled Devanagari. With `language=hi` and a romanised-Hinglish `initial_prompt` they
write romanised Hindi, which call-guard's romanised rules understand, so Hindi transcripts are
shown romanised. Segments that only echo the prompt are dropped.

Real-STT results (`LIVE_STT_TESTS=1`, macOS `say` voices, 4 s chunks through the API):

| audio | transcript (excerpt) | result |
|---|---|---|
| English scam, Samantha, 15.6 s | near verbatim; chunk-boundary artefact "share the OTP. Do not. TPU just received" | CRITICAL 100, digital arrest, authority, isolation, credential, safe account, legal threat |
| English benign, 6.6 s | verbatim | LOW 0, no indicators |
| Hindi (Devanagari script read by Lekha hi_IN), `hi` | "Mekh Police se boh raha hoon. Aap Digital Arrest me hai. Kisi ko mat baat baat aayi, abhi paise tranceh ahar ki je." | CRITICAL 94; "transfer kijiye" garbled so no transfer cue |
| Hinglish (Rishi en_IN), `auto` | "main CBI officer Boll Rahe Hun, art digital arrest mehoh. Kisi ko mat batana, Abhih OTP batayye." | CRITICAL 90; "digital arrest" phrase missed, isolation + OTP caught |

Synthetic TTS is clean speech; real phone audio over a speakerphone, accents, noise and
overlapping speakers will be worse, and Hindi with `base` on CPU is the weakest case.

## Privacy

* Raw audio is never written to disk (tests patch `open`/`tempfile` and check temp dirs): it lives
  in memory while a chunk is transcribed; only a 0.5 s tail of the latest chunk is kept to reduce
  cut words (duplicated leading words after the overlap are removed; a word split exactly at the
  boundary can still be garbled or doubled).
* Transcripts are never logged (tests use `caplog`) or persisted: they live in server memory until
  Stop (`/end` deletes the session) or 10 idle minutes. Logs carry seq numbers, durations, counts
  and status codes only; no session ids.
* The browser keeps the transcript only in the page until Clear or navigation; leaving the page or
  switching to the simulation tab stops the microphone and ends the session.

## API (`/api/live-audio`, any authenticated demo role; owner-only sessions)

| method and path | notes |
|---|---|
| `GET /capabilities` | STT engine/model/status/languages, scorer, LLM, chunk limits, `mode: incremental_chunked` |
| `POST /sessions` `{language: auto\|hi\|en}` | `201 {session_id, chunk_ms, max_chunk_bytes, ...}`; caps: 2 per user (citizen: per address), 50 global (`409 too_many_sessions`, `503 server_busy`); 10/min |
| `POST /sessions/{id}/chunks?seq=N&start_ms=M` body WAV | 16 kHz mono PCM16 only, <= 15 s, <= 512 KiB (`422 invalid_audio` + code, `413`, `415`); `seq` re-send returns the cached result (`duplicate: true`), late chunks are placed by `start_ms`, missing seqs reported in `gaps`; seq more than 64 ahead `422`; 40/min per user (`429` + `Retry-After`) |
| `GET /sessions/{id}` | full state for reconnect |
| `POST /sessions/{id}/end` | final summary, then the session is deleted |

Other users' sessions answer `404`. Errors are `{"error": code, "detail"?: code}`; no input is
echoed. The demo server raises its 64 KiB body cap to 512 KiB only for the chunk path.

## Threat model (short)

* Covert recording: capture starts only from the Start click after a consent checkbox; the
  browser permission prompt and the browser/OS recording indicators are never bypassed; a red
  "Microphone on" indicator and timer are shown; Pause disables the track; Stop and page exit stop
  all tracks and close the AudioContext.
* Data exposure: audio not stored; transcripts not logged or persisted; LLM prompt contains only
  indicator labels and redacted excerpts; sessions are owner-only.
* Abuse/DoS: auth required, per-user and global session caps, chunk rate limit, body and duration
  caps, one transcription at a time with a timeout, idle TTL.
* Misleading output: score documented as uncalibrated; every indicator says "detected, not
  confirmed"; failures are shown as unavailable/stale, never as low risk; no simulated
  transactions, blocks or analyst actions appear in this mode.
* Not covered: speaker separation (the caller and the user are one stream), adversarial audio,
  very long sessions beyond the bounded transcript (oldest segments dropped, flagged).

## Tests

```
cd services/live_audio && python -m pytest -W error -q                      # fake STT engine
LIVE_STT_TESTS=1 HF_HUB_OFFLINE=1 python -m pytest -W error -q -m stt -s     # real Whisper (macOS say + ffmpeg)
```

Browser end-to-end: `web-console/e2e/live-audio.spec.ts` and `live-audio-denied.spec.ts`
(see web-console/README.md).
