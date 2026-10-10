# demo-server

The console's backend: one Python process that runs the platform's **real service code** and
exposes it through the `/api` REST + `/api/stream` WebSocket contract described in
`docs/superpowers/specs/2026-10-10-console-and-demo-server-design.md`.

**Everything is simulated.** Transactions, calls, victims, mules and banks come from
`sim-engine`; the hero scenario is a constructed hard case; benchmark numbers are simulator-only.
`GET /api/demo/state` and every REST body carry `"simulated": true`.

## What runs inside

```
                          one process, one InMemoryBus, simulated clock
 sim_engine hero scenario ──► call-guard  SessionScorer + handle_event (real consumer path)
                         └──► txn-guard   bank_a, bank_b: TxnGuardService + hold stores + BusAuditSink
 analyst (auto, or a human via POST /holds/{ref}/resolve)
                         └──► antibody-hub AntibodyStore (SQLite) + Hub outbox ──► bus ──► bank_b (real consumer)
 every audit entry       └──► evidence-ledger app (consumer, SQLite store, Ed25519 signer), used over its HTTP API
```

The wiring is the one in `tests/e2e/audit` (`scam_audit.harness`), made incremental: the engine
feeds the same objects one scenario item at a time in simulated time and reports what happened.
Differences from the audit harness: all victim transfers of both victims are scored (the harness
scores A's transfers and B's first), the unrelated-case entry and the antibody extend/revoke are
not played, and pass-through payments (mule forwards) are observed by the ring graph only.

Relation to the Docker microservice stack: same libraries and code paths, different packaging.
The stack runs each service in its own container behind the gateway; this process composes them
in-process so one URL can serve a demo. The `/api` shapes are the contract both implementations
are meant to honour.

## Run

```
pip install -e contracts -e libs/svckit -e services/sim_engine -e services/call_guard \
    -e services/txn_guard -e services/antibody_hub -e services/evidence_ledger \
    -e "services/demo_server[dev]"
make demo-server                          # PORT (default 8080); python -m demo_server
DEMO_STATIC_DIR=web-console/dist make demo-server   # also serve the built console (SPA fallback)
```

Docker (build context is the repository root):
`docker build -f services/demo_server/Dockerfile -t demo-server . && docker run -p 8080:8080 -e DEMO_JWT_SECRET=... demo-server`

Without `DEMO_STATIC_DIR` the root path serves a small placeholder page.

## API

All routes are under `/api`. Errors are `{"error": code, "detail"?: string}`; validation errors
(`422`, `error: "invalid_request"`) carry `loc`/`msg`/`type` only, never the submitted value or
unknown key names. Bodies are capped at 64 KiB (`413`). Rate limits are per role per minute
(citizen 30, analyst/officer 300, admin 600; unauthenticated login 60), fixed window, fail open.

