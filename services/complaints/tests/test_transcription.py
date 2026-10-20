"""Uploaded audio evidence transcription (Phase 6). MOCKED speech-to-text: ``stt_fakes.FakeStt``
returns scripted segments; decoding is real (WAV files generated with ``wave``). The real call-
guard scorer is used where indicators are located (``importorskip``). Real Whisper is exercised
only by the opt-in ``test_stt_live.py``."""

import asyncio
import dataclasses
import datetime as dt
import logging

import httpx
import pytest
from conftest import MIN, PNG, WAV, create
from sqlalchemy import select
from stt_fakes import FakeStt, SttError, make_wav
from test_pipeline_v2 import StubCategory, StubScorer, pipe

from complaints.analysis import ComplaintAnalysisPipeline
from complaints.schema import analyses, evidence_transcripts
from complaints.transcribe import NoEngine

BENIGN = {"category": "OTHER", "title": "Call about my parcel",
          "description": "I got a call about a parcel and I want to report what was said in it."}  # fmt: skip


def cl(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def up(client, headers, cid, name="call.wav", data=None, ctype="audio/wav"):
    data = data if data is not None else make_wav()
    r = await client.post(f"/complaints/{cid}/evidence", files={"file": (name, data, ctype)},
                          headers=headers)  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()


async def analyse(client, headers, cid):
    r = await client.post(f"/complaints/{cid}/analysis", headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def stub_pipe(score=0.1, reasons=()):
    return pipe(StubScorer(score, reasons), StubCategory("OTHER", 0.7))


def transcript_rows(app):
    with app.state.store.engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(select(evidence_transcripts)).all()]


# ------------------------------------------------------------------ store + reuse + timestamps
async def test_transcript_is_stored_with_timestamps_and_reused(make_app, tok):
    eng = FakeStt()
    app = make_app(analysis=stub_pipe(), stt_engine=eng)
    c = cl(app)
    comp = await create(c, tok("a"), **BENIGN)
    ev = await up(c, tok("a"), comp["id"])
    assert ev["kind"] == "audio" and ev["transcription"]["status"] == "not_attempted"
    a1 = await analyse(c, tok("a"), comp["id"])
    assert len(eng.calls) == 1
    pcm_len, rate, lang = eng.calls[0]
    assert (rate, lang) == (16000, "auto") and pcm_len == 16000 * 2  # 1 s mono PCM16
    assert a1["capabilities"]["audio_transcription"] == "available"
    [summary] = a1["audio_transcription"]
    assert summary["status"] == "transcribed" and summary["segment_count"] == 3
    assert summary["reused"] is False and summary["evidence_id"] == ev["id"]
    t = (await c.get(f"/complaints/{comp['id']}/evidence/{ev['id']}/transcript",
                     headers=tok("a"))).json()  # fmt: skip
    assert t["status"] == "transcribed" and t["engine"] == "fake_stt" and t["model"] == "fake-1"
    assert t["segments"][1] == {"start_s": 1.0, "end_s": 1.0, "text": "You are under digital arrest."}
    assert t["duration_s"] == 1.0 and t["transcribed_s"] == 1.0 and t["truncated"] is False
    # re-analysis reuses the stored transcript: the engine is not called again
    a2 = await analyse(c, tok("a"), comp["id"])
    assert len(eng.calls) == 1 and a2["audio_transcription"][0]["reused"] is True
    st = [s for s in a2["provenance"]["stages"] if s["stage"] == "audio_transcription"]
    assert st == [{"stage": "audio_transcription", "source": "ml", "method": "fake_stt:fake-1",
                   "status": "ran", "reason": None, "version": "fake-1",
                   "evidence_id": ev["id"], "reused": True}]  # fmt: skip
    assert len(transcript_rows(app)) == 1
    d = (await c.get(f"/complaints/{comp['id']}", headers=tok("a"))).json()
    tv = d["evidence"][0]["transcription"]
    assert tv["status"] == "transcribed" and tv["source"] == "stored" and tv["segment_count"] == 3
    assert tv["transcript_available"] is True


async def test_segment_times_are_kept_within_the_audio(make_app, tok):
    eng = FakeStt([(0.0, 2.5, "Hello there, this is a test call."), (2.5, 4.0, "Goodbye now.")])
    app = make_app(analysis=stub_pipe(), stt_engine=eng)
    c = cl(app)
    comp = await create(c, tok("a"), **BENIGN)
    ev = await up(c, tok("a"), comp["id"], data=make_wav(seconds=5.0))
    await analyse(c, tok("a"), comp["id"])
    t = (await c.get(f"/complaints/{comp['id']}/evidence/{ev['id']}/transcript",
                     headers=tok("a"))).json()  # fmt: skip
    assert [(s["start_s"], s["end_s"]) for s in t["segments"]] == [(0.0, 2.5), (2.5, 4.0)]


async def test_indicators_point_to_the_evidence_and_time_offsets(make_app, tok):
    pytest.importorskip("call_guard")
    eng = FakeStt()
    app = make_app(analysis=ComplaintAnalysisPipeline(), stt_engine=eng)
    c = cl(app)
    comp = await create(c, tok("a"), **BENIGN)
    ev = await up(c, tok("a"), comp["id"], data=make_wav(seconds=10.0))
    a = await analyse(c, tok("a"), comp["id"])
    tr = [i for i in a["indicators"] if i["stage"] == "audio_transcript"]
    assert tr, a["indicators"]
    by_code = {i["code"]: i for i in tr}
    da = by_code["DIGITAL_ARREST_PHRASE"]
    assert da["source"] == "rule" and da["method"] == "call_guard.rules"
    assert da["evidence"] == {
        "type": "evidence_transcript", "field": "evidence_transcript", "evidence_id": ev["id"],
        "start_s": 3.0, "end_s": 6.5, "snippet": "You are under digital arrest.",
        "granularity": "segment",
    }  # fmt: skip
    assert a["score_components"]["transcript_score"] >= 70
    assert a["risk_level"] in ("HIGH", "CRITICAL") and a["status"] == "COMPLETED"
    assert any("The audio recording matched" in p["text"] and "at 0:03-0:06" in p["text"]
               for p in a["explanation_parts"])  # fmt: skip
    sc = next(s for s in a["provenance"]["stages"] if s["stage"] == "transcript_scoring")
    assert sc["status"] == "ran"


# ------------------------------------------------------------------ limits
async def test_long_file_is_transcribed_up_to_the_limit_and_marked_truncated(make_app, tok,
                                                                            settings):  # fmt: skip
    eng = FakeStt()
    s = dataclasses.replace(settings, stt_max_seconds_per_file=5.0)
    app = make_app(settings=s, analysis=stub_pipe(), stt_engine=eng)
    c = cl(app)
    comp = await create(c, tok("a"), **BENIGN)
    ev = await up(c, tok("a"), comp["id"], data=make_wav(seconds=8.0, rate=16000, channels=1))
    a = await analyse(c, tok("a"), comp["id"])
    assert eng.calls[0][0] == 5 * 16000 * 2
    [summ] = a["audio_transcription"]
    assert summ["status"] == "truncated" and summ["truncated"] is True
    assert summ["duration_s"] == 8.0 and summ["transcribed_s"] == 5.0
    assert a["capabilities"]["audio_transcription"] == "available"
    assert any("only up to the per-file limit of 5 s" in x for x in a["limitations"])
    t = (await c.get(f"/complaints/{comp['id']}/evidence/{ev['id']}/transcript",
                     headers=tok("a"))).json()  # fmt: skip
    assert t["status"] == "truncated" and t["truncated"] is True
    assert max(seg["end_s"] for seg in t["segments"]) <= 5.0


async def test_per_run_file_cap_then_next_run_continues(make_app, tok, settings):
    eng = FakeStt()
    app = make_app(settings=settings, analysis=stub_pipe(), stt_engine=eng)
    c = cl(app)
    comp = await create(c, tok("a"), **BENIGN)
    ids = [(await up(c, tok("a"), comp["id"], f"c{i}.wav", make_wav(freq=300 + 100 * i)))["id"]
           for i in range(3)]  # fmt: skip
    a1 = await analyse(c, tok("a"), comp["id"])
    st = {x["evidence_id"]: x["status"] for x in a1["audio_transcription"]}
    assert [st[i] for i in ids] == ["transcribed", "transcribed", "not_attempted_limit"]
    assert len(eng.calls) == 2
    assert a1["status"] == "PARTIAL" and a1["risk_level"] is None  # never LOW with a gap
    assert a1["capability_reasons"]["audio_transcription"] == "not_attempted_limit"
    left = [u for u in a1["unanalysed_attachments"] if u["type"] == "audio"]
    assert left == [{"type": "audio", "mime_type": "audio/wav", "count": 1, "analysed": False,
                     "reason": "not transcribed in this run: the per-run file limit was reached; "
                     "run the analysis again to continue", "status": "not_attempted_limit",
                     "evidence_id": ids[2]}]  # fmt: skip
    ev = (await c.get(f"/complaints/{comp['id']}/evidence", headers=tok("a"))).json()["items"]
    assert ev[2]["transcription"]["status"] == "not_attempted_limit"
    assert ev[2]["transcription"]["source"] == "last_analysis"
    a2 = await analyse(c, tok("a"), comp["id"])
    assert len(eng.calls) == 3
    assert [x["status"] for x in a2["audio_transcription"]] == ["transcribed"] * 3
    assert [x["reused"] for x in a2["audio_transcription"]] == [True, True, False]
    assert a2["risk_level"] == "LOW" and a2["status"] == "COMPLETED"


async def test_run_budget_gives_timeout_then_budget_exhausted(make_app, tok, settings):
    eng = FakeStt(delay=0.6)
    s = dataclasses.replace(settings, stt_run_budget_s=0.3)
    app = make_app(settings=s, analysis=stub_pipe(), stt_engine=eng)
    c = cl(app)
    comp = await create(c, tok("a"), **BENIGN)
    for i in range(2):
        await up(c, tok("a"), comp["id"], f"c{i}.wav", make_wav(freq=500 + i * 50))
    a = await analyse(c, tok("a"), comp["id"])
    assert [x["status"] for x in a["audio_transcription"]] == ["timeout", "budget_exhausted"]
    assert a["capability_reasons"]["audio_transcription"] == "multiple_reasons"
    assert a["risk_level"] is None and a["status"] == "PARTIAL"
    assert transcript_rows(app) == []  # transient outcomes are not stored; the next run retries
    stages = {s_["evidence_id"]: s_["status"] for s_ in a["provenance"]["stages"]
              if s_["stage"] == "audio_transcription"}  # fmt: skip
    assert sorted(stages.values()) == ["skipped", "timeout"]
    await asyncio.sleep(0.7)  # let the timed-out worker thread finish


# ------------------------------------------------------------------ failure statuses
@pytest.mark.parametrize(
    ("engine", "status", "reason"),
    [
        (NoEngine("engine_disabled"), "engine_not_configured", "engine_disabled"),
        (NoEngine("faster_whisper_not_installed"), "engine_not_configured",
         "faster_whisper_not_installed"),
        (FakeStt(state="unavailable", reason="model_not_available"),
         "model_not_available_offline", "model_not_available"),
        (FakeStt(error=SttError("model_not_available")), "model_not_available_offline",
         "model_not_available"),
        (FakeStt(error=SttError("timeout", status="timeout")), "timeout", "timeout"),
        (FakeStt(error=RuntimeError("boom")), "engine_error", "engine_error"),
    ],
)  # fmt: skip
async def test_engine_failures_have_precise_statuses_and_never_low(make_app, tok, engine, status,
                                                                    reason):  # fmt: skip
    app = make_app(analysis=stub_pipe(), stt_engine=engine)
    c = cl(app)
    comp = await create(c, tok("a"), **BENIGN)
    ev = await up(c, tok("a"), comp["id"])
    a = await analyse(c, tok("a"), comp["id"])
    [summ] = a["audio_transcription"]
    assert (summ["status"], summ["reason"]) == (status, reason)
    assert a["capabilities"]["audio_transcription"] == "unavailable"
    assert a["capability_reasons"]["audio_transcription"] == status
    assert "audio_transcription" in a["unavailable_capabilities"]
    # the text alone is LOW (stub score 10), but a recording could not be analysed
    assert a["risk_level"] is None and a["risk_score"] is None and a["status"] == "PARTIAL"
    assert a["score_components"]["withheld"] is True
    assert any("audio recording could not be transcribed" in x for x in a["limitations"])
    assert "no speech-to-text engine is installed" not in str(a)
    assert transcript_rows(app) == []
    tv = (await c.get(f"/complaints/{comp['id']}/evidence", headers=tok("a"))).json()["items"][0]
    assert tv["transcription"]["status"] == status and tv["transcription"]["reason"] == reason
    r = await c.get(f"/complaints/{comp['id']}/evidence/{ev['id']}/transcript", headers=tok("a"))
    assert r.status_code == 404 and r.json()["error"] == "transcript_not_available"
    assert r.json()["transcription"]["status"] == status


async def test_undecodable_file_is_decode_failed_and_stored_once(make_app, tok):
    eng = FakeStt()
    app = make_app(analysis=stub_pipe(), stt_engine=eng)
    c = cl(app)
    comp = await create(c, tok("a"), **BENIGN)
    await up(c, tok("a"), comp["id"], data=WAV)  # a WAV header followed by zeros
    a = await analyse(c, tok("a"), comp["id"])
    assert a["audio_transcription"][0]["status"] == "decode_failed"
    assert eng.calls == []
    [row] = transcript_rows(app)
    assert row["status"] == "decode_failed" and row["error_reason"] == "wav_unreadable"
    a2 = await analyse(c, tok("a"), comp["id"])  # definitive: not retried
    assert a2["audio_transcription"][0]["reused"] is True and eng.calls == []
    assert a2["risk_level"] is None


async def test_pyav_missing_is_decode_unavailable(make_app, tok, monkeypatch):
    import sys

    from conftest import MP3

    monkeypatch.setitem(sys.modules, "av", None)  # import av -> ImportError
    eng = FakeStt()
    app = make_app(analysis=stub_pipe(), stt_engine=eng)
    c = cl(app)
    comp = await create(c, tok("a"), **BENIGN)
    await up(c, tok("a"), comp["id"], "call.mp3", MP3, "audio/mpeg")
    a = await analyse(c, tok("a"), comp["id"])
    summ = a["audio_transcription"][0]
    assert (summ["status"], summ["reason"]) == ("decode_unavailable", "pyav_not_installed")
    assert transcript_rows(app) == [] and eng.calls == []
    h = (await c.get("/models", headers=tok("a"))).json()["stt"]
    assert h["decoders"]["mp3_ogg_m4a"] == "unavailable"


async def test_concurrent_runs_transcribe_each_file_once(make_app, tok):
    eng = FakeStt(delay=0.2)
    app = make_app(analysis=stub_pipe(), stt_engine=eng)
    c = cl(app)
    comp = await create(c, tok("a"), **BENIGN)
    await up(c, tok("a"), comp["id"])
    r1, r2 = await asyncio.gather(analyse(c, tok("a"), comp["id"]),
                                  analyse(c, tok("a"), comp["id"]))  # fmt: skip
    assert len(eng.calls) == 1
    assert sorted(x["audio_transcription"][0]["reused"] for x in (r1, r2)) == [False, True]


# ------------------------------------------------------------------ access and deletion
async def test_transcript_access_owner_and_staff_only(make_app, tok, inv, admin):
    app = make_app(analysis=stub_pipe(), stt_engine=FakeStt())
    c = cl(app)
    await c.post("/auth/entry", json={"entry": "investigator"}, headers=inv)
    comp = await create(c, tok("a"), **BENIGN)
    ev = await up(c, tok("a"), comp["id"])
    img = (await c.post(f"/complaints/{comp['id']}/evidence",
                        files={"file": ("s.png", PNG, "image/png")}, headers=tok("a"))).json()  # fmt: skip
    assert img["kind"] == "image" and img["transcription"] is None
    await analyse(c, tok("a"), comp["id"])
    url = f"/complaints/{comp['id']}/evidence/{ev['id']}/transcript"
    for h in (tok("a"), inv, admin):
        r = await c.get(url, headers=h)
        assert r.status_code == 200 and r.json()["segments"], r.text
    r = await c.get(url, headers=tok("b"))
    assert r.status_code == 404 and r.json()["error"] == "not_found"
    assert "segments" not in r.text
    other = await create(c, tok("b"), **BENIGN)
    r = await c.get(f"/complaints/{other['id']}/evidence/{ev['id']}/transcript", headers=tok("b"))
    assert r.status_code == 404
    r = await c.get(f"/complaints/{comp['id']}/evidence/{img['id']}/transcript", headers=tok("a"))
    assert r.status_code == 404 and r.json()["error"] == "not_audio_evidence"
    r = await c.get(f"/complaints/{comp['id']}/evidence/nope/transcript", headers=tok("a"))
    assert r.status_code == 404


@pytest.mark.parametrize("who", ["owner", "admin"])
async def test_transcript_is_deleted_with_the_evidence(make_app, tok, admin, who):
    app = make_app(analysis=stub_pipe(), stt_engine=FakeStt())
    c = cl(app)
    comp = await create(c, tok("a"), **BENIGN)
    ev = await up(c, tok("a"), comp["id"])
    await analyse(c, tok("a"), comp["id"])
    assert len(transcript_rows(app)) == 1
    h = tok("a") if who == "owner" else admin
    r = await c.delete(f"/complaints/{comp['id']}/evidence/{ev['id']}", headers=h)
    assert r.status_code == 200, r.text
    assert transcript_rows(app) == []
    r = await c.get(f"/complaints/{comp['id']}/evidence/{ev['id']}/transcript", headers=tok("a"))
    assert r.status_code == 404
    # the next analysis has no audio left
    a = await analyse(c, tok("a"), comp["id"])
    assert a["audio_transcription"] == []
    assert a["capabilities"]["audio_transcription"] == "skipped_no_input"


async def test_transcript_text_is_never_logged(make_app, tok, caplog):
    caplog.set_level(logging.DEBUG)
    app = make_app(analysis=stub_pipe(), stt_engine=FakeStt())
    c = cl(app)
    comp = await create(c, tok("a"), **BENIGN)
    ev = await up(c, tok("a"), comp["id"], name="secret-name.wav")
    await analyse(c, tok("a"), comp["id"])
    await c.get(f"/complaints/{comp['id']}/evidence/{ev['id']}/transcript", headers=tok("a"))
    # the server's own loggers (the test client's httpx logger prints request URLs)
    logs = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("complaints"))
    assert "digital arrest" not in logs and "officer Sharma" not in logs
    assert "secret-name" not in logs and ev["id"] not in logs
    assert "evidence transcription status=transcribed" in logs


