"""Migration 10 (complaint network): tables, indexes, idempotent backfill of the identifiers of
complaints stored before it, and identifiers written when a complaint is created. Test data only.
"""

import datetime as dt
import os
from pathlib import Path

import legacy_schema_v0 as legacy
import sqlalchemy as sa

from complaints import migrations
from complaints.network import value_hash
from complaints.schema import metadata
from complaints.store import ComplaintStore

PG_URL = os.getenv("COMPLAINTS_TEST_POSTGRES_URL")
T0 = "2026-01-01 00:00:00"


def _url(tmp_path: Path) -> str:
    if not PG_URL:
        return f"sqlite:///{tmp_path / 'n.db'}"
    eng = sa.create_engine(PG_URL)
    with eng.begin() as conn:
        metadata.drop_all(conn)
        legacy.metadata.drop_all(conn)
    eng.dispose()
    return PG_URL


def _entities(url: str) -> set[tuple[str, str, str, str]]:
    eng = sa.create_engine(url)
    try:
        with eng.connect() as c:
            rows = c.execute(
                sa.text(
                    "SELECT complaint_id, entity_type, value_norm, display FROM complaint_entities"
                )
            ).all()
        return {tuple(r) for r in rows}
    finally:
        eng.dispose()


def _seed_v9(url: str) -> None:
    """A database at version 9 (before the network tables) with two complaints."""
    st = ComplaintStore(url)
    st.close()
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("DROP TABLE link_reviews"))
        c.execute(sa.text("DROP TABLE complaint_entities"))
        c.execute(sa.text("DELETE FROM schema_migrations WHERE version = 10"))
        c.execute(
            sa.text(
                "INSERT INTO users (id, idp_subject, email, email_verified, role, is_active, "
                "created_at, allow_case_matching, display_name_set) VALUES "
                "('u1', 'sub-1', 'old@example.org', :t, 'CITIZEN', :t, :d, :t, :f)"
            ),
            {"t": True, "f": False, "d": T0},
        )
        for cid, upi, url_ in (
            ("c1", "Test.Mule@OKAXIS", "https://kyc.example.co.in/a"),
            ("c2", "test.mule@okaxis", None),
        ):
            c.execute(
                sa.text(
                    "INSERT INTO complaints (id, owner_user_id, category, title, description, "
                    "reported_at, status, created_at, updated_at, upi_id, suspicious_url, "
                    "suspect_phone) VALUES (:id, 'u1', 'UPI_FRAUD', 'Old', 'An older complaint "
                    "row', :d, 'SUBMITTED', :d, :d, :upi, :url, '12')"
                ),
                {"id": cid, "upi": upi, "url": url_, "d": T0},
            )
    eng.dispose()


def test_fresh_database_has_the_network_tables(tmp_path):
    url = _url(tmp_path)
    ComplaintStore(url).close()
    eng = sa.create_engine(url)
    try:
        insp = sa.inspect(eng)
        assert {"complaint_entities", "link_reviews"} <= set(insp.get_table_names())
        idx = {i["name"] for i in insp.get_indexes("complaint_entities")}
        assert {"ix_entities_value", "ix_entities_complaint"} <= idx
    finally:
        eng.dispose()
    assert migrations.MIGRATIONS[9].version == 10
    assert migrations.MIGRATIONS[9].name == "complaint_network"


def test_backfill_of_existing_complaints_is_idempotent(tmp_path):
    url = _url(tmp_path)
    _seed_v9(url)
    st = ComplaintStore(url, create_schema=False)
    try:
        assert st.migrate() == [10]
        got = _entities(url)
        assert ("c1", "upi", "test.mule@okaxis", "te•••@okaxis") in got
        assert ("c2", "upi", "test.mule@okaxis", "te•••@okaxis") in got
        assert ("c1", "url", "https://kyc.example.co.in/a", "kyc.example.co.in/a") in got
        assert ("c1", "domain", "example.co.in", "example.co.in") in got
        # the invalid legacy phone "12" produced nothing; nothing from free text
        assert {t for _, t, _, _ in got} == {"upi", "url", "domain"}
        assert len(got) == 4
        assert st.migrate() == []
        assert _entities(url) == got  # re-run: no duplicates
    finally:
        st.close()


def test_backfill_repairs_a_dropped_table(tmp_path):
    url = _url(tmp_path)
    _seed_v9(url)
    ComplaintStore(url).close()
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("DROP TABLE complaint_entities"))
    eng.dispose()
    ComplaintStore(url).close()  # re-checked at start-up: recreated and filled again
    assert len(_entities(url)) == 4


def test_pre_versioning_database_gets_entities(tmp_path):
    url = _url(tmp_path)
    eng = sa.create_engine(url)
    with eng.begin() as conn:
        legacy.metadata.create_all(conn)
        conn.execute(
            sa.text(
                "INSERT INTO users (id, idp_subject, email, email_verified, role, is_active, "
                "created_at) VALUES ('u1', 'sub-1', 'old@example.org', :t, 'CITIZEN', :t, :d)"
            ),
            {"t": True, "d": T0},
        )
        conn.execute(
            sa.text(
                "INSERT INTO complaints (id, owner_user_id, category, title, description, "
                "reported_at, status, created_at, updated_at, suspect_phone) VALUES ('c1', 'u1', "
                "'PHISHING', 'Old', 'An older complaint row', :d, 'SUBMITTED', :d, :d, "
                "'98765 43210')"
            ),
            {"d": T0},
        )
    eng.dispose()
    ComplaintStore(url).close()
    assert _entities(url) == {("c1", "phone", "+919876543210", "••••••3210")}


def test_create_complaint_writes_its_identifiers_in_the_same_transaction(tmp_path):
    from complaints.identity import Identity

    url = _url(tmp_path)
    st = ComplaintStore(url)
    try:
        now = dt.datetime.now(dt.UTC)
        u = st.upsert_user(Identity("s1", "a@example.org", True, None), now, frozenset())
        fields = {
            "category": "UPI_FRAUD", "title": "t", "description": "d" * 12, "reported_at": now,
            "upi_id": "mule.one@ybl", "bank_account": "123456789", "ifsc": "HDFC0001234",
        }  # fmt: skip
        c, _ = st.create_complaint(u["id"], fields, now, None, "h")
        got = _entities(url)
        assert got == {
            (c["id"], "upi", "mule.one@ybl", "mu•••@ybl"),
            (c["id"], "bank_account", "123456789", "••••••6789"),
        }
        eng = sa.create_engine(url)
        with eng.connect() as conn:
            h = conn.execute(
                sa.text(
                    "SELECT value_hash, source_field FROM complaint_entities WHERE entity_type = 'upi'"
                )
            ).one()
        eng.dispose()
        assert h == (value_hash("upi", "mule.one@ybl"), "upi_id")
    finally:
        st.close()