| Method and path | Roles | Notes |
|---|---|---|
| `GET /health` | none | `{"status":"ok","simulated":true}` |
| `POST /auth/demo-login` `{role, code?}` | none | HS256 JWT, 8 h, claims `sub, role, iat, exp, jti`; no passwords. `DEMO_LOGIN_CODE`, when set, must be supplied as `code` |
| `POST /demo/start` `{seed?, speed?, mode?, pacing?}` | officer, admin | `seed` 0..2^31-1 (1), `speed` 0.25..20 (10), `mode` `hero` / `hero+noise`, `pacing` `real` (default) / `cinematic`; restarts; returns `{run_id}` |
| `POST /demo/control` `{action, t?, wall?, speed?}` | officer, admin | `pause` / `resume` / `stop` / `seek` (exactly one of `t` simulated seconds or `wall` seconds on the run's pacing timeline, else `422`); `speed` may accompany any action; returns the state |
| `GET /demo/state` | any | `{run_id, status, t, duration, speed, step, counters, simulated, mode, seed, epoch, last_event_id, banks, pacing, wall_duration, wall_offset}` |
| `GET /demo/runs` | any | bounded list of recent runs |
| `GET /stream` (WebSocket `?token=`) | any | see below |
| `GET /holds?state=open\|all` | analyst, officer, admin | deadline ascending; exact `amount_inr` as a string |
| `POST /holds/{txn_ref}/resolve` `{action, decision_seq?}` | analyst, admin | `release` / `confirm_block`; idempotent for the same action, `409` for the other or a stale `decision_seq` |
| `GET /antibodies?state=active\|all` | analyst, officer, admin | limited view: `antibody_ref`, kind, source bank, state, `key_hash_prefix` (8 hex), times |
| `GET /ledger/head` | analyst, officer, admin | head plus `public_key_hex`, `key_id`, `algorithm` |
| `GET /ledger/entries?from_seq=&limit=` | analyst, officer, admin | `limit` 1..200 |
| `GET /ledger/checkpoints/latest`, `GET /ledger/checkpoints` | analyst, officer, admin | signed checkpoints (`{"checkpoint": null}` before the first) |
| `POST /cases` `{case_id?, title?, case_refs?}` | officer, admin | defaults: `hero-case`, refs = victim A's first `txn_ref`, the shared mule's `payee_ref`, A's `call_ref`; `201` new, `200` same, `409` different content |
| `GET /packages/{case_id}` | officer, admin | signed zip, `ETag` = SHA-256 of the body; each export appends a `package.exported` ledger entry |
| `POST /verify` `{case_id}` | officer, admin | runs the package's `verify.py` pinned to the ledger key (below) |
| `GET /ring/{case_id}/graph` | analyst, officer, admin | `case_id` is a ring id, a case id of this run, or `current` |
| `POST /ring/{case_id}/simulate` `{remove: [<=50 node ids]}` or `{k: 1..5}` | analyst, officer, admin | `{harm_before, harm_after, reduction_pct, removed, cut_edges, cut_edge_list}`; money as decimal strings |
| `GET /ring/{case_id}/best-takedown?k=` | analyst, officer, admin | greedy result and, when the ring has <= 12 members and k <= 3, the optimal one and `gap_pct` |
| `GET /geo/hotspots` | analyst, officer, admin | cells (city, lat, lon, `count`, `prev_count`, `trend`) and cash-out points |
| `GET /benchmark/summary` | any | the bundled benchmark JSON |
| `POST /citizen/assess` `{message, lang?}` | any | see Citizen assistant |

Examples:

```
TOKEN=$(curl -s -X POST :8080/api/auth/demo-login -H 'content-type: application/json' \
        -d '{"role":"officer"}' | jq -r .token)
curl -s -X POST :8080/api/demo/start -H "authorization: Bearer $TOKEN" \
     -H 'content-type: application/json' -d '{"seed":1,"speed":10,"mode":"hero+noise"}'
curl -s :8080/api/demo/state -H "authorization: Bearer $TOKEN"
curl -s -X POST :8080/api/cases -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{}'
curl -s -X POST :8080/api/verify -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
     -d '{"case_id":"hero-case"}'
```

### Stream (`/api/stream?token=<jwt>`)

The token is validated, and the `Origin` header checked (allowlist `DEMO_CORS_ORIGINS`, or Origin
host equal to the request `Host`; no `Origin` header is accepted), before the handshake is
accepted. The first message is always a `scenario.state` snapshot: the counters plus the last 200
events. Live events follow. Client messages are ignored; one over 1 KiB closes with `1009`.
Close codes: `4429` too slow (per-client queue of 256 overflowed), `4503` connection cap reached.

Every event: `{id, t, wall, wall_offset, kind, run_id, ...}`. `id` is a per-run integer, strictly increasing
with no gaps in the live stream; `t` is simulated seconds since the first call; `wall` is a UTC
ISO timestamp.

| `kind` | extra fields |
|---|---|
| `scenario.started` | `seed, mode, duration, simulated, banks` |
| `scenario.step` | `key, title, caption` (`call_begins, call_flagged, transfer_held, analyst_confirms, antibody_shared, bank_b_blocks` or `bank_b_misses`, `analyst_released`, `cash_out`, `evidence_sealed`) |
| `call.chunk` | `call_ref, victim (A/B/null), speaker, text, score, reasons[], lang, channel, model_version`; `score` is the call-guard session risk after this chunk; `text` is the synthetic script line |
| `call.alert` | `call_ref, victim, score, reasons[], threshold, model_version` |
| `txn.scored` | `txn_ref, bank, bank_name, rail, amount_bucket, decision, score, reasons[], seq, victim, hero` |
| `hold.created`, `hold.upgraded`, `hold.resolved` | `txn_ref, bank, decision, decision_seq, score, reasons[], rail, amount_bucket, deadline_t, state`; upgraded adds `from_decision`; resolved adds `action, resolved_by, source` (`auto` / `manual`) |
| `antibody.published` | `antibody_ref, antibody_kind, bank` |
| `antibody.applied` | `antibody_ref, bank, latency_ms` (measured: publication to application at bank_b) |
| `ledger.entry` | `seq, event_type, service, actor_ref, entry_hash_prefix` (12 hex) |
| `ring.updated` | `ring_id, members, victims, inflow_bucket` |
| `geo.point` | `lat, lon, point_kind (scam_call / held_transfer / protected / cash_out), city, state` |
| `metric.tick` | `counters` (every 30 simulated seconds) |
| `scenario.finished` | `reason (completed / stopped / error)`; completed adds `ledger_entries, antibody_published, victim_b_protected, latency_ms` |
| `scenario.state` | snapshot: `status, speed, step, duration, counters, events[]` |

Deviations from the spec text, both forced by the envelope's own `kind` key: the spec's
`geo.point.kind` is `point_kind` and `antibody.published.kind` is `antibody_kind`.

Reasons are `{code, weight, detail}`; `detail` is the services' own text, built from numbers and
fixed phrases, passed through `demo_server/sanitise.py` before it enters any event or snapshot:
currency amounts (`Rs`/`INR`/`₹` with Indian or Western grouping), grouped numbers, bare numbers
of 5+ digits and multiples of 4+ digits are replaced by the event's amount bucket label (or
`[amount withheld]` when no bucket applies). Amounts appear on the stream only as buckets. REST
views for analyst/officer/admin carry the exact INR amount as a decimal string (`amount_inr`).
Residual digit runs in a detail are limited to ISO dates and the 8-hex antibody short id.

