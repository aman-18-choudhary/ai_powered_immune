# ScamShield Immune: web console (phase F1 + F2)

Premium, animated frontend for the demo server. Seven pages, all in one shell:

| Route | Page | What it does |
|---|---|---|
| `/` | Live Interception | **Live microphone** (default): consent, real mic capture, live transcript, risk, indicators, warnings, safety actions (real audio, see below). **Simulated scenario (demo)** tab (`/?mode=simulation`): the cinematic scenario player (about 75 s at 1x), phone, two bank consoles, map, finale with verify |
| `/command` | Command Centre | KPIs, India map (size-scaled cells, mule-hop arcs), incident feed, ring takedown simulator |
| `/analyst` | Analyst Console | deadline-sorted virtualised hold queue, countdown rings, reason meanings, upgrade history, guarded resolve, optimistic UI |
| `/evidence` | Evidence | animated hash chain, entry inspector (hash recomputed in the browser), case builder, package export (ETag), verify |
| `/citizen` | Citizen Shield | mobile-first assistant in en / hi / ta / bn, three samples, one verdict card, nothing stored |
| `/insights` | Insights | benchmark with Wilson CIs, rail table + NEFT callout, ablations, delay sensitivity, hero case, prevalence table |
| `/about` | About | animated architecture diagram, real vs simulated, limits, how to run, credits |

Everything on screen is simulated except the Live microphone mode; the SIMULATION pill and "About these numbers" popover are always visible.

## Live microphone mode

`src/components/live/LiveMonitor.tsx`, state in `src/state/liveAudio.ts`, audio helpers in
`src/lib/liveAudio/` (capture via AudioWorklet with a ScriptProcessor fallback, streaming
resampler to 16 kHz, Int16 + WAV encoder, chunker), strings in `src/lib/i18n/liveAudio.ts` (en, hi).

* The microphone is requested only when the user ticks the consent box and presses **Start
  monitoring**. Friendly messages for permission denied (with how to re-enable), no microphone,
  microphone in use, insecure context (https or localhost needed) and server/STT unavailable.
* Chunks (`chunk_ms` from the server, default 4 s) go through an ordered upload queue: one request
  at a time, seq numbers, retry with backoff on network errors, 5xx and 429; a lost session stops
  monitoring. **Pause** disables the track and stops sending; **Resume**; **Stop** stops every
  track, closes the AudioContext, sends the last partial chunk, ends the server session and drops
  audio buffers. Leaving the page or switching tab stops the microphone.
* Everything displayed comes from server responses: transcript with timestamps and language,
  segments highlighted and linked to the indicators they triggered, risk gauge (uncalibrated
  0-100) with a per-chunk sparkline, indicators labelled "detected, not confirmed", timestamped
  warnings (announced through an `aria-live` region), static safety actions, service status,
  processing errors. No transactions, holds, mule matches or analyst actions appear here.
* Browser test: `e2e/live-audio.spec.ts` (needs a server with `LIVE_STT_ENGINE=faster_whisper` and
  `LIVE_AUDIO_WAV` = a 16 kHz mono speech WAV; Chromium's fake device, or
  `LIVE_E2E_DEVICE_SHIM=1` where the fake capture device does not work, which replaces only the
  device with a MediaStream playing the WAV) and `e2e/live-audio-denied.spec.ts`.

```
say -v Samantha -o scam.aiff "This is officer Sharma from the CBI. You are under digital arrest. Share the OTP now."
ffmpeg -i scam.aiff -ar 16000 -ac 1 -c:a pcm_s16le scam-16k.wav
LIVE_AUDIO_WAV=$PWD/scam-16k.wav CONSOLE_URL=http://localhost:8080 npx playwright test e2e/live-audio.spec.ts e2e/live-audio-denied.spec.ts
```

## Shortcuts

| Where | Keys |
|---|---|
| Anywhere | `Ctrl/Cmd + K` command palette (pages, start/pause demo, theme, motion) |
| Live (Simulated scenario tab) | `Space` play/pause, `Left/Right` step, `F` fullscreen |
| Analyst | `J`/`K` move, `C` confirm mule, `R` release (both ask for a reason note), `?` help |