# ------------------------------------------------------------------ health, models, legacy
async def test_health_and_models_report_the_stt_state(make_app, tok, admin):
    c = cl(make_app(stt_engine=NoEngine("faster_whisper_not_installed")))
    h = (await c.get("/admin/health", headers=admin)).json()["stt"]
    assert h["status"] == "engine_not_configured" and h["reason"] == "faster_whisper_not_installed"
    assert h["limits"] == {"max_seconds_per_file": 180.0, "max_files_per_run": 2,
                           "run_budget_s": 120.0}  # fmt: skip
    c2 = cl(make_app(stt_engine=FakeStt()))
    m = (await c2.get("/models", headers=tok("a"))).json()["stt"]
    assert m["status"] == "ready" and m["engine"] == "fake_stt" and m["local"] is True
    assert "audio evidence" in m["used_for"]


async def test_disabled_by_config(make_app, tok, settings, admin):
    s = dataclasses.replace(settings, stt_enabled=False)
    eng = FakeStt()
    app = make_app(settings=s, analysis=stub_pipe(), stt_engine=eng)
    c = cl(app)
    comp = await create(c, tok("a"), **BENIGN)
    await up(c, tok("a"), comp["id"])
    a = await analyse(c, tok("a"), comp["id"])
    assert a["audio_transcription"][0]["status"] == "engine_not_configured"
    assert a["audio_transcription"][0]["reason"] == "disabled_by_config" and eng.calls == []
    h = (await c.get("/admin/health", headers=admin)).json()["stt"]
    assert h["evidence_transcription"] == "disabled"


