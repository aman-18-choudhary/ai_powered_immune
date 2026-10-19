"""Investigator case tools (Phase 12): notes, timeline, report export. Synthetic TEST DATA only."""

import pytest
import sqlalchemy as sa

from complaints.schema import admin_audit

DESC = "Synthetic test complaint: a caller asked me to approve a UPI collect request."


@pytest.fixture
def mk(client, tok):
    async def _mk(who: str = "a", **fields) -> str:
        body = {"category": "UPI_FRAUD", "title": f"Report {who}", "description": DESC,
                "upi_id": "mule.one@ybl", **fields}  # fmt: skip
        r = await client.post("/complaints", json=body, headers=citizen(tok, who))
        assert r.status_code == 201, r.text
        return r.json()["id"]

    return _mk


def citizen(tok, who):
    return tok("a", sub=f"user_{who}", email=f"citizen.{who}@example.org")


def audits(app, action):
    with app.state.store.engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(
            sa.select(admin_audit).where(admin_audit.c.action == action))]  # fmt: skip


async def test_notes_are_staff_only_append_only_and_audited_without_text(app, client, inv, tok, mk):
    a = await mk("a")
    r = await client.post(f"/complaints/{a}/notes", json={"body": "  Called the bank; hold placed.\x07 "},
                          headers=inv)  # fmt: skip
    assert r.status_code == 201, r.text
    note = r.json()
    assert note["body"] == "Called the bank; hold placed."  # trimmed, control character removed
    await client.post(f"/complaints/{a}/notes", json={"body": "Second note"}, headers=inv)
    listed = (await client.get(f"/complaints/{a}/notes", headers=inv)).json()
    assert listed["total"] == 2 and [n["body"] for n in listed["items"]] == [
        "Second note", "Called the bank; hold placed."]  # newest first  # fmt: skip
    # the complainant never sees them, not even through the complaint itself
    owner = citizen(tok, "a")
    assert (await client.get(f"/complaints/{a}/notes", headers=owner)).status_code == 403
    assert (await client.post(f"/complaints/{a}/notes", json={"body": "x"}, headers=owner)).status_code == 403
    detail = (await client.get(f"/complaints/{a}", headers=owner)).text
    assert "hold placed" not in detail and "Second note" not in detail
    rows = audits(app, "case_note_add")
    assert len(rows) == 2 and rows[0]["after"]["complaint_id"] == a
    assert "hold placed" not in str(rows) and "Second note" not in str(rows)


async def test_note_validation(client, inv, tok, mk):
    a = await mk("a")
    for body in ({"body": ""}, {"body": "   "}, {"body": "x" * 4001}, {"body": "ok", "extra": 1}, {}):
        r = await client.post(f"/complaints/{a}/notes", json=body, headers=inv)
        assert r.status_code == 422, body
    assert (await client.post("/complaints/nope/notes", json={"body": "x"}, headers=inv)).status_code == 404
    r = await client.delete(f"/complaints/{a}", headers=citizen(tok, "a"))
    assert r.status_code == 204
    assert (await client.post(f"/complaints/{a}/notes", json={"body": "x"}, headers=inv)).status_code == 404
    assert (await client.get(f"/complaints/{a}/notes", headers=inv)).status_code == 404


async def test_timeline_merges_the_case_history_in_order(client, inv, tok, mk):
    a = await mk("a")
    b = await mk("b")
    r = await client.patch(f"/complaints/{a}/status", json={"status": "UNDER_REVIEW", "note": "Picked up"},
                           headers=inv)  # fmt: skip
    assert r.status_code == 200, r.text
    r = await client.post("/complaint-links/review", headers=inv, json={
        "complaint_a": a, "complaint_b": b, "decision": "confirmed", "note": "same UPI"})  # fmt: skip
    assert r.status_code == 201, r.text
    await client.post(f"/complaints/{a}/notes", json={"body": "Asked for statement"}, headers=inv)
    tl = (await client.get(f"/complaints/{a}/timeline", headers=inv)).json()["items"]
    kinds = [x["kind"] for x in tl]
    assert kinds == ["complaint_created", "status_changed", "link_review", "investigation_note"]
    assert tl[1]["note"] == "Picked up" and "SUBMITTED -> UNDER_REVIEW" in tl[1]["text"]
    assert "not a finding of crime" in tl[2]["text"] and tl[2]["related_complaint_id"] == b
    assert all(x["at"] for x in tl) and [x["at"] for x in tl] == sorted(x["at"] for x in tl)
    assert (await client.get(f"/complaints/{a}/timeline", headers=citizen(tok, "a"))).status_code == 403


async def test_report_owner_and_staff_versions(app, client, inv, tok, mk):
    a = await mk("a", city="Pune", state="Maharashtra")
    await mk("b")
    await client.patch(f"/complaints/{a}/status", json={"status": "UNDER_REVIEW", "note": "Picked up"},
                       headers=inv)  # fmt: skip
    await client.post(f"/complaints/{a}/notes", json={"body": "Internal: suspect known"}, headers=inv)
    owner = await client.get(f"/complaints/{a}/report", headers=citizen(tok, "a"))
    assert owner.status_code == 200
    assert owner.headers["content-type"].startswith("text/markdown")
    assert owner.headers["content-disposition"] == f'attachment; filename="complaint-{a[:8]}-report.md"'
    assert owner.headers["cache-control"] == "no-store"
    md = owner.text
    assert md.startswith("# Complaint report: Report a") and "complainant version" in md
    assert "| UPI ID | mule.one@ybl |" in md and "| Place | Pune, Maharashtra |" in md
    assert "| IFSC | Not provided |" in md and DESC in md
    assert "Picked up" in md  # status-history notes are the complainant's to read
    assert "Internal: suspect known" not in md and "(by `" not in md
    assert "not proof that anyone committed a crime" in md
    staff = (await client.get(f"/complaints/{a}/report", headers=inv)).text
    assert "staff version" in staff and "Internal: suspect known" in staff and "(by `" in staff
    # someone else's complaint: 404, like the complaint itself
    assert (await client.get(f"/complaints/{a}/report", headers=citizen(tok, "b"))).status_code == 404
    rows = audits(app, "case_report_export")
    assert [r["after"]["version"] for r in rows] == ["owner", "staff"]


async def test_report_escapes_table_breaking_characters(client, tok, mk):
    a = await mk("a", title="Pipe | in title", bank_name="Bank | Co")
    md = (await client.get(f"/complaints/{a}/report", headers=citizen(tok, "a"))).text
    assert "| Bank | Bank \\| Co |" in md
