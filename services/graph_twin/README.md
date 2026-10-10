# graph-twin

Fraud-ring detection and takedown simulation over the payment graph. It consumes transactions,
transaction decisions and antibodies, keeps an incrementally updated graph of accounts, tokens and
devices, finds mule networks (victims -> several pass-through mules -> a cash-out account), and
answers "what happens to the projected losses if we freeze these accounts?" with a max-flow model.

Model version: `graph-v1`. Python >= 3.11. Tests: `pytest -W error` (about 1 minute).

## Design

```
txn.events ──┐
txn.decisions ├─ svckit.consume (graph-twin-<role>) ─> GraphTwin (sync core, run in to_thread)
antibody.published ┘     │                                 │  in-memory networkx.DiGraph (+ relay index)
                          │                                 │  SQLAlchemy Core tables (restart rebuilds)
                          └ DLQ on malformed                ├─ detection: touched components only, debounced
                                                            ├─ ring registry (stable ids, merge rule, status)
HTTP (FastAPI, gateway trust) ── published ring snapshots ──┘  audit outbox -> Drainer -> ledger.append
```

* **Core** (`twin.py`): `apply_txn`, `apply_decision`, `apply_antibody`, `detect_dirty`, `sweep`. One
  `RLock` serialises graph + database changes; each call is one database transaction and the
  in-memory change is undone if it fails. The async layer (`service.py`) runs every call with
  `asyncio.to_thread`; the event loop does coordination only.
* **Graph** (`graph.py`): nodes `a:<hash>` (account), `t:<token>` (payer without an account
  reference), `d:<token>` (device). Edges: `pay` (aggregated per (src, dst): count, total in integer
  paise, first/last timestamp, rails, number of flagged payments) and `uses` (account -> device it
  paid from; two accounts on one device are linked). Node state keeps in/out totals and first/last
  timestamps, the payers with a flagged payment (capped at 64), the antibody expiry and the latest
  payee age. Times are integer microseconds, money is integer paise: totals are exact.
* **Persistence** (`store.py`): `gt_txns` (idempotency record + decision level + source for the
  timeline), `gt_decisions` (latest `decision_seq` per txn; `applied = false` rows are the pending
  buffer), `gt_nodes`, `gt_edges`, `gt_antibodies`, `gt_rings`, `gt_audit_outbox`. SQLite in tests, Postgres in
  compose (`GRAPH_DATABASE_URL`, `python -m graph_twin.migrate`). A restart rebuilds the graph from
  nodes and edges, loads the rings and marks every seed and ring core dirty so the first detection
  pass reconciles.
* **Deviation: no Neo4j.** The plan names a Neo4j driver. There is no way to run or test Neo4j in
  this environment, so the twin keeps the graph in memory (networkx) with SQL persistence. The
  store methods in `store.py` are the surface a graph database would have to implement.

### Account identity assumption

A mule is paid under a `payee_hash` and later pays under a `payer_token`: two unrelated
identifiers. Without a link, a mule's incoming victim payments cannot be connected to its sweep.
The twin therefore uses one additive, optional `Transaction.payer_account_ref` (contracts): the
keyed hash of the PAYING account, computed with the same function, kind (`"payee"`) and
federation key as `payee_hash`, so one account has one hash whether it is paid or paying. A paying
bank can compute it from its own ledger. The simulator fills it (benign payers, victims, mule
forwards). Events without it fall back to a `t:<payer_token>` node: those payers can be victims
but cannot be linked to an account, so rings that forward under such tokens are not found.
Antibody `key_hash` of kind `mule_account` is the `payee_hash` of the mule and flags the same node.

Normalisation. The hash is only as good as the producer's normalisation: the same account must
reach the twin under ONE canonical form on both sides. A UPI VPA (`name@bank`) and the bank
account number behind it are different strings, so a mule paid by VPA and sweeping from its
account number is two nodes unless the producer maps both to one canonical identifier before
hashing; the twin does not guess. Cross-service linkability: `payer_account_ref` is the same
keyed hash that other services hold as `payee_hash`, so it lets any holder of the federation key
(or of both datasets) join a paying account to the payments it received. It is as sensitive as
`payee_hash` and is never logged or returned in full (12-hex prefixes only).

