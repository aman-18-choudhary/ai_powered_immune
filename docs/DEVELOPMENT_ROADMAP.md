# Development roadmap

Phase 0 deliverable, written 2026-10-15. It maps the target phases (0-14) onto what the
repository already contains, so completed work is not rebuilt. Each phase ends with a report in
`docs/reports/PHASE_<N>_REPORT.md` and an update of [PROJECT_STATUS.md](../PROJECT_STATUS.md), and
no phase starts without the owner's explicit approval.

This roadmap replaces [PROJECT_IMPLEMENTATION_PLAN.md](../PROJECT_IMPLEMENTATION_PLAN.md) (phases
A-F of 2026-10-14) as the active plan; that file is kept for history.

## 1. How earlier work maps onto the target phases

| Earlier work (dates, commits) | What it delivered | Target phases |
|---|---|---|
| Original build (2026-10-07 → 10-11) | Microservices, simulator, call/txn guards, antibody hub, evidence ledger, graph twin, geo intel, citizen shield, gateway, console, Docker Compose, CI | 1, parts of 4, 7, 9, 10 (simulator only) |
| Step B1 (`ed17a58`..`c5d9767`) | complaints service: identity (Clerk verifier, demo verifier), roles, allowlist, bootstrap, access lapse, complaints, evidence, alerts, admin | 2, 3 |
| Step B2a (`d76448d`..`0e0f936`) | Analysis pipeline v2, category model category-v1 (synthetic), URL and entity checks, optional Ollama explanations | 4, 5 |
| Live microphone (`fd52779`..`e2384e3`) | services/live_audio, console Live microphone mode | 7 |
| Phase A (`214e755`..`8e4dee0`) | Migrations, bank details, profile/privacy, evidence removal, complaints mounted in demo-server, local dev sign-in; console sign-in, citizen dashboard, complaint form/detail, investigator queue, admin | 2, 3, 11, 12 |
| Phase B (`a34754b`..`91ebfeb`, pushed 2026-10-16) | CRITICAL level, provenance, confidence interpretation, failure semantics, `/analyze` inputs, analysis history, `/models`, grounded local LLM (qwen2.5:0.5b), Check page, Models page | 4, 5, 11 |
| Phase 6 (`9dbad27`..HEAD, 2026-10-16/17, **not pushed**) | Audio evidence transcription (migration 9, transcript endpoint, timed indicators), precise capability messages, shared LLM grounding (`svckit.llm_grounding`) for complaints and Live Protection; [report](reports/PHASE_6_REPORT.md) | 6, 4, 7, 11 |

## 2. Phase status

