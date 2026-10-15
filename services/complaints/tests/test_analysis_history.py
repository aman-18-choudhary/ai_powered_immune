"""GET /complaints/{id}/analyses (versions newest first, owner/staff scoped, paginated) and alert
de-duplication on re-runs (one alert per complaint, kind and level)."""

import httpx
import pytest
from conftest import create
from test_pipeline_v2 import StubCategory, StubScorer, pipe


@pytest.fixture
def p():
    return pipe(StubScorer(0.5), StubCategory("PHISHING", 0.6))


@pytest.fixture
def cl(make_app, p):
    app = make_app(analysis=p)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _run(cl, cid, h):
    r = await cl.post(f"/complaints/{cid}/analysis", headers=h)
    assert r.status_code == 201, r.text
    return r.json()


async def test_history_newest_first_paginated(cl, tok, p):
    c = await create(cl, tok("a"))
    for score in (0.5, 0.8, 0.95):
        p._scorer.score = score
        await _run(cl, c["id"], tok("a"))
    r = await cl.get(f"/complaints/{c['id']}/analyses", headers=tok("a"))
    assert r.status_code == 200
    page = r.json()
    assert page["total"] == 3 and page["limit"] == 20 and page["offset"] == 0
    assert [x["version"] for x in page["items"]] == [3, 2, 1]
    assert [x["risk_level"] for x in page["items"]] == ["CRITICAL", "HIGH", "MEDIUM"]
    first = page["items"][0]
    for k in ("id", "created_at", "status", "risk_score", "score_semantics", "pipeline_version",
              "classification", "confidence", "model_versions"):  # fmt: skip
        assert k in first, k
    assert first["model_versions"]["category"] == "category-stub"
    p2 = (await cl.get(f"/complaints/{c['id']}/analyses?limit=1&offset=1", headers=tok("a"))).json()
    assert [x["version"] for x in p2["items"]] == [2] and p2["total"] == 3
    assert (
        await cl.get(f"/complaints/{c['id']}/analyses?limit=0", headers=tok("a"))
    ).status_code == 422


async def test_history_scope(cl, tok, inv):
    c = await create(cl, tok("a"))
    await _run(cl, c["id"], tok("a"))
    assert (await cl.get(f"/complaints/{c['id']}/analyses", headers=tok("b"))).status_code == 404
    staff = await cl.get(f"/complaints/{c['id']}/analyses", headers=inv)
    assert staff.status_code == 200 and staff.json()["total"] == 1
    assert (await cl.get(f"/complaints/{c['id']}/analyses")).status_code == 401
    empty = await create(cl, tok("a"))
    r = (await cl.get(f"/complaints/{empty['id']}/analyses", headers=tok("a"))).json()
    assert r == {"items": [], "total": 0, "limit": 20, "offset": 0}
    assert (await cl.get("/complaints/nope/analyses", headers=tok("a"))).status_code == 404


async def test_rerun_does_not_duplicate_alerts_per_level(cl, tok, p, inv):
    c = await create(cl, tok("a"))
    p._scorer.score = 0.8  # HIGH
    await _run(cl, c["id"], tok("a"))
    await _run(cl, c["id"], tok("a"))
    await _run(cl, c["id"], inv)  # an investigator re-run: same complaint, kind and level
    alerts = (await cl.get("/alerts", headers=tok("a"))).json()["items"]
    assert [(a["kind"], a["risk_level"]) for a in alerts] == [("HIGH_RISK_ANALYSIS", "HIGH")]
    p._scorer.score = 0.95  # CRITICAL is a new level: one more alert
    await _run(cl, c["id"], tok("a"))
    await _run(cl, c["id"], tok("a"))
    p._scorer.score = 0.5  # MEDIUM: no alert
    await _run(cl, c["id"], tok("a"))
    alerts = (await cl.get("/alerts", headers=tok("a"))).json()["items"]
    assert sorted(a["risk_level"] for a in alerts) == ["CRITICAL", "HIGH"]
    assert all(a["kind"] == "HIGH_RISK_ANALYSIS" for a in alerts)
    other = await create(cl, tok("a"))  # a different complaint alerts on its own
    p._scorer.score = 0.8
    await _run(cl, other["id"], tok("a"))
    assert (await cl.get("/alerts", headers=tok("a"))).json()["total"] == 3
