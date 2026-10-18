# Accuracy batch 2 report

Date: 2026-10-18. Branch `accuracy-batch1` (accuracy work only, on top of the pushed `6cc5554`;
the paused Phase 9 commits are not on this branch). Final code commit: `74dbf9b`. Not pushed.

> **Read this first.** Both evaluation sets are SYNTHETIC, hand-written, and were written by the
> same author as the rules (v2 was frozen *before* any batch 2 rule was written, but with the
> planned rule families in mind). Perfect scores on them show the rules are consistent with the
> cases they were designed around; they are **regression evidence, not real-world accuracy**. The
> more informative signals are the separately phrased rule tests (section 4) and the adversarial
> probes that found real mismatches (section 3). No real-world precision or recall is known.

## 1. Scope

Fix 5 (genuine bank OTP false positive), fix 6 (missing scam categories and links inside text) and
fix 2 (evaluation set v2). Fixes 7 (transcript reliability) and 9 (Live Protection) are **not**
part of this batch.

| Commit | Change |
|---|---|
| `b811963` | SYNTHETIC dataset v2 (73 cases, sha256 `d2966d6e…`) frozen before any rule; runner metrics for v2; v2 baseline |
| `a19b69e` | call-guard rules-v2: protective OTP warnings are advisory unless the sentence carves out an exception ("except me", "but me", "only to me"); Hinglish OTP asks; six message-level rule classes (below) |
| `ea08e37` | complaints: offline checks for links written in the title/description; regression guards for v1 and v2 |
| `60f97d9` | citizen checkers: reason texts for the new codes (en/hi/ta/bn); tests that both checkers get the new rules |
| `f21ecde` | console: labels for the new codes; evidence of links in text shown as "extracted from the text, not opened or verified" |
| `74dbf9b` | rules narrowed after the adversarial review (section 3) |

### New rule classes (call-guard `rules-v2`)

| Code | Weight | Fires when | Evidence reason |
|---|---|---|---|
| UPI_PIN_TO_RECEIVE | 0.65 | in one sentence: enter a (UPI) PIN or approve a (collect) request **and** a receive-money word; not when the sentence is negated ("never need a PIN") | receiving money never needs a PIN |
| ADVANCE_FEE_DEMAND | 0.60 | a job-specific fee (joining/activation/onboarding/training/kit) or a fee with a start/unlock purpose, with a job/task context in the same or adjacent sentence; or a fee/tax demanded to withdraw/release earnings | asks for money to start work or to release earnings |
| GUARANTEED_RETURNS | 0.60 | guaranteed/assured returns **and** an unrealistic term (≥20 %, double/triple, returns per day), or "double your money in N days"; not when negated | promises unrealistic guaranteed returns |
| SEXTORTION_THREAT | 0.65 | private media **and** a non-negated threat to spread it **and** a condition or payment demand | threatens to spread private material unless paid |
| ACCOUNT_BLOCK_THREAT | 0.45 | a block/suspend threat in one sentence **and** a KYC/update/verify/link demand; not when the message sends the customer to a branch, store or office | threatens a block unless KYC is updated or a link used |
| REPORTED_SCAM_ENCOUNTER | 0.60 | first-person harm (I transferred/paid…, kept me on a call, debited) **and** a known tactic (digital arrest, safe/RBI account, drugs in a parcel, screen recording, PIN to receive…) | describes money or details taken through a known scam method |

Each class is below 0.7 on its own, so HIGH or CRITICAL still needs two independent cues. The
message-level classes skip news, fiction and awareness sentences (and sentences that mention
scams/scammers/fraudsters) and run after advisory clauses are removed. They do **not** skip
relayed speech, so a victim's own account can count.

### Links inside the text

`complaints.signals.extract_urls` finds `http(s)://`, `www.` and disguised (`hxxp`, `[.]`)
links in the title and description, strips trailing punctuation, keeps the original span and
offsets as evidence, caps at 5 links and 2,048 characters, and never resolves or fetches anything
(a test fails on any DNS or socket call). Each link gets the existing offline lexical checks;
results carry `extracted: true, verified: false, fetched: false`. An unparseable link is reported
as such and adds nothing to the score. The citizen checkers do not analyse links (unchanged).

## 2. Results (SYNTHETIC sets; flagged = MEDIUM or higher)

| Set | Code | Scams flagged | Legitimate flagged | Precision | Recall | FPR | FNR |
|---|---|---|---|---|---|---|---|
| v1 baseline (before batch 1) | `89f2aff` | 10/10 | 6/8 | 0.625 | 1.0 | 0.75 | 0.0 |
| v1 after batch 1 review | `f8f4162` | 2/10 | 1/8 | 0.667 | 0.2 | 0.125 | 0.8 |
| **v1 final** | `74dbf9b` | **10/10** | **0/8** | 1.0 | 1.0 | 0.0 | 0.0 |
| v2 before batch 2 | `b62260d` | 8/36 | 0/28 | 1.0 | 0.222 | 0.0 | 0.778 |
| **v2 final** | `74dbf9b` | **36/36** | **0/28** | 1.0 | 1.0 | 0.0 | 0.0 |

Denominators: scam and legitimate cases only; ambiguous and insufficient cases are reported
separately. The v1 baseline recall of 1.0 came from the category prior removed in batch 1 (no
evidence); the final 10/10 is backed by rule-based indicators in every case.

At HIGH or above (two independent cues): v1 3/10 scams, 0/8 legitimate; v2 12/36 scams, 0/28
legitimate. Most single-pattern scams are MEDIUM ("moderate scam indicators") by design.