### Playback, time and determinism

* Pacing (`POST /demo/start {"pacing": ...}`; the web console passes `"cinematic"`). `real`
  (default) waits `slice / speed`. `cinematic` maps simulated gaps to wall time with a documented
  compression (`demo_server/pacing.py`): between consecutive breakpoints (scenario item times,
  metric ticks, start, end) a gap `g` simulated seconds takes `g` wall seconds at 1x if
  `g <= 0.5`, else `min(2.2, 0.5 + 0.006 (g - 0.5))`, divided by `speed`. The seed-1 hero run
  (157 events, 2609 simulated seconds) plays in about 75 s at 1x and about 15 s at 5x.
  `t` stays simulated time. `wall_offset` is the event's position (seconds at 1x) on the run's
  timeline (equal to `t` under `real`); `wall_duration` is the timeline's total, so a scrubber can
  work in compressed time and seek with `{"action":"seek","wall":<seconds>}`. Pacing affects only
  wall timing: kinds, order, ids, `t` and scores are the same in both modes (tested).

* `t` is simulated time; the engine advances it in slices of at most 5 s and waits
  `slice / speed` wall seconds between slices (`speed` 0.25..20; changing it applies from the next
  slice). Pause takes effect within one slice.
* For a given `(seed, mode)` the event kinds, order, ids, `t` and scores are the same on every
  run and at every speed (tested). Not covered: `wall`, `run_id`, `antibody.applied.latency_ms`
  (real measurements), and anything a human does through `POST /holds/{ref}/resolve`.
