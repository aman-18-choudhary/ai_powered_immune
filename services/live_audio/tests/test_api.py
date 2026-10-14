import builtins
import logging
import os
import tempfile

from live_audio.stt import SttTimeout, SttUnavailable

from .conftest import auth, send_chunk, start_session, wav_bytes

SCAM = [
    [(0.2, 3.5, "This is officer Sharma from the CBI.")],
    [(0.6, 3.8, "You are under digital arrest.")],
    [(0.6, 3.8, "Do not tell anyone. Share the OTP now.")],
]


# ------------------------------------------------------------------- auth + capabilities
def test_auth_required(make_client):
    c = make_client()
    assert c.get("/api/live-audio/capabilities").status_code == 401
    assert c.post("/api/live-audio/sessions", json={}).status_code == 401
    bad = {"Authorization": "Bearer not-a-jwt"}
    assert c.post("/api/live-audio/sessions", json={}, headers=bad).status_code == 401
    r = c.post("/api/live-audio/sessions/abc/chunks?seq=0", content=wav_bytes(), headers=bad)
    assert r.status_code == 401


def test_capabilities(make_client):
    c = make_client()
    r = c.get("/api/live-audio/capabilities", headers=auth())
    assert r.status_code == 200
    j = r.json()
    assert j["mode"] == "incremental_chunked"
    assert j["stt"]["engine"] == "fake"
    assert j["stt"]["languages"] == ["auto", "hi", "en"]
    assert j["scorer"]["status"] == "ready"
    assert j["llm"]["status"] == "not_configured"
    assert j["audio"] == {"sample_rate": 16000, "channels": 1, "bits": 16, "container": "wav"}
    assert j["chunk_ms"] == 4000 and j["max_chunk_ms"] == 15000
    assert j["privacy"]["raw_audio_stored"] is False


# ------------------------------------------------------------------------- sessions
def test_create_session_validates_language(make_client):
    c = make_client()
    r = c.post("/api/live-audio/sessions", json={"language": "fr"}, headers=auth())
    assert r.status_code == 422
    assert "fr" not in r.text
    r = c.post("/api/live-audio/sessions", json={"language": "hi", "x": 1}, headers=auth())
    assert r.status_code == 422
    r = c.post("/api/live-audio/sessions", json={"language": "hi"}, headers=auth())
    assert r.status_code == 201
    j = r.json()
    assert j["chunk_ms"] == 4000 and j["max_chunk_bytes"] > 128_000 and j["language"] == "hi"


def test_owner_only(make_client):
    c = make_client()
    sid = start_session(c)
    other = auth("user-b")
    assert c.get(f"/api/live-audio/sessions/{sid}", headers=other).status_code == 404
    assert send_chunk(c, sid, 0, h=other).status_code == 404
    assert c.post(f"/api/live-audio/sessions/{sid}/end", headers=other).status_code == 404
    assert c.get(f"/api/live-audio/sessions/{sid}", headers=auth()).status_code == 200


def test_per_user_and_global_caps(make_client):
    c = make_client(cfg={"max_sessions_per_user": 2, "max_sessions": 3})
    start_session(c)
    start_session(c)
    r = c.post("/api/live-audio/sessions", json={}, headers=auth())
    assert r.status_code == 409 and r.json()["error"] == "too_many_sessions"
    start_session(c, auth("user-b"))
    r = c.post("/api/live-audio/sessions", json={}, headers=auth("user-c"))
    assert r.status_code == 503 and r.json()["error"] == "server_busy"


def test_idle_ttl_expiry_cleans_up(make_client, clock):
    c = make_client(cfg={"session_idle_ttl_s": 600})
    sid = start_session(c)
    clock.advance(599)
    assert c.get(f"/api/live-audio/sessions/{sid}", headers=auth()).status_code == 200
    clock.advance(601)
    assert c.get(f"/api/live-audio/sessions/{sid}", headers=auth()).status_code == 404
    assert c.app.state.live_audio.sessions.count() == 0


