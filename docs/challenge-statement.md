# Challenge statement

**AI for Digital Public Safety: Defeating Counterfeiting, Fraud & Digital Arrest Scams**

Source: the challenge statement supplied with the project brief; not independently verified.
The figures below are quoted from that brief and are not derived from anything in this
repository. This page paraphrases the brief briefly; it is not a verbatim reproduction.

## Problem context (as stated in the brief)

* 1.14 million cybercrime complaints were reported in 2023, up 60% from 2022.
* Digital-arrest scam losses were over Rs 1,776 crore in the first nine months of 2024.
* The RBI Annual Report 2025 flagged record seizures of fake Indian currency notes (FICN).

The brief lists five example problem areas. Their wording is **not reproduced here**: the text
of those five items was not available in this repository when this page was written, and they
are not guessed. Paste them from the brief if this page is used outside the repository.

## Evaluation focus (as stated in the brief)

* Counterfeit detection accuracy.
* Digital-arrest detection precision and recall.
* Fraud-network lead time before mass victimisation.
* False-positive rate for citizen-facing tools, which must be very low.
* Auditability for legal admissibility.

## Judging weights

| Criterion | Weight |
|---|---|
| Innovation | 25 |
| Business Impact | 25 |
| Technical Excellence | 20 |
| Scalability | 15 |
| User Experience | 15 |

## Deliverables

| Deliverable | Where |
|---|---|
| Working prototype | this repository, `make demo` ([README](../README.md#60-second-quickstart)) |
| Architecture diagram | [architecture.md](architecture.md) |
| Presentation deck | [deck/ScamShield-Immune.pptx](deck/ScamShield-Immune.pptx), [PDF](deck/ScamShield-Immune.pdf) |
| Demo video | [demo/hero-run.webm](demo/hero-run.webm) (no audio; script for a voice-over in [demo-script.md](demo-script.md)) |

## How this repository maps, and what was deliberately dropped

| Evaluation focus | Status | Where | Note |
|---|---|---|---|
| Counterfeit detection accuracy | **Dropped by decision** (design spec, section 1) | n/a | Counterfeit currency detection is not built and no figure is reported. The scope went to digital-arrest and mule-network scams. |
| Digital-arrest precision and recall | Addressed, simulator only | [benchmark_report.md](benchmark_report.md) | Measured against simulator ground truth, with confidence intervals. Not a real-world figure. |
| Lead time before mass victimisation | Addressed, simulator only | [benchmark_report.md](benchmark_report.md) (Lead time) | Few campaigns, so the uncertainty is wide. |
| False-positive rate (citizen-facing) | Addressed, simulator only | [benchmark_report.md](benchmark_report.md) (per-rail); `services/citizen_shield` | Held-benign and step-up rates reported per rail; NEFT is benign-traffic only. |
| Auditability for legal admissibility | Tamper-evidence and offline verification built; **no admissibility claim** | [audit-trail.md](audit-trail.md) | Packages are verifiable offline; the Section 63 certificate is a template for the operator and counsel. |

Digital-arrest scams, mule rings, geographic hotspots and citizen-facing checks map to
`services/call_guard`, `txn_guard`, `antibody_hub`, `graph_twin`, `geo_intel` and
`citizen_shield`; see the service table in the [README](../README.md#services). Criterion-level
evidence is in [judging-map.md](judging-map.md).
