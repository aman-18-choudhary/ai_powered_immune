"""CRITICAL risk level persistence (migration 6): the analyses CHECK allows CRITICAL, new rows
store the exact level, rows written before the migration (CRITICAL stored as HIGH with the exact
level in ``detail``) are backfilled and still read correctly; list filter and stats accept it.

Runs on Postgres too when ``COMPLAINTS_TEST_POSTGRES_URL`` is set.
"""

import datetime as dt
import os
from pathlib import Path

import httpx
import legacy_schema_v0 as legacy
import pytest
import sqlalchemy as sa
from conftest import create
from test_pipeline_v2 import StubScorer, pipe

from complaints import migrations
from complaints.schema import RISK_LEVELS, metadata
from complaints.store import ComplaintStore

PG_URL = os.getenv("COMPLAINTS_TEST_POSTGRES_URL")
T0 = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)


def _url(tmp_path: Path) -> str:
    if not PG_URL:
        return f"sqlite:///{tmp_path / 'crit.db'}"
    eng = sa.create_engine(PG_URL)
    with eng.begin() as conn:
        metadata.drop_all(conn)
        legacy.metadata.drop_all(conn)
    eng.dispose()
    return PG_URL


def test_schema_allows_critical():
    assert "CRITICAL" in RISK_LEVELS
    names = [m.name for m in migrations.MIGRATIONS]
    assert "analyses_critical_level" in names


async def test_new_critical_result_is_stored_exactly(make_app, tok):
    app = make_app(analysis=pipe(StubScorer(0.97)))
    cl = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    c = await create(cl, tok("a"))
    a = (await cl.post(f"/complaints/{c['id']}/analysis", headers=tok("a"))).json()
    assert a["risk_level"] == "CRITICAL"
    with app.state.store.engine.connect() as conn:
        stored = conn.execute(sa.text("select risk_level from analyses")).scalar_one()
    assert stored == "CRITICAL"
    crit = (await cl.get("/complaints?risk_level=CRITICAL", headers=tok("a"))).json()
    assert crit["total"] == 1 and crit["items"][0]["latest_analysis"]["risk_level"] == "CRITICAL"
    high = (await cl.get("/complaints?risk_level=HIGH", headers=tok("a"))).json()
    assert high["total"] == 0  # the filter is exact
    bogus = await cl.get("/complaints?risk_level=SEVERE", headers=tok("a"))
    assert bogus.status_code == 422
    stats = (await cl.get("/me/stats", headers=tok("a"))).json()
    assert stats["by_risk_level"] == {
        "LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 1, "not_scored": 0, "not_analysed": 0,
    }  # fmt: skip


async def test_stats_by_risk_level_uses_latest_analysis(make_app, tok):
    p = pipe(StubScorer(0.5))
    app = make_app(analysis=p)
    cl = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    c1 = await create(cl, tok("a"))
    await create(cl, tok("a"))  # never analysed
    await cl.post(f"/complaints/{c1['id']}/analysis", headers=tok("a"))  # MEDIUM
    p._scorer.score = 0.75
    await cl.post(f"/complaints/{c1['id']}/analysis", headers=tok("a"))  # HIGH (latest)
    s = (await cl.get("/me/stats", headers=tok("a"))).json()["by_risk_level"]
    assert s["HIGH"] == 1 and s["MEDIUM"] == 0 and s["not_analysed"] == 1
    other = (await cl.get("/me/stats", headers=tok("b"))).json()["by_risk_level"]
    assert sum(other.values()) == 0  # own data only


def _db_at_version_5(url: str) -> None:
    """A database as written before migration 6: the old CHECK, CRITICAL stored as HIGH."""
    eng = sa.create_engine(url)
    with eng.begin() as conn:
        legacy.metadata.create_all(conn)
        conn.execute(
            sa.text(
                "INSERT INTO users (id, idp_subject, email, email_verified, role, is_active, "
                "created_at) VALUES ('u1', 'sub-1', 'old@example.org', :t, 'CITIZEN', :t, "
                "'2026-01-01 00:00:00')"
            ),
            {"t": True},
        )
        for cid in ("c1", "c2", "c3"):
            conn.execute(
                sa.text(
                    "INSERT INTO complaints (id, owner_user_id, category, title, description, "
                    "reported_at, status, created_at, updated_at) VALUES (:id, 'u1', 'PHISHING', "
                    "'Old', 'An older complaint row', '2026-01-01 00:00:00', 'SUBMITTED', "
                    "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"
                ),
                {"id": cid},
            )
        rows = [
            ("a1", "c1", "HIGH", {"analysis_schema": 2, "risk_level": "CRITICAL"}, 95),
            ("a2", "c2", "HIGH", {"analysis_schema": 2, "risk_level": "HIGH"}, 75),
            ("a3", "c3", "HIGH", None, 80),  # v1 row without detail
        ]
        tbl = legacy.metadata.tables["analyses"]
        for aid, cid, lvl, detail, score in rows:
            conn.execute(
                tbl.insert().values(
                    id=aid, complaint_id=cid, version=1, created_at=T0,
                    pipeline_version="complaints-analysis-v2+x+y", capabilities={},
                    risk_score=score, risk_level=lvl, classification=None, confidence=None,
                    indicators=[], explanation=None, recommended_actions=[], limitations=[],
                    status="COMPLETED", detail=detail,
                )
            )  # fmt: skip
    # the old CHECK refuses CRITICAL
    with pytest.raises(sa.exc.IntegrityError), eng.begin() as conn:
        conn.execute(sa.text("UPDATE analyses SET risk_level = 'CRITICAL' WHERE id = 'a2'"))
    eng.dispose()


def test_migration_widens_check_and_backfills_old_critical_rows(tmp_path):
    url = _url(tmp_path)
    _db_at_version_5(url)
    st = ComplaintStore(url)
    try:
        with st.engine.connect() as c:
            got = dict(c.execute(sa.text("select id, risk_level from analyses")).all())
        assert got == {"a1": "CRITICAL", "a2": "HIGH", "a3": "HIGH"}
        assert st.latest_analysis("c1")["detail"]["risk_level"] == "CRITICAL"  # data kept
        with st.engine.begin() as c:
            c.execute(sa.text("UPDATE analyses SET risk_level = 'CRITICAL' WHERE id = 'a2'"))
        with pytest.raises(sa.exc.IntegrityError), st.engine.begin() as c:
            c.execute(sa.text("UPDATE analyses SET risk_level = 'SEVERE' WHERE id = 'a2'"))
        items, total = st.list_complaints(
            owner_id=None, filters={"risk_level": "CRITICAL"}, sort="-created_at", limit=10,
            offset=0,
        )  # fmt: skip
        assert total == 2 and {i["id"] for i in items} == {"c1", "c2"}
        assert st.migrate() == []  # re-run is a no-op
    finally:
        st.close()