def test_end_returns_summary_and_deletes(make_client, engine):
    engine.script = [SCAM[1]]
    c = make_client()
    sid = start_session(c)
    assert send_chunk(c, sid, 0).status_code == 200
    r = c.post(f"/api/live-audio/sessions/{sid}/end", headers=auth())
    assert r.status_code == 200
    j = r.json()
    assert j["ended"] is True and j["chunks_processed"] == 1
    assert j["risk"]["level"] in ("HIGH", "CRITICAL")
    assert "not a confirmation of fraud" in j["statement"]
    assert "transcript" not in j
    assert c.get(f"/api/live-audio/sessions/{sid}", headers=auth()).status_code == 404
    assert c.app.state.live_audio.sessions.count() == 0


# ------------------------------------------------------------------- chunk validation
def test_chunk_validation_errors(make_client):
    c = make_client()
    sid = start_session(c)
    r = send_chunk(c, sid, 0, body=wav_bytes(seconds=0.5, channels=2))
    assert r.status_code == 422 and r.json()["error"] == "invalid_audio"
    assert r.json()["detail"] == "unsupported_channels"
    r = send_chunk(c, sid, 0, body=wav_bytes(seconds=0.5, rate=44100))
    assert r.json()["detail"] == "unsupported_rate"
    r = send_chunk(c, sid, 0, body=wav_bytes(seconds=16))
    assert r.status_code == 413 or r.json()["detail"] == "too_long"
    r = send_chunk(c, sid, 0, body=b"")
    assert r.status_code == 422 and r.json()["detail"] == "empty"
    r = send_chunk(c, sid, 0, body=b"\x00" * 600_000)
    assert r.status_code == 413
    r = c.post(
        f"/api/live-audio/sessions/{sid}/chunks?seq=0",
        content=wav_bytes(),
        headers={**auth(), "Content-Type": "text/plain"},
    )
    assert r.status_code == 415
    for q in ("", "?seq=-1", "?seq=abc", "?seq=100001", "?seq=1&start_ms=-5"):
        r = c.post(
            f"/api/live-audio/sessions/{sid}/chunks{q}",
            content=wav_bytes(),
            headers={**auth(), "Content-Type": "audio/wav"},
        )
        assert r.status_code == 422, q
        assert "abc" not in r.text
    # nothing was processed by any of the rejected uploads
    s = c.get(f"/api/live-audio/sessions/{sid}", headers=auth()).json()
    assert s["chunks_processed"] == 0


# ------------------------------------------------------------------ seq handling
def test_seq_idempotent_resend(make_client, engine):
    engine.script = [SCAM[0]]
    c = make_client()
    sid = start_session(c)
    r1 = send_chunk(c, sid, 0)
    r2 = send_chunk(c, sid, 0)
    assert r1.status_code == r2.status_code == 200
    assert r2.json()["duplicate"] is True
    assert r2.json()["segments"] == r1.json()["segments"]
    assert len(engine.calls) == 1
    s = c.get(f"/api/live-audio/sessions/{sid}", headers=auth()).json()
    assert len(s["transcript"]) == 1


def test_out_of_order_and_gaps(make_client, engine):
    engine.script = [[(0.5, 2.0, "two")], [(0.5, 2.0, "one")], [(0.5, 2.0, "five")]]
    c = make_client()
    sid = start_session(c)
    r = send_chunk(c, sid, 2, start_ms=8000)
    assert r.json()["gaps"] == [0, 1]
    r = send_chunk(c, sid, 1, start_ms=4000)
    assert r.json()["gaps"] == [0]
    assert r.json()["late"] is True
    r = send_chunk(c, sid, 5, start_ms=20000)
    assert r.json()["gaps"] == [0, 3, 4]
    s = c.get(f"/api/live-audio/sessions/{sid}", headers=auth()).json()
    # transcript ordered by time, not arrival
    assert [x["text"] for x in s["transcript"]] == ["one", "two", "five"]
    assert s["transcript"][0]["start_s"] == 4.5
    assert s["gaps"] == [0, 3, 4]


def test_seq_too_far_ahead_rejected(make_client):
    c = make_client()
    sid = start_session(c)
    r = send_chunk(c, sid, 5000)
    assert r.status_code == 422 and r.json()["error"] == "seq_out_of_window"


