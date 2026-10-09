# Console + Demo Server — Design Spec

Date: 2026-10-10
Status: Approved direction (user: "complete working project with a premium, animated frontend and backend").
Related: `2026-10-07-scam-immune-system-design.md` (system), `docs/audit-trail.md`.

## 1. Goal

A judge opens one URL, presses **Play**, and watches the platform defeat a digital-arrest scam
end to end: a call is flagged, a transfer is held, an analyst confirms a mule, the *second bank*
blocks the same mule seconds later, and a court-verifiable evidence package is produced. The same
console also works as a real operator UI (hold queue, antibodies, ledger, benchmark) and a
citizen-facing assistant.

Honesty rules (visible in the UI): data is simulator-generated; the hero is a constructed hard
case; benchmark numbers are simulator-only. A persistent "SIMULATION" badge and an "About these
numbers" panel are required.

## 2. Architecture

```
web-console (React)  <--REST+WebSocket-->  demo-server (FastAPI, one process)
                                              |  runs the REAL service code in-process:
                                              |  call-guard scorer, txn-guard x2 (bank_a, bank_b),
                                              |  antibody-hub store/outbox, evidence-ledger app,
                                              |  sim-engine hero scenario, benchmark summary
```

`demo-server` is the "single-process composition" of the platform (same libraries and code paths
as the e2e audit test; InMemoryBus). Task 20 additionally ships the multi-container Docker stack;
the console talks to either through the same `/api` contract (gateway proxies in the stack).

## 3. API contract (v1) — all under `/api`

Auth (demo mode): `POST /api/auth/demo-login {role: "officer"|"analyst"|"citizen"|"admin"}` ->
`{token, role, sub}` (HS256 JWT, 8 h; secret from `DEMO_JWT_SECRET`, random per process if unset;
no passwords; clearly marked demo-only). All other routes need `Authorization: Bearer`.
WebSocket auth: `?token=` query on `/api/stream` (token only, never logged).

| Method + path | Roles | Purpose |
|---|---|---|
| `POST /api/demo/start` `{seed?:int, speed?:number(0.25..20), mode?:"hero"|"hero+noise"}` | officer, admin | (Re)start a scenario run; returns `{run_id}` |
| `POST /api/demo/control` `{action:"pause"|"resume"|"stop"|"seek", t?:number, speed?:number}` | officer, admin | Playback control |
| `GET /api/demo/state` | any | `{run_id, status, t, duration, speed, step, counters}` |
| `GET /api/stream` (WS) | any | Ordered JSON events (below) |
| `GET /api/holds?state=open|all` | analyst, officer, admin | Hold list (deadline asc) |
| `POST /api/holds/{txn_ref}/resolve` `{action, decision_seq?}` | analyst, admin | Resolve a hold |
| `GET /api/antibodies?state=active|all` | analyst, officer, admin | Antibody list (limited view per bank rules) |
| `GET /api/ledger/head` / `entries?from_seq=&limit=` / `checkpoints/latest` | officer, admin, analyst | Ledger read |
| `POST /api/cases` / `GET /api/packages/{case_id}` | officer, admin | Case + signed package download (zip) |
| `POST /api/verify` `{case_id}` | officer, admin | Runs the shipped verify.py on the package pinned to the ledger key; returns exit code, label, redacted seqs, per-check results |
| `GET /api/ring/{case_id}/graph` / `POST /api/ring/{case_id}/simulate {remove:[...]|k}` | officer, analyst, admin | Ring graph (opaque node ids) and takedown simulation (graph-twin algorithm; see §6) |
| `GET /api/geo/hotspots` | officer, analyst, admin | Hotspot cells (lat/lon, count, trend) + cash-out points |
| `GET /api/benchmark/summary` | any | Last benchmark run as JSON (headline, per-rail FPR, ablations, sensitivity, limitations) |
| `POST /api/citizen/assess` `{message, lang?}` | citizen, any | Verdict `{label, score, reasons[], advice, lang, uncertain, model_version}` |
| `GET /api/health` | none | liveness |

Errors: `{error: code, detail?: string}`; validation errors never echo input; request ids
sanitised; bodies capped (64 KiB); rate limiting per role.

### Stream events (`/api/stream`)
Every event: `{id:int(seq), t:number(sim seconds), wall:iso, kind, ...}`. Kinds:
`scenario.started|step|finished` (step: `{key, title, caption}`), `call.chunk`
`{call_ref, speaker, text, score, reasons[]}`, `call.alert` `{call_ref, score, reasons[]}`,
`txn.scored` `{txn_ref, bank, rail, amount_bucket, decision, score, reasons[], seq}`,
`hold.created|upgraded|resolved`, `antibody.published` `{antibody_ref, kind, bank}`,
`antibody.applied` `{bank, latency_ms}`, `ledger.entry` `{seq, event_type, actor_ref, entry_hash_prefix}`,
`ring.updated` `{ring_id, members, inflow_bucket}`, `geo.point` `{lat, lon, kind}`,
`metric.tick` `{counters}`. No raw identifiers; amounts only as buckets in stream, exact INR
(string) only in officer/analyst REST views.

