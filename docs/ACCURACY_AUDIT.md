# Accuracy and reliability audit

Date: 2026-10-17/18. Branch `build/implementation` at `8f4c086` (`6cc5554` on GitHub plus three
local, unreviewed Phase 9 commits). Auditor: the controller session. No application code was
changed for this audit.

**Method.** (1) Read the complaint-to-result path in code. (2) Ran hand-written probe inputs
through the *real* pipelines (call-guard rules + classifier `clf-v1`, category model
`category-v1`, risk formula; no LLM) and recorded the actual outputs. Probe texts are
**SYNTHETIC**, written for this audit; they are not real reports and not a statistical sample.
They show that specific failure modes exist; they do not measure rates. (3) Reviewed evaluation
evidence that exists in the repository.

**Headline finding.** On today's code the system can show a legitimate message as HIGH risk with
**no evidence at all**, can show common scam types as LOW or "Safe", and cannot currently state
any real-world accuracy for any capability. The causes are concrete and fixable; they are listed
below with file references.

## 1. Complaint-to-result trace

| Step | Code | Observed reliability issue |
|---|---|---|
| Input and validation | `services/complaints/complaints/validation.py` | Sound: required fields, format checks, NULLs for absent fields (tests pass) |
| Evidence extraction | `complaints/transcribe.py` (audio); OCR absent; documents absent | Transcript segment confidence (`avg_logprob`, `no_speech_prob`, available in `live_audio/stt.py:53-60`) is **not stored or shown**; garbled segments become evidence |
| URL processing | `complaints/signals.py` | Only the `suspicious_url` **field** is analysed; a link written inside the description is ignored (probe S5: `url_adjustment 0`) |
| Rules | `services/call_guard/call_guard/rules.py` | Written for digital-arrest call scripts; **no rules** for UPI collect/PIN-to-receive, job/task fees, guaranteed returns, sextortion, KYC block threats; two rules misfire on genuine bank SMS (section 3) |
| ML | `call_guard` `clf-v1` (TF-IDF+LR), `complaints/ml` `category-v1` | Both trained on simulator/synthetic data only; `clf-v1` has no metrics file; its score can contribute without any listed indicator (probe S7: score 0.40, reason `NO_RISK_INDICATORS`) |
| Risk score | `complaints/analysis.py:223-226` `CATEGORY_PRIOR`, formula in `score_components` | **The category guess is converted into a risk score** (50-70 points) with no indicator (section 2) |
| LLM explanation | `libs/svckit/svckit/llm_grounding.py` | Grounded and rejected when wrong (verified live 2026-10-17); qwen2.5:0.5b output is mostly rejected; checks are pattern-based |
| Result status | `complaints/analysis.py` | Status semantics are honest about failed stages, but `COMPLETED` + `HIGH` can be produced from a prior alone |
| Persistence and alerts | `complaints/store.py:1040-1060` | A citizen gets a `HIGH_RISK_ANALYSIS` alert for HIGH/CRITICAL, including prior-only HIGH results |
| UI | complaint header chip "Critical risk · 100/100", investigator queue risk filter, citizen alert | The headline reads as a verdict even when it rests on no evidence |

## 2. Highest-risk failure mode: risk without evidence

`risk = min(100, max(text_score, transcript_score, category_prior) + URL adjustment)` with
`CATEGORY_PRIOR = {DIGITAL_ARREST: 70, OTP_SCAM: 60, UPI_FRAUD: 55, PHISHING: 55,
INVESTMENT_SCAM: 55, BANK_FRAUD: 50, JOB_SCAM: 50, SOCIAL_MEDIA_FRAUD: 45, OTHER: 0}`
(`complaints/analysis.py:223`). Whenever `category-v1` names a scam category at or above its
abstention threshold (0.25), that category's prior becomes the score, even with zero indicators.
The category model answers *which kind of scam this would be*, not *whether this is a scam*.

Probe results (real pipeline, 2026-10-17; `ind` = indicators found):

