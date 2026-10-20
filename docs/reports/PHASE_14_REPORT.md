# Phase 14 report: security, reliability and deployment

Dates: 2026-10-19 and 2026-10-20. All pushed to `main` and `build/implementation` after full
validation (Python 3.13 suite, Python 3.11 CI equivalent from a clean clone, Postgres, Playwright).

## 1. Done

| Item | What it does | Evidence |
|---|---|---|
| Production guard | `COMPLAINTS_ENV=production` refuses demo/local sign-in, the test code outbox, non-https CORS origins and a missing evidence key at start-up | config tests |
| Loopback default | `python -m demo_server` listens on 127.0.0.1 unless `HOST` is set (the image sets 0.0.0.0; compose publishes on 127.0.0.1) | test; server seen on 127.0.0.1 only |
| Evidence encryption at rest | AES-256-GCM with `COMPLAINTS_EVIDENCE_KEY`, the storage name bound as associated data; downloads decrypt in memory; transcription/OCR use a 0600 temporary copy removed after the analysis; old plaintext files stay readable; `python -m complaints.encrypt_evidence` encrypts them in place with hash checks | unit tests (swap and missing key refused, legacy files, migration, audio and OCR through temp copies); full Playwright suite on a server with encryption on: 26/26, every stored file ciphertext, no plaintext left in `.tmp` |
| Malware scanning | uploads scanned by a local ClamAV daemon (clamd INSTREAM) before storing: infected refused (422), clean marked `scan: clean`; scanner down: stored as `not_scanned` or refused (503) with `COMPLAINTS_AV_REQUIRED=1` | fake-clamd tests; real ClamAV 1.5.4 (3.6 M signatures) detected the EICAR test file; full Playwright 26/26 on a server with scanning required, encryption and OCR on |
| Download audit | every evidence download recorded (`evidence_download`, ids only) | test |
| Backups | `python -m complaints.backup --out / --verify / --restore`: consistent snapshot (SQLite online backup, `pg_dump`), evidence as stored, SHA-256 manifest without the connection string; restore only into empty targets | SQLite and Postgres round trips, tampering detected; a live backup of the running :8080 data verified |
| Retention | `python -m complaints.retention --older-than-days N [--apply]` erases complaints their owners deleted more than N days ago; dry run by default; N required | tests incl. idempotence and untouched recent/active complaints |
| Load check | `scripts/load_check.py` drives a running server with concurrent synthetic users and reports percentiles and errors | runs below |
| CI | console job (tsc, eslint, vitest, build); flaky-by-design tests fixed (ledger timing contract, fixed-window rate-limiter clocks) | CI green since `e84c490` |

## 2. Load check results (this laptop, one process, SQLite, real model, OCR and encryption on)

| Users | Wall time | Errors | `POST /complaints` p95 | `GET /complaints/{id}` p95 | analysis p95 |
|---|---|---|---|---|---|
| 40 | 1.95 s | 0 | 348 ms | 140 ms | 1306 ms |
| 150 | 6.89 s | 0 | 1453 ms | 5473 ms | 2557 ms |

* **Correctness under concurrency:** no errors and every request answered 2xx at both levels.
* **Latency degrades with load.** At 150 simultaneous users, reads wait up to about 5.5 s on one
  laptop process that also runs 150 rule analyses, the embedding model and OCR.
* **A Similar query after a burst of new complaints** waits for the index to catch up, about 5 s
  for 150 new texts. This is by design: results are never stale.
* **These are not capacity figures.** Production would use Postgres, several workers, and Qdrant
  as a server.

## 3. Not done (blocked or a decision)

* **Docker build of the stack:** no Docker daemon on this machine.
* **Clerk sign-in with real keys:** no keys.
* **Malware scanning runs only where clamd runs:** about 1 GB of memory, and the signatures need
  `freshclam` updates. It is not started by default on this laptop.
* **Retention:** the period and schedule are policy; nothing runs the command automatically.
* **Backups:** nothing schedules them; run from cron or a timer.
* **Shared rate limiting across processes (Redis):** not built; the limits are per process.
* **Database column encryption** (complaint text, transcripts, OCR text): not built.