## Run

```
# terminal 1: backend (simulated data, real service code)
cd /Users/aman/lastproject && source .venv/bin/activate
PORT=8080 DEMO_CORS_ORIGINS=http://localhost:5173 python -m demo_server

# terminal 2: console with hot reload (proxies /api incl. WebSocket to :8080)
make console-dev            # http://localhost:5173

# or one URL: build and serve from the demo server
make demo                   # http://localhost:8080  (DEMO_STATIC_DIR=web-console/dist)
```

Choose a demo role on the splash (no password). Officer or Admin can run the scenario; Analyst or
Admin can press **Confirm mule**; Citizen is read-only here.

## Scripts

| Script | What |
|---|---|
| `npm run dev` | Vite dev server (5173), `/api` proxy (`DEMO_API` overrides the target) |
| `npm run build` | `tsc --noEmit` then production build into `dist/` |
| `npm run typecheck` / `npm run lint` | strict TS (`noUncheckedIndexedAccess`) / ESLint (typescript-eslint) |
| `npm test` | Vitest + Testing Library (unit + component, uses the recorded fixture) |
| `npm run test:e2e` | Playwright against a running console + demo server (`CONSOLE_URL`, default 5173): smoke (golden path, about 75 s) and `pages.spec.ts` (analyst, evidence, citizen, insights, palette, 404, axe-core in dark and light; about 2.5 minutes in all). Any console error or warning fails a test |
| `node e2e/shots.mjs` | screenshots of every page x 3 viewports x 2 themes into `docs/screenshots/` (`ONLY=`, `THEMES=`, `OUT=`); then `python3 e2e/optimise-shots.py` |

## Structure

```
src/
  app/          App (routes, providers), Shell (rail, top bar), Splash (role cards), SimulationPill
  routes/       Landing, Command, Analyst, Evidence, Citizen, Insights, About, NotFound
  components/
    ui/         shadcn-style Radix wrappers: button, dialog, tabs, tooltip, toaster, popover, switch, scroll-area
    player/     PhonePane, BankPane, MapPane, Timeline, Controls, FinaleCard, LedgerChain, notes (static copy)
    command/    KpiStrip, IncidentFeed (virtualised), AntibodyTicker, RingPanel (takedown simulator), BenchmarkMini
    common/     Panel, IndiaMap (lazy), CountdownRing, RefChip, SeverityBadge, ReasonChips, Brand
  lib/          api (client, endpoints), ws (stream client, gap tracker), format, geo (projection, arcs, ring layout), motion, hooks
  state/        session, runStore (zustand) + reducer (pure), live (WS + poller), playback, evidence, toasts
  styles/       tokens.css (three layers), globals.css (Tailwind v4 theme mapping, ambient backdrop, motion CSS)
  mocks/        hero-run.json etc.: recorded from the real server, used by tests
  types/        api.ts (what the server really sends) and guards.ts (runtime narrowing of WS events)
```

## Design tokens (`src/styles/tokens.css`)

Three layers: **primitive** (`--p-*` palette, spacing, radii, fonts) -> **semantic** (meaning,
theme-switched) -> **component** (panel, chip, pill, phone). Tailwind v4 maps the semantic layer in
`@theme inline` (`bg-surface`, `text-muted`, `text-safe`, `border-line`...). Dark is default;
`html.light` / `data-theme="light"` swaps the semantic layer only.

| Token | Dark | Meaning |
|---|---|---|
| `--bg` | `#0B0F17` | page ink |
| `--surface` / `--surface-solid` | `#111827` (78% glass) | panels |
| `--safe` | `#34D399` | safe, blocked |
| `--warn` | `#FBBF24` | verify, step-up |
| `--danger` | `#F43F5E` | scam, critical |
| `--intel` | `#A78BFA` (`--intel-strong` `#8B5CF6`) | intelligence, antibody |
| `--flow` | `#22D3EE` | data flow, payment paths |
| `--fg` / `--fg-muted` / `--fg-subtle` | slate 100 / 400 / 500 | text (AA on `--bg`) |

