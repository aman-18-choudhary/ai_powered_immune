from datetime import timedelta

import httpx

from graph_twin import views
from graph_twin.api import create_app
from graph_twin.config import Settings
from graph_twin.store import metadata

from .conftest import T0, Clock, decision, h, make_campaign, txn

OFFICER = {"X-Principal-Role": "officer", "X-Principal-Sub": "officer-1"}


def test_schema_columns_fit_every_identifier_the_twin_accepts():
    """``_ident`` accepts up to 128 printable characters (bank, txn id, antibody id, key hash);
    SQLite ignores VARCHAR lengths, Postgres raises DataError: check the declared lengths."""
    t = metadata.tables
    for table, col, need in (
        ("gt_txns", "bank", 128), ("gt_nodes", "bank", 128), ("gt_txns", "txn_id", 128),
        ("gt_txns", "src", 130), ("gt_txns", "dst", 130), ("gt_txns", "device", 130),
        ("gt_nodes", "key", 130), ("gt_antibodies", "antibody_id", 128),
        ("gt_antibodies", "key_hash", 128), ("gt_edges", "src", 130), ("gt_edges", "dst", 130),
    ):  # fmt: skip
        assert t[table].c[col].type.length >= need, (table, col)


def test_longest_accepted_identifiers_round_trip(twin, clock):
    clock.set(T0 + timedelta(hours=1))
    long = "b" * 128
    t = txn(h("p"), h("q"), T0, 500, bank=long, tid="t" * 128)
    assert twin.apply_txn(t)
    assert twin.stats()["txns_applied"] == 1


def test_decisions_applied_counts_the_pending_then_applied_path(twin, clock):
    clock.set(T0 + timedelta(hours=1))
    t = txn(h("p"), h("q"), T0, 500)
    assert twin.apply_decision(decision(t.txn_id, "step_up"))  # arrives first: pending
    assert twin.counters["decisions_applied"] == 0
    twin.apply_txn(t)  # the buffered decision is applied with the transaction
    assert twin.counters["decisions_applied"] == 1
    assert twin.pending_decisions == 0


async def test_graph_view_is_built_outside_the_twin_lock(db_url, monkeypatch):
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "1")
    clock = Clock()
    app = create_app(db_url, clock=clock, settings=Settings(), background=False)
    twin, svc = app.state.twin, app.state.service
    make_campaign(twin, clock, victims=9, mules=3)
    await svc.flush()
    (rid,) = twin.published
    held: list[bool] = []
    real = views.paise_str

    def spy(p: int) -> str:
        held.append(twin.lock._is_owned())  # type: ignore[attr-defined]
        return real(p)

    monkeypatch.setattr(views, "paise_str", spy)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    r = await c.get(f"/rings/{rid}/graph", headers=OFFICER)
    assert r.status_code == 200 and r.json()["node_count"] > 3
    assert held and not any(held)
    await svc.stop()
    twin.store.close()
