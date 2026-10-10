from conftest import login, wait_status
from fastapi.testclient import TestClient

NOTE = (
    "Constructed hard case: seasoned mule account and a call-guard miss; only the shared threat "
    "memory saves victim B; simulator data"
)


def test_scenario_kind_and_note_everywhere(app_factory):
    with TestClient(app_factory()) as c:
        off = login(c, "officer")
        s = c.get("/api/demo/state", headers=off).json()
        assert s["scenario_kind"] == "hero_hard_case" and s["scenario_note"] == NOTE
        c.post("/api/demo/start", json={"seed": 1, "speed": 20}, headers=off)
        s = wait_status(c, off, ("finished",))
        assert s["scenario_kind"] == "hero_hard_case" and s["scenario_note"] == NOTE
        hist = c.app.state.engine.log.history
        started = next(e for e in hist if e["kind"] == "scenario.started")
        assert started["scenario_kind"] == "hero_hard_case" and started["scenario_note"] == NOTE
        cit = login(c, "citizen")["Authorization"].split()[1]
        with c.websocket_connect(f"/api/stream?token={cit}") as ws:
            snap = ws.receive_json()
        assert snap["scenario_kind"] == "hero_hard_case" and snap["scenario_note"] == NOTE
        cap = next(
            e["caption"]
            for e in hist
            if e["kind"] == "scenario.step" and e["key"] == "antibody_shared"
        )
        assert "every bank" not in cap and "subscribed" in cap and "single-process" in cap
        cap = next(
            e["caption"]
            for e in hist
            if e["kind"] == "scenario.step" and e["key"] == "analyst_confirms"
        )
        assert "human analyst" not in cap