Risk is never colour alone: severity uses icon + label everywhere.

## Motion principles

Framer Motion for state-driven motion, CSS for ambient loops (aurora, pulse rings, dash flow);
transform and opacity only. Route fade/slide 180-240 ms, staggered list entry, spring counters,
countdown ring, hash-chain link draw, antibody ripple, typing effect. **Reduced motion** is honoured
from `prefers-reduced-motion` and the in-app toggle (`html[data-motion="reduced"]`): Framer is put
in `reducedMotion="always"`, CSS animations collapse, counters and typing become instant; all state
is also textual. Stream events are batched once per animation frame; the incident feed is windowed;
the map has well under 150 animated elements.

## Data layer

* `ApiClient`: fetch wrapper with abort, status -> error code mapping, 401 -> sign-out, friendly
  messages that never echo server text.
* `StreamClient`: `/api/stream?token=` (URL never logged), snapshot-then-live, `GapTracker` (gap ->
  drop socket and re-snapshot, duplicates dropped), exponential backoff with jitter, `4401/4403` do
  not retry, per-frame batching with a force-flush cap.
* Event store: pure `applyEvents(state, batch)` reducer with bounded lists; derived selectors for KPIs
  and time-to-block. Playback state is `/api/demo/state` polled while running, interpolated at the
  run speed for a smooth scrubber.
* Pacing: the console always starts runs with `pacing: "cinematic"` (1x = server 1x, about 75 s for
  the hero). The scrubber and its labels use the pacing timeline (`wall_offset` / `wall_duration`)
  and seek by `wall`; the simulated clock is shown beside it. A 422 fallback to real pacing exists
  only defensively.

## i18n (citizen page)

`src/lib/i18n/citizen.ts` holds a typed table: `en` defines the keys, `hi`, `ta`, `bn` must match
(compile-time and `citizen.test.ts`, which also checks `{placeholders}` and that nothing is a copy
of English). To add a language: add the code to `LANGS`, a native name to `LANG_NAMES`, a full
table, run `npm test`. Verdict reasons and advice are localised by the API (`lang`).

## Notes on the API contract

* The resolve body is `{action, decision_seq}` only. The analyst's reason note is mandatory in the UI
  (length, no digit runs, no emails) but kept in memory as a **local audit note**; it is not sent.
* There is no report route: **Report this** shows a local acknowledgement and says nothing is sent.
* There is no tamper-demo route, so the "try tampering" button is omitted.
* Ledger entries are fetched as raw text so integer-valued floats keep their source form
  (`1.0`) and `payload_hash` can be recomputed in the browser exactly as the ledger does.
* Prevalence-adjusted precision is computed in the browser from the JSON's recall and FPR (the
  file has no such table) and is labelled as such.

## Bundle (production build, gzip)

| Chunk | Size |
|---|---|
| `index` (app, React, router, Radix, palette) | about 123 KB |
| `motion` (Framer Motion) | 47.9 KB |
| `Command` / `Analyst` / `Evidence` / `Citizen` / `Insights` / `About` (lazy) | 9.4 / 8.7 / 10.0 / 9.7 / 7.7 / 7.1 KB |
| `IndiaMap` + `geo` (lazy) | 13.8 + 8.4 KB |
| CSS | 11.6 KB |
| **Initial JS** | **about 171 KB** (budget 500 KB; each route chunk budget 120 KB) |

Fonts are self-hosted Inter and JetBrains Mono (latin subset woff2). No third-party hosts at runtime.

## Known limits

* Ring graph and takedown are "computed from this run's transactions".
* Seeking replays the run server side; markers for future steps appear after a first play (they are
  remembered per seed in sessionStorage).
* Hold timeline ledger rows are matched by simulated time window: the stream has no per-entry txn ref.
* The hash recomputation needs a secure context (`localhost` or https) and a browser with JSON
  source-text access (current Chrome, Firefox, Safari); otherwise it says it cannot recompute.
* The resolve note and the citizen message are never persisted or sent beyond what the API takes.
* Dev server only: Framer Motion warns in the console when the OS asks for reduced motion.
