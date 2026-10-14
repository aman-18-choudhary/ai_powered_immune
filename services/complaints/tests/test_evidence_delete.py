"""DELETE /complaints/{id}/evidence/{evidence_id}: owner while SUBMITTED, or ADMIN."""

from conftest import PDF, PNG, create


async def up(client, headers, cid, name="shot.png", data=PNG):
    r = await client.post(
        f"/complaints/{cid}/evidence", files={"file": (name, data, "image/png")}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()


def _files(settings):
    return [p for p in settings.evidence_dir.rglob("*") if p.is_file() and ".tmp" not in p.parts]


async def test_owner_removes_evidence_while_submitted(client, tok, settings):
    c = await create(client, tok("a"))
    ev = await up(client, tok("a"), c["id"])
    keep = await up(client, tok("a"), c["id"], "bill.pdf", PDF)
    assert len(_files(settings)) == 2
    r = await client.delete(f"/complaints/{c['id']}/evidence/{ev['id']}", headers=tok("a"))
    assert r.status_code == 200, r.text
    assert r.json() == {"id": ev["id"], "status": "deleted", "file_removed": True}
    assert len(_files(settings)) == 1
    # excluded from lists, detail, download and the evidence filter
    items = (await client.get(f"/complaints/{c['id']}/evidence", headers=tok("a"))).json()["items"]
    assert [i["id"] for i in items] == [keep["id"]]
    detail = (await client.get(f"/complaints/{c['id']}", headers=tok("a"))).json()
    assert [e["id"] for e in detail["evidence"]] == [keep["id"]]
    dl = await client.get(f"/complaints/{c['id']}/evidence/{ev['id']}/download", headers=tok("a"))
    assert dl.status_code == 404
    lst = (await client.get("/complaints", headers=tok("a"))).json()["items"]
    assert lst[0]["evidence_count"] == 1
    # a second delete of the same id is a 404
    r2 = await client.delete(f"/complaints/{c['id']}/evidence/{ev['id']}", headers=tok("a"))
    assert r2.status_code == 404


async def test_removed_bytes_can_be_uploaded_again(client, tok):
    c = await create(client, tok("a"))
    ev = await up(client, tok("a"), c["id"])
    await client.delete(f"/complaints/{c['id']}/evidence/{ev['id']}", headers=tok("a"))
    again = await up(client, tok("a"), c["id"])
    assert again["id"] != ev["id"] and again["status"] == "stored"


async def test_foreign_ids_are_404_never_403(client, tok, inv):
    a = await create(client, tok("a"))
    b = await create(client, tok("b"))
    ev_a = await up(client, tok("a"), a["id"])
    ev_b = await up(client, tok("b"), b["id"])
    # another citizen: 404 (identical to a missing id)
    r = await client.delete(f"/complaints/{a['id']}/evidence/{ev_a['id']}", headers=tok("b"))
    assert r.status_code == 404 and r.json()["error"] == "not_found"
    # evidence of another complaint addressed through one's own complaint: 404
    r = await client.delete(f"/complaints/{a['id']}/evidence/{ev_b['id']}", headers=tok("a"))
    assert r.status_code == 404
    # unknown / malformed ids
    for path in (f"/complaints/{a['id']}/evidence/nope", f"/complaints/nope/evidence/{ev_a['id']}",
                 f"/complaints/{a['id']}/evidence/" + "x" * 200):  # fmt: skip
        assert (await client.delete(path, headers=tok("a"))).status_code == 404
    # still stored
    assert (
        len((await client.get(f"/complaints/{a['id']}/evidence", headers=tok("a"))).json()["items"])
        == 1
    )


async def test_investigator_cannot_remove(client, tok, inv):
    c = await create(client, tok("a"))
    ev = await up(client, tok("a"), c["id"])
    r = await client.delete(f"/complaints/{c['id']}/evidence/{ev['id']}", headers=inv)
    assert r.status_code == 403 and r.json()["error"] == "forbidden_role"


async def test_owner_cannot_remove_after_review_started(client, tok, inv):
    c = await create(client, tok("a"))
    ev = await up(client, tok("a"), c["id"])
    r = await client.patch(f"/complaints/{c['id']}/status", json={"status": "UNDER_REVIEW"},
                           headers=inv)  # fmt: skip
    assert r.status_code == 200
    r = await client.delete(f"/complaints/{c['id']}/evidence/{ev['id']}", headers=tok("a"))
    assert r.status_code == 409 and r.json()["error"] == "evidence_not_deletable"
    items = (await client.get(f"/complaints/{c['id']}/evidence", headers=tok("a"))).json()["items"]
    assert len(items) == 1


async def test_admin_removes_any_time_owner_is_alerted_and_audited(
    client, tok, inv, admin, settings
):
    c = await create(client, tok("a"))
    ev = await up(client, tok("a"), c["id"], "my-private-name.png")
    await client.patch(f"/complaints/{c['id']}/status", json={"status": "UNDER_REVIEW"},
                       headers=inv)  # fmt: skip
    r = await client.delete(f"/complaints/{c['id']}/evidence/{ev['id']}", headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "deleted"
    assert _files(settings) == []
    alerts = (await client.get("/alerts", headers=tok("a"))).json()["items"]
    assert any(a["kind"] == "EVIDENCE_REMOVED" for a in alerts)
    audit = (await client.get("/admin/audit", params={"limit": 100}, headers=admin)).json()["items"]
    rows = [a for a in audit if a["action"] == "evidence_delete"]
    assert len(rows) == 1
    assert rows[0]["before"] == {
        "complaint_id": c["id"],
        "evidence_id": ev["id"],
        "status": "stored",
    }
    assert rows[0]["after"] == {"status": "deleted"}
    assert "my-private-name" not in str(rows[0])


async def test_owner_delete_audited_without_alert(client, tok, admin):
    c = await create(client, tok("a"))
    ev = await up(client, tok("a"), c["id"])
    await client.delete(f"/complaints/{c['id']}/evidence/{ev['id']}", headers=tok("a"))
    alerts = (await client.get("/alerts", headers=tok("a"))).json()["items"]
    assert not any(a["kind"] == "EVIDENCE_REMOVED" for a in alerts)
    audit = (await client.get("/admin/audit", params={"limit": 100}, headers=admin)).json()["items"]
    assert [a["action"] for a in audit].count("evidence_delete") == 1


async def test_missing_file_is_reported_not_faked(client, tok, settings):
    c = await create(client, tok("a"))
    ev = await up(client, tok("a"), c["id"])
    for p in _files(settings):
        p.unlink()
    r = await client.delete(f"/complaints/{c['id']}/evidence/{ev['id']}", headers=tok("a"))
    # the file was already gone: the row is marked deleted and the response says so honestly
    assert r.status_code == 200 and r.json()["file_removed"] is True


async def test_analysis_ignores_removed_evidence(client, tok, analysis):
    c = await create(client, tok("a"))
    ev = await up(client, tok("a"), c["id"])
    await client.delete(f"/complaints/{c['id']}/evidence/{ev['id']}", headers=tok("a"))
    r = await client.post(f"/complaints/{c['id']}/analysis", headers=tok("a"))
    assert r.status_code == 201
    assert analysis.calls[-1]["evidence"] == []