| # | Phase | Status | Remaining work |
|---|---|---|---|
| 0 | Requirements, architecture, design | **This phase** | Approval of these documents |
| 1 | Repository and development foundation | **Complete; CI verified green on GitHub 2026-10-16** (after the CI repair, commits `2bb5687`..`7b97731`) | Run the Compose stack once a Docker daemon is available; add the console tests to CI |
| 2 | Authentication and RBAC | **Complete and verified locally; Clerk blocked** | Live Clerk test with real keys; production guard that refuses local/demo auth (finding 5) |
| 3 | Complaint and evidence management | **Complete and verified**; complaint details edit while SUBMITTED and the evidence download audit added 2026-10-19 | Storage hardening (encryption at rest, malware scanning) |
| 4 | Fraud detection and risk assessment | **Implemented; tests verified; Check page verified by hand**; role-inversion check tightened in Phase 6 | Evaluate a 3B local model where hardware allows (0.5b answers are mostly rejected) |
| 5 | Dataset and model evaluation | **Partial** | category-v1 already ships held-out metrics with a confusion matrix, language slices, noise-robustness and hard-negative results (`services/complaints/complaints/ml/artifacts/category-v1.metrics.json`, `MODEL_CARD.md`), all on synthetic data. clf-v1 metrics file added 2026-10-19 ([PHASE_5_CLF_METRICS.md](reports/PHASE_5_CLF_METRICS.md): near-perfect on its own generators, ROC-AUC 0.59 on independent messages). Remaining: one documented dataset pipeline across models; permitted public or real data |
| 6 | Audio, screenshot and URL analysis | **Complete**: audio transcription (Phase 6); screenshot OCR with local Tesseract 5 (eng + hin) added 2026-10-19, scored by the same rules with indicators pointing to the image | PDF text extraction; OCR quality on real screenshots not measured; Docker images do not include Tesseract (reported as unavailable there) |
| 7 | Live Protection | **Complete and verified** (incremental, not streaming); LLM explainer grounded in Phase 6 | Clearer permission-denied message (finding 8) |
| 8 | Fraud DNA | **Implemented and tested**: cached multilingual MiniLM (offline), Qdrant embedded, redacted text, idempotent sync, staff-only `GET /complaints/{id}/similar`, similar-cases panel, synthetic evaluation with held-out families | Evaluation on real complaints; weak Hinglish (romanised Hindi) recall; threshold not calibrated on real data |
| 9 | Network intelligence | **Implemented and tested (branch `phase9-network`, not pushed)**: identifiers from structured fields, observed/inferred links, confirm/reject reviews with audit, related panel, graph, clusters, POST identifier search, case-matching opt-out enforced everywhere | Evaluation on real complaints; identifiers in free text and evidence (not extracted, by design for now); Neo4j not needed at this size |
| 10 | Geospatial intelligence | **Implemented and tested**: grid hotspots from persisted coordinates, filters, period comparison, places without coordinates, staff map on the bundled India map | Few complaints carry coordinates (optional); no geocoding of free-text places; PostGIS not needed at this size |
| 11 | Citizen application | **Complete** (2026-10-19): details edit while SUBMITTED, system/light default theme, readable alert wording, own case report download | Native-speaker review of hi/ta/bn texts |
| 12 | Investigator and admin applications | **Implemented**: investigation notes, case timeline (per-case audit view), Markdown case report export (owner and staff versions, audited), intelligence panels from phases 8-10, read-only configuration view | PDF export (the Markdown report prints from any viewer) |
| 13 | Multi-channel and multi-agent extensions | **Not started** | WhatsApp/IVR via a provider (only when configured and tested); language expansion with evaluation per language; multi-agent orchestration only if justified |
| 14 | Security, reliability and deployment | **Partial**: production auth guard, loopback default, evidence encryption at rest, malware scanning (ClamAV) and download audit, backups (verify/restore), retention erasure command, load check, console CI job, rate-limit window test fixes (2026-10-19) | Retention period and schedule (policy); database column encryption; shared rate limiting (Redis); Docker build (no daemon here); scheduled backups; capacity testing beyond the load check ([report](reports/PHASE_14_REPORT.md)) |

## 3. Recommended order from here (and why it differs from the numeric order)

1. ~~Push Phase B~~ (done 2026-10-16).
2. ~~Phase 6 (audio evidence + message fixes) with the Phase 4/7 grounding fixes~~ (implemented
   2026-10-16/17; **push awaits approval**).
3. Push Phase 6 once approved.
4. **Phase 9 then 8 then 10**: shared identifiers (phase 9) come straight from stored fields and
   need no new software; Fraud DNA (8) needs an embedding install; geospatial (10) needs complaints
   with coordinates to be meaningful.
5. **Phase 12** panels follow the intelligence back ends.
6. **Phase 5** evaluation work can run in parallel at any point.
7. **Phase 14** hardening before any deployment; Phase 13 last and optional.

Reason for the change from strictly numeric order: phases 1-3 and most of 4, 7 and 11 already exist
and pass their tests; rebuilding them would add risk without benefit.

## 4. Phase gate (applies to every phase)

1. Verify existing work related to the phase before changing it.
2. Implement only the approved missing work; tests for success and failure paths.
3. Run the relevant suites; record exact results and who ran them.
4. Review the diff and check for secrets; stage only intended files.
5. Commit in small logical commits; push only when approved and possible; record hashes.
6. Write `docs/reports/PHASE_<N>_REPORT.md`, update `PROJECT_STATUS.md` and this roadmap.
7. Stop for the owner's review.