* `ledger.entry` events are emitted at synchronisation points after each scenario step, waiting at
  most 1 s of wall time for the ledger consumer; if the ledger lags more than that, entries
  appear at a later point instead. Scoring, holds and stream events never wait for the ledger.
* `seek` restarts the run from scratch (same seed, new resources), replays it without pacing to
  `t`, then continues live (or stays paused if it was paused or finished). The replayed events are
  identical to the original, ids included; manual analyst actions taken earlier are re-applied at
  their recorded simulated time; subscribers receive one `scenario.state` snapshot. A seek costs
  as much compute as running to `t` (about 0.3 s for the hero scenario).
* Human-in-the-loop: `confirm_block` on the held transfer to the shared mule publishes the
  antibody at once and the automatic confirmation (60 simulated seconds after the hold) then does
  nothing; `release` means no antibody is published and bank B is not protected by one.

### Evidence and verification

`POST /verify` exports the case package from the ledger, unpacks it into a private temporary
directory and runs the `verify.py` shipped in the package with `python -I -S verify.py <dir>
--trusted-pubkey <ledger public key>`: separate interpreter, minimal environment (`PATH` only),
20 s timeout, 64 KiB output cap, sanitised and capped output lines. Before running it, the
server checks that the package's `verify.py` is byte-identical to the ledger's own verifier, so a
tampered package cannot supply its own. Response: `{exit_code, status, lines, redacted_seqs,
entries, checkpoints, case_id, pinned_key_id, public_key_hex}`, `status` one of `PASS_PINNED`
(exit 0), `INTEGRITY_OK_UNPINNED` (exit 2), `FAIL` (exit 1, timeouts, truncated output). The
signing key is held by the ledger's `Signer` only; its public half is returned. The signing key is
generated per process, so packages from one process are not verifiable against another's key.

### Citizen assistant

`POST /citizen/assess` scores the text with the real call-guard scorer in-process and returns
`{label, score, reasons[{code, text, weight}], advice{key, steps[]}, lang, uncertain,
model_version, thresholds}`. `likely_scam` at score >= 0.7 (the call-guard alert threshold),
`suspicious` at >= 0.35, otherwise `safe`: "no clear scam cues found", not "verified safe".
Reason and advice texts come from a static dictionary in `en`, `hi`, `ta`, `bn`. `uncertain` is
true for empty text, text over 4,000 characters (the first 4,000 are scored), an unsupported
`lang`, fewer than four words, a score within 0.08 of a threshold, or a rules-only scorer. The
message is scored in memory; it is not stored, logged (only its length is) or echoed.

### Ring graph, takedown and geography