| Probe (synthetic, legitimate) | Status | Level | Score | Category (conf) | Indicators |
|---|---|---|---|---|---|
| Fraud-awareness news about digital arrest scams | COMPLETED | **HIGH** | 70 | DIGITAL_ARREST (0.64) | none |
| Genuine customs duty notice (official India Post) | COMPLETED | **HIGH** | 70 | DIGITAL_ARREST (0.31) | none |
| Genuine bank OTP SMS ("do not share this OTP") | COMPLETED | **HIGH** | 71 | OTP_SCAM (0.31) | ISOLATION_DEMAND, CREDENTIAL_ASK (both misfires) |
| Police passport-verification notice | COMPLETED | MEDIUM | 50 | JOB_SCAM (0.25) | none |
| Family member asking for ₹2000 on UPI | COMPLETED | MEDIUM | 55 | UPI_FRAUD (0.29) | none |
| Job offer letter | COMPLETED | MEDIUM | 50 | JOB_SCAM (0.42) | none |
| Courier delivery update | COMPLETED | LOW | 0 | UNDETERMINED | none |
| Bank debit alert | COMPLETED | LOW | 15 | OTHER | URGENCY |

| Probe (synthetic, scam) | Level | Score | Where the score came from |
|---|---|---|---|
| Digital arrest (English) | CRITICAL | 100 | 5 rules + classifier (well supported) |
| Digital arrest (Hinglish) | CRITICAL | 100 | 4 rules + classifier (well supported) |
| UPI collect request "enter PIN to receive" | MEDIUM | 55 | **prior only, no indicator** |
| Job task with registration fee | MEDIUM | 50 | **prior only** |
| Guaranteed-returns investment | MEDIUM | 55 | **prior only** |
| SBI KYC block with phishing link in text | MEDIUM | 55 | prior + URGENCY; link in text not analysed |
| Sextortion threat | HIGH | 70 | prior (category wrongly DIGITAL_ARREST 0.35); classifier 40 with no indicator |
| Victim narrative of a digital arrest | HIGH | 70 | prior; classifier 40 with no indicator |
| OTP theft (Hinglish) | MEDIUM | 60 | prior + URGENCY |
| Empty / 2-word input | INSUFFICIENT_INPUT | none | correct |

So the same number (for example HIGH 70) can mean "five independent scam indicators" or "the
category model guessed a scam type with confidence 0.31". The UI cannot tell them apart.

## 3. Other concrete failure modes found

1. **Simulator citizen checker says "Safe" for scams.** `services/citizen_shield/policy.py:39`
   maps score < 0.35 to `safe`; the console shows a green "Safe" heading
   (`web-console/src/lib/i18n/citizen.ts:29`). Probes: UPI collect, job task, investment →
   score 0.00 → **"Safe"**; KYC phishing 0.15 → **"Safe"**; genuine bank OTP SMS 0.71 →
   **"Likely scam"**. Empty input also returns `safe` (`assess.py:70`).
2. **Rule misfires on genuine bank messages.** "Do not share this OTP with anyone" triggers
   ISOLATION_DEMAND (weight 0.55) and CREDENTIAL_ASK (0.35) (`call_guard/rules.py`); the
   existing advisory-clause filter (`rules.py:67`, `:585`) does not catch this phrasing.
