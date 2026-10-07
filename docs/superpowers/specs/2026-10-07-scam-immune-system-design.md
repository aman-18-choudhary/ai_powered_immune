# Federated Scam Immune System — Design Spec

Date: 2026-10-07
Status: Draft for review

## 1. Intent

Build a finance-grade, microservice-based Digital Public Safety platform that moves law enforcement and banks from reactive investigation to predictive neutralisation of digital-arrest scams and mule networks.

**Team/time:** 4+ people, 3+ weeks (plan assumes 8 phases; compressible, see section 9).
**Stack:** Python (FastAPI) backend, React frontend, Docker / Docker Compose (Kubernetes manifests for scale).
**Data:** Fully synthetic but financially realistic (UPI/IMPS/NEFT behaviour, realistic amounts, limits, timing, benign-dominant class balance, mule fan-in/fan-out).
**Out of scope:** Counterfeit currency detection (dropped by decision). Real telecom/bank integrations (simulated through adapters).

**Success criteria (maps to evaluation focus):**
- Scam-detection precision/recall measured against simulator ground truth.
- Citizen/transaction-facing false-positive rate very low (target < 0.1% of benign transactions held).
- Detection lead time before mass victimisation measured and reported.
- Every verdict explainable; every action auditable; evidence packages verifiable.

**Concept spine (all three layers):**
1. Immune system: shared threat memory; one detection protects all other banks/channels.
2. Reflex: interception inside the golden minutes before money moves.
3. Brain: fraud-network digital twin for takedown simulation.

**Demo hero (3 min):** A simulated digital-arrest call is flagged live; the victim's transfer is held and verified; the mule fingerprint is published as an antibody; a second victim at another bank in another state is protected within seconds; the graph/map shows the ring and a simulated takedown; the evidence package is generated and verified.

## 2. Services

| Service | Responsibility | Data store | Key tech |
|---|---|---|---|
| sim-engine | Seeded synthetic ecosystem with ground-truth labels | Postgres + object storage | Python, numpy, Faker-style generators |
| call-guard | Scores call/transcript streams for digital-arrest scripts, spoofing, urgency | Redis (session state) | Transformer classifier + rules |
| txn-guard | Transaction risk scoring, p99 < 300 ms; decision: allow / step-up / hold-and-verify | Redis feature store, Postgres | FastAPI, gradient-boosted model |
| antibody-hub | Federated threat memory; hashed signals shared across banks | Postgres + Bloom filter cache | Salted/keyed hashing, Bloom filters or PSI |
| graph-twin | Fraud graph, ring detection, takedown simulation | Neo4j | GNN/graph algorithms |
| geo-intel | Hotspot and cash-out geospatial analytics | PostGIS | deck.gl |
| evidence-ledger | Append-only hash-chained audit log, signed evidence packages | Postgres (append-only) | Merkle chain, Ed25519 signatures |
| citizen-shield | Multilingual chat verdicts and guided reporting | Postgres | LLM with guardrails, web chat (WhatsApp adapter mocked) |
| gateway | AuthN/Z, rate limits, routing, PII tokenisation at edge | Postgres (users/roles) | FastAPI, JWT, RBAC |
| web-console | Command-centre, bank-analyst and citizen UIs | n/a | React, TypeScript, Tailwind, shadcn/ui |

Shared infrastructure: Redpanda/Kafka event bus, Postgres, Redis, Neo4j, PostGIS, Prometheus + Grafana, OpenTelemetry tracing. All services run via `docker compose`; Kubernetes manifests added in Phase 6.

Rules: services communicate only via events and versioned APIs (OpenAPI/JSON Schema contracts in a shared `contracts/` package); no service reads another's database.

## 3. Core data flow

1. sim-engine emits call events, transactions, complaints onto the bus.
2. call-guard flags a live scam session (score, reasons, model version) -> event `call.risk`.
3. txn-guard consumes `call.risk` plus transaction features; on a risky transfer returns **hold-and-verify** (never silent block; victim or analyst confirms).
4. Confirmed mule fingerprint -> antibody-hub publishes keyed hash; all subscribed banks' txn-guard instances update within seconds.
5. graph-twin links mule to ring; geo-intel plots cash-out points.
6. evidence-ledger records every step; package export verifies chain integrity.

## 4. Finance-grade requirements

