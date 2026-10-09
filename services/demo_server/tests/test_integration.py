"""One full hero run through the HTTP API and the stream: ordered milestones, evidence package,
pinned verification, and no raw identifier anywhere (the e2e audit's scan, over the demo server)."""

import io
import json
import time
import zipfile

import pytest
from conftest import SECRET, instant, login, make_settings
from demo_server.api import create_app
from fastapi.testclient import TestClient
from sim_engine.replay import build_hero_scenario
from sim_engine.world import build_world


@pytest.fixture(scope="module")
def full(tmp_path_factory):
    app = create_app(make_settings(tmp_path_factory.mktemp("integ")), sleep=instant)
    out: dict = {"rest": {}}
    t_wall = time.time()
    with TestClient(app) as c:
        off = login(c, "officer")
        tok = off["Authorization"].split()[1]
        events = []
        with c.websocket_connect(f"/api/stream?token={tok}") as ws:
            snap = ws.receive_json()
            assert snap["kind"] == "scenario.state" and snap["status"] == "idle"
            r = c.post("/api/demo/start", json={"seed": 1, "speed": 20}, headers=off)
            assert r.status_code == 200, r.text
            out["run_id"] = r.json()["run_id"]
            while True:
                ev = ws.receive_json()
                events.append(ev)
                if ev["kind"] == "scenario.finished":
                    break
        out["run_seconds"] = time.time() - t_wall
        out["events"] = events
        out["client"], out["off"] = c, off
        rest = out["rest"]
        for name, path, role in (
            ("state", "/api/demo/state", "officer"),
            ("holds", "/api/holds?state=all", "analyst"),
            ("antibodies", "/api/antibodies?state=all", "analyst"),
            ("head", "/api/ledger/head", "officer"),
            ("entries", "/api/ledger/entries?limit=200", "officer"),
            ("ckpt", "/api/ledger/checkpoints/latest", "officer"),
            ("graph", "/api/ring/current/graph", "analyst"),
            ("geo", "/api/geo/hotspots", "analyst"),
            ("bench", "/api/benchmark/summary", "citizen"),
        ):
            r = c.get(path, headers=login(c, role))
            assert r.status_code == 200, (path, r.text)
            rest[name] = r.json()
        case = c.post("/api/cases", json={}, headers=off)
        assert case.status_code == 201, case.text
        rest["case"] = case.json()
        pk = c.get(f"/api/packages/{rest['case']['case_id']}", headers=off)
        assert pk.status_code == 200
        out["package"], out["etag"] = pk.content, pk.headers["etag"]
        v = c.post("/api/verify", json={"case_id": rest["case"]["case_id"]}, headers=off)
        assert v.status_code == 200, v.text
        rest["verify"] = v.json()
        sim = c.post("/api/ring/current/simulate", json={"k": 2}, headers=login(c, "analyst"))
        rest["simulate"] = sim.json()
        a = c.post(
            "/api/citizen/assess",
            json={
                "message": "This is the CBI. You are under digital arrest. Transfer to RBI safe account now."
            },
            headers=login(c, "citizen"),
        )
        rest["assess"] = a.json()
        out["final_events"] = list(app.state.engine.log.history)
        yield out


def first(events, kind, **match):
    for i, e in enumerate(events):
        if e["kind"] == kind and all(e.get(k) == v for k, v in match.items()):
            return i
    raise AssertionError(f"no {kind} {match}")


def test_ids_strictly_increasing_and_t_monotone(full):
    ev = full["events"]
    ids = [e["id"] for e in ev]
    assert ids == sorted(ids) and len(set(ids)) == len(ids) and ids[0] == 1
    ts = [e["t"] for e in ev]
    assert ts == sorted(ts)
    assert all(e["run_id"] == full["run_id"] for e in ev)


def test_milestone_order(full):
    ev = full["events"]
    i_alert = first(ev, "call.alert", victim="A")
    i_hold_a = first(ev, "hold.created", bank="bank_a")
    i_pub = first(ev, "antibody.published")
    i_app = first(ev, "antibody.applied", bank="bank_b")
    i_b = first(ev, "txn.scored", bank="bank_b", victim="B", decision="hold_verify")
    i_hold_b = first(ev, "hold.created", bank="bank_b")
    i_led = [i for i, e in enumerate(ev) if e["kind"] == "ledger.entry"]
    i_fin = first(ev, "scenario.finished")
    assert i_alert < i_hold_a < i_pub < i_app < i_b < i_hold_b < i_fin
    assert any(i > i_hold_b for i in i_led)  # ledger entries keep arriving after B is held
    assert ev[i_app]["latency_ms"] < 5000
    codes = {r["code"] for r in ev[i_b]["reasons"]}
    assert "ANTIBODY_MATCH" in codes
    fin = ev[i_fin]
    assert fin["reason"] == "completed" and fin["victim_b_protected"] is True
    assert ev[0]["kind"] == "scenario.started"


