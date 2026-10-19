"""Complaint hotspots (Phase 10). Every complaint and coordinate here is synthetic TEST DATA."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from complaints.schema import complaints

# two points inside one 0.25-degree cell in Mumbai, one in Delhi
MUMBAI_1 = (19.07, 72.88)
MUMBAI_2 = (19.10, 72.85)
DELHI = (28.61, 77.21)


@pytest.fixture
def mk(client, tok):
    async def _mk(who: str = "a", at: tuple[float, float] | None = None, **fields) -> str:
        h = tok("a", sub=f"user_{who}", email=f"citizen.{who}@example.org")
        body = {"category": "UPI_FRAUD", "title": f"Report {who}",
                "description": "Synthetic test complaint for the hotspot tests.", **fields}  # fmt: skip
        if at:
            body["latitude"], body["longitude"] = at
        r = await client.post("/complaints", json=body, headers=h)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    return _mk


def citizen(tok, who):
    return tok("a", sub=f"user_{who}", email=f"citizen.{who}@example.org")


async def hot(client, h, **params):
    r = await client.get("/geo/complaints/hotspots", headers=h, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def set_created(app, cid, when):
    with app.state.store.engine.begin() as conn:
        conn.execute(sa.update(complaints).where(complaints.c.id == cid).values(created_at=when))


async def test_staff_only(client, tok, inv, admin):
    assert (await client.get("/geo/complaints/hotspots", headers=citizen(tok, "a"))).status_code == 403
    assert (await client.get("/geo/complaints/hotspots")).status_code == 401
    assert (await client.get("/geo/complaints/hotspots", headers=admin)).status_code == 200


async def test_cells_count_complaints_and_return_cell_centres_not_points(client, inv, mk):
    await mk("a", MUMBAI_1)
    await mk("b", MUMBAI_2, category="PHISHING")
    await mk("c", DELHI)
    res = await hot(client, inv, cell=0.25)
    assert res["totals"] == {"complaints": 3, "with_coordinates": 3, "without_coordinates": 0,
                             "cells": 2}  # fmt: skip
    top = res["cells"][0]
    assert top["count"] == 2 and top["by_category"] == {"UPI_FRAUD": 1, "PHISHING": 1}
    assert (top["lat"], top["lon"]) == (19.125, 72.875)  # centre of [19.0,19.25) x [72.75,73.0)
    assert top["bounds"] == {"south": 19.0, "west": 72.75, "north": 19.25, "east": 73.0}
    assert top["by_risk"] == {"not_scored": 2}
    blob = str(res)
    assert "19.07" not in blob and "72.88" not in blob  # no complaint's own coordinates
    assert "not of offences" in res["note"]
    one_degree = await hot(client, inv, cell=1.0)
    assert [c["count"] for c in one_degree["cells"]] == [2, 1]


async def test_filters_and_min_count(client, inv, mk):
    await mk("a", MUMBAI_1)
    await mk("b", MUMBAI_2, category="PHISHING")
    await mk("c", DELHI)
    only = await hot(client, inv, category="PHISHING")
    assert only["totals"]["complaints"] == 1
    big = await hot(client, inv, min_count=2)
    assert [c["count"] for c in big["cells"]] == [2]
    box = await hot(client, inv, bbox="28,77,29,78")
    assert box["totals"]["with_coordinates"] == 1
    none = await hot(client, inv, risk_level="HIGH")
    assert none["cells"] == [] and none["totals"]["complaints"] == 0


async def test_complaints_without_coordinates_are_listed_by_place(client, inv, mk):
    await mk("a", city="Pune", state="Maharashtra")
    await mk("b", city="Pune", state="Maharashtra")
    await mk("c")
    res = await hot(client, inv)
    assert res["totals"]["without_coordinates"] == 3 and res["cells"] == []
    assert res["places"][0] == {"state": "Maharashtra", "city": "Pune", "count": 2}
    assert {"state": None, "city": None, "count": 1} in res["places"]


async def test_opted_out_and_deleted_complaints_are_not_counted(client, inv, tok, mk):
    await mk("a", MUMBAI_1)
    b = await mk("b", MUMBAI_2)
    d = await mk("d", MUMBAI_2)
    assert (await hot(client, inv))["cells"][0]["count"] == 3
    r = await client.patch("/me", json={"privacy": {"allow_case_matching": False}},
                           headers=citizen(tok, "b"))  # fmt: skip
    assert r.status_code == 200
    r = await client.delete(f"/complaints/{d}", headers=citizen(tok, "d"))
    assert r.status_code == 204
    res = await hot(client, inv)
    assert res["cells"][0]["count"] == 1 and res["totals"]["complaints"] == 1
    assert b not in str(res)


async def test_period_comparison(app, client, inv, mk):
    now = datetime.now(UTC)
    old = await mk("a", MUMBAI_1)
    set_created(app, old, now - timedelta(days=40))
    for w in ("b", "c"):
        cid = await mk(w, MUMBAI_2)
        set_created(app, cid, now - timedelta(days=5))
    start = (now - timedelta(days=30)).isoformat()
    res = await hot(client, inv, **{"from": start, "compare": "true"})
    cell = res["cells"][0]
    assert cell["count"] == 2 and cell["previous_count"] == 1 and cell["change"] == 1
    prev = res["comparison"]["previous"]
    assert prev["to"].startswith(start[:16])


async def test_validation(client, inv):
    bad = [{"cell": 0.3}, {"category": "NOPE"}, {"risk_level": "EXTREME"}, {"bbox": "1,2,3"},
           {"bbox": "50,10,40,20"}, {"compare": "true"}, {"min_count": 0},
           {"from": "2026-10-10T00:00:00Z", "to": "2026-10-01T00:00:00Z"}]  # fmt: skip
    for params in bad:
        r = await client.get("/geo/complaints/hotspots", headers=inv, params=params)
        assert r.status_code == 422, params
