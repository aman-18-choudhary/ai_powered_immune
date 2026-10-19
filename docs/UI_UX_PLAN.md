# UI/UX plan

Phase 0 deliverable, written 2026-10-15 from `web-console/src` (routes in `app/App.tsx`,
navigation in `app/nav.ts`, tokens in `styles/tokens.css`). It documents the console as built and
the screens still to come. One frontend only: **Vite + React 18 + TypeScript + Tailwind v4 +
Framer Motion** (no Next.js). Original visual spec:
`docs/superpowers/specs/2026-10-10-console-and-demo-server-design.md`.

## 1. Two clearly separated experiences

| Experience | Who | Data | Visual marker |
|---|---|---|---|
| **Account area** (complaints, checks, investigator, admin) | Signed-in CITIZEN / INVESTIGATOR / ADMIN | **Real records** from the complaints service | No SIMULATION badge; pages state "These are real records, not simulated" |
| **Simulation demo** (Live Interception player, Command, Analyst, Evidence, Citizen Shield, Insights, About) | Demo roles | Simulator output | Persistent **SIMULATION** badge and "About these numbers" panel |

The Live microphone mode on the Live Interception page is real (consent-based) and sits beside the
simulated player, which is kept behind a SIMULATION tab.

## 2. Screens that exist

| Route | Screen | Roles | Status |
|---|---|---|---|
| `/sign-in` (and `/` when signed out) | Sign-in with **Citizen** / **Investigator** entry; Clerk when configured, otherwise local development sign-in (email + code from the server terminal, labelled dev-only); simulation demo role cards in a separate, labelled panel | public | Verified by hand (citizen) |
| `/me` | Citizen dashboard: stats, complaint history (status/category filters, pagination), empty state "Register your first complaint", alerts (unread count, mark read), safety guidance and official resources (1930, cybercrime.gov.in, contact your bank) | CITIZEN | Verified by hand |
| `/me/complaints/new` | Complaint form: required category/title/description; every other field labelled optional; geolocation only on explicit click; evidence files uploaded one by one with per-file stored / rejected / failed | CITIZEN | Verified by hand (mandatory-only) |
| `/me/complaints/:id` | Complaint detail: all fields ("Not provided" when empty), status history, evidence (download, add, remove while SUBMITTED), analysis panel (score labelled uncalibrated, indicators grouped Rules / ML / LLM, confidence interpretation, version history), Run analysis | owner | Verified by hand (Phase A version); Phase B panel verified by agent Playwright only |
| `/me/profile` | Display name and the case-matching privacy toggle | all account roles | Unverified by hand |
| `/check` | Check a message or link (optional URL, UPI, phone, email, amount, payment method); nothing stored; pre-fills a complaint without submitting | all account roles | Verified by hand 2026-10-15 |
| `/models` | Model versions, held-out metrics, synthetic-data warning, Ollama status | all account roles | Unverified by hand |
| `/investigator`, `/investigator/complaints/:id` | Queue with search, filters (category, status, risk incl. CRITICAL, dates, city/state), sort, pagination; detail with status change + note | INVESTIGATOR, ADMIN | Agent Playwright only |
| `/admin` | Users (role change with confirmation, activate/deactivate), audit log, service health | ADMIN | Agent Playwright only |
| `/`, `/command`, `/analyst`, `/evidence`, `/citizen`, `/insights`, `/about`, `/simulation` | Simulator pages and the Live microphone mode | demo roles | Existing, tested |

Navigation is role-aware (`navItems` in `app/nav.ts`) and mirrored in the command palette (⌘K).
The role always comes from `/auth/session`; the frontend never sends a role.

## 3. Screens still to build (planned)

| Screen | Purpose | Phase |
|---|---|---|
| Evidence analysis states | Show audio transcription and OCR results (or "unavailable" with the reason) per evidence file | 6 |
| Similar cases panel | **Built (Phase 8)**: on the staff complaint page under Related complaints; band + cosine score bar (not a probability), explanation with shared rule indicators and category, a separate badge when an identifier is also shared, loading/unavailable/opted-out/empty states, "not proof" notice; Fraud DNA card on the Models page | 8 |
| Network view | **Built (Phase 9)**: `/investigator/network`, staff only. Deterministic radial SVG graph (no graph library), depth 1 or 2; line style and text distinguish observed (solid), inferred (dashed), confirmed (thick, arced) and rejected (dotted); keyboard-selectable nodes; a text list of every connection with its evidence; legend; clusters; POST identifier search. Related complaints panel on the staff complaint page with confirm/reject dialogs. "Leads, not proof" and "a confirmed link is not a finding of crime" shown wherever links are | 9 |
| Geographic view | Leaflet map of complaints with valid coordinates; date/category/risk filters; cluster counts; time comparison. OpenStreetMap tiles only receive tile coordinates, never complaint data; a privacy note on the page; offline fallback to the existing d3-geo map when tiles are unreachable | 10 |
| Investigation notes and case report export | Notes timeline; report download with access control | 12 |
| Complaint edit (while SUBMITTED) | Add bank details or other fields after creation | 11 |
| Configuration view (admin) | Read-only view of effective configuration, then authorised changes | 12 |

## 4. Visual language

* **Theme today:** the console starts in the **dark** theme; a toggle switches to light and is
  remembered in `localStorage` (`lib/theme.tsx`). Both themes are fully tokenised.
  **Planned:** make light the default for the public and citizen areas, as the target
  specification asks, keeping dark for the investigator dashboard. Glass panels, hairlines, Inter
  for text and JetBrains Mono for ids and hashes (self-hosted).
* **Investigator area:** same tokens, denser layout, monospace ids, risk chips with icon + label.
* Semantic accents: emerald (safe), amber (verify), crimson (critical), violet (intelligence),
  cyan (data flow). Defined as tokens in `styles/tokens.css`, never hard-coded.
* Motion: Framer Motion page transitions and list entry; `prefers-reduced-motion` turns off
  non-essential motion.

## 5. State handling (applies to every data-driven component)

| State | Pattern |
|---|---|
| Loading | Skeletons or an inline spinner with text; buttons show progress and are disabled while busy |
| Empty | `EmptyState` with a next action (for example "Register your first complaint") |
| Error | Friendly message from the API `message`, field errors mapped from 422 `detail`, retry where safe |
| Unavailable | Named capability with the server's reason (for example "Screenshot OCR: unavailable (no OCR engine installed)") |
| Success | Confirmation with the record id; uploads report only what the server stored |
| Async results | `aria-live` regions announce completion (for example "Analysis finished: high risk") |

## 6. Responsive design and accessibility

* Breakpoints: citizen screens mobile-first from 360 px; investigator ≥ 1024 px; command centre ≥
  1280 px. No horizontal scroll at 360 px (checked by an agent-run Playwright test).
* Labels on every input, visible focus rings, keyboard operation, risk never conveyed by colour
  alone, WCAG AA contrast in both themes (axe checks run inside the agent's Playwright suite; not
  independently audited).

## 7. Known UX limitations

* Alerts show the service wording, so citizens see status codes such as `UNDER_REVIEW`.
* Microphone denial shows one generic message for both "permission denied" and "no microphone".
* With Clerk enabled, a reload shows a blank screen while `/auth/config` loads (up to 8 s on
  timeout).
* Audio and screenshot evidence is stored but not analysed yet.