3. **Live Protection flags conversations *about* scams.** A spoken discussion of scam news
   ("scammers… say you are under digital arrest… transfer to a safe account. Never share your
   OTP") → **HIGH 69** (`SAFE_ACCOUNT_TRANSFER`); a family request for book money → MEDIUM 40
   (`MONEY_TRANSFER_INSTRUCTION`) (`live_audio/detect.py`). No handling of reported speech.
4. **Unexplained score contributions.** The classifier can raise the score (0.40) while the only
   reason is `NO_RISK_INDICATORS`; the user sees a number with no evidence.
5. **Transcripts without reliability.** Hindi via Whisper `base` is romanised and partly garbled
   (real test: "Mekh Police se boh raha hoon…"), yet its segments are scored and cited as
   evidence with no confidence or "low-quality transcript" marker. No word error rate has been
   measured for any language.
6. **Links inside text are ignored** by URL analysis (only the URL field is used); the result
   does not say so.
7. **Category confidence is uncalibrated** and trained on synthetic templates; held-out
   macro-F1 0.715 is on synthetic data only. In probes it named DIGITAL_ARREST for a customs
   notice and for sextortion.
8. **Prior-only HIGH results trigger citizen alerts** (`store.py:1045`).
9. **Phase 9 network code is unreviewed** (3 local commits, frontend unfinished): identifier
   normalisation collisions and opt-out enforcement are untested by the controller.
10. **CI flakiness.** Run #35 failed on a flaky test (unidentified) on the same commit that passed
    in run #34.

## 4. What is genuinely working (verified)

| Capability | Evidence |
|---|---|
| Authorisation and roles (citizen/investigator/admin, allowlist, ownership 404s) | Unit/integration tests; browser checks of sign-in |
| Complaint intake with optional fields stored as NULL | Tests; by hand |
| Evidence upload validation (content sniffing, size limits, honest per-file result) | Tests |
| Failure semantics for missing input and failed stages (INSUFFICIENT_INPUT, PARTIAL, no LOW when a core stage failed) | Tests; probes E1/E2 |
| Detection of classic digital-arrest scripts in English and Hinglish | Probes S1/S6; real Whisper tests on TTS audio |
| Provenance per indicator (rule/ML/LLM, method, evidence span or audio time) | Tests; by hand |
| LLM grounding rejects the observed role confusion and falls back to rules | Tests; live qwen2.5:0.5b runs |
| Consent-gated microphone capture | Scripted Chromium check |
| CI on Python 3.11 | GitHub runs #30-#34 green |

## 5. Implemented but not validated (must be labelled EXPERIMENTAL)

* Risk score and bands (no labelled real data; currently mixes evidence with a category prior).
* Category model `category-v1` (synthetic training and test data).
* Script classifier `clf-v1` (simulator/authored data; no metrics file).
* Rules outside digital-arrest scripts (largely absent).
* URL heuristics (offline lexical; no labelled phishing set).
* Audio transcription accuracy (no WER; only clean TTS audio tested; Hindi quality poor).
* Live Protection risk levels (no labelled conversations; false positives shown above).
* LLM explanations (small model; pattern-based checks).
* Simulator citizen checker verdicts and all simulator benchmark numbers.
* Phase 9 complaint network (unreviewed, unfinished).
* OCR and document extraction: not implemented (reported as unavailable, correctly).

## 6. What can and cannot be validated with available data

| Capability | Can validate locally | Needs |
|---|---|---|
| Rules | Yes: deterministic tests plus a labelled synthetic set with hard negatives | Real messages for real-world rates |
| Risk score (as evidence strength) | Yes: invariants (no non-LOW score without an indicator; monotonic with independent indicators; no double counting) | Real labelled outcomes for calibration |
| Category model | Partially: synthetic held-out plus probes | Real labelled complaints |
| Transcription | Yes for WER on TTS audio with known scripts (en, hi via macOS voices) and on self-recorded clips with written transcripts | Real call audio for representative WER |
| URL heuristics | Yes: official-domain allowlist checks, synthetic look-alikes | A licensed phishing URL list for rates |
| Network links | Yes, fully: deterministic identifier normalisation and privacy tests | — |
| Live Protection | Yes: scripted conversations (scam, awareness, family, bank) | Real conversations |
| Public datasets | Candidate: UCI SMS Spam Collection (CC BY 4.0, English, spam ≠ fraud; only as a hard-negative/coverage aid) | Licence and relevance review before use |

## 7. Top 10 fixes (ranked by impact, then effort)

Status 2026-10-18: fixes **1, 2, 3, 4, 5, 6 and 8 done** (local, not pushed): batch 1 in
[reports/ACCURACY_BATCH_1_REPORT.md](reports/ACCURACY_BATCH_1_REPORT.md), batch 2 in
[reports/ACCURACY_BATCH_2_REPORT.md](reports/ACCURACY_BATCH_2_REPORT.md). Fixes 7, 9 and 10 open.
Both citizen checkers now use explained-only scoring.

| # | Fix | Failure mode addressed | Effort | Acceptance criteria |
|---|---|---|---|---|
| 1 | **Remove the category prior from the risk score**; the score reflects evidence-backed indicators only; with no indicator the result is "no scam indicators found" (LOW) or `INSUFFICIENT_EVIDENCE` when the text is short/ambiguous; category shown separately as a model guess | Section 2 | S | On the probe/eval set no MEDIUM/HIGH/CRITICAL without ≥1 evidence-backed indicator; every non-zero score component listed with evidence |
| 2 | **Versioned evaluation set v1 + harness** (synthetic, labelled; scams per category, hard negatives, EN/HI/Hinglish, empty/contradictory, audio with known transcripts, unrelated vs linked complaint pairs); fixed splits; reports precision, recall, F1, FPR, FNR, per class, per language, confusion matrix; results committed; CI regression thresholds | No measurable accuracy today | M | `make eval` reproduces identical numbers; results file versioned; thresholds enforced in CI |
| 3 | **Classifier contribution must be explained or not counted**; no `NO_RISK_INDICATORS` with a non-zero score | §3.4 | S | Invariant test across the eval set |
| 4 | **Citizen checker wording**: "No scam indicators found (not a guarantee)" instead of "Safe"; neutral styling; blank input = insufficient | §3.1 | S | No "Safe" string in API or UI; tests for blank and no-indicator inputs |
| 5 | **Fix the two rule misfires** on genuine bank SMS (advisory/negated phrasing) | §3.2 | S-M | Hard-negative bank SMS set: zero ISOLATION_DEMAND/CREDENTIAL_ASK hits; digital-arrest recall unchanged on the eval set |
| 6 | **Rule coverage for common non-digital-arrest scams** (UPI collect/PIN-to-receive, task/registration fee, guaranteed returns, KYC block threat, sextortion) and URL heuristics on links found in text (labelled "link mentioned in text") | §2, §3.6 | M | Per-category recall reported on the eval set; hard-negative FPR reported; no regression on existing tests |
| 7 | **Transcript reliability**: store per-segment `avg_logprob`/`no_speech_prob`; mark low-confidence segments "unreliable", exclude them from scoring or cap their weight; show it in the UI; measure WER on the audio eval set | §3.5 | M | WER reported per language; low-confidence segments never sole evidence for HIGH; UI shows the marker |
| 8 | **Alert and headline gating**: citizen alerts and the header chip only when evidence-backed indicators exist; wording "indicators found", not a verdict | §3.8 | S | Tests: prior-only or no-indicator results produce no alert |
| 9 | **Live Protection reported-speech/awareness handling** and a minimum of two independent indicators for HIGH | §3.3 | M | Scripted conversation set: awareness and family conversations stay LOW; scam scripts still HIGH+ |
| 10 | **Finish Phase 9 under these criteria** (no edge without a shared identifier; normalisation collision tests; opt-out everywhere; precision of links on the labelled pair set = 1.0 for observed links) | §3.9 | M | Labelled pair set: zero false observed links; opt-out tests across all endpoints |

Also tracked: CI flaky test (§3.10); the 7 unformatted files; Phase 8/10 stay unstarted until
fixes 1-4 land.

## 8. Metrics that will demonstrate improvement

Before/after on the fixed evaluation set v1: false-positive rate on hard negatives (target: no
MEDIUM+ without indicators; FPR reported, not assumed), recall per scam category, precision/recall
of the "likely scam" band, confusion matrix of the category model, WER per language, link
precision for the network, and the count of results whose score has no supporting indicator
(target 0). All reported as measured on synthetic data until real labelled data exists.

## 9. Must remain EXPERIMENTAL until validated on real data

Risk bands and score semantics; category model; script classifier; URL heuristics; Hindi/Hinglish
transcription; Live Protection levels; LLM explanations; complaint network inferred links;
simulator benchmark numbers.
