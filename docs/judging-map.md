# Judging map

Where to find the evidence for each criterion, and what that evidence does not show. All numbers
are simulator-only (seed 42 unless stated); the sources are
[docs/benchmark_report.md](benchmark_report.md) and
`services/demo_server/demo_server/data/benchmark_summary.json`.

## Innovation (25)

| Claim | Evidence | Caveat |
|---|---|---|
| Shared threat memory without sharing customer data: banks exchange keyed hashes, the hub never holds the key | `services/antibody_hub/README.md` (Threat model and key handling); `contracts/scam_contracts/hashing.py`; hub tests grep DB dump, bus and logs for raw identifiers | key distribution and rotation among banks is out of scope; the hash is linkable by anyone holding the federation key |
| One detection protects a second victim at another bank | hero scenario: model alone allowed B's first transfer in 39 of 40 seeds (1 step-up); with the antibody 40 of 40 held for verification ([report, Hero scenario](benchmark_report.md)); `tests/e2e/audit` | constructed hard case (seasoned mule, off-script call); the 40 seeds vary mule age, amounts and timing, not the script |
| Reflex layer: hold-and-verify inside the golden minutes, never a silent block | `services/txn_guard/README.md` (Hold-and-verify service); late call-risk upgrades; holds never auto-release | verify steps cost real users time: held-benign 0.000% in this run, 0.103% on first calibration was accepted as a trade-off |
| Brain layer: ring detection and takedown what-if | `services/graph_twin/README.md` (Detection, Harm model); console `/command` ([screenshot](screenshots/command-1440x900-dark.png)) | the simulator is easy for this method (one sink, 3-6 young mules); harm projection is stationary and unvalidated on real data |
| Evidence a third party can verify without trusting the operator | `services/evidence_ledger/README.md`; `docs/audit-trail.md`; `make verify-package` (PASS_PINNED); [screenshot](screenshots/evidence-verified-1440x900-dark.png) | integrity is not truthfulness; no external timestamping; no admissibility claim |

## Business impact (25)

| Claim | Evidence | Caveat |
|---|---|---|
| High precision at the hold operating point | precision 100% [99.3, 100.0], 0 held of 21,736 benign (FPR upper CI 0.018%) | prevalence in the run is 3.03%; precision depends on prevalence (prevalence table in the report) |
| Recall on scam transactions | 82.6% [79.6, 85.3]; victim-transfer recall 100% with antibodies, 94.5% without; mule-forward recall 62.4% | simulator ground truth; the analyst is an assumed oracle with a 60 s delay |
| Detected before mass victimisation | 5 of 5 campaigns detected before the 10th victim, mean lead time about 6.5 h on the first hold ([report, Lead time](benchmark_report.md)) | few campaigns, wide uncertainty; the simulator's victims are minutes to hours apart |
| Money at stake | the report counts INR 18,227,511.69 of victim transfers after the first hold, all of it held ("money prevented, upper bound") | an upper bound by construction: a held transfer is treated as stopped for good and `hold_verify` as ending in rejection; real prevented loss is lower |
| Low friction for citizens | benign step-up 0.097% overall; per rail UPI 0.016%, IMPS 0.37%, NEFT 0.83% flagged (hold 0.000% on all rails) | NEFT has no scam data and its thresholds are hand-scaled |
| Works for the citizen directly | `/citizen` multilingual verdict card (en, hi, ta, bn) ([screenshot](screenshots/citizen-verdict-390x844-dark.png)) | rule-and-classifier scorer on authored and simulated text; "safe" means no clear scam cues found |
| Honest about uncertainty | report Limitations; console "About these numbers"; `/insights` | n/a |

## Technical excellence (20)

| Claim | Evidence | Caveat |
|---|---|---|
| Large automated test base | About 1,500 Python tests collected across 12 packages (contracts 29, svckit 179, sim-engine 51, call-guard 141, txn-guard 320, hub 165, ledger 191, graph-twin 125, gateway 60, demo-server 182, audit 27, bench 38) plus 26 console test files and Playwright specs; `make test-all` | counts are `--collect-only`; a few Postgres tests are opt-in |
| Adversarial review of every service | SDD ledger: each task reviewed by an independent reviewer with fix rounds until findings closed; findings turned into regression tests (ledger attack campaign of about 15,000 mutations, none passing a pinned verify) | the review ledger is a working file under `.superpowers/` (git-ignored) |
| Correctness under at-least-once delivery | idempotent replays republish identical decisions; per-payer ordering contract; parity tests (0 differences, 1,586 transactions); outbox + breaker for audit | not exercised against a real Kafka cluster |
| Reproducible benchmark | deterministic JSON (byte-identical for a seed), disjoint train/calibration/eval seeds enforced, ablations, Wilson intervals, sensitivity sweep | simulator-only; same generators as training |
| Packaging | multi-stage non-root Dockerfiles, compose with healthchecks and required secrets, static checks (`scripts/check_dockerfiles.py`) | images were not built and the stack was not run (no Docker daemon) |
| Security hygiene | JWT minimum secret length, RBAC, rate limits, PII guard on the ledger, isolated verifier, log redaction, origin-checked WebSocket | gateway does not inject per-service secrets, so upstream trust relies on network isolation |

## Scalability (15)

| Claim | Evidence | Caveat |
|---|---|---|
| Partitioning designed for per-payer order | keying contract in `contracts/scam_contracts/topics.py`; [architecture, Deployment notes](architecture.md#7-deployment-notes) | not load-tested; both bank instances in the compose file share a consumer group, so per-bank routing of transaction events is not modelled |
| Stateless scorers, state in Redis; replicated hub and ledger over Postgres | `services/txn_guard/README.md`, `services/antibody_hub/README.md` (Operations), `infra/docker-compose.yml` | graph-twin is a single replica (no sharding) |
| Bounded work everywhere | graph-twin bounds and eviction (`GRAPH_MAX_NODES` 200,000, heavy-job queue, deadlines); drainer concurrency caps; body and response caps | measured on simulator-scale graphs only |
| Latency | consumer-path p50 about 7 ms, p99 about 14 ms per transaction with fakeredis stores (txn-guard README); target p99 under 300 ms | in-process figure, not a load test |
| Degrades safely | rules-only fallback, antibody bootstrap fail-open, audit never on the hot path ([failure modes](architecture.md#6-failure-modes)) | fail-open means weaker protection while degraded |

## UX (15)

| Claim | Evidence | Caveat |
|---|---|---|
| Three audiences, seven pages | `/` live interception, `/command`, `/analyst`, `/evidence`, `/citizen`, `/insights`, `/about` ([README](../README.md)); [screenshots](screenshots/) at 1440, 1024 and 390 px in light and dark | n/a |
| Cinematic hero player with honest labelling | 71 s at 1x; SIMULATION pill and constructed-case label always visible; [demo script](demo-script.md) | the demo video is not recorded |
| Accessibility | WCAG AA tokens, axe-core checks in both themes (Playwright `pages.spec.ts`), keyboard shortcuts and command palette (Ctrl/Cmd+K), motion toggle, risk never by colour alone | axe covers automated rules only; e2e was not re-run in this delivery pass |
| Analyst workflow | deadline-sorted hold queue with countdown rings, reason meanings, guarded resolve with a note ([screenshot](screenshots/analyst-1440x900-dark.png)) | demo analyst is auto-confirming unless a human acts |
| Citizen flow | mobile-first, one verdict per screen, explicit uncertainty, nothing stored ([screenshot](screenshots/citizen-verdict-ta-390x844-dark.png)) | four languages, not twelve |
