"""A human plays the analyst: manual resolve, idempotency, conflicts, and the auto flow yielding."""

import threading
import time

import pytest
from conftest import login, make_settings
from fastapi.testclient import TestClient

from demo_server.api import create_app

GATE_T = 832.0  # simulated seconds: right after bank A holds the transfer to the shared mule


class Gated:
    """Injected pacing sleep that blocks once the run reaches ``GATE_T`` until released."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.engine = None

    async def __call__(self, d: float) -> None:
        import asyncio

        while (
            self.engine is not None and self.engine.state.t >= GATE_T and not self.release.is_set()
        ):
            await asyncio.sleep(0.01)


@pytest.fixture
def gated(tmp_path):
    g = Gated()
    app = create_app(make_settings(tmp_path), sleep=g)
    g.engine = app.state.engine
    with TestClient(app) as c:
        yield c, g


def wait_for(fn, timeout=30.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.02)
    raise AssertionError("timeout")


def start_and_hold(c):
    off = login(c, "officer")
    c.post("/api/demo/start", json={"seed": 1, "speed": 20}, headers=off)
    an = login(c, "analyst")

    def first_hold():
        r = c.get("/api/holds?state=open", headers=an)
        hs = r.json().get("holds") if r.status_code == 200 else None
        return hs[0] if hs else None

    return off, an, wait_for(first_hold)


def events(c):
    return list(c.app.state.engine.log.history)


def test_manual_confirm_publishes_antibody_and_auto_flow_yields(gated):
    c, g = gated
    off, an, hold = start_and_hold(c)
    assert (
        hold["state"] == "open" and hold["bank"] == "bank_a" and hold["decision"] == "hold_verify"
    )
    assert c.get("/api/antibodies?state=all", headers=an).json()["antibodies"] == []
    r = c.post(
        f"/api/holds/{hold['txn_ref']}/resolve", json={"action": "confirm_block"}, headers=an
    )
    assert r.status_code == 200 and r.json()["hold"]["state"] == "blocked"
    assert r.json()["hold"]["resolved_by"].startswith("demo-analyst-")
    abs_ = c.get("/api/antibodies?state=active", headers=an).json()["antibodies"]
    assert len(abs_) == 1 and abs_[0]["state"] == "active" and len(abs_[0]["key_hash_prefix"]) == 8
    # the same action again is idempotent; the opposite action conflicts
    again = c.post(
        f"/api/holds/{hold['txn_ref']}/resolve", json={"action": "confirm_block"}, headers=an
    )
    assert again.status_code == 200
    clash = c.post(f"/api/holds/{hold['txn_ref']}/resolve", json={"action": "release"}, headers=an)
    assert clash.status_code == 409 and clash.json()["error"] == "hold_conflict"
    g.release.set()
    wait_for(lambda: c.get("/api/demo/state", headers=off).json()["status"] == "finished")
    ev = events(c)
    assert sum(e["kind"] == "antibody.published" for e in ev) == 1  # the auto step did not repeat
    assert sum(e["kind"] == "hold.resolved" and e["txn_ref"] == hold["txn_ref"] for e in ev) == 1
    res = next(e for e in ev if e["kind"] == "hold.resolved")
    assert res["source"] == "manual"
    b = next(e for e in ev if e["kind"] == "txn.scored" and e.get("victim") == "B")
    assert "ANTIBODY_MATCH" in {x["code"] for x in b["reasons"]}


def test_manual_release_means_no_antibody_and_bank_b_is_not_protected(gated):
    c, g = gated
    off, an, hold = start_and_hold(c)
    r = c.post(f"/api/holds/{hold['txn_ref']}/resolve", json={"action": "release"}, headers=an)
    assert r.status_code == 200 and r.json()["hold"]["state"] == "released"
    g.release.set()
    wait_for(lambda: c.get("/api/demo/state", headers=off).json()["status"] == "finished")
    ev = events(c)
    assert not any(e["kind"] == "antibody.published" for e in ev)
    keys = [e.get("key") for e in ev if e["kind"] == "scenario.step"]
    assert "analyst_released" in keys
    b = next(e for e in ev if e["kind"] == "txn.scored" and e.get("victim") == "B")
    assert "ANTIBODY_MATCH" not in {x["code"] for x in b["reasons"]}
    fin = ev[-1]
    assert fin["kind"] == "scenario.finished" and fin["victim_b_protected"] is False


def test_resolve_errors(gated):
    c, g = gated
    off, an, hold = start_and_hold(c)
    assert c.post("/api/holds/txn_ffffffffffffffff/resolve", json={"action": "release"},
                  headers=an).status_code == 404  # fmt: skip
    r = c.post(
        f"/api/holds/{hold['txn_ref']}/resolve",
        json={"action": "release", "decision_seq": 99},
        headers=an,
    )
    assert r.status_code == 409  # the hold changed since the caller read it
    assert c.post(f"/api/holds/{hold['txn_ref']}/resolve", json={"action": "delete"},
                  headers=an).status_code == 422  # fmt: skip
    assert c.post(f"/api/holds/{hold['txn_ref']}/resolve", json={"action": "release"},
                  headers=login(c, "officer")).status_code == 403  # fmt: skip
    g.release.set()


def test_seek_replays_manual_resolution(gated):
    c, g = gated
    off, an, hold = start_and_hold(c)
    c.post(f"/api/holds/{hold['txn_ref']}/resolve", json={"action": "confirm_block"}, headers=an)
    g.release.set()
    wait_for(lambda: c.get("/api/demo/state", headers=off).json()["status"] == "finished")
    n = sum(e["kind"] == "antibody.published" for e in events(c))
    r = c.post("/api/demo/control", json={"action": "seek", "t": 2000}, headers=off)
    assert r.status_code == 200 and r.json()["epoch"] == 1
    ev = events(c)
    assert sum(e["kind"] == "antibody.published" for e in ev) == n == 1
    done = next(e for e in ev if e["kind"] == "hold.resolved")
    assert done["resolved_by"].startswith("demo-analyst-")  # the human's action is re-applied


def test_double_resolve_race_is_single_effect(gated):
    c, g = gated
    off, an, hold = start_and_hold(c)
    out: list[int] = []

    def go():
        r = c.post(
            f"/api/holds/{hold['txn_ref']}/resolve", json={"action": "confirm_block"}, headers=an
        )
        out.append(r.status_code)

    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out == [200] * 4
    g.release.set()
    wait_for(lambda: c.get("/api/demo/state", headers=off).json()["status"] == "finished")
    ev = events(c)
    assert sum(e["kind"] == "antibody.published" for e in ev) == 1
    assert sum(e["kind"] == "hold.resolved" and e["txn_ref"] == hold["txn_ref"] for e in ev) == 1


def test_confirm_makes_exactly_one_antibody_even_with_hub_submission_env_set(
    gated, monkeypatch
):
    """The demo engine submits to the hub itself; txn-guard's confirm-to-hub submission is wired
    only by ``create_service_app`` (never by the harness), so a confirm must not double-submit
    even when the txn-guard variables are present in the environment."""
    monkeypatch.setenv("HUB_URL", "http://hub.invalid")
    monkeypatch.setenv("TXN_BANK_ID", "bank_a")
    monkeypatch.setenv("TXN_HUB_SUBMIT", "1")
    c, g = gated
    off, an, hold = start_and_hold(c)
    h = c.app.state.engine.run.harness
    for bank in h.banks.values():
        assert bank.holds.hub_info()["hub_submit"] == "disabled"
    r = c.post(
        f"/api/holds/{hold['txn_ref']}/resolve", json={"action": "confirm_block"}, headers=an
    )
    assert r.status_code == 200 and r.json()["hold"]["state"] == "blocked"
    g.release.set()
    wait_for(lambda: c.get("/api/demo/state", headers=off).json()["status"] == "finished")
    abs_ = c.get("/api/antibodies?state=all", headers=an).json()["antibodies"]
    assert len(abs_) == 1
    ev = events(c)
    assert sum(e["kind"] == "antibody.published" for e in ev) == 1
    assert sum(e["kind"] == "antibody.applied" for e in ev) == 1
    assert [e["key"] for e in ev if e["kind"] == "scenario.step"].count("antibody_shared") == 1
