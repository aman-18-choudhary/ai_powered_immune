"""Complaint API: creation, validation, idempotency, ownership matrix, status flow, deletion."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from conftest import MIN, create


async def test_create_with_only_mandatory_fields_stores_nulls(client, tok):
    c = await create(client, tok("a"))
    for f in ("amount", "currency", "payment_method", "upi_id", "utr_reference", "suspect_phone",
              "suspect_email", "suspicious_url", "location_text", "city", "state", "latitude",
              "longitude", "social_platform", "social_account", "incident_at"):  # fmt: skip
        assert c[f] is None, f
    assert c["status"] == "SUBMITTED" and c["reported_at"] and c["id"]
    assert c["status_history"][0]["to_status"] == "SUBMITTED"


async def test_empty_strings_normalised_to_null(client, tok):
    c = await create(client, tok("a"), city="", state="  ", upi_id="", suspect_phone="", amount=None)
    assert c["city"] is None and c["state"] is None and c["upi_id"] is None
    assert c["suspect_phone"] is None


async def test_create_with_all_optional_fields(client, tok):
    now = datetime.now(UTC)
    c = await create(
        client,
        tok("a"),
        incident_at=(now - timedelta(days=2)).isoformat(),
        amount="1250.50",
        payment_method="UPI",
        upi_id="Scammer.99@OkBank",
        utr_reference="4123 4567 8901",
        suspect_phone="98765 43210",
        suspect_email="Bad.Guy@Example.org",
        suspicious_url="https://pay.example.com/verify?x=1",
        location_text="Near the station",
        city="Pune",
        state="Maharashtra",
        latitude=18.52,
        longitude=73.85,
        social_platform="Instagram",
        social_account="@fake_support",
    )
    assert c["amount"] == "1250.50" and c["currency"] == "INR" and c["payment_method"] == "UPI"
    assert c["upi_id"] == "scammer.99@okbank"
    assert c["suspect_phone"] == "+919876543210"
    assert c["suspect_email"] == "bad.guy@example.org"
    assert c["utr_reference"] == "412345678901"
    assert c["latitude"] == 18.52 and c["longitude"] == 73.85
    assert c["incident_at"].endswith("+00:00") or c["incident_at"].endswith("Z")


@pytest.mark.parametrize(
    "field,value",
    [
        ("category", "NOPE"),
        ("title", ""),
        ("title", "x" * 201),
        ("description", "short"),
        ("description", "d" * 5001),
        ("amount", -1),
        ("amount", "10.123"),
        ("amount", "abc"),
        ("upi_id", "no-at-sign"),
        ("suspect_phone", "12"),
        ("suspect_phone", "abcdefghij"),
        ("suspect_email", "not-an-email"),
        ("suspicious_url", "ftp://x.example/a"),
        ("suspicious_url", "javascript:alert(1)"),
        ("suspicious_url", "https://user:pw@evil.example/"),
        ("suspicious_url", "https://" + "a" * 2100 + ".example"),
        ("latitude", 91),
        ("longitude", 181),
        ("payment_method", "BARTER"),
        ("currency", "rupees"),
        ("city", "c" * 101),
    ],
)
async def test_invalid_fields_422_no_echo(client, tok, field, value):
    marker = f"{value}"[:50]
    body = {**MIN, field: value}
    if field in ("latitude", "longitude"):
        body.setdefault("latitude", 10.0)
        body.setdefault("longitude", 10.0)
        body[field] = value
    r = await client.post("/complaints", json=body, headers=tok("a"))
    assert r.status_code == 422, r.text
    text = r.text
    assert "input" not in r.json()["detail"][0] and "ctx" not in r.json()["detail"][0]
    if len(marker) > 4:
        assert marker not in text


async def test_unknown_and_privileged_fields_rejected(client, tok):
    for extra in ({"status": "RESOLVED"}, {"owner_user_id": "x"}, {"role": "ADMIN"}, {"id": "1"}):
        r = await client.post("/complaints", json={**MIN, **extra}, headers=tok("a"))
        assert r.status_code == 422


async def test_lat_lon_must_come_together(client, tok):
    r = await client.post("/complaints", json={**MIN, "latitude": 10.0}, headers=tok("a"))
    assert r.status_code == 422


async def test_incident_in_future_rejected_reported_at_rules(client, tok):
    fut = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    r = await client.post("/complaints", json={**MIN, "incident_at": fut}, headers=tok("a"))
    assert r.status_code == 422
    r = await client.post("/complaints", json={**MIN, "reported_at": fut}, headers=tok("a"))
    assert r.status_code == 422
    past = datetime.now(UTC) - timedelta(days=3)
    c = await create(client, tok("a"), reported_at=past.isoformat())
    assert c["reported_at"].startswith(past.strftime("%Y-%m-%dT%H:%M"))
    c2 = await create(client, tok("a"))
    now = datetime.now(UTC)
    assert abs(datetime.fromisoformat(c2["reported_at"]) - now) < timedelta(seconds=30)


async def test_naive_datetime_is_read_as_utc(client, tok):
    c = await create(client, tok("a"), incident_at="2024-01-05T10:00:00")
    assert datetime.fromisoformat(c["incident_at"]) == datetime(2024, 1, 5, 10, tzinfo=UTC)


async def test_only_citizens_create(client, tok, inv, admin):
    for h in (inv, admin):
        r = await client.post("/complaints", json=MIN, headers=h)
        assert r.status_code == 403


async def test_idempotency_key_replay(client, tok):
    h = {**tok("a"), "Idempotency-Key": "key-123"}
    r1 = await client.post("/complaints", json=MIN, headers=h)
    r2 = await client.post("/complaints", json=MIN, headers=h)
    assert r1.status_code == 201 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"] and r2.headers["idempotent-replay"] == "true"
    lst = (await client.get("/complaints", headers=tok("a"))).json()
    assert lst["total"] == 1
    # same key, different body -> conflict, nothing created
    r3 = await client.post("/complaints", json={**MIN, "title": "other title"}, headers=h)
    assert r3.status_code == 409 and r3.json()["error"] == "idempotency_key_reuse"
    # another user may reuse the same key string
    hb = {**tok("b"), "Idempotency-Key": "key-123"}
    assert (await client.post("/complaints", json=MIN, headers=hb)).status_code == 201


async def test_idempotency_key_validated(client, tok):
    h = {**tok("a"), "Idempotency-Key": "bad key with spaces"}
    assert (await client.post("/complaints", json=MIN, headers=h)).status_code == 422
    h = {**tok("a"), "Idempotency-Key": "k" * 200}
    assert (await client.post("/complaints", json=MIN, headers=h)).status_code == 422


async def test_idempotency_key_expires_after_24h(make_app, settings, idp, analysis):
    import httpx
    from conftest import bearer

    t = {"now": datetime(2026, 1, 1, tzinfo=UTC)}
    app = make_app(clock=lambda: t["now"])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        h = {**bearer(idp.token(sub="u1", email="u1@example.org")), "Idempotency-Key": "k1"}
        a = await c.post("/complaints", json=MIN, headers=h)
        t["now"] += timedelta(hours=25)
        b = await c.post("/complaints", json=MIN, headers=h)
        assert a.status_code == 201 and b.status_code == 201 and a.json()["id"] != b.json()["id"]


async def test_concurrent_same_idempotency_key_creates_one(client, tok):
    h = {**tok("a"), "Idempotency-Key": "race-1"}
    rs = await asyncio.gather(*[client.post("/complaints", json=MIN, headers=h) for _ in range(6)])
    assert {r.status_code for r in rs} <= {200, 201}
    assert len({r.json()["id"] for r in rs}) == 1
    assert (await client.get("/complaints", headers=tok("a"))).json()["total"] == 1


# ---- ownership matrix -------------------------------------------------------------------
async def test_ownership_matrix(client, tok, inv, admin):
    c = await create(client, tok("a"))
    cid = c["id"]
    assert (await client.get(f"/complaints/{cid}", headers=tok("a"))).status_code == 200
    assert (await client.get(f"/complaints/{cid}", headers=inv)).status_code == 200
    assert (await client.get(f"/complaints/{cid}", headers=admin)).status_code == 200
    # another citizen: 404 exactly like a missing id
    other = await client.get(f"/complaints/{cid}", headers=tok("b"))
    missing = await client.get("/complaints/11111111-1111-1111-1111-111111111111", headers=tok("b"))
    assert other.status_code == 404 and other.json() == missing.json()
    for method, path, body in [
        ("PATCH", f"/complaints/{cid}/status", {"status": "UNDER_REVIEW"}),
        ("DELETE", f"/complaints/{cid}", None),
        ("GET", f"/complaints/{cid}/evidence", None),
        ("GET", f"/complaints/{cid}/analysis", None),
        ("POST", f"/complaints/{cid}/analysis", None),
    ]:
        r = await client.request(method, path, json=body, headers=tok("b"))
        assert r.status_code in (403, 404), (method, path, r.status_code)
    # lists
    assert (await client.get("/complaints", headers=tok("b"))).json()["total"] == 0
    assert (await client.get("/complaints", headers=tok("a"))).json()["total"] == 1
    assert (await client.get("/complaints", headers=inv)).json()["total"] == 1
    assert (await client.get("/complaints", headers=admin)).json()["total"] == 1


async def test_malformed_id_is_404_not_500(client, tok):
    r = await client.get("/complaints/not-a-uuid", headers=tok("a"))
    assert r.status_code == 404


async def test_citizen_list_ignores_staff_filters_scope(client, tok):
    await create(client, tok("a"))
    await create(client, tok("b"), city="Delhi")
    r = await client.get("/complaints?city=Delhi", headers=tok("a"))
    assert r.json()["total"] == 0  # filters never widen the owner scope


async def test_list_filters_sort_and_bounds(client, tok, inv):
    await create(client, tok("a"), category="UPI_FRAUD", city="Pune", state="MH",
                 incident_at="2025-03-01T00:00:00Z", title="alpha one")  # fmt: skip
    await create(client, tok("b"), category="PHISHING", city="Delhi", state="DL",
                 incident_at="2025-04-01T00:00:00Z", title="beta two", amount="500")  # fmt: skip
    r = await client.get("/complaints?category=UPI_FRAUD", headers=inv)
    assert [i["city"] for i in r.json()["items"]] == ["Pune"]
    r = await client.get("/complaints?city=delhi", headers=inv)  # case-insensitive exact
    assert r.json()["total"] == 1
    r = await client.get("/complaints?state=MH&status=SUBMITTED", headers=inv)
    assert r.json()["total"] == 1
    r = await client.get("/complaints?q=beta", headers=inv)
    assert r.json()["total"] == 1
    r = await client.get("/complaints?q=%25", headers=inv)  # LIKE wildcard is escaped
    assert r.json()["total"] == 0
    r = await client.get(
        "/complaints?incident_from=2025-03-15T00:00:00Z&incident_to=2025-12-31T00:00:00Z",
        headers=inv,
    )
    assert r.json()["total"] == 1
    r = await client.get("/complaints?sort=incident_at", headers=inv)
    assert [i["city"] for i in r.json()["items"]] == ["Pune", "Delhi"]
    r = await client.get("/complaints?sort=-incident_at", headers=inv)
    assert [i["city"] for i in r.json()["items"]] == ["Delhi", "Pune"]
    r = await client.get("/complaints?limit=1&offset=1&sort=incident_at", headers=inv)
    assert len(r.json()["items"]) == 1 and r.json()["total"] == 2
    for bad in ("limit=0", "limit=101", "offset=-1", "offset=100001", "sort=password",
                "status=NOPE", "category=NOPE", f"q={'x' * 101}", "has_evidence=maybe",
                "risk_level=EXTREME"):  # fmt: skip
        assert (await client.get(f"/complaints?{bad}", headers=inv)).status_code == 422, bad


async def test_has_evidence_and_risk_level_filters(client, tok, inv, analysis):
    from conftest import PNG

    a = await create(client, tok("a"), title="has file")
    await create(client, tok("a"), title="no file")
    up = await client.post(
        f"/complaints/{a['id']}/evidence", files={"file": ("shot.png", PNG, "image/png")}, headers=tok("a")
    )
    assert up.status_code == 201
    r = await client.get("/complaints?has_evidence=true", headers=inv)
    assert [i["title"] for i in r.json()["items"]] == ["has file"]
    r = await client.get("/complaints?has_evidence=false", headers=inv)
    assert [i["title"] for i in r.json()["items"]] == ["no file"]
    analysis.outcome = analysis.outcome.__class__(**{**analysis.outcome.__dict__, "risk_level": "HIGH", "risk_score": 90})
    await client.post(f"/complaints/{a['id']}/analysis", headers=tok("a"))
    r = await client.get("/complaints?risk_level=HIGH", headers=inv)
    assert [i["title"] for i in r.json()["items"]] == ["has file"]
    assert (await client.get("/complaints?risk_level=LOW", headers=inv)).json()["total"] == 0


# ---- status flow ------------------------------------------------------------------------
async def test_status_transitions_and_history(client, tok, inv):
    c = await create(client, tok("a"))
    cid = c["id"]

    async def patch(to, note=None, headers=inv):
        return await client.patch(
            f"/complaints/{cid}/status", json={"status": to, "note": note}, headers=headers
        )

    assert (await patch("RESOLVED")).status_code == 409  # SUBMITTED -> RESOLVED not allowed
    assert (await patch("SUBMITTED")).status_code == 409
    assert (await patch("UNDER_REVIEW", "picked up")).status_code == 200
    assert (await patch("UNDER_REVIEW")).status_code == 409  # no-op transition
    assert (await patch("INVESTIGATING")).status_code == 200
    assert (await patch("RESOLVED")).status_code == 200
    d = (await client.get(f"/complaints/{cid}", headers=tok("a"))).json()
    assert d["status"] == "RESOLVED"
    assert [h["to_status"] for h in d["status_history"]] == [
        "SUBMITTED", "UNDER_REVIEW", "INVESTIGATING", "RESOLVED"
    ]  # fmt: skip
    assert d["status_history"][1]["from_status"] == "SUBMITTED"
    assert d["status_history"][1]["note"] == "picked up"
    assert all(h["actor_user_id"] for h in d["status_history"])


async def test_status_patch_authz_and_validation(client, tok, inv, admin):
    c = await create(client, tok("a"))
    cid = c["id"]
    r = await client.patch(f"/complaints/{cid}/status", json={"status": "UNDER_REVIEW"}, headers=tok("a"))
    assert r.status_code == 403  # owner is not staff
    r = await client.patch(f"/complaints/{cid}/status", json={"status": "BOGUS"}, headers=inv)
    assert r.status_code == 422 and "BOGUS" not in r.text
    r = await client.patch(f"/complaints/{cid}/status", json={"status": "REJECTED", "note": "n" * 501}, headers=inv)
    assert r.status_code == 422
    r = await client.patch(f"/complaints/{cid}/status", json={"status": "REJECTED"}, headers=admin)
    assert r.status_code == 200
    r = await client.patch("/complaints/11111111-1111-1111-1111-111111111111/status",
                           json={"status": "REJECTED"}, headers=inv)  # fmt: skip
    assert r.status_code == 404


async def test_concurrent_status_changes_one_wins(client, tok, inv, admin):
    c = await create(client, tok("a"))
    cid = c["id"]
    rs = await asyncio.gather(
        client.patch(f"/complaints/{cid}/status", json={"status": "UNDER_REVIEW"}, headers=inv),
        client.patch(f"/complaints/{cid}/status", json={"status": "UNDER_REVIEW"}, headers=admin),
    )
    assert sorted(r.status_code for r in rs) == [200, 409]
    d = (await client.get(f"/complaints/{cid}", headers=inv)).json()
    assert len(d["status_history"]) == 2


async def test_status_change_alerts_owner_only(client, tok, inv):
    c = await create(client, tok("a"))
    await client.patch(f"/complaints/{c['id']}/status", json={"status": "UNDER_REVIEW"}, headers=inv)
    mine = (await client.get("/alerts", headers=tok("a"))).json()["items"]
    assert len(mine) == 1 and mine[0]["kind"] == "STATUS_CHANGED" and mine[0]["complaint_id"] == c["id"]
    assert mine[0]["read_at"] is None
    assert (await client.get("/alerts", headers=inv)).json()["items"] == []  # no fabricated alerts
    assert (await client.get("/alerts", headers=tok("b"))).json()["items"] == []


# ---- soft delete ------------------------------------------------------------------------
async def test_soft_delete_rules(client, tok, inv, admin):
    c = await create(client, tok("a"))
    cid = c["id"]
    assert (await client.delete(f"/complaints/{cid}", headers=tok("b"))).status_code == 404
    assert (await client.delete(f"/complaints/{cid}", headers=inv)).status_code == 403
    assert (await client.delete(f"/complaints/{cid}", headers=tok("a"))).status_code == 204
    assert (await client.get(f"/complaints/{cid}", headers=tok("a"))).status_code == 404
    assert (await client.get(f"/complaints/{cid}", headers=inv)).status_code == 404
    assert (await client.get("/complaints", headers=inv)).json()["total"] == 0
    assert (await client.delete(f"/complaints/{cid}", headers=tok("a"))).status_code == 404
    audit = (await client.get("/admin/audit", headers=admin)).json()["items"]
    assert any(a["action"] == "complaint_delete" for a in audit)


async def test_cannot_delete_after_review_started(client, tok, inv):
    c = await create(client, tok("a"))
    await client.patch(f"/complaints/{c['id']}/status", json={"status": "UNDER_REVIEW"}, headers=inv)
    r = await client.delete(f"/complaints/{c['id']}", headers=tok("a"))
    assert r.status_code == 409 and r.json()["error"] == "not_deletable"
    assert (await client.get(f"/complaints/{c['id']}", headers=tok("a"))).status_code == 200


async def test_json_body_cap_64kib(client, tok):
    big = {**MIN, "description": "x" * 70000}
    r = await client.post("/complaints", json=big, headers=tok("a"))
    assert r.status_code == 413


async def test_rate_limit_per_user_fail_open(make_app, settings, idp):
    import dataclasses

    import httpx
    from conftest import bearer

    s = dataclasses.replace(settings, rate_write_per_min=3)
    app = make_app(settings=s)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        h = bearer(idp.token(sub="rl", email="rl@example.org"))
        codes = [(await c.post("/complaints", json=MIN, headers=h)).status_code for _ in range(5)]
        assert codes[:3] == [201] * 3 and codes[3:] == [429, 429]
        h2 = bearer(idp.token(sub="rl2", email="rl2@example.org"))
        assert (await c.post("/complaints", json=MIN, headers=h2)).status_code == 201