def test_stream_schema_basics(full):
    for e in full["events"]:
        assert set(e) >= {"id", "t", "wall", "kind", "run_id"}
        assert e["wall"].endswith("Z") and "T" in e["wall"]
    chunk = next(e for e in full["events"] if e["kind"] == "call.chunk")
    assert set(chunk) >= {"call_ref", "speaker", "text", "score", "reasons"}
    assert chunk["call_ref"].startswith("call_ref:")
    ts = next(e for e in full["events"] if e["kind"] == "txn.scored")
    assert set(ts) >= {"txn_ref", "bank", "rail", "amount_bucket", "decision", "score", "reasons"}
    assert ts["amount_bucket"] in {"<1k", "1k-10k", "10k-100k", "100k-1m", ">=1m"}
    assert "amount_inr" not in json.dumps(full["events"])  # exact amounts never on the stream
    led = next(e for e in full["events"] if e["kind"] == "ledger.entry")
    assert len(led["entry_hash_prefix"]) == 12 and led["seq"] >= 1


def test_state_and_simulated_flag(full):
    s = full["rest"]["state"]
    assert s["simulated"] is True and s["status"] == "finished"
    assert s["counters"]["ledger_entries"] >= 8
    assert full["rest"]["bench"]["simulated"] is True


def test_holds_view_has_exact_amounts_as_strings(full):
    holds = full["rest"]["holds"]["holds"]
    assert holds and all(isinstance(h["amount_inr"], str) for h in holds)
    assert holds == sorted(holds, key=lambda h: (h["deadline_t"], h["txn_ref"]))
    assert any(h["state"] == "blocked" for h in holds)


def test_evidence_package_and_pinned_verify(full):
    v = full["rest"]["verify"]
    assert v["exit_code"] == 0 and v["status"] == "PASS_PINNED", v
    assert v["public_key_hex"] == full["rest"]["head"]["public_key_hex"]
    assert v["entries"] and v["redacted_seqs"] is not None
    assert any("PASS" in ln for ln in v["lines"])
    with zipfile.ZipFile(io.BytesIO(full["package"])) as z:
        names = set(z.namelist())
    assert {"verify.py", "entries.json", "manifest.json", "chain_proof.json"} <= names
    assert full["etag"].startswith('"') or len(full["etag"]) == 64


def test_ring_and_geo(full):
    g = full["rest"]["graph"]
    assert len(g["members"]) >= 3 and g["victims"] == 2
    assert all(len(n["id"]) == 12 for n in g["nodes"])
    roles = {n["role"] for n in g["nodes"]}
    assert {"victim", "mule", "cash_out"} <= roles
    sim = full["rest"]["simulate"]
    assert sim["reduction_pct"] > 0 and isinstance(sim["harm_before"], str)
    geo = full["rest"]["geo"]
    assert geo["hotspots"] and geo["cash_out_points"]


def test_citizen_assess_flags_script(full):
    a = full["rest"]["assess"]
    assert a["label"] == "likely_scam" and a["score"] >= 0.7
    assert a["reasons"] and a["advice"]["key"] == "likely_scam"


def test_no_raw_identifiers_anywhere(full):
    """The e2e audit's scan: every raw id the simulator holds for the hero actors."""
    w = build_world(5, 600)
    meta = build_hero_scenario(w, seed=1).metadata
    victims = [w.citizen_by_token(meta.victim_a_token), w.citizen_by_token(meta.victim_b_token)]
    needles = {
        meta.victim_a_token,
        meta.victim_b_token,
        meta.shared_mule_payee_hash,
        meta.cashout_payee_hash,
        *meta.mule_payee_hashes,
    }  # full payee hashes
    for v in victims:
        needles |= {v.citizen_id, v.account_id, v.device_id}
    needles |= {a.account_id for a in w.accounts.values()}
    needles |= {a.holder_id for a in w.accounts.values()}
    haystacks = {
        "stream": json.dumps(full["final_events"]),
        "rest": json.dumps(full["rest"]),
    }
    with zipfile.ZipFile(io.BytesIO(full["package"])) as z:
        for n in z.namelist():
            if n != "verify.py":
                haystacks["pkg:" + n] = z.read(n).decode("utf-8", "replace")
    for where, text in haystacks.items():
        for needle in needles:
            assert needle not in text, (where, needle[:8])


def test_run_time_budget(full):
    assert full["run_seconds"] < 30, full["run_seconds"]
