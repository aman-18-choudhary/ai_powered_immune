"""Opt-in: set GEO_TEST_POSTGRES_URL=postgresql+psycopg2://user:pw@host/db (needs [postgres])."""

import os
from datetime import timedelta

import pytest

from geo_intel.config import Settings
from geo_intel.service import GeoService
from geo_intel.store import GeoStore, metadata

from .conftest import T0, complaint

URL = os.getenv("GEO_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="GEO_TEST_POSTGRES_URL not set")


def test_ingest_dedupe_counts_and_prune_on_postgres(clock):
    store = GeoStore(URL or "")
    try:
        with store.tx() as conn:
            conn.execute(metadata.tables["gi_complaints"].delete())
        sv = GeoService(store, Settings(retention_days=1), None, clock)
        c = complaint(cid="pg-dup-000001")
        assert sv.ingest(c) == "ingested" and sv.ingest(c) == "duplicate"
        sv.ingest(complaint(T0 - timedelta(hours=1), "DEL"))
        rows = store.window_counts(
            T0 - timedelta(hours=8), T0 - timedelta(hours=2), T0 - timedelta(hours=1), T0
        )
        assert {r["city_code"] for r in rows} == {"MUM", "DEL"}
        assert store.max_ts() == T0
    finally:
        store.close()