- **Auditability:** hash-chained append-only ledger; each entry carries timestamp, actor/service, input hashes, model version, decision, explanation. Evidence package includes chain-of-custody and a verification script (court-admissibility style, Section 63 BSA-oriented).
- **Privacy/compliance (DPDP, RBI/NPCI-aligned):** PII tokenised at the gateway; cross-bank sharing only of keyed hashes; consent and purpose logging; role-based access; retention limits; data minimisation tests.
- **Explainability:** top-k reason codes per verdict, calibrated probabilities, model cards, threshold governance.
- **Reliability:** idempotency keys on all state-changing events, retries with dead-letter queue, circuit breakers, health/readiness probes, graceful degradation (txn-guard falls back to rules if models are unavailable).
- **Security:** least-privilege service accounts, secrets via env/secret store, dependency scanning, input validation at boundaries.
- **Performance:** txn-guard p99 < 300 ms at target load; load-tested in Phase 6.
- **Realism of synthetic data:** distributions for amounts, hour-of-day, merchant categories, UPI per-transaction limits, IMPS/NEFT settlement windows, mule account ageing and rapid pass-through; validated against published statistical shape, not eyeballed.

## 5. Evaluation plan

Simulator ground truth enables: precision, recall, F1, false-positive rate, and **lead time** (time from first scam-infrastructure signal to detection versus time to mass victimisation). A reproducible benchmark script outputs a report used in the deck. Ablations: with vs without antibody sharing; with vs without call-risk signal in txn-guard.

## 6. UI/UX design

Users: (a) police command-centre officer, (b) bank fraud analyst, (c) citizen.

**Design system (three-layer tokens):** primitive (colour, spacing, type scale) -> semantic (`risk-critical`, `risk-warn`, `safe`, `surface`, `text-muted`) -> component tokens (alert card, verdict badge, graph node). Implemented as CSS variables with Tailwind and shadcn/ui; light and dark themes; WCAG AA contrast; risk is never conveyed by colour alone (icon + label).

**Command centre:** map-first layout (deck.gl) with live incident feed, risk heat layers, and a graph panel; "Simulate takedown" side panel showing projected harm reduction; evidence export button. Keyboard-navigable, dense but scannable.

**Bank analyst console:** alert queue sorted by time-to-money-movement, a countdown showing the golden-minutes window, one-click verify/release/hold, reason codes in plain language.

**Citizen shield:** mobile-first chat, one verdict per screen (Safe / Suspicious / Likely scam), plain-language advice, large "Report" action, language selector (initially English, Hindi, plus 2-3 regional languages; extendable to 12). Calm, non-alarming tone; explicit uncertainty when unsure.

**Demo choreography:** split view showing victim phone, bank console, and command centre simultaneously, so the antibody protection moment is visible at a glance.

## 7. Error handling

Hold-and-verify instead of hard blocks; all failures degrade to safe defaults (rules-only scoring, queue for analyst review); every dropped or dead-lettered event is logged to the ledger and alerted.

## 8. Testing

- Unit tests per service; contract tests against `contracts/` schemas.
- Integration tests with Docker Compose (end-to-end hero scenario as an automated test).
- Data-realism tests for the simulator.
- Model evaluation tests with metric thresholds as CI gates.
- Ledger tamper test (mutate an entry; verification must fail).
- Load test for txn-guard latency; privacy test asserting no raw PII crosses bank boundaries.

## 9. Phases

| Phase | Weeks | Outcome |
|---|---|---|
| 0 Foundations | 1 | Repo, Compose, event bus, gateway/auth, CI, contracts |
| 1 Simulator | 1-2 | Realistic labelled UPI/IMPS/NEFT + call data, validated |
| 2 Detection core | 2-3 | call-guard, txn-guard, first metrics |
| 3 Immune memory | 3-4 | antibody-hub, cross-bank second-victim demo |
| 4 Network intelligence | 4-5 | graph-twin, geo-intel, takedown simulation |
| 5 Evidence + citizen | 5-6 | evidence-ledger packages, citizen-shield |
| 6 Console + hardening | 6-7 | React console, load/security tests, dashboards |
| 7 Delivery | 7-8 | Architecture diagram, deck, demo video, benchmarks |

Three-week compression: merge Phases 0+1, cut citizen-shield to a minimal web chat, defer geo-intel to a static heatmap, keep antibody-hub and the hero demo intact.

## 10. Risks

- Federation looks abstract: mitigated by the split-screen demo choreography.
- Synthetic data seen as unrealistic: mitigated by documented distributions and validation tests.
- Scope: mitigated by phase gating and the compression plan.
