# ScamShield Immune: web console (phase F1)

Premium, animated frontend for the demo server: the **Live Interception player** (`/`) and the
**Command Centre** (`/command`). Other routes (`/analyst`, `/evidence`, `/citizen`, `/insights`,
`/about`) render a polished "coming in phase F2" page in the real layout. Everything on screen is
simulated; the SIMULATION pill and "About these numbers" popover are always visible.

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
| `npm run test:e2e` | Playwright smoke against a running console + demo server (`CONSOLE_URL`, default 5173; a full run takes about 2.5 minutes) |

## Structure

```
src/
  app/          App (routes, providers), Shell (rail, top bar), Splash (role cards), SimulationPill
  routes/       Landing (player), Command, Phase2 (F2 placeholders), NotFound
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
* Speed: 1x/2x/5x/0.5x. With `pacing: "cinematic"` (server feature, detected by trying it and
  falling back on 422) 1x is server 1x; with real pacing 1x = 4 simulated s per second.

## Bundle (production build, gzip)

| Chunk | Size |
|---|---|
| `index` (app, React, router, Radix) | 127.1 KB |
| `motion` (Framer Motion) | 47.9 KB |
| `Command` route (lazy) | 9.6 KB |
| `IndiaMap` + `geo` (d3-geo, topojson, bundled outlines; lazy) | 13.3 + 8.4 KB |
| CSS | 10.5 KB |
| **Initial JS** | **about 176 KB** (budget 500 KB) |

Fonts are self-hosted Inter and JetBrains Mono (latin subset woff2). No third-party hosts at runtime.
India outlines: `geojson-india` (MIT) simplified by `scripts/build-india-topo.mjs` to 28 KB.

## Known limits

* F2 pages are placeholders. Ring graph and takedown are "computed from this run's transactions".
* Seeking replays the run server side; markers for future steps appear after a first play (they are
  remembered per seed in sessionStorage).
* Package size is measured by exporting the package once at the finale (officer/admin).
* The Confirm mule button needs the analyst or admin role; the automatic confirmation happens 60
  simulated seconds after the hold otherwise.
