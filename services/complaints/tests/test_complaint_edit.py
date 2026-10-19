"""Phase 11: the complainant adds or corrects details while the complaint is SUBMITTED.
Synthetic TEST DATA only."""

import sqlalchemy as sa

from complaints.schema import admin_audit

DESC = "Synthetic test complaint: a caller asked me to approve a UPI collect request."


def citizen(tok, who):
    return tok("a", sub=f"user_{who}", email=f"citizen.{who}@example.org")


async def create(client, tok, who="a", **fields):
    body = {"category": "UPI_FRAUD", "title": f"Report {who}", "description": DESC, **fields}
    r = await client.post("/complaints", json=body, headers=citizen(tok, who))
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_owner_adds_details_and_clears_them(client, tok):
    a = await create(client, tok)
    h = citizen(tok, "a")
    r = await client.patch(f"/complaints/{a}", headers=h, json={
        "bank_name": "Example Bank", "bank_account": "1234 5678 9012", "ifsc": "sbin0001234",
        "amount": "4999.50", "latitude": 19.07, "longitude": 72.88})  # fmt: skip
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["bank_account"] == "123456789012" and c["ifsc"] == "SBIN0001234"
    assert c["currency"] == "INR" and (c["latitude"], c["longitude"]) == (19.07, 72.88)
    assert c["title"] == "Report a" and c["description"] == DESC  # unchanged
    r = await client.patch(f"/complaints/{a}", headers=h, json={"bank_name": None})
    assert r.status_code == 200 and r.json()["bank_name"] is None
    assert r.json()["bank_account"] == "123456789012"  # keys not sent stay


async def test_only_optional_details_and_valid_values(client, tok):
    a = await create(client, tok)
    h = citizen(tok, "a")
    bad = [{}, {"title": "New"}, {"description": "x" * 20}, {"category": "OTHER"},
           {"status": "RESOLVED"}, {"upi_id": "not a upi"}, {"latitude": 10.0},
           {"latitude": 10.0, "longitude": None}, {"ifsc": "XX"},
           {"incident_at": "2999-01-01T00:00:00Z"}]  # fmt: skip
    for body in bad:
        r = await client.patch(f"/complaints/{a}", headers=h, json=body)
        assert r.status_code == 422, body
        assert "not a upi" not in r.text  # values are never echoed


async def test_only_the_owner_and_only_while_submitted(client, tok, inv):
    a = await create(client, tok)
    body = {"city": "Pune"}
    assert (await client.patch(f"/complaints/{a}", headers=citizen(tok, "b"), json=body)).status_code == 404
    assert (await client.patch(f"/complaints/{a}", headers=inv, json=body)).status_code == 403
    assert (await client.patch(f"/complaints/{a}", json=body)).status_code == 401
    assert (await client.patch("/complaints/nope", headers=citizen(tok, "a"), json=body)).status_code == 404
    r = await client.patch(f"/complaints/{a}/status", json={"status": "UNDER_REVIEW"}, headers=inv)
    assert r.status_code == 200
    r = await client.patch(f"/complaints/{a}", headers=citizen(tok, "a"), json=body)
    assert r.status_code == 409 and r.json()["error"] == "complaint_not_editable"


async def test_identifiers_follow_the_edit_and_the_audit_has_no_values(app, client, tok, inv):
    a = await create(client, tok, "a")
    b = await create(client, tok, "b", upi_id="mule.one@ybl")
    rel = (await client.get(f"/complaints/{a}/related", headers=inv)).json()
    assert rel["items"] == []
    r = await client.patch(f"/complaints/{a}", headers=citizen(tok, "a"), json={"upi_id": "Mule.One@ybl"})
    assert r.status_code == 200
    rel = (await client.get(f"/complaints/{a}/related", headers=inv)).json()
    assert [i["complaint"]["id"] for i in rel["items"]] == [b]  # linked through the new UPI ID
    r = await client.patch(f"/complaints/{a}", headers=citizen(tok, "a"), json={"upi_id": None})
    assert (await client.get(f"/complaints/{a}/related", headers=inv)).json()["items"] == []
    with app.state.store.engine.connect() as conn:
        rows = conn.execute(sa.select(admin_audit).where(admin_audit.c.action == "complaint_edit")).all()
    assert [r.after["fields"] for r in rows] == [["upi_id"], ["upi_id"]]
    assert "mule" not in str([r.after for r in rows]).casefold()
    tl = (await client.get(f"/complaints/{a}/timeline", headers=inv)).json()["items"]
    edits = [x for x in tl if x["kind"] == "details_edited"]
    assert len(edits) == 2 and edits[0]["text"] == "Details updated by the complainant: upi id."


async def test_unchanged_values_are_not_recorded(app, client, tok):
    a = await create(client, tok, city="Pune")
    r = await client.patch(f"/complaints/{a}", headers=citizen(tok, "a"), json={"city": "Pune"})
    assert r.status_code == 200
    with app.state.store.engine.connect() as conn:
        n = conn.execute(sa.select(sa.func.count()).select_from(admin_audit)
                         .where(admin_audit.c.action == "complaint_edit")).scalar_one()  # fmt: skip
    assert n == 0


async def test_explicit_null_coordinates_mean_not_given_on_create_and_clear_on_edit(client, tok):
    # regression: a JSON null used to reach the range check and fail with an internal error
    a = await create(client, tok, latitude=None, longitude=None)
    h = citizen(tok, "a")
    r = await client.patch(f"/complaints/{a}", headers=h, json={"latitude": 19.07, "longitude": 72.88})
    assert r.status_code == 200
    r = await client.patch(f"/complaints/{a}", headers=h, json={"latitude": None, "longitude": None})
    assert r.status_code == 200 and r.json()["latitude"] is None and r.json()["longitude"] is None
    r = await client.patch(f"/complaints/{a}", headers=h, json={"latitude": 91, "longitude": 0})
    assert r.status_code == 422
