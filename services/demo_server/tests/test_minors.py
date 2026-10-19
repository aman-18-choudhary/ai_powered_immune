import asyncio
import gc
import hashlib
import json
import logging

import pytest
from conftest import SECRET, login, make_settings, wait_status
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse

from demo_server.api import create_app
from demo_server.engine import Engine


async def slow(_: float) -> None:
    await asyncio.sleep(0.01)


# ---- 6: runs list reflects teardown/finish
@pytest.mark.asyncio
async def test_runs_list_marks_superseded_and_final_status(tmp_path):
    e = Engine(make_settings(tmp_path), sleep=slow)
    await e.start()
    try:
        first = await e.start_run(1, 20.0, "hero")
        await asyncio.sleep(0.3)
        second = await e.start_run(1, 20.0, "hero")
        rec = {r["run_id"]: r for r in e.runs}
        assert rec[first]["status"] == "superseded" and rec[first]["events"] > 0
        e.sleep = lambda _s: asyncio.sleep(0)  # type: ignore[assignment]
        await e.join(60)
        rec = {r["run_id"]: r for r in e.runs}
        assert rec[second]["status"] == "finished" and rec[second]["events"] >= 150
        third = await e.start_run(1, 20.0, "hero")
        await e.join(60)
        await e.start_run(1, 20.0, "hero")
        assert {r["run_id"]: r for r in e.runs}[third]["status"] == "finished"
    finally:
        await e.aclose()


# ---- 7: rate limit keyed by client ip for citizens
def test_relogin_does_not_reset_citizen_bucket(app_factory, monkeypatch):
    # The limiter's windows are aligned to the wall clock (60 s); on the real clock these 35
    # requests can straddle a minute boundary, the count restarts and no 429 comes. Pin the clock
    # inside one window so the test checks the bucket, not the time of day.
    import demo_server.api as api
    from demo_server.security import RateLimiter

    mid_window = 6_000_000 * 60 + 10.0
    monkeypatch.setattr(
        api,
        "RateLimiter",
        lambda limits, window: RateLimiter(limits, window, clock=lambda: mid_window),
    )
    with TestClient(app_factory()) as c:
        codes = []
        h: dict[str, str] = {}
        for i in range(35):
            if i % 5 == 0:
                h = login(c, "citizen")
            codes.append(c.get("/api/demo/state", headers=h).status_code)
        assert codes[:30] == [200] * 30 and 429 in codes[30:]


def test_forwarded_for_only_trusted_when_configured(app_factory):
    def run(trust: bool) -> list[int]:
        app = app_factory(trust_proxy=trust)
        with TestClient(app) as c:
            h = login(c, "citizen")
            return [
                c.get(
                    "/api/demo/state", headers={**h, "X-Forwarded-For": f"10.0.0.{i}"}
                ).status_code
                for i in range(35)
            ]

    assert 429 in run(False)  # header ignored: one bucket
    assert 429 not in run(True)  # each forwarded address has its own bucket


# ---- 8: caching
def test_static_cache_headers_and_benchmark_etag(tmp_path):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<html>x</html>")
    (root / "assets" / "app-1a2b3c.js").write_text("x")
    app = create_app(make_settings(tmp_path / "w", static_dir=root))
    with TestClient(app) as c:
        assert c.get("/assets/app-1a2b3c.js").headers["cache-control"] == (
            "public, max-age=31536000, immutable"
        )
        assert c.get("/").headers["cache-control"] == "no-cache"
        assert c.get("/index.html").headers["cache-control"] == "no-cache"
        assert c.get("/deep/link").headers["cache-control"] == "no-cache"
        h = login(c, "citizen")
        r = c.get("/api/benchmark/summary", headers=h)
        etag = r.headers["etag"]
        assert etag == '"' + hashlib.sha256(r.content).hexdigest() + '"'
        r2 = c.get("/api/benchmark/summary", headers={**h, "If-None-Match": etag})
        assert r2.status_code == 304 and r2.content == b"" and r2.headers["etag"] == etag
        r3 = c.get("/api/benchmark/summary", headers={**h, "If-None-Match": '"nope"'})
        assert r3.status_code == 200 and json.loads(r3.content)["simulated"] is True


