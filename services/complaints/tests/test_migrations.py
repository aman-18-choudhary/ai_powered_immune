"""Versioned migrations: fresh database, databases at the pre-versioning schema (with and without
the ``analyses.detail`` column), re-runs are no-ops, ``python -m complaints.migrate``.

``legacy_schema_v0.py`` is a frozen copy of ``complaints/schema.py`` as released before
``schema_migrations`` existed. With ``COMPLAINTS_TEST_POSTGRES_URL`` set the same tests run on
Postgres (the tables are dropped first).
"""

import os
import subprocess
import sys
from pathlib import Path

import legacy_schema_v0 as legacy
import pytest
import sqlalchemy as sa

from complaints import migrations
from complaints.schema import metadata
from complaints.store import ComplaintStore

PG_URL = os.getenv("COMPLAINTS_TEST_POSTGRES_URL")


def _url(tmp_path: Path) -> str:
    if not PG_URL:
        return f"sqlite:///{tmp_path / 'm.db'}"
    eng = sa.create_engine(PG_URL)
    with eng.begin() as conn:
        metadata.drop_all(conn)
        legacy.metadata.drop_all(conn)
    eng.dispose()
    return PG_URL


def _applied(url: str) -> list[tuple[int, str]]:
    eng = sa.create_engine(url)
    try:
        with eng.connect() as c:
            rows = c.execute(
                sa.text("SELECT version, name FROM schema_migrations ORDER BY version")
            )
            return [(int(v), n) for v, n in rows]
    finally:
        eng.dispose()


def _columns(url: str) -> dict[str, set[str]]:
    eng = sa.create_engine(url)
    try:
        with eng.connect() as c:
            insp = sa.inspect(c)
            return {t: {col["name"] for col in insp.get_columns(t)} for t in insp.get_table_names()}
    finally:
        eng.dispose()


def _expected() -> dict[str, set[str]]:
    return {t.name: {c.name for c in t.columns} for t in metadata.sorted_tables}


ALL = [(m.version, m.name) for m in migrations.MIGRATIONS]


def test_migration_list_is_ordered_and_starts_with_the_baseline():
    versions = [m.version for m in migrations.MIGRATIONS]
    assert versions == sorted(versions) and len(set(versions)) == len(versions)
    assert versions[:2] == [1, 2]
    assert migrations.MIGRATIONS[0].name == "baseline"
    assert migrations.MIGRATIONS[1].name == "analyses_detail_column"
    assert migrations.LATEST == versions[-1]


def test_fresh_database_gets_every_table_and_every_version(tmp_path):
    url = _url(tmp_path)
    ComplaintStore(url).close()
    assert _applied(url) == ALL
    cols = _columns(url)
    for table, want in _expected().items():
        assert want <= cols[table], table


def test_rerun_is_a_noop(tmp_path):
    url = _url(tmp_path)
    st = ComplaintStore(url)
    first = st.migrate()
    again = st.migrate()
    st.close()
    assert first == []  # the constructor already applied everything
    assert again == []
    assert _applied(url) == ALL


def _legacy_db(url: str, *, with_detail: bool) -> None:
    eng = sa.create_engine(url)
    with eng.begin() as conn:
        legacy.metadata.create_all(conn)
        if not with_detail:
            conn.execute(sa.text("ALTER TABLE analyses DROP COLUMN detail"))
        conn.execute(
            sa.text(
                "INSERT INTO users (id, idp_subject, email, email_verified, role, is_active, "
                "created_at) VALUES ('u1', 'sub-1', 'old@example.org', :t, 'CITIZEN', :t, "
                "'2026-01-01 00:00:00')"
            ),
            {"t": True},
        )
        conn.execute(
            sa.text(
                "INSERT INTO complaints (id, owner_user_id, category, title, description, "
                "reported_at, status, created_at, updated_at) VALUES ('c1', 'u1', 'PHISHING', "
                "'Old one', 'An older complaint row', '2026-01-01 00:00:00', 'SUBMITTED', "
                "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO complaint_evidence (id, complaint_id, uploader_user_id, "
                "original_filename, mime_type, size_bytes, sha256, storage_key, status, "
                "created_at) VALUES ('e1', 'c1', 'u1', 'a.png', 'image/png', 3, :h, :k, "
                "'stored', '2026-01-01 00:00:00')"
            ),
            {"h": "0" * 64, "k": "a" * 32},
        )
    eng.dispose()


@pytest.mark.parametrize("with_detail", [True, False])
def test_pre_versioning_database_upgrades_cleanly(tmp_path, with_detail):
    url = _url(tmp_path)
    _legacy_db(url, with_detail=with_detail)
    assert "schema_migrations" not in _columns(url)
    st = ComplaintStore(url)
    try:
        assert st.get_user("u1")["email"] == "old@example.org"  # data kept
        assert st.get_user("u1")["allow_case_matching"] is True  # server default
        assert st.get_complaint("c1")["bank_account"] is None
        assert [e["id"] for e in st.list_evidence("c1")] == ["e1"]
        assert st.migrate() == []
        # the widened evidence CHECK accepts "deleted" and still refuses unknown values
        with st.engine.begin() as c:
            c.execute(sa.text("UPDATE complaint_evidence SET status = 'deleted' WHERE id = 'e1'"))
        with pytest.raises(sa.exc.IntegrityError), st.engine.begin() as c:
            c.execute(sa.text("UPDATE complaint_evidence SET status = 'bogus' WHERE id = 'e1'"))
        assert st.list_evidence("c1") == []
    finally:
        st.close()
    assert _applied(url) == ALL
    cols = _columns(url)
    assert "detail" in cols["analyses"]
    for table, want in _expected().items():
        assert want <= cols[table], table


def test_migrate_returns_the_versions_it_applied(tmp_path):
    url = _url(tmp_path)
    _legacy_db(url, with_detail=False)
    st = ComplaintStore(url, create_schema=False)
    try:
        assert st.migrate() == [v for v, _ in ALL]
        assert st.migrate() == []
    finally:
        st.close()


def test_module_entry_point_migrates(tmp_path):
    url = _url(tmp_path)
    env = {**os.environ, "COMPLAINTS_DATABASE_URL": url}
    out = subprocess.run(
        [sys.executable, "-m", "complaints.migrate"], env=env, capture_output=True, text=True,
        timeout=120, check=False,
    )  # fmt: skip
    assert out.returncode == 0, out.stderr
    assert "complaints schema ready" in out.stdout
    assert f"version {migrations.LATEST}" in out.stdout
    assert _applied(url) == ALL
    out2 = subprocess.run(
        [sys.executable, "-m", "complaints.migrate"], env=env, capture_output=True, text=True,
        timeout=120, check=False,
    )  # fmt: skip
    assert out2.returncode == 0 and "applied: none" in out2.stdout
