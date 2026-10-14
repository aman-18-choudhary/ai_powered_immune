"""Live microphone feature mounted in the demo server (auth, body caps, ownership, limits).

The STT engine is a scripted fake here (real-STT tests live in services/live_audio, opt-in).
"""

import struct

from conftest import instant, login, make_settings
from fastapi.testclient import TestClient
from live_audio.config import LiveAudioConfig
from live_audio.stt import Segment, SttResult

from demo_server.api import create_app


class ScriptEngine:
    name, model = "fake", "fake"

    def __init__(self, texts):
        self.texts = list(texts)

    def status(self):
        return {"status": "ready", "reason": None}

    def preload(self):
        return None

    def transcribe(self, pcm16, sample_rate, language):
        t = self.texts.pop(0) if self.texts else ""
        segs = [Segment(0.1, 3.5, t, "en", -0.2, 0.01)] if t else []
        return SttResult(segs, "fake", "fake", "en")


def wav(seconds: float) -> bytes:
    pcm = b"\x00\x00" * int(16000 * seconds)
    fmt = struct.pack("<HHIIHH", 1, 1, 16000, 32000, 2, 16)
    body = (
        b"WAVE"
        + b"fmt "
        + struct.pack("<I", 16)
        + fmt
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


def client(tmp_path, texts=(), **cfg) -> TestClient:
    app = create_app(
        make_settings(tmp_path),
        sleep=instant,
        live_audio_config=LiveAudioConfig(**cfg),
        live_audio_engine=ScriptEngine(texts),
    )
    return TestClient(app)


def test_requires_auth_and_any_role_can_use_it(tmp_path):
    with client(tmp_path) as c:
        assert c.get("/api/live-audio/capabilities").status_code == 401
        for role in ("citizen", "analyst", "officer", "admin"):
            h = login(c, role)
            r = c.get("/api/live-audio/capabilities", headers=h)
            assert r.status_code == 200, role
            assert r.json()["mode"] == "incremental_chunked"
            assert r.json()["scorer"]["status"] == "ready"  # the engine's call-guard scorer


def test_chunk_upload_allows_audio_sized_bodies_only_on_chunk_path(tmp_path):
    with client(
        tmp_path, ["This is officer Sharma from the CBI. You are under digital arrest."]
    ) as c:
        h = login(c, "citizen")
        sid = c.post("/api/live-audio/sessions", json={"language": "en"}, headers=h).json()[
            "session_id"
        ]
        body = wav(4.0)  # 128 KiB: above the global 64 KiB cap
        r = c.post(
            f"/api/live-audio/sessions/{sid}/chunks?seq=0",
            content=body,
            headers={**h, "Content-Type": "audio/wav"},
        )
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["risk"]["level"] in ("HIGH", "CRITICAL")
        assert "DIGITAL_ARREST_PHRASE" in {i["code"] for i in j["indicators"]}
        assert "simulated" not in j  # real audio path: no simulation flag
        # the global cap still applies everywhere else
        r = c.post(
            "/api/citizen/assess",
            content=b'{"message":"' + b"a" * 70_000 + b'"}',
            headers={**h, "Content-Type": "application/json"},
        )
        assert r.status_code == 413
        # and the chunk path has its own cap
        r = c.post(
            f"/api/live-audio/sessions/{sid}/chunks?seq=1",
            content=b"\x00" * 600_000,
            headers={**h, "Content-Type": "audio/wav"},
        )
        assert r.status_code == 413


def test_other_users_get_404(tmp_path):
    with client(tmp_path) as c:
        a, b = login(c, "officer"), login(c, "analyst")
        sid = c.post("/api/live-audio/sessions", json={}, headers=a).json()["session_id"]
        assert c.get(f"/api/live-audio/sessions/{sid}", headers=b).status_code == 404
        assert c.post(f"/api/live-audio/sessions/{sid}/end", headers=b).status_code == 404
        assert c.post(f"/api/live-audio/sessions/{sid}/end", headers=a).status_code == 200
        assert c.get(f"/api/live-audio/sessions/{sid}", headers=a).status_code == 404


def test_citizen_session_cap_keyed_by_address(tmp_path):
    with client(tmp_path, max_sessions_per_user=2) as c:
        ids = []
        for _ in range(2):
            h = login(c, "citizen")  # a fresh citizen token each time (free to mint)
            ids.append(c.post("/api/live-audio/sessions", json={}, headers=h).status_code)
        h = login(c, "citizen")
        r = c.post("/api/live-audio/sessions", json={}, headers=h)
        assert ids == [201, 201]
        assert r.status_code == 409 and r.json()["error"] == "too_many_sessions"


def test_spa_fallback_does_not_shadow_live_audio(tmp_path):
    with client(tmp_path) as c:
        h = login(c, "officer")
        assert c.get("/api/live-audio/nope", headers=h).status_code == 404
        assert (
            c.get("/api/live-audio/capabilities", headers=h).headers["cache-control"] == "no-store"
        )
