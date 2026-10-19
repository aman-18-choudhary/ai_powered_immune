# Phase 8 report: Fraud DNA (similar complaints)

Date: 2026-10-19. Branch `phase8-fraud-dna`, on top of `e84c490` (the CI fix, below).

> **Read this first.** Fraud DNA finds complaints whose **descriptions are worded alike**. A
> high similarity suggests a similar scam *method*; it is **not** proof that the same person,
> group or crime is involved, and it is a different thing from a shared identifier (Phase 9).
> The thresholds were chosen and measured on a small **SYNTHETIC** set written by the feature's
> author. No real-world precision or recall is known.

## 1. The CI failure (runs #38, #39 and #40), root cause and fix

* **Failing tests.**
  * #38 and #39: `demo_server` `tests/test_pacing.py::test_both_modes_same_events_and_wall_offset`.
  * #40: `tests/test_engine.py::test_seek_replays_deterministically_then_continues`.
  * In every case a `ledger.entry` event sat in a different position between two runs of the same
    seed. The log was read through the GitHub API with the repository credential git already uses
    (the owner granted access).
* **Root cause (confirmed).**
  * The tests asserted byte-identical event sequences. The engine's documented contract
    guarantees only the non-ledger events; ledger entries wait at most `LEDGER_WAIT_S` (1 s) for
    the ledger consumer and otherwise appear later.
  * On GitHub's Linux runners the lag past 1 s happens intermittently. A probe workflow on a
    temporary branch, since deleted, logged the "ledger lagging" breaker with **both** the
    dependency versions of the last green run (#37) and the current ones.
  * So it was not the fastapi 0.146 / starlette 1.9 / httpx2 2.14 updates. It was not the Ubuntu
    image either: #37 and #38 used the same image, 24.04.5, version 20261012.
* **Why it showed up only now.** Not established. #37 passed on the same image and code path,
  most likely by timing luck. The trigger of the lag on Linux is not identified.
* **Fix: `873430f` and `e84c490`, pushed with approval.**
  * Every test that compares runs now uses contract helpers in `tests/conftest.py`: non-ledger
    events identical, and the ledger sequence identical up to a lagging tail.
  * A regression test forces the lag in one run.
  * A guard test shows the helpers still reject real differences.
* **Stress check.** With `LEDGER_WAIT_S = 0` the old tests fail in exactly three places: the two
  CI failures plus one latent one. All 215 demo-server tests pass with the fix.
* **Result.** CI runs #43 and #44 on `e84c490` pass.

## 2. What was built

* **Text** (`complaints/dna.py`, `dna_text`):
  * Title and description only.
  * Links, email and UPI addresses, IFSC codes and digit runs of 8 or more digits become
    placeholders before embedding.
  * Fewer than 20 letters left gives `insufficient_text`; placeholders don't count as letters.
    A test found the bug where they did.
* **Model:**
  * `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, cached revision
    `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`, 384 dimensions, CPU.
  * Loaded with `local_files_only=True`. A test with all sockets blocked passes.
  * Missing library or model gives an honest `unavailable` with the reason.
* **Index:**
  * Qdrant embedded mode, one collection per text version, model and dimension; the payload holds
    the complaint id and text hash only.
  * The demo server stores it at `<COMPLAINTS_DATA_DIR>/dna`.
  * A background sync adds new complaints and removes deleted, opted-out and too-short ones. It
    is idempotent: a second sync changes nothing.
  * Create, delete and case-matching changes mark the index stale, and the next query syncs first.
* **API:**
  * `GET /complaints/{id}/similar?limit=1..50`, INVESTIGATOR and ADMIN only (citizens 403,
    anonymous 401, unknown or deleted complaint 404, bad limit 422), in the read rate bucket (changed from the analysis bucket in Phase 10: the panel polls while the model loads).
  * Each item carries: similarity, band, rule-based indicators shared by both latest analyses, same
    category, an `identifier_link` from the network reported separately, and an explanation.
  * Response states: `ok`, `loading`, `unavailable` (with a reason), `insufficient_text`, and
    `matching: "disabled"` for opted-out complaints.
* **Privacy:**
  * Only matchable complaints are indexed.
  * Every candidate is re-checked in SQL at request time with the same filter as the network.
    A test proves an excluded complaint disappears even when the index is stale.
  * Excluded complaints are not counted.
  * The embedder never receives identifiers, and nothing reaches the logs; both are tested.
* **Console:**
  * Similar complaints panel on the staff complaint page.
  * Fraud DNA card on the Models page.
  * The admin health page now shows `sql`, `local` and `ready` as working. This fixes a Phase 9
    defect where the graph row read "Unavailable (sql)".
* **Dependencies:**
  * `qdrant-client>=1.19,<2` is a regular dependency (1.19.1 installed).
  * `sentence-transformers>=6.1,<7` is the optional `dna` extra (6.1.0, with torch 2.14.1 CPU,
    transformers 5.20.1 and huggingface-hub 1.33.0).
  * CI skips the `dna` extra like `stt`.
  * `pip check` is clean. Whisper still works: real-STT tests pass.
* **Qdrant workaround:** qdrant-client 1.19.1 leaves a SQLite connection unclosed in on-disk mode
  (a ResourceWarning under `-W error`). The fix is `force_disable_check_same_thread=True`, safe
  because all index calls are serialised by a lock; it also avoids SQLite cross-thread errors.

## 3. Evaluation (SYNTHETIC)

* **Dataset:** `evaluation/datasets/fraud_dna_v1.jsonl`, sha256 `bc3158d2…`, frozen in `148c515`
  before any model run.
  * 12 scam families, each with 5 independently worded reports: 3 English, 1 Hindi, 1 Hinglish.
  * 10 legitimate and 6 ambiguous reports.
  * Split by family: 6 dev families and 6 held-out test families.
* **Rule, fixed in advance:** the lowest threshold with dev precision ≥ 0.90 (similar) or ≥ 0.98
  (strong). The result is 0.58 and 0.62.
* **Runner and result:** `evaluation/run_dna_eval.py`; result in
  `evaluation/results/fraud_dna_v1__phase8.json`, reproduced twice with identical numbers.

| Held-out test families | Precision | Recall | False pairs / unrelated pairs |
|---|---|---|---|
| similar (>= 0.58) | 0.82 | 0.52 | 7 / 535 |
| strong (>= 0.62) | 0.91 | 0.35 | 2 / 535 |

* **Per query (test):** the nearest report is the same scam method for 25 of 30 queries, another
  method for 2, and nothing passes the threshold for 3.
* **Legitimate reports:** none is matched above the threshold.
* **Below target:** precision on the test families (0.82) is below the 0.90 the rule targeted on
  dev, so the threshold does not fully generalise. I did not re-tune on the test split.
* **Weak spots:**
  * Hinglish (romanised Hindi) recall is very low: 0.11 for English–Hinglish pairs.
  * Adjacent methods are confused: UPI collect versus bank OTP calls, and sextortion versus
    loan-app morphed-photo threats.

## 4. Tests run (2026-10-19, final code)

| Suite | Result |
|---|---|
| `scripts/test_all.sh`, Python 3.13 | 17/17 packages (complaints 617 passed, 8 skipped; demo_server 214; console 292) |
| CI equivalent, Python 3.11, fresh clone, `TZ=UTC`, no torch (as on GitHub) | 16/16 packages, ruff clean, `pip check` clean (complaints 612 passed, 13 skipped) |
| Complaints on a disposable Postgres 14 | 618 passed, 7 skipped |
| Real services: complaints transcription and DNA with real Whisper and the real model | 43 passed |
| Real services: live_audio with real STT and the live local LLM | 120 passed |
| Playwright, all specs, fresh local server with the real model | 26/26 (network spec covers Fraud DNA: similar-but-not-related, opted-out and deleted excluded, citizen 403, axe at 360 px) |
| Console vitest, `tsc`, eslint, build | 292 passed; clean |
| Frozen accuracy sets v1 and v2 | identical to the recorded results |

One Playwright run failed the smoke spec while the 3.11 CI reproduction was running on the same
machine. It passed alone, and the full suite passed 26/26 on a quiet machine. I attribute that
failure to CPU contention.

## 5. Limitations

* **No real-world accuracy.** The thresholds are tied to this model and to synthetic data.
* **Hinglish** is poorly matched.
* **Matching is pairwise against the index.** Results are capped at 200 nearest candidates before
  the privacy re-check; `truncated` reports when that cap is hit.
* **Single process.** Qdrant embedded mode is one-process (file lock), so several replicas would
  need a Qdrant server; that is not built.
* **Model loading:** the first query after a start can report `loading` for about 15 s while the
  model loads.
* **Read audit:** reads are not audited, which is consistent with the rest of the service; request
  logs carry hashed user ids.