`ring.py` builds an account graph from the scenario's own transactions (accounts keyed by the
keyed account hash, ids shown as opaque 12-hex prefixes), detects the ring from flagged payees and
antibodies, and computes harm as the max flow of projected victim inflow to the ring's terminals
over a 7-day horizon (edge capacity = observed total scaled by horizon over observed span, at
least one day). It is a model: it assumes the observed rate continues and that nobody re-routes
after a freeze. Background (noise) traffic is not graphed. `geo.py` maps the simulator's
city/district/state to a static table of Indian cities (`CITIES`); no coordinate of an individual
simulated citizen is exposed.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PORT`, `HOST` | `8080`, `0.0.0.0` | listen address |
| `DEMO_JWT_SECRET` | random per process (WARNING) | HS256 secret, at least 32 bytes if set |
| `DEMO_LOGIN_CODE` | unset | optional code required by `demo-login` |
| `DEMO_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080` | explicit origins for CORS and the WebSocket origin check; `*` is rejected |
| `DEMO_STATIC_DIR` | unset | directory of the built console to serve |
| `DEMO_WORK_DIR` | a temp directory | per-run SQLite files; removed at shutdown |
| `DEMO_HISTORY_SIZE` | 5000 | event ring buffer |
| `DEMO_CLIENT_QUEUE` | 256 | per-WebSocket queue |
| `DEMO_MAX_WS_CLIENTS` | 50 | concurrent stream clients |
| `LOG_LEVEL` | `INFO` | |

## Security notes

* Demo auth only: anyone who can reach the server can obtain a token for any role (unless
  `DEMO_LOGIN_CODE` is set). Do not expose it with real data; there is none.
* JWT: algorithm pinned to HS256, `exp`/`iat`/`sub`/`role`/`jti` required, role validated against
  the enum, secret length enforced for configured secrets.
* Hardening copied from the gateway and ledger services: sanitised request ids, streaming body
  cap, no echo in validation errors, minimal access log (method, route template, status, request
  id, duration; no query string, headers, body or token; run uvicorn with `access_log=False`,
  which `python -m demo_server` does), per-role rate limiting that fails open, CORS only for the
  listed origins, `nosniff`, `frame-ancestors 'none'`, `no-referrer`, `no-store` on `/api`.
* Log redaction (`demo_server/logredact.py`, installed by the app factory and `__main__`): the
  WebSocket token travels in `?token=` (the client contract) and uvicorn logs the request target
  of WebSocket connections even with `access_log=False`. Every log record created by Python
  logging in this process after the app is created passes through a redactor that replaces
  `token=<value>`, `Authorization`/`Bearer` values and anything shaped like a JWT, and rewrites
  `call_<hex>` call ids to their `call_ref:` form. Tested against real uvicorn connect, reject
  and disconnect. Not covered: output that does not go through Python logging (for example a
  reverse proxy's own access log in front of this server, which will see the query string).
  `python -m demo_server` also sets `ws_max_size=4096`, ping interval/timeout 20 s and
  `server_header=False`.
* The in-process ledger app trusts principal headers only with a per-process random
  `LEDGER_GATEWAY_SECRET` (set in the process environment at start unless already set), compared
  in constant time by the ledger.
* The synthetic scam call script is shown on purpose (it is the victim's phone pane); it comes
  from `sim-engine` and contains no real identifiers. No raw simulator identifier (citizen,
  account, device, payer token, full payee hash) appears in any stream event, REST body, log
  line or package; the integration test scans for them.
* Limits: one process, in-memory rate limiter and event history (reset on restart), one run at a
  time, SQLite files in a temp directory.

## Benchmark summary

`demo_server/data/benchmark_summary.json` is the committed output of
`scam_bench.run_benchmark --json-out` (schema: `tests/e2e/bench/README.md`). Regenerate:

```
python -m scam_bench.run_benchmark --seed 42 --out "" --include-latency \
    --json-out services/demo_server/demo_server/data/benchmark_summary.json
```

The full run (with delay sensitivity and 40 hero seeds) took 194 s on the development machine;
for a quicker file add `--no-sensitivity --hero-seeds 0` (about 30 s). Without `--include-latency`
the file is byte-reproducible for a given seed and code version; with it, `meta.runtime_s` and
`latency_ms` are machine-dependent.

## Tests

```
cd services/demo_server && python -m pytest -W error -q     # about 30 s
ruff check . && ruff format --check .
```

Unit and integration tests cover: engine ordering, determinism across speeds, pause/resume/stop,
seek equivalence, restart and double start, bounded history and runs list, event-loop heartbeat,
ledger slowdown; stream snapshot, late joiners, token/origin/size/cap, slow-client drop; the role
matrix for every endpoint (none, each role, `alg=none`, expired, unknown role, missing claims);
body cap, request ids, no-echo 422, rate limit, CORS, headers, logs; citizen edge cases;
ring max-flow on hand-built graphs and greedy-versus-exhaustive; geography; manual analyst
actions; pinned verification (pass for a real package, fail for tampered ones, forged verifier,
timeout, output flood); the benchmark schema; one full run with the milestone order, the evidence
package and a scan for raw identifiers.

Measured on the development machine (Python 3.13, instant pacing): the hero run emits 157
events and takes about 0.5 s; `hero+noise` emits 527 events (305 scored transactions, 85 call
chunks) in about 7 s; process RSS about 190 MB after model load and about 205 MB after a noisy
run; one run's event history is about 60 KB (hero) to 210 KB (noise) of JSON.
