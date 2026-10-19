# Phase 12 report: investigator case tools

Date: 2026-10-19. Branch `phase12-investigator`, on top of `16f4f0a` (Phase 10).

## 1. What was built

* **Investigation notes** (`investigation_notes`, migration 11).
  * Endpoints: `GET` and `POST /complaints/{id}/notes`, INVESTIGATOR and ADMIN only. Citizens,
    including the owner, get 403; unknown or deleted complaints 404.
  * A note is 1–4000 characters, trimmed, with control characters removed. Extra keys are a 422.
  * Notes are append-only: there is no edit or delete route.
  * Each addition is audited as `case_note_add` with the complaint and note ids and the length.
    The text never reaches the audit log or the logs.
  * Notes never appear in the complaint as the complainant sees it. They are separate from
    status-history notes, which complainants do read.
* **Case timeline:** `GET /complaints/{id}/timeline` (staff), one ordered list built from the
  tables that already record each event:
  * registration and status changes (with their notes);
  * evidence added or removed (name, type, size, SHA-256 prefix);
  * audio transcription results and analysis versions (status, level, score);
  * link reviews ("a link is not a finding of crime") and investigation notes.

  Nothing is inferred or stored twice.
* **Case report:** `GET /complaints/{id}/report` (owner or staff) returns Markdown as an attachment
  named `complaint-<id8>-report.md`.
  * Contents: every field (absent ones say "Not provided"), the description, the latest analysis
    (with "uncalibrated heuristic, not a probability"), the evidence with hashes, and the timeline.
  * A notice that indicators and leads are not findings.
  * The owner's version leaves out investigation notes, link reviews and user ids; the staff
    version includes them.
  * Every export is audited as `case_report_export` with the version. Other users get 404.
  * Table-breaking characters are escaped.
* **Console:** notes and timeline panels on the staff complaint page, and a "Download report" button
  for owners and staff.

## 2. Tests (2026-10-19)

| Suite | Result |
|---|---|
| `tests/test_case_tools.py` | 5 passed: staff-only and audit without text, validation and deleted complaints, timeline order and content, owner vs staff report and audit, escaping |
| Console `caseTools.test.tsx` | 3 passed |
| Complaints, SQLite / Postgres 14 | 630 passed, 8 skipped / 631 passed, 7 skipped |
| Playwright, all specs, fresh local server | 26/26: the owner downloads the complainant version; staff add a note, see it and the link review in the timeline, and the staff report contains the note |
| Full suites | see the push summary (Python 3.13 `test_all.sh`, Python 3.11 CI equivalent) |

## 3. Limitations

* **Format:** the report is Markdown, not PDF. It reads and prints from any viewer; no PDF engine
  is installed.
* **Audit coverage:** reads of notes and timelines are not audited (the service audits writes and
  exports).
* **Not built:** the configuration view (Phase 12 roadmap item).