### Detection signals and algorithm (`rings.py`, `registry.py`)

Structure alone also describes legitimate merchants, payroll and settlement chains, so every
candidate needs a SIGNAL.

1. **Seeds**: accounts that received at least one `step_up` / `hold_verify` transaction (the LATEST
   `decision_seq` per transaction counts; a later `allow` clears it) or that carry an active
   `mule_account` antibody (revocation and expiry clear it).
2. **Relays** (pass-through accounts): received >= INR 10,000 (`GRAPH_RELAY_MIN_INR`), forwarded
   >= 70% (`GRAPH_PASSTHROUGH_RATIO`) of it, first and last outflow within `GRAPH_PASSTHROUGH_MAX_S`
   (3600) of the first and last inflow, and >= 2 distinct payers. Kept in an incremental index
   (`relay_of`, `relay_into`) updated for the nodes an event touches, so detection never scans a
   fan-in.
3. **Expansion** from evidence-bearing relays and antibody-named accounts only (an account that
   merely received a flagged payment, such as a victim or a merchant with a false positive,
   neither seeds nor expands): follow a relay's outflow to accounts that are themselves
   admissible (an antibody, an evidence-bearing relay, or an account that is >= 30% funded by the
   ring's relays and is a relay, young or flagged), include evidence-bearing relays that feed a
   member and evidence-bearing relays sharing a device (devices used by more than 20 accounts are
   ignored). Bounded by `GRAPH_MAX_COMPONENT` (2,000) per run. Members, victims, inflow and
   takedown candidates are the CORE accounts (mules, cash-outs, antibody-named); other accounts
   that pay into the core are external payers.
4. **Components and communities**: weakly connected components over payment edges + shared
   devices; a component with more than 12 accounts is split with seeded Louvain (`seed=42`, weight
   log1p(INR)).
5. **Roles**: `mule` = relay; `cash_out` = non-relay that receives >= 30% of its inflow from the
   ring's relays; `unknown` = an antibody-named account that is neither (other accounts are not members). Victims are the external payers of the members
   (`victim_payer` in the graph view, never members).
6. **Gates**: >= 3 accounts (mules + cash-outs, `GRAPH_MIN_RING_SIZE`) with >= 2 mules, OR an
   antibody and >= 2 accounts; >= 2 distinct flagged payers (`GRAPH_MIN_SIGNALLED_PAYERS`) or an
   antibody; >= 1 victim; score >= 0.35. Without an antibody, >= 2 mules must be
   evidence-bearing: the flagged payers of the mule are at least `GRAPH_MIN_FLAGGED_SHARE`
   (10%) of its distinct payers. A merchant that settles its takings to one account is a
   pass-through account too, but one or two false-positive flags among hundreds of customers do
   not make it a mule. In a simulator campaign every payer of a mule is a victim (share ~1.0).
7. **Score** (0-1): 0.25 min(1, victims/10) + 0.20 min(1, flagged victims/3) + 0.20 pass-through
   share (INR into cash-outs / INR entering the ring) + 0.15 antibody + 0.10 share of core members
   with payee age < 30 days + 0.05 shared device + 0.05 min(1, core members/5). **Confidence** =
   fraction of {>= 2 flagged payers, antibody, cash-out identified, pass-through share >= 0.5,
   shared device} present.
8. **Identity and lifecycle**: `ring_id` = 16 hex of `sha256(sorted core member keys)` at first
   detection, never recomputed. Rings sharing ANY core member merge into the oldest (by
   `first_detected`, then id); absorbed ids remain resolvable (`merged_from`). Status: `new` ->
   `growing` when core, victims, inflow or members increase -> `stable` after
   `GRAPH_STABLE_AFTER_S` (1800) without growth (stream time = the injected clock). A ring leaves
   the registry when its last activity is older than the retention window.
   **Retirement**: every refresh re-runs the gates on the ring's current evidence. A ring that no
   longer passes (flags cleared by a later `decision_seq`, antibody revoked or expired) and has no
   member with an active antibody becomes `retired`: it stays resolvable by id (tombstone,
   persisted), leaves `GET /rings` unless `?status=retired`, is refused by simulate and
   best-takedown (409), is not protected from eviction, and emits one `ring.retired` ledger entry.
   It returns under the same id with status `growing` when the evidence comes back. A core member
   that later stops looking like a relay (for example one late payment) keeps its role in a ring
   that already exists; a fresh pass over the final graph would not admit it (see Limitations).
9. **Incremental**: a detection pass starts from the touched nodes, finds nearby seeds through the
   relay index and re-expands those plus the cores of the rings it touches. Cost is O(touched
   neighbourhood), not O(graph) (6,000-payer merchant: detect 8 ms, event + detect 1.4 ms).
   Passes are debounced (`GRAPH_DETECT_INTERVAL_S`, 0.2 s).

### Harm model and takedown (`takedown.py`)

A model, not a prediction. `harm(ring)` = the INR that would flow from victims into the ring's
terminal accounts (cash-outs, plus members that have not forwarded anywhere yet) over the next
`GRAPH_HARM_HORIZON_DAYS` (7) if the observed behaviour continued. Every ring edge gets the
capacity `total_paise * H / W` (integer arithmetic), where `W = max(1 day, last_seen - first_seen)`
of the ring; a super-source feeds the external payers, terminals feed a super-sink, and harm is
the Dinic max flow (exact integers). Victims beyond `GRAPH_FLOW_VICTIM_CAP` (500) are folded into
one non-removable source per member; rings above `GRAPH_MAX_RING_NODES` (2,000) are refused (422).

* `simulate_takedown(g, ring, remove)` -> `TakedownResult(harm_before_inr, harm_after_inr,
  reduction_pct, removed, edges_cut)`; removing a node deletes it and its edges.
* `best_takedown(g, ring, k)`: greedy by marginal harm reduction, ties to the smallest key; it
  stops early when nothing improves. Rings of at most `GRAPH_TAKEDOWN_POOL` (40) members try every
  member; bigger rings try a restricted pool: members that carry flow in one maximum flow,
  terminals (cash-outs and dead-end collectors) and articulation points of the flow network first,
  then by throughput, at most 40. `best_takedown_exhaustive` (API: `best-takedown?exact=true`)
  enumerates all k-subsets for rings of <= 12 members and k <= 3. Greedy is not optimal in
  general: on multi-sink rings (several cash-outs, shared mules) an independent probe found it
  suboptimal in 22 of 600 random rings, worst gap 26.8 percentage points of reduction; on the
  simulator's single-sink rings the gap is 0. Use `exact=true` where it applies.
* Bounded work: at most `GRAPH_HEAVY_CONCURRENCY` (2) heavy jobs run and `GRAPH_HEAVY_QUEUE` (4)
  wait; more are refused with 429 and `Retry-After`. A deadline (`GRAPH_FLOW_TIMEOUT_S`, 5 s) is
  enforced cooperatively between max-flow computations, so an abandoned job stops at its next
  check (one max flow is the longest uninterruptible step). Results are cached per (ring version,
  request) with single flight for identical concurrent requests. Measured on this machine, 5
  greedy picks: 0.28 s (100 members), 0.55 s (200), 1.1 s (400), 3.6 s (1,000).
* `takedown_timeline(store, ring, removal_ts, remove)`: harm before/after using only transactions
  before `removal_ts` (what a responder knew), plus `observed_after_ts_inr`, the INR that really
  reached the terminals from that time on (hindsight, for demos and benchmarks).

Assumptions that matter: the observed rate continues (stationarity); a burst counts as at least a
day of activity; victims and mules do not re-route after a freeze (overstates a takedown's effect);
edges are aggregates, so there is no per-hour shape. On the simulator the projection is far above
what really followed (see Evaluation): campaigns end after a few hours, the model projects 7 days.

## HTTP API

Behind the gateway. Principal headers are trusted only with `GRAPH_GATEWAY_SECRET` (constant-time
compare of UTF-8 bytes; non-ASCII input fails the compare, answer 401) or `TRUST_GATEWAY_HEADERS=1`;
otherwise 401 on everything except the probes. Readers: officer, analyst, admin. Simulate: officer,
admin.

| Method and path | Role | Notes |
|---|---|---|
| `GET /rings?status=&limit=` | reader | `limit` 1-200 (50); `status` new/growing/stable/retired (retired rings are listed only when asked for); ordered by first detection; `ETag` over the (id, version) pairs, `If-None-Match` -> 304 |
| `GET /rings/{ring_id}` | reader | summary; ETag = `ring_id-version` |
| `GET /rings/{ring_id}/graph` | reader | nodes (`id` = 12-hex prefix, `role`, `tier` 0 victim / 1 mule / 2 chained mule / 3 cash-out, `bank`, `flags`, first/last seen, `in_inr`, `out_inr`) and edges (`count`, `total_inr`, first/last ts, `rails`, `flagged`); at most `GRAPH_MAX_RESPONSE_NODES` (500) nodes, `truncated` flag; ETag |
| `POST /rings/{ring_id}/simulate` | officer, admin | body `{remove: [<=50 node ids]}` or `{k: 1..5}`; node ids must be this ring's 12-hex ids; returns decimal-string harm before/after, `reduction_pct`, removed, `edges_cut`; 409 for a retired ring, 429 + `Retry-After` when the heavy queue is full, 503 on timeout |
| `GET /rings/{ring_id}/best-takedown?k=&exact=` | reader | greedy k (1-5) and its effect; `exact=true` enumerates (<= 12 members and k <= 3, else 422); same 409/429/503 |
| `GET /stats` | reader | counts and counters, no identifiers |
| `GET /healthz`, `GET /readyz` | none | probes |
| `GET /metrics` | `GRAPH_METRICS_TOKEN` or gateway trust + reader | Prometheus text |

Ids in responses are opaque prefixes: for accounts the first 12 hex of the keyed hash (the same
prefix family as the ledger's `payee_ref`), for tokens and devices 12 hex of a SHA-256. Amounts
are decimal strings with two places. JSON is deterministic (stable ordering), so equal inputs give
equal bytes. 422 responses carry `loc/msg/type` only, never the input. Request bodies are capped
(`GRAPH_MAX_BODY_BYTES`, 16 KiB, 413). Logs hold method, route template, status, request id and
duration (run uvicorn with `access_log=False`); an incoming `X-Request-Id` is kept only if it is
8-64 chars of `[A-Za-z0-9-]`.

## Ledger audit

`ring.detected`, `ring.merged`, `ring.status_changed`, `ring.retired` (actor `system:graph-twin`) and
`takedown.simulated` (actor `safe_actor(principal sub)`). Payload: `ring_id, event, ring_version,
members_by_role, victims, signalled_victims, inflow_bucket (<10k ... >=10m), score (2 dp), status,
antibody, at` plus `from_status/to_status`, `absorbed_ring_id`, or `mode, removed_count,
reduction_pct (1 dp), harm_bucket`; `model_version = graph-v1`; `case_refs = ["ring:<ring_id>"]`
(`ring:` is an allowlisted namespace in `svckit.pii`). Built with `svckit.ledger.build_or_placeholder`
(a refused payload becomes a fixed-shape placeholder).

Delivery guarantees: detection entries are written to `gt_audit_outbox` in the same database
transaction as the ring change; `takedown.simulated` is stored by a background job (capped at 100
in flight, `graph_twin_audit_dropped_total` counts overflow and failures) after the response is
built. Delivery is at-least-once through `svckit.drain.Drainer` (fire-and-forget, concurrency cap,
timeout, breaker) with a periodic sweeper over unsent rows; the ledger deduplicates equal entries.
No ingestion or API path awaits the ledger. A takedown entry can be lost if the process stops
between the response and the background write. Dedupe keys: `detected:<ring>`, `merged:<ring>:<absorbed>`,
`status:<ring>:<from>:<to>:<version>`, `retired:<ring>:<version>`, and for simulations (actor, ring, version, removal set).

## Configuration

| Variable | Default | |
|---|---|---|
| `GRAPH_DATABASE_URL` | `sqlite://` | SQLAlchemy URL (Postgres: `postgresql+psycopg2://...`) |
| `KAFKA_BOOTSTRAP` | unset | set to start the three consumers on KafkaBus (in-memory bus otherwise) |
| `GRAPH_GATEWAY_SECRET`, `TRUST_GATEWAY_HEADERS`, `GRAPH_METRICS_TOKEN` | unset | trust and metrics gates |
| `GRAPH_CREATE_SCHEMA` | `1` | `0` after `python -m graph_twin.migrate` |
| `GRAPH_RETENTION_DAYS` | 30 | edge/txn retention, ring expiry; older events are counted and dropped |
| `GRAPH_MAX_NODES` | 200000 | eviction target 95% (see below) |
| `GRAPH_PASSTHROUGH_MAX_S` / `_RATIO` | 3600 / 0.7 | relay timing and outflow share |
| `GRAPH_HARM_HORIZON_DAYS` | 7 | harm projection |
| `GRAPH_MAX_RESPONSE_NODES` | 500 | graph view cap |
| `GRAPH_MIN_RING_SIZE`, `_MIN_MULES`, `_MIN_SCORE`, `_MIN_SIGNALLED_PAYERS`, `_MIN_FLAGGED_SHARE`, `_RELAY_MIN_PAYERS`, `_RELAY_MIN_INR`, `_SINK_MIN_SHARE` | 3, 2, 0.35, 2, 0.10, 2, 10000, 0.3 | detection gates |
| `GRAPH_MAX_PENDING_DECISIONS`, `_PENDING_TTL_S` | 50000, 86400 | pending-decision buffer |
| `GRAPH_FANIN_SCAN_CAP`, `_MAX_COMPONENT`, `_DEVICE_MAX_ACCOUNTS`, `_LOUVAIN_MIN_NODES` | 5000, 2000, 20, 12 | per-run bounds |
| `GRAPH_MAX_RINGS`, `_MAX_DIRTY`, `_MAX_RING_NODES`, `_FLOW_VICTIM_CAP`, `_FLOW_TIMEOUT_S`, `_HEAVY_CONCURRENCY`, `_HEAVY_QUEUE`, `_TAKEDOWN_POOL` | 1000, 100000, 2000, 500, 5, 2, 4, 40 | bounds for rings, work queue, flows and the greedy pool |
| `GRAPH_DETECT_INTERVAL_S`, `_MAINTENANCE_INTERVAL_S`, `_STABLE_AFTER_S`, `_FUTURE_SKEW_S` | 0.2, 60, 1800, 3600 | timing |
| `LEDGER_DRAIN_CONCURRENCY`, `LEDGER_EMIT_TIMEOUT_S`, `LEDGER_BREAKER_COOLDOWN_S` | 8, 0.5, 10 | `svckit.Drainer` |

## Bounds and eviction

Retention: edges whose last activity is older than the window, transactions, decisions and
antibodies are deleted by the sweep (every `GRAPH_MAINTENANCE_INTERVAL_S`); node aggregates are
recomputed from the remaining edges. Memory: about 1.5 KB per node including its share of edges
(measured with tracemalloc on 55,000 nodes / 60,000 edges), so roughly 300 MB at the default
200,000 nodes. Eviction (when the count exceeds `GRAPH_MAX_NODES`, down to 95%) removes the
oldest-last-seen nodes and NEVER removes: members of a registered ring and their direct
neighbours (its victims), a node with a signal (flagged inflow or active antibody), the payer of a
flagged payment, or the nodes of the event being applied. If only protected nodes remain the
count stays above the cap and `evict_blocked` is incremented. Other bounds: pending-decision
buffer (oldest dropped, counted), dirty set, ring registry (`GRAPH_MAX_RINGS`; candidates beyond it
are counted), background audit jobs, heavy jobs (concurrency, wait queue, cooperative deadline), per-run scan caps (a node's fan-in scan stops at `GRAPH_FANIN_SCAN_CAP`; ring victim counts
of a bigger fan-in are marked `victims_truncated`).

## Evaluation (simulator)

Reproduce: `python -m graph_twin.evaluate` (needs `sim-engine`). World seeds 7101, 7102, 7103
(disjoint from txn-guard 101-106 / 201-203 / 9001-9004 and call-guard 1000 / 2000 / 3000; checked
in `tests/test_evaluation.py`), 600 citizens, 7 simulated days of benign traffic (about 6,600
transactions) plus 5 campaigns x 25 victims (3-6 young mules, one cash-out each) starting on days
2-5: about 7,200 events per seed, replayed in time order with detection after EVERY event.

Signals: txn-guard decisions are replaced by a label-free stand-in (`graph_twin.evaluate.signal_level`):
`hold_verify` for a payment to an account younger than 30 days of >= INR 20,000, `step_up` from
INR 10,000. It produces realistic false-positive holds on benign traffic (young citizen accounts,
new merchants). Results depend on that stand-in, not on the real model.

| | seed 7101 | seed 7102 | seed 7103 |
|---|---|---|---|
| rings detected / campaigns | 5 / 5 | 5 / 5 | 5 / 5 |
| rings not matching a campaign | 0 | 0 | 0 |
| member precision / recall (mule + cash-out accounts vs `GroundTruth`) | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| victims whose first transfer preceded detection (of 25) | 4, 2, 2, 3, 2 | 4, 2, 3, 2, 2 | 2, 2, 4, 3, 3 |
| victim INR sent after detection | 84.5-96.6% | 85.0-90.0% | 84.6-96.1% |

Time to detection: median 2, mean 2.7, maximum 4 victims of 25 (a ring needs two flagged payers
and a first sweep before the mules count as pass-through). The INR column shows that detection at
victim 2-4 still leaves about 90% of the money to be stopped, because campaigns ramp over hours.
Benign: 0 rings on the evaluation worlds and on three further 1,500-citizen x 10-day benign worlds
(23,000 events each; 53-68 accounts carried a false-positive hold); also none for a popular
merchant with 400 payers and no signal, a merchant with one or two false-positive holds, a
payroll chain of established payees, an A -> B -> C chain with one flag, two merchants of 300
payers settling to one account with one false-positive step-up each, three sub-merchants of 300
payers settling to one aggregator with two each, and two merchants of 600 payers with 20
false-positive flags each (flagged share 3%; `tests/test_false_rings.py`). An independent review
with the real txn-guard scorer on three fresh 800-citizen 14-day worlds reported precision 1.00 /
recall 1.00 / 0 false rings.

Batch invariance (`tests/test_invariance.py`): a 350-citizen, 5-day simulator world with 5
campaigns x 16 victims plus benign merchants gives the same rings (core members, victims, inflow
in paise) whether detection runs after every event, every 200 events, every 500 events, once at
the end, once at the end with all decisions arriving last, or after every event with arrivals
shuffled inside 5-minute windows. Ring ids are not compared (an id is the hash of the core at
first detection and so depends on when that happened).

Takedown on the 15 detected rings: greedy equals the exhaustive optimum for k = 1, 2, 3 (gap 0.0
percentage points everywhere; rings have 4-7 members and a single cash-out, so removing the
cash-out already removes all modelled harm). Freezing the two greedy picks at detection time
projects a 100% reduction. The projection is much larger than hindsight: at detection the
7-day projection was INR 5.1-24.7 million per campaign while INR 0.97-3.5 million really reached the
cash-out afterwards, so absolute projected harm should be read as an upper bound under the
stationarity assumption, and reduction percentages as the relative effect.

Ingestion measured on this machine: about 2,400 events/s with in-memory SQLite (transaction +
decision + detection after every event), 680-740 events/s with file SQLite (17,000 nodes), one
database transaction per event; Postgres was not benchmarked. Dominant cost: one transaction and
about 8 statements per event.

## Threat model

* The twin trusts the gateway for identity and roles; it runs on the internal network only.
* Inputs are pseudonymous hashes and tokens. Responses expose 12-hex prefixes only; logs and
  ledger payloads carry no identifiers (ids are hashed or bucketed; the PII guard checks payloads).
  A 12-hex account prefix is 48 bits of a keyed hash: it identifies an account across services
  that hold the same hash, and is not reversible without the federation key.
* Poisoning: an attacker who can inject transactions or antibodies can create or hide rings. Gates
  (signals, >= 2 mules, share thresholds) reduce accidental rings, not deliberate injection;
  events come from authenticated producers only.
* Resource exhaustion: every cache, buffer, scan and job is capped (see Bounds); future-dated
  events (> 1 h ahead) are rejected, events older than the retention window are dropped and counted.
* A malformed event is dead-lettered by `svckit.consume` (handler failures are retried first).
  The consume loop logs the exception type, pydantic error type codes, payload length and a
  12-hex prefix of the payload SHA-256; it does not log the payload, the exception message or a
  traceback (regression-tested in `libs/svckit`). The dead-letter topic holds the raw message, so
  it carries the same sensitivity as the input topic.

## Limitations (honest)

* The simulator is easy for this method: one cash-out sink, 3-6 young mules per campaign, sweeps
  within 45 minutes, no mixing with legitimate traffic, no layering through several hops, no
  split cash-out. Perfect precision/recall here says little about real fraud networks.
* Pass-through is judged on edge AGGREGATES (first/last timestamps and totals), not per transaction:
  a mule that collects for more than `GRAPH_PASSTHROUGH_MAX_S` before its first sweep is not a relay
  (this also delays detection), and a long-lived account that mixes legitimate and mule traffic
  can look like a non-relay.
* Rings need >= 2 flagged payers (or an antibody) and >= 2 mules whose flagged payer share is at
  least `GRAPH_MIN_FLAGGED_SHARE`; a campaign with one flagged victim stays invisible, and a mule
  that mixes a few victims into heavy legitimate traffic (share below 10%) is not recognised
  without an antibody. Accounts without `payer_account_ref` cannot be linked as mules.
* Relay status is an aggregate over the whole retained history of an account. A mule that makes
  one payment hours after its sweeps stops being a relay: a ring that already contains it keeps
  it, a fresh pass over the same final graph would not admit it. The registry therefore depends
  on the pass history in that corner (the batch-invariance test covers simulator campaigns,
  where mules do not do this).
* Stationary harm projection with a 1-day floor and no re-routing (see above); unvalidated on real
  data. `reduction_pct` compares scenarios of the same model.
* Within-edge aggregates are not decayed; an edge stays until its last activity leaves the window.
* Sweep, eviction and detection hold one process-wide lock for O(touched) to O(nodes) work; a
  single replica owns the graph (no sharding). Several replicas on one database would each build a
  complete graph from the shared tables only at start.
* Louvain splitting is applied only to components of more than 12 accounts; a split can separate
  mules from their sink (they are re-merged when a later pass shares a core member).
* Not covered: Postgres throughput, a real Kafka run, multi-hop layering beyond the relay chain
  handling, device-token antibodies.