async def test_default_engine_comes_from_the_live_audio_env(settings, idp, monkeypatch):
    from dna_fakes import FakeEmbedder

    from complaints.api import create_app

    monkeypatch.setenv("LIVE_STT_ENGINE", "none")
    app = create_app(settings, jwks=idp.jwks(), dna_embedder=FakeEmbedder(), dna_background=False)
    try:
        c = cl(app)
        r = await c.get("/models", headers={"Authorization": f"Bearer {idp.token()}"})
        st = r.json()["stt"]
        live = pytest.importorskip("live_audio")
        assert live and st["status"] == "engine_not_configured" and st["reason"] == "engine_disabled"
    finally:
        app.state.store.close()


async def test_legacy_rows_with_the_old_reason_read_accurately(make_app, tok):
    app = make_app()
    c = cl(app)
    comp = await create(c, tok("a"), **MIN)
    with app.state.store.engine.begin() as conn:
        conn.execute(analyses.insert().values(
            id="legacy-audio", complaint_id=comp["id"], version=1,
            created_at=dt.datetime.now(dt.UTC),
            pipeline_version="complaints-analysis-v2+clf-v1+category-v1",
            capabilities={"audio_transcription": "unavailable"}, risk_score=40,
            risk_level="MEDIUM", classification=None, confidence=None, indicators=[],
            explanation="x", recommended_actions=[], limitations=[], status="PARTIAL",
            detail={"capability_reasons": {"audio_transcription":
                                           "no_transcription_engine_installed"},
                    "unanalysed_attachments": [{"type": "audio", "mime_type": "audio/mpeg",
                                                "count": 1, "analysed": False,
                                                "reason": "no speech-to-text engine is installed"}]},
        ))  # fmt: skip
    g = (await c.get(f"/complaints/{comp['id']}/analysis", headers=tok("a"))).json()
    assert g["capability_reasons"]["audio_transcription"] == "not_implemented_at_analysis_time"
    [u] = g["unanalysed_attachments"]
    assert u["reason"] == "audio transcription of evidence was not implemented when this " \
        "analysis ran" and u["status"] == "not_implemented_at_analysis_time"
    assert g["audio_transcription"] == [] and g["risk_level"] == "MEDIUM"