**v2 per category (scams flagged / total):** digital arrest 6/6, investment 5/5, job 6/6, OTP 3/3,
phishing/KYC 6/6, sextortion 4/4, UPI 6/6. **Per language:** English 25/25 scams and 0/23
legitimate flagged; Hindi 4/4 and 0/2; Hinglish 7/7 and 0/3. **Per kind:** awareness 0/7, bank SMS
0/11, ordinary conversation 0/3, genuine job 0/3 legitimate flagged; narratives 6/6 and links 8/8
scams flagged, 0/4 genuine links flagged.

**v2 confusion (expected label → result):** scam → HIGH/CRITICAL 12, MEDIUM 24; legitimate → no
indicators 28; ambiguous → no indicators 4, LOW with indicators 1 (none HIGH); insufficient →
insufficient 4.

Other measures (both sets): flags without a rule-based indicator 0; warning verdicts without a
listed reason 0; console and Docker citizen checkers disagree on 0 cases; links found in text with URL findings on
all 8 v2 scam messages that contain a link, URL findings on 0 of 4 genuine links.

Result files: `evaluation/results/accuracy_probes_v1__final-batch2.json`,
`accuracy_probes_v2__final-batch2.json`.

## 3. Adversarial review of batch 2 (found and fixed in `74dbf9b`)

Hand-written probes outside both datasets found these mismatches in the first rules-v2 version:

| Probe | Problem | Fix |
|---|---|---|
| "PPF gives assured returns and interest is compounded daily." | GUARANTEED_RETURNS fired ("daily" alone counted as unrealistic) | "daily" counts only when attached to returns/profit |
| "I applied for a job yesterday. Also, I need to pay the security deposit for my new flat first." | ADVANCE_FEE_DEMAND across unrelated sentences | job-specific fee or stated purpose, job context adjacent |
| "Job fair on Sunday. Registration fee Rs 100 per candidate at the venue." | fired on a possibly genuine fee | generic registration/security fees alone no longer qualify |
| "…Pay the photographer Rs 5,000 or he won't send it to the family." | SEXTORTION_THREAT on a negated leak | negated leaks ignored |
| "Don't share the OTP with anyone but me." (scam) | **missed**: clause split at "but" hid the exception | exception guard checks the whole sentence |
| "…which is a secure account with high interest." | REPORTED_SCAM_ENCOUNTER tactic too generic | "secure account" removed |

All are now regression tests (`services/call_guard/tests/test_rules_v2.py`).

## 4. Tests actually run on the final commit `74dbf9b` (2026-10-18)

| Suite | Result | Kind |
|---|---|---|
| `scripts/test_all.sh`, Python 3.13 (branch code loaded first) | 17/17 packages: contracts 44, svckit 296, antibody_hub 160 (5 skipped), call_guard 203, citizen_shield 77, complaints 525 (7 skipped), demo_server 213, evidence_ledger 184 (7 skipped), gateway 75, geo_intel 64 (1 skipped), graph_twin 122 (3 skipped), live_audio 115 (5 skipped), sim_engine 56, txn_guard 376, e2e audit 32, e2e bench 38, console vitest 271 | unit/integration (mocks where stated) |
| CI test job on Python 3.11 from a fresh clone of `accuracy-batch1` | 16/16 packages, `pip check` clean (complaints 520, 12 skipped: PyAV and optional STT not installed in CI, as in GitHub) | CI-equivalent |
| Complaints on a disposable Postgres 14 | 526 passed, 6 skipped | integration, real database |
| Playwright, all specs, against a server started from this branch (real faster-whisper) | 23/23 | browser, real backend |
| Opt-in real services: complaints real Whisper (4) + live local LLM qwen2.5:0.5b (2); live_audio full suite with real STT and live LLM enabled | 6/6; 120/120 | real services |
| Console `tsc --noEmit`, eslint, `vite build` | clean; main chunk 103.3 KB gzip | static |
| Frozen v1 and v2 evaluations | as in section 2 | SYNTHETIC regression |
| Simulator regression (e2e bench, e2e audit, txn_guard) | passed (in the rows above) | simulator |

Skipped and why: Postgres-only tests on SQLite runs; opt-in real-STT and live-LLM tests in the
default runs (run separately above); PyAV decoding tests in the Python 3.11 CI environment. **Not
run:** a Docker build of the stack (no Docker daemon); the Docker citizen checker was tested
through its real code path in process (citizen-shield → HTTP → call-guard app), not in a
container.

## 5. Remaining known limitations and failure cases

* **No real-world accuracy is known.** All measurements are on SYNTHETIC cases written by the rule
  author.
* The older digital-arrest rule SAFE_ACCOUNT_TRANSFER fires on "I transferred money to my savings
  account, which is a secure account" (MEDIUM 69): its "secure" synonym is broad; not changed in
  this batch.
* Rules are pattern-based: rephrasings outside the patterns are missed (2 of 52 separately
  phrased tests failed before the patterns were widened; more such gaps certainly exist).
  Bengali and Tamil scam phrasing has no new-rule coverage.
* Most single-pattern scams are MEDIUM, not HIGH; this is intended (two independent cues for HIGH)
  but means citizen alerts (HIGH/CRITICAL with a rule-based indicator) fire for fewer cases.
* ACCOUNT_BLOCK_THREAT would fire on a genuine notice that threatens a block and asks for an
  in-app KYC update without directing to a branch (expected from the rule's design; not measured).
* Links are only checked lexically and offline; no reputation or destination check exists.
* The new hi/ta/bn reason texts and the console headings from batch 1 need a native-speaker review.
* Not in this batch: transcript reliability markers and WER (fix 7); Live Protection
  false positives on conversations about scams (fix 9). Live Protection also now applies the
  rules-v2 classes to transcripts (tests pass), which has not been evaluated on conversations.
