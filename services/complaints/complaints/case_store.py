"""Investigator case tools (Phase 12): investigation notes, the case timeline and the report.

* Notes: staff-only working notes, append-only. Distinct from the status-history note, which the
  complainant sees. Adding one is audited (``case_note_add``) with the complaint and note ids and
  the length, never the text.
* Timeline: one ordered list built from the tables that already record what happened to a
  complaint (creation, status changes, evidence, transcripts, analyses, link reviews, notes).
  Nothing is inferred and nothing is stored twice.
* Report: a Markdown document built from the same data for the owner or staff. The owner's
  version leaves out staff-only content (investigation notes, link reviews, user ids). Exports by
  anyone are audited (``case_report_export``).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, or_, select

from .network_store import _iso
from .schema import (
    admin_audit,
    analyses,
    complaint_evidence,
    complaint_status_history,
    complaints,
    evidence_transcripts,
    investigation_notes,
    link_reviews,
)
from .store import ComplaintStore, NotFound, new_id

NOTE_MAX = 4000
NOTE_MIN = 1


class CaseStore:
    def __init__(self, store: ComplaintStore) -> None:
        self.store = store

    def _alive(self, conn: Any, cid: str) -> None:
        row = conn.execute(select(complaints.c.deleted_at).where(complaints.c.id == cid)).first()
        if row is None or row.deleted_at is not None:
            raise NotFound()

    # ------------------------------------------------------------------ notes
    def add_note(self, cid: str, author: str, body: str, now: datetime) -> dict[str, Any]:
        with self.store._tx() as conn:
            self._alive(conn, cid)
            nid = new_id()
            conn.execute(insert(investigation_notes).values(
                id=nid, complaint_id=cid, author_user_id=author, body=body, created_at=now,
            ))  # fmt: skip
            self.store._audit(
                conn, author, "case_note_add", None, {},
                {"complaint_id": cid, "note_id": nid, "length": len(body)}, now,
            )  # fmt: skip
        return {"id": nid, "complaint_id": cid, "author_user_id": author, "body": body,
                "created_at": _iso(now)}  # fmt: skip

    def list_notes(self, cid: str, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        n = investigation_notes
        with self.store.engine.connect() as conn:
            self._alive(conn, cid)
            total = conn.execute(
                select(func.count()).select_from(n).where(n.c.complaint_id == cid)
            ).scalar_one()
            rows = conn.execute(
                select(n).where(n.c.complaint_id == cid)
                .order_by(n.c.created_at.desc(), n.c.id.desc()).limit(limit).offset(offset)
            ).all()  # fmt: skip
        return [
            {"id": r.id, "complaint_id": r.complaint_id, "author_user_id": r.author_user_id,
             "body": r.body, "created_at": _iso(r.created_at)}
            for r in rows
        ], int(total)  # fmt: skip

    # ------------------------------------------------------------------ timeline
    def timeline(self, cid: str, staff: bool) -> list[dict[str, Any]]:
        ev: list[dict[str, Any]] = []
        with self.store.engine.connect() as conn:
            self._alive(conn, cid)
            c = conn.execute(select(complaints).where(complaints.c.id == cid)).one()
            ev.append({"at": c.created_at, "kind": "complaint_created",
                       "text": "Complaint registered by the complainant.",
                       "actor_user_id": c.owner_user_id})  # fmt: skip
            au = admin_audit
            edits = select(au).where(
                au.c.action == "complaint_edit", au.c.target_user_id == c.owner_user_id
            )
            for r in conn.execute(edits):
                after = r.after if isinstance(r.after, dict) else {}
                if after.get("complaint_id") != cid:
                    continue
                fields = ", ".join(str(f).replace("_", " ") for f in after.get("fields", []))
                ev.append({"at": r.created_at, "kind": "details_edited",
                           "text": f"Details updated by the complainant: {fields}.",
                           "actor_user_id": r.actor_user_id})  # fmt: skip
            h = complaint_status_history
            for r in conn.execute(select(h).where(h.c.complaint_id == cid)):
                if r.from_status is None:
                    continue  # the initial SUBMITTED row is the creation above
                ev.append({"at": r.created_at, "kind": "status_changed",
                           "text": f"Status {r.from_status} -> {r.to_status}.",
                           "note": r.note, "actor_user_id": r.actor_user_id})  # fmt: skip
            e = complaint_evidence
            for r in conn.execute(select(e).where(e.c.complaint_id == cid)):
                ev.append({"at": r.created_at, "kind": "evidence_added",
                           "text": f"Evidence added: {r.original_filename} ({r.mime_type}, "
                                   f"{r.size_bytes} bytes, sha256 {r.sha256[:12]}...).",
                           "actor_user_id": r.uploader_user_id})  # fmt: skip
                if r.deleted_at is not None:
                    ev.append({"at": r.deleted_at, "kind": "evidence_removed",
                               "text": f"Evidence removed: {r.original_filename}."})  # fmt: skip
            t = evidence_transcripts
            for r in conn.execute(select(t).where(t.c.complaint_id == cid)):
                ev.append({"at": r.created_at, "kind": "transcript",
                           "text": f"Audio evidence transcription: {r.status}"
                                   + (f" ({r.engine} {r.model}, {r.language})" if r.engine else "")
                                   + "."})  # fmt: skip
            a = analyses
            for r in conn.execute(select(a).where(a.c.complaint_id == cid)):
                d = r.detail if isinstance(r.detail, dict) else {}
                level = d.get("risk_level") or r.risk_level
                ev.append({"at": r.created_at, "kind": "analysis",
                           "text": f"Analysis v{r.version}: {r.status}"
                                   + (f", risk {level} ({r.risk_score}/100)" if level else "")
                                   + "."})  # fmt: skip
            if staff:
                lr = link_reviews
                mine = or_(lr.c.complaint_low == cid, lr.c.complaint_high == cid)
                for r in conn.execute(select(lr).where(mine)):
                    other = r.complaint_high if r.complaint_low == cid else r.complaint_low
                    ev.append({"at": r.created_at, "kind": "link_review",
                               "text": f"Link with complaint {other[:8]} {r.decision} "
                                       f"({r.basis} basis). A link is not a finding of crime.",
                               "note": r.note, "actor_user_id": r.reviewer_user_id,
                               "related_complaint_id": other})  # fmt: skip
                n = investigation_notes
                for r in conn.execute(select(n).where(n.c.complaint_id == cid)):
                    ev.append({"at": r.created_at, "kind": "investigation_note",
                               "text": "Investigation note added.", "note": r.body,
                               "actor_user_id": r.author_user_id})  # fmt: skip
        ev.sort(key=lambda x: (x["at"], x["kind"]))
        out = []
        for x in ev:
            x = {**x, "at": _iso(x["at"])}
            if not staff:
                x.pop("actor_user_id", None)
            out.append(x)
        return out

    def audit_export(self, cid: str, actor: str, staff: bool, now: datetime) -> None:
        with self.store._tx() as conn:
            self.store._audit(conn, actor, "case_report_export", None, {},
                              {"complaint_id": cid, "version": "staff" if staff else "owner"},
                              now)  # fmt: skip


def _join(sep: str, *parts: Any) -> str | None:
    vals = [str(p) for p in parts if p not in (None, "")]
    return sep.join(vals) if vals else None


def _md_escape(s: str | None) -> str:
    return "" if s is None else str(s).replace("\r", "").replace("|", "\\|")


def render_report(detail: dict[str, Any], timeline: list[dict[str, Any]], staff: bool,
                  generated_at: datetime) -> str:  # fmt: skip
    """Markdown case report from the complaint detail view and the timeline."""
    c = detail
    L: list[str] = []
    L.append(f"# Complaint report: {_md_escape(c['title'])}")
    L.append("")
    version = "staff version (contains internal notes)" if staff else "complainant version"
    L.append(f"Complaint ID `{c['id']}` · generated {_iso(generated_at)} · {version}")
    L.append("")
    L.append("> Analysis results are indicators, not findings. Shared identifiers and similar "
             "descriptions are leads, not proof that anyone committed a crime.")  # fmt: skip
    L.append("")
    L.append("## Complaint")
    L.append("")
    L.append("| Field | Value |")
    L.append("|---|---|")
    fields = [
        ("Status", c.get("status")), ("Category", c.get("category")),
        ("Reported", c.get("reported_at")), ("Incident time", c.get("incident_at")),
        ("Amount", f"{c['amount']} {c.get('currency') or ''}".strip() if c.get("amount") else None),
        ("Payment method", c.get("payment_method")), ("UPI ID", c.get("upi_id")),
        ("UTR / reference", c.get("utr_reference")), ("Bank", c.get("bank_name")),
        ("Bank account", c.get("bank_account")), ("IFSC", c.get("ifsc")),
        ("Suspect phone", c.get("suspect_phone")), ("Suspect email", c.get("suspect_email")),
        ("Suspicious link", c.get("suspicious_url")),
        ("Social account", _join(" ", c.get("social_platform"), c.get("social_account"))),
        ("Place", _join(", ", c.get("location_text"), c.get("city"), c.get("state"))),
        ("Coordinates", _join(", ", c.get("latitude"), c.get("longitude"))),
    ]  # fmt: skip
    for k, v in fields:
        L.append(f"| {k} | {_md_escape(v) if v not in (None, '') else 'Not provided'} |")
    L.append("")
    L.append("### Description")
    L.append("")
    L.append(_md_escape(c.get("description")))
    L.append("")
    a = c.get("latest_analysis")
    L.append("## Latest analysis")
    L.append("")
    if a:
        assessment = a.get("assessment") or "not available"
        L.append(f"Version {a.get('version')}, {a.get('status')}; assessment `{assessment}`; "
                 f"risk {a.get('risk_level') or 'none'} ({a.get('risk_score')}/100, an "
                 "uncalibrated heuristic, not a probability).")  # fmt: skip
    else:
        L.append("No analysis has been run.")
    L.append("")
    L.append("## Evidence")
    L.append("")
    if c.get("evidence"):
        for e in c["evidence"]:
            name = _md_escape(e.get("original_filename"))
            L.append(f"* {name} ({e.get('mime_type')}, {e.get('size_bytes')} bytes, "
                     f"sha256 `{e.get('sha256')}`), {e.get('status')}")  # fmt: skip
    else:
        L.append("No evidence files.")
    L.append("")
    L.append("## Timeline")
    L.append("")
    for x in timeline:
        line = f"* {x['at']}: {_md_escape(x['text'])}"
        if x.get("note") and (staff or x["kind"] == "status_changed"):
            line += f" Note: {_md_escape(x['note'])}"
        if staff and x.get("actor_user_id"):
            line += f" (by `{x['actor_user_id'][:8]}`)"
        L.append(line)
    L.append("")
    return "\n".join(L)