async def test_no_audio_means_skipped_no_input():
    out = await stub_pipe().analyse({**BENIGN, "evidence": []})
    assert out.capabilities["audio_transcription"] == "skipped_no_input"
    assert out.extra["capability_reasons"]["audio_transcription"] == "no_audio_evidence"
    assert out.status == "COMPLETED" and out.risk_level == "LOW"


# ------------------------------------------------------------------ category sees transcripts
async def test_category_model_classifies_description_and_transcripts(make_app, tok):
    # finding 2 (2026-10-17): an audio-only complaint was categorised from the description alone
    cat = StubCategory("DIGITAL_ARREST", 0.7)
    app = make_app(analysis=pipe(StubScorer(0.1), cat), stt_engine=FakeStt())
    c = cl(app)
    comp = await create(c, tok("a"), **BENIGN)
    await up(c, tok("a"), comp["id"], data=make_wav(seconds=10.0))
    a = await analyse(c, tok("a"), comp["id"])
    assert "You are under digital arrest." in cat.seen[-1]  # the transcript reached the model
    assert BENIGN["description"] in cat.seen[-1]  # together with the description
    assert a["category_model"]["evidence"]["audio_transcripts_included"] == 1
    assert a["classification"] == "DIGITAL_ARREST"


async def test_category_model_sees_only_the_description_without_audio():
    cat = StubCategory("OTHER", 0.7)
    out = await pipe(StubScorer(0.1), cat).analyse({**BENIGN, "evidence": []})
    assert cat.seen[-1] == f"{BENIGN['title']}\n{BENIGN['description']}"
    assert out.extra["category_model"]["evidence"]["audio_transcripts_included"] == 0


async def test_encrypted_audio_evidence_is_still_transcribed(make_app, settings, tok):
    # Phase 14: with an evidence key the recording is ciphertext on disk; the decoder gets a
    # temporary plaintext copy that is removed after the analysis
    s = dataclasses.replace(settings, evidence_key=bytes(range(32)))
    eng = FakeStt()
    app = make_app(settings=s, analysis=stub_pipe(), stt_engine=eng)
    c = cl(app)
    comp = await create(c, tok("a"), **BENIGN)
    await up(c, tok("a"), comp["id"])
    from complaints.evidence import MAGIC

    stored = [p for p in s.evidence_dir.rglob("*") if p.is_file() and ".tmp" not in p.parts]
    assert stored and all(p.read_bytes().startswith(MAGIC) for p in stored)
    await analyse(c, tok("a"), comp["id"])
    rows = transcript_rows(app)
    assert rows and rows[0]["status"] == "transcribed"
    assert not list((s.evidence_dir / ".tmp").glob("plain-*"))