# ------------------------------------------------------------------ risk updates
def test_scam_script_raises_risk_with_warnings_and_segment_links(make_client, engine):
    engine.script = list(SCAM)
    c = make_client()
    sid = start_session(c)
    levels = []
    all_warnings = []
    for i in range(3):
        r = send_chunk(c, sid, i, start_ms=4000 * i)
        assert r.status_code == 200
        j = r.json()
        assert j["status"]["stt"] == "ok" and j["status"]["analysis"] == "ok"
        levels.append(j["risk"]["score"])
        all_warnings += j["warnings"]
    assert levels == sorted(levels)
    assert j["risk"]["level"] == "CRITICAL"
    assert j["risk"]["score_semantics"] == "uncalibrated_heuristic"
    assert "not a confirmation of fraud" in j["risk"]["statement"]
    assert [w["level"] for w in all_warnings][-1] == "CRITICAL"
    assert all("at_s" in w and "wall" in w for w in all_warnings)
    ind = {i["code"]: i for i in j["indicators"]}
    assert "AUTHORITY_IMPERSONATION" in ind and "DIGITAL_ARREST_PHRASE" in ind
    seg_ids = {s["id"] for s in c.get(f"/api/live-audio/sessions/{sid}", headers=auth()).json()["transcript"]}
    for i in j["indicators"]:
        assert set(i["segment_ids"]) <= seg_ids
        assert i["status"] == "detected_not_confirmed"
    assert j["actions"] and all(a["source"] == "static_guidance" for a in j["actions"])
    assert j["explanation"]["rules"]
    assert j["explanation"]["llm"] is None and j["explanation"]["llm_status"] == "not_configured"
    assert len(j["risk"]["history_tail"]) == 3


def test_benign_stays_low(make_client, engine):
    engine.script = [[(0.1, 3.0, "Hi, I am at the station, see you at dinner.")]]
    c = make_client()
    sid = start_session(c)
    j = send_chunk(c, sid, 0).json()
    assert j["risk"]["level"] == "LOW" and j["risk"]["status"] == "assessed"
    assert j["indicators"] == [] and j["warnings"] == []


def test_silence_yields_no_segments_and_no_invented_text(make_client, engine):
    engine.script = [[]]
    c = make_client()
    sid = start_session(c)
    j = send_chunk(c, sid, 0).json()
    assert j["segments"] == []
    assert j["status"]["stt"] == "ok" and j["status"]["analysis"] == "no_speech"
    assert j["risk"]["status"] == "not_assessed"


def test_stt_unavailable_no_risk_update_and_flagged(make_client, engine):
    engine.script = [SCAM[1], SttUnavailable("model_not_available"), SttTimeout("timeout")]
    c = make_client()
    sid = start_session(c)
    j0 = send_chunk(c, sid, 0).json()
    j1 = send_chunk(c, sid, 1).json()
    assert j1["status"]["stt"] == "unavailable"
    assert j1["segments"] == []
    assert j1["risk"]["score"] == j0["risk"]["score"]
    assert j1["risk"]["stale"] is True and j1["risk"]["status"] == "stale"
    assert j1["errors"] and j1["errors"][0]["stage"] == "stt"
    assert j1["errors"][0]["code"] == "model_not_available"
    j2 = send_chunk(c, sid, 2).json()
    assert j2["status"]["stt"] == "timeout"
    assert j2["risk"]["level"] != "LOW"


def test_stt_unavailable_first_chunk_is_not_low(make_client, engine):
    engine.script = [SttUnavailable("engine_disabled")]
    c = make_client()
    sid = start_session(c)
    j = send_chunk(c, sid, 0).json()
    assert j["risk"]["status"] == "stale"
    assert j["risk"]["level"] is None and j["risk"]["score"] is None


