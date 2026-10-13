"""Opt-in Postgres run: COMPLAINTS_TEST_POSTGRES_URL=postgresql+psycopg2://... pytest tests/test_postgres.py"""

import os

import pytest

URL = os.getenv("COMPLAINTS_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="COMPLAINTS_TEST_POSTGRES_URL not set")


def test_schema_and_core_flow_on_postgres():
    from complaints.store import ComplaintStore

    st = ComplaintStore(URL)
    try:
        _flow(st)
    finally:
        st.close()


def _flow(st):
    from datetime import UTC, datetime

    from complaints.identity import Identity

    st.create_schema()
    st.create_schema()  # idempotent
    now = datetime.now(UTC)
    ident = Identity(subject=f"pg-{now.timestamp()}", email="pg@example.org", email_verified=True,
                     display_name=None)  # fmt: skip
    u = st.upsert_user(ident, now, bootstrap_emails=frozenset())
    assert u["role"] == "CITIZEN"
    c, created = st.create_complaint(
        u["id"], {"category": "PHISHING", "title": "t" * 5, "description": "d" * 12,
                  "amount": None, "reported_at": now}, now, idempotency_key=None, request_hash="h",
    )  # fmt: skip
    assert created and st.get_complaint(c["id"])["owner_user_id"] == u["id"]