# ---- 10: ws handshake rejection headers; no never-retrieved warnings
def test_ws_rejection_carries_security_headers(app_factory):
    with TestClient(app_factory()) as c:
        with pytest.raises(WebSocketDenialResponse) as ei:
            with c.websocket_connect("/api/stream?token=nope"):
                pass
        hd = ei.value.headers
        assert ei.value.status_code == 403
        assert hd["x-content-type-options"] == "nosniff" and hd["cache-control"] == "no-store"


def test_send_timeout_leaves_no_unretrieved_task_exception(app_factory, monkeypatch, caplog):
    from starlette.websockets import WebSocket

    async def hang(self, data):  # noqa: ARG001
        await asyncio.sleep(30)

    app = app_factory(ws_send_timeout_s=0.05)
    with TestClient(app) as c:
        tok = login(c, "officer")["Authorization"].split()[1]
        monkeypatch.setattr(WebSocket, "send_text", hang)
        with caplog.at_level(logging.ERROR, logger="asyncio"):
            try:
                with c.websocket_connect(f"/api/stream?token={tok}") as ws:
                    try:
                        ws.receive_json()
                    except Exception:
                        pass
            except Exception:
                pass
            for _ in range(3):
                gc.collect()
    assert not [r for r in caplog.records if "never retrieved" in r.getMessage()]


# ---- 11: ledger responses flag the simulated clock; verify reports checkpoints
def test_ledger_simulated_clock_and_verify_checkpoints(hero_client):
    c = hero_client
    off = login(c, "officer")
    for path in ("/api/ledger/head", "/api/ledger/entries?limit=2", "/api/ledger/checkpoints"):
        assert c.get(path, headers=off).json()["simulated_clock"] is True
    assert c.post("/api/cases", json={}, headers=off).status_code in (200, 201)
    v = c.post("/api/verify", json={"case_id": "hero-case"}, headers=off).json()
    assert v["status"] == "PASS_PINNED"
    assert v["checkpoint_verified"] is True and v["checkpoints"] >= 1


# ---- 12: strict start body; seek at the end finishes
def test_start_body_strict_types(fresh_client):
    c = fresh_client
    off = login(c, "officer")
    for body in ({"seed": "1"}, {"seed": 1.5}, {"seed": True}, {"speed": "10"}, {"speed": True}):
        assert c.post("/api/demo/start", json=body, headers=off).status_code == 422, body
    assert c.post("/api/demo/start", json={"seed": 1, "speed": 20}, headers=off).status_code == 200


def test_seek_at_or_after_end_reports_finished(app_factory):
    with TestClient(app_factory()) as c:
        off = login(c, "officer")
        c.post("/api/demo/start", json={"seed": 1, "speed": 20}, headers=off)
        wait_status(c, off, ("finished",))
        for t in (1e6, c.get("/api/demo/state", headers=off).json()["duration"]):
            r = c.post("/api/demo/control", json={"action": "seek", "t": t}, headers=off)
            assert r.json()["status"] == "finished", r.json()
            assert r.json()["t"] == r.json()["duration"]
        assert SECRET


# ---- finding 7: the server listens on loopback unless HOST says otherwise
@pytest.mark.parametrize("host_env, expected", [(None, "127.0.0.1"), ("0.0.0.0", "0.0.0.0")])
def test_main_binds_loopback_by_default(monkeypatch, host_env, expected):
    import demo_server.__main__ as entry

    seen = {}
    monkeypatch.setattr(entry.uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setattr(entry, "create_app", lambda: object())
    if host_env is None:
        monkeypatch.delenv("HOST", raising=False)
    else:
        monkeypatch.setenv("HOST", host_env)
    entry.main()
    assert seen["host"] == expected