def test_scorer_failure_marks_unavailable_not_low(make_client, engine):
    class Boom:
        model_version = "boom"
        fallback_mode = False

        def score_text(self, text):
            raise RuntimeError("boom")

    engine.script = [SCAM[1]]
    c = make_client(scorer=Boom())
    sid = start_session(c)
    j = send_chunk(c, sid, 0).json()
    assert j["status"]["analysis"] == "unavailable"
    assert j["segments"] and j["segments"][0]["text"]  # transcript still shown
    assert j["risk"]["level"] is None and j["risk"]["status"] == "stale"
    assert j["errors"][0]["stage"] == "analysis"


def test_rate_limit_chunks(make_client, engine):
    engine.default = []
    c = make_client(cfg={"chunks_per_minute": 3})
    sid = start_session(c)
    codes = [send_chunk(c, sid, i).status_code for i in range(4)]
    assert codes == [200, 200, 200, 429]


def test_rate_limit_sessions(make_client):
    c = make_client(cfg={"sessions_per_minute": 2, "max_sessions_per_user": 10})
    assert [
        c.post("/api/live-audio/sessions", json={}, headers=auth()).status_code for _ in range(3)
    ] == [201, 201, 429]


# ------------------------------------------------------------------ overlap tail
def test_overlap_tail_prepended_and_duplicates_suppressed(make_client, engine):
    engine.script = [
        [(0.2, 3.9, "you are under digital")],
        # the new chunk starts with the 0.5 s tail of the previous one
        [(0.0, 0.45, "digital"), (0.3, 3.0, "digital arrest, stay on the call")],
    ]
    c = make_client()
    sid = start_session(c)
    send_chunk(c, sid, 0, start_ms=0)
    j = send_chunk(c, sid, 1, start_ms=4000).json()
    assert engine.calls[1][0] == int(4.5 * 16000) * 2  # tail + chunk
    texts = [s["text"] for s in j["segments"]]
    assert texts == ["arrest, stay on the call"]
    assert abs(j["segments"][0]["start_s"] - 3.8) < 1e-6


# ------------------------------------------------------------------ privacy
def test_no_transcript_in_logs(make_client, engine, caplog):
    engine.script = [[(0.0, 3.0, "My secret phrase is pineapple, transfer money now.")]]
    caplog.set_level(logging.DEBUG)
    c = make_client()
    sid = start_session(c)
    send_chunk(c, sid, 0)
    c.post(f"/api/live-audio/sessions/{sid}/end", headers=auth())
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "pineapple" not in text
    assert sid not in text


def test_no_audio_written_to_disk(make_client, engine, monkeypatch, tmp_path):
    engine.script = [SCAM[0]]
    c = make_client()
    sid = start_session(c)
    monkeypatch.chdir(tmp_path)
    before = set(os.listdir(tempfile.gettempdir()))
    real_open = builtins.open
    writes = []

    def guarded_open(file, mode="r", *a, **kw):
        if any(m in mode for m in ("w", "a", "x", "+")):
            writes.append(str(file))
        return real_open(file, mode, *a, **kw)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", lambda *a, **k: writes.append("ntf"))
    monkeypatch.setattr(tempfile, "mkstemp", lambda *a, **k: writes.append("mkstemp"))
    r = send_chunk(c, sid, 0)
    assert r.status_code == 200
    assert writes == []
    assert os.listdir(tmp_path) == []
    after = set(os.listdir(tempfile.gettempdir()))
    assert not [f for f in after - before if f.endswith((".wav", ".pcm", ".raw"))]
    # the session keeps at most the 0.5 s overlap tail in memory, nothing else
    sess = c.app.state.live_audio.sessions._sessions[sid]
    assert len(sess.tail_pcm) <= 16000


def test_state_endpoint_for_reconnect(make_client, engine):
    engine.script = [SCAM[0]]
    c = make_client()
    sid = start_session(c, language="en")
    send_chunk(c, sid, 0)
    s = c.get(f"/api/live-audio/sessions/{sid}", headers=auth()).json()
    assert s["session_id"] == sid and s["language"] == "en"
    assert s["next_seq"] == 1 and s["chunks_processed"] == 1
    assert s["transcript"][0]["text"] == "This is officer Sharma from the CBI."
    assert s["risk"]["level"] in ("MEDIUM", "HIGH")
    assert "indicators" in s and "warnings" in s and "actions" in s
