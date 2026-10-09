import asyncio
import json
import time

import jwt
import pytest
from conftest import SECRET, instant, login, make_settings
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from demo_server.events import CLOSE_FULL, CLOSE_SLOW, EventLog


def tok(c, role="officer"):
    return login(c, role)["Authorization"].split()[1]


@pytest.fixture
def client(app_factory):
    with TestClient(app_factory()) as c:
        yield c


def test_unauthenticated_and_bad_tokens_are_rejected_before_accept(client):
    now = int(time.time())
    expired = jwt.encode(
        {"sub": "s", "role": "officer", "iat": now - 20, "exp": now - 10, "jti": "j"}, SECRET
    )
    for q in ("", "?token=", "?token=garbage", f"?token={expired}"):
        with pytest.raises((WebSocketDisconnect, WebSocketDenialResponse)):
            with client.websocket_connect("/api/stream" + q):
                pass


def test_origin_allowlist(client):
    t = tok(client)
    url = f"/api/stream?token={t}"
    with pytest.raises((WebSocketDisconnect, WebSocketDenialResponse)):
        with client.websocket_connect(url, headers={"Origin": "https://evil.example"}):
            pass
    with client.websocket_connect(url, headers={"Origin": "http://localhost:5173"}) as ws:
        assert ws.receive_json()["kind"] == "scenario.state"
    with client.websocket_connect(url) as ws:  # non-browser client: no Origin header
        assert ws.receive_json()["kind"] == "scenario.state"
    # same-origin (Origin host equals Host) is allowed for the single-process deployment
    with client.websocket_connect(url, headers={"Origin": "http://testserver"}) as ws:
        assert ws.receive_json()["kind"] == "scenario.state"


def test_snapshot_then_live_events_in_id_order(client):
    off = login(client, "officer")
    t = off["Authorization"].split()[1]
    with client.websocket_connect(f"/api/stream?token={t}") as ws:
        snap = ws.receive_json()
        assert snap["kind"] == "scenario.state" and snap["status"] == "idle"
        assert snap["simulated"] is True and snap["events"] == []
        client.post("/api/demo/start", json={"seed": 1, "speed": 20}, headers=off)
        seen = []
        while True:
            e = ws.receive_json()
            seen.append(e)
            if e["kind"] == "scenario.finished":
                break
        ids = [e["id"] for e in seen]
        assert ids == list(range(1, len(ids) + 1))
        assert all(e["run_id"] == seen[0]["run_id"] for e in seen)


def test_late_joiner_gets_state_snapshot_then_nothing_stale(client):
    off = login(client, "officer")
    client.post("/api/demo/start", json={"seed": 1, "speed": 20}, headers=off)
    deadline = time.time() + 30
    while time.time() < deadline:
        if client.get("/api/demo/state", headers=off).json()["status"] == "finished":
            break
        time.sleep(0.02)
    t = off["Authorization"].split()[1]
    with client.websocket_connect(f"/api/stream?token={t}") as ws:
        snap = ws.receive_json()
    assert snap["kind"] == "scenario.state" and snap["status"] == "finished"
    ev = snap["events"]
    assert 0 < len(ev) <= 200
    assert [e["id"] for e in ev] == sorted(e["id"] for e in ev)
    assert ev[-1]["id"] == snap["id"] == snap["last_event_id"]
    assert snap["counters"]["txns_scored"] == 5 and snap["counters"]["ledger_entries"] >= 8
    # a second late joiner sees the same ids (a replay carries stable ids, no renumbering)
    with client.websocket_connect(f"/api/stream?token={t}") as ws:
        again = ws.receive_json()
    assert [e["id"] for e in again["events"]] == [e["id"] for e in ev]


def test_oversized_client_message_closes_connection(client):
    t = tok(client)
    with client.websocket_connect(f"/api/stream?token={t}") as ws:
        ws.receive_json()
        ws.send_text("x" * 5000)
        with pytest.raises(WebSocketDisconnect) as ei:
            for _ in range(50):
                ws.receive_json()
        assert ei.value.code == 1009


def test_connection_cap(app_factory):
    with TestClient(app_factory(max_ws_clients=1)) as c:
        t = tok(c)
        with c.websocket_connect(f"/api/stream?token={t}") as first:
            first.receive_json()
            with c.websocket_connect(f"/api/stream?token={t}") as second:
                with pytest.raises(WebSocketDisconnect) as ei:
                    second.receive_json()
                assert ei.value.code == CLOSE_FULL


def test_slow_client_is_dropped_with_close_code(client):
    t = tok(client)
    eng = client.app.state.engine
    with client.websocket_connect(f"/api/stream?token={t}") as ws:
        ws.receive_json()
        (sub,) = list(eng.log.subscribers)
        sub.dropped = True  # what a full queue does
        with pytest.raises(WebSocketDisconnect) as ei:
            for _ in range(20):
                ws.receive_json()
        assert ei.value.code == CLOSE_SLOW
    assert not eng.log.subscribers


def test_event_log_bounded_queue_never_blocks_the_publisher():
    async def go() -> None:
        log = EventLog(history_size=50, client_queue=8, max_clients=3)
        slow = log.subscribe({"kind": "scenario.state"})
        fast = log.subscribe({"kind": "scenario.state"})
        assert slow and fast
        t0 = time.perf_counter()
        for i in range(1, 101):
            log.append({"id": i, "kind": "x"})
            while not fast.queue.empty():  # a reader that keeps up
                fast.queue.get_nowait()
        assert time.perf_counter() - t0 < 0.5  # publishing is synchronous and non-blocking
        assert slow.dropped and slow not in log.subscribers  # never consumed: dropped
        assert not fast.dropped and fast in log.subscribers
        assert slow.queue.qsize() == 0  # its backlog was freed immediately
        assert len(log.history) == 50 and log.history[0]["id"] == 51
        assert log.dropped_total == 1
        # capacity
        a, b = log.subscribe({}), log.subscribe({})
        assert a and b and log.subscribe({}) is None
        await asyncio.sleep(0)

    asyncio.run(go())


def test_snapshot_is_json_serialisable_and_sized(client):
    off = login(client, "officer")
    client.post("/api/demo/start", json={"seed": 1, "speed": 20}, headers=off)
    time.sleep(0.5)
    t = off["Authorization"].split()[1]
    with client.websocket_connect(f"/api/stream?token={t}") as ws:
        raw = ws.receive_text()
    assert len(raw) < 400_000 and json.loads(raw)["kind"] == "scenario.state"
    _ = SECRET, instant, make_settings
