import io
import logging
import re
import socket
import threading
import time

import pytest
import uvicorn
from conftest import SECRET, login, make_settings
from fastapi.testclient import TestClient
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

from demo_server import logredact
from demo_server.api import create_app

JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")


def test_redact_unit():
    t = "WebSocket /api/stream?token=eyJabc.def.ghi&x=1 [accepted]"
    assert "eyJ" not in logredact.redact(t) and "x=1" in logredact.redact(t)
    assert "secret" not in logredact.redact("Authorization: Bearer secret123")
    assert "secret" not in logredact.redact("hdr Bearer secret123")
    out = logredact.redact("call_id=call_0123456789abcdef score=1")
    assert "call_ref:" in out and "0123456789abcdef" not in out


def test_uvicorn_logs_carry_no_jwt(tmp_path):
    cap = io.StringIO()
    h = logging.StreamHandler(cap)
    h.setFormatter(logging.Formatter("%(name)s %(message)s"))
    root = logging.getLogger()
    old = root.level
    root.addHandler(h)
    root.setLevel(logging.DEBUG)
    for n in ("uvicorn", "uvicorn.error", "uvicorn.access", "websockets"):
        logging.getLogger(n).setLevel(logging.DEBUG)
    app = create_app(make_settings(tmp_path))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    cfg = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="debug",
        access_log=True,
        ws_max_size=4096,
        server_header=False,
    )
    server = uvicorn.Server(cfg)
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        with TestClient(app) as c:  # only to mint a token with the same secret
            tok = login(c, "officer")["Authorization"].split()[1]
        with connect(f"ws://127.0.0.1:{port}/api/stream?token={tok}") as ws:
            assert '"scenario.state"' in ws.recv()
        with pytest.raises(InvalidStatus):
            connect(f"ws://127.0.0.1:{port}/api/stream?token={tok}x")
        bad = tok.rsplit(".", 1)[0] + ".AAAA"
        with pytest.raises(InvalidStatus) as ei:
            connect(f"ws://127.0.0.1:{port}/api/stream?token={bad}")
        assert "server" not in {k.lower() for k in ei.value.response.headers}
    finally:
        server.should_exit = True
        th.join(10)
        root.removeHandler(h)
        root.setLevel(old)
    text = cap.getvalue()
    assert "WebSocket" in text or "websocket" in text.lower()
    assert not JWT.search(text), text
    assert tok not in text and SECRET not in text