## 4. Visual identity and motion

- Look: premium dark "operations" UI. Surfaces `#0B0F17` / `#111827` glass panels with 1px
  hairlines, subtle grid + noise; accents: emerald `#34D399` (safe/blocked), amber `#FBBF24`
  (step-up/verify), crimson `#F43F5E` (critical/scam), electric violet `#8B5CF6` (intelligence /
  antibody), cyan `#22D3EE` (data flow). Light theme supported (tokens, not hard-coded colours).
- Type: Inter (UI), JetBrains Mono (hashes, ids, numbers) via `@fontsource` (self-hosted).
- Tokens: three layers (primitive -> semantic -> component) as CSS variables consumed by
  Tailwind; shadcn/ui primitives (Radix) for dialogs, tabs, tooltips, tables, toasts.
- Motion (Framer Motion): page transitions (fade/slide 180-240 ms), staggered list entry,
  animated number counters (spring), pulsing hotspot rings, flowing dashed arcs along payment
  paths, countdown ring on holds, hash-chain "block link" animation on new ledger entries,
  antibody "ripple" from bank A to bank B, victim-phone typing effect for call chunks.
  `prefers-reduced-motion` disables non-essential motion; all essential state is also textual.
- Accessibility: WCAG AA contrast, keyboard navigable, focus rings, ARIA live regions for alerts,
  risk never conveyed by colour alone (icon + label).
- Responsive: command centre >= 1280 px designed; analyst >= 1024; citizen mobile-first (360 px).

## 5. Pages

1. **Landing / Live Interception (`/`)** — cinematic scenario player: 4-pane split (Victim A phone,
   Bank A console, Bank B console, Command-centre map) + timeline scrubber, step captions, speed,
   play/pause, "Explain this step" popovers; finale card: "Victim B protected in N ms" with
   evidence-package download + verify badge.
2. **Command Centre (`/command`)** — KPI strip (animated), India map (hotspots, cash-out points,
   animated arcs), incident feed, ring graph + takedown simulator (slider "freeze k accounts" with
   projected harm reduction), antibody ticker, benchmark mini-panel.
3. **Analyst Console (`/analyst`)** — hold queue with countdown rings, reason chips, resolve
   actions with confirmation, diff view of upgrades, keyboard shortcuts.
4. **Evidence (`/evidence`)** — animated hash chain, entry inspector, checkpoint list, package
   export, **Verify** button showing the offline verifier result (PASS/UNPINNED/FAIL), redaction
   view, honest-limits panel.
5. **Citizen Shield (`/citizen`)** — mobile-first chat-style assistant: paste a message/call
   summary, language picker (en/hi/ta/bn), single large verdict card (Safe / Suspicious / Likely
   scam) with reasons and next steps, "Report" action, uncertainty copy.
6. **Insights (`/insights`)** — benchmark: headline metrics with CIs, per-rail FPR table, ablation
   comparison (antibody/no-antibody/no-call), delay sensitivity chart, limitations (always
   visible), seeds/commit.
7. **About (`/about`)** — what is real vs simulated, architecture diagram, honest limits.

## 6. Ring graph and takedown (interim)

Graph-twin is not yet built as a service. The demo server computes the hero campaign's ring from
the scenario's own transactions with a small deterministic module (`demo_server/ring.py`):
accounts as nodes (opaque ids), payments as edges, harm = max-flow of projected victim inflow to
cash-out nodes (networkx), `simulate_takedown` / greedy `best_takedown`. This module is written to
be lifted into the graph-twin service later; the API shape above is final. UI labels it
"computed from this run's transactions".

## 7. Quality gates

- demo-server: pytest (unit + one integration that drives a full scenario and checks the event
  order, the verify result 0 with pin, no raw identifiers in any event/response), ruff, `-W error`.
- web-console: TypeScript strict, ESLint, Vitest for logic/components, Playwright smoke (golden
  path: login -> start scenario -> see alert, hold, antibody applied, blocked, verify PASS),
  Lighthouse-ish budgets (bundle < 500 KB gz initial), visual check via screenshots in the
  built-in browser at 1440, 1024 and 390 px, no console errors.
- All numbers on screen come from the API (no hard-coded metrics), and the SIMULATION badge is
  always visible.
