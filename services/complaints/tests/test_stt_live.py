"""REAL speech-to-text through the complaints evidence path (opt-in): ``LIVE_STT_TESTS=1
HF_HUB_OFFLINE=1 python -m pytest -W error -q -s tests/test_stt_live.py``.

Audio is synthesised with macOS ``say`` (Samantha en_US, Lekha hi_IN, Rishi en_IN), converted to
WAV with ffmpeg (44.1 kHz stereo, so the stdlib decoder mixes and resamples) and to M4A/AAC with
``afconvert`` (decoded by PyAV), uploaded as evidence and analysed with the real call-guard
scorer, the real category model and the cached faster-whisper model (``LIVE_STT_MODEL``, default
``base``; never downloaded: ``local_files_only``). Nothing is mocked. Transcripts, levels and
indicator time offsets are printed for the report.

Thresholds (fuzzy, as in services/live_audio/tests/test_stt_real.py): a key word is found when a
transcript word has a ``difflib`` ratio >= 0.75 with it; ``min_hits`` key words must be found.
"""

import difflib
import os
import re
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from conftest import create

pytestmark = [
    pytest.mark.skipif(os.environ.get("LIVE_STT_TESTS") != "1", reason="set LIVE_STT_TESTS=1"),
    pytest.mark.skipif(shutil.which("say") is None, reason="macOS `say` not available"),
]

FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
SCAM_EN = (
    "Hello, this is officer Sharma calling from the CBI. A parcel in your name was seized with "
    "drugs. You are under digital arrest. Do not tell anyone about this call and do not "
    "disconnect. Transfer the money to the safe account now."
)
HINDI = "मैं पुलिस से बोल रहा हूँ। आप डिजिटल अरेस्ट में हैं। किसी को मत बताइए। अभी पैसे ट्रांसफर कीजिए।"
HINGLISH = (
    "Main CBI officer bol raha hoon. Aap digital arrest mein ho. Kisi ko mat batana, abhi paise "
    "transfer karo."
)
CASES = {
    "english_wav": ("Samantha", SCAM_EN, "wav", ["officer", "CBI", "digital", "arrest", "safe"], 4),
    "english_m4a": ("Samantha", SCAM_EN, "m4a", ["officer", "CBI", "digital", "arrest", "safe"], 4),
    "hindi_wav": ("Lekha", HINDI, "wav", ["police", "digital", "arrest"], 1),
    "hinglish_wav": ("Rishi", HINGLISH, "wav", ["CBI", "officer", "digital", "arrest"], 2),
}


def synth(tmp: Path, voice: str, text: str, name: str, fmt: str) -> tuple[bytes, str, str]:
    aiff = tmp / f"{name}.aiff"
    subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
    if fmt == "wav":
        out = tmp / f"{name}.wav"
        subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-i", str(aiff), "-ar", "44100",
                        "-ac", "2", "-c:a", "pcm_s16le", str(out)], check=True)  # fmt: skip
        return out.read_bytes(), "audio/wav", f"{name}.wav"
    out = tmp / f"{name}.m4a"
    subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", str(aiff), str(out)], check=True)
    return out.read_bytes(), "audio/mp4", f"{name}.m4a"


def fuzzy_hits(transcript: str, words: list[str]) -> list[str]:
    toks = re.findall(r"\w+", transcript.lower())
    return [w for w in words
            if any(difflib.SequenceMatcher(None, w.lower(), t).ratio() >= 0.75 for t in toks)]  # fmt: skip


@pytest.fixture(scope="module")
def engine():
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from complaints.transcribe import engine_from_env

    eng = engine_from_env({"LIVE_STT_ENGINE": "faster_whisper",
                           "LIVE_STT_MODEL": os.environ.get("LIVE_STT_MODEL", "base")})  # fmt: skip
    if eng.status().get("status") == "unavailable":
        pytest.skip(f"engine unavailable: {eng.status()}")
    return eng


@pytest.mark.parametrize("name", list(CASES))
async def test_real_transcription_of_uploaded_evidence(name, engine, make_app, tok, tmp_path):
    pytest.importorskip("call_guard")
    from complaints.analysis import ComplaintAnalysisPipeline

    voice, text, fmt, keys, min_hits = CASES[name]
    data, mime, fname = synth(tmp_path, voice, text, name, fmt)
    app = make_app(analysis=ComplaintAnalysisPipeline(), stt_engine=engine)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=300)
    comp = await create(c, tok("a"), category="OTHER", title="Recorded call",
                        description="I received a phone call today and I recorded part of it.")  # fmt: skip
    r = await c.post(f"/complaints/{comp['id']}/evidence", files={"file": (fname, data, mime)},
                     headers=tok("a"))  # fmt: skip
    assert r.status_code == 201, r.text
    eid = r.json()["id"]
    a = (await c.post(f"/complaints/{comp['id']}/analysis", headers=tok("a"))).json()
    [summ] = a["audio_transcription"]
    t = (await c.get(f"/complaints/{comp['id']}/evidence/{eid}/transcript",
                     headers=tok("a"))).json()  # fmt: skip
    transcript = " ".join(s["text"] for s in t.get("segments", []))
    hits = fuzzy_hits(transcript, keys)
    tr_ind = [(i["code"], i["evidence"].get("start_s"), i["evidence"].get("end_s"))
              for i in a["indicators"] if i["stage"] == "audio_transcript"]  # fmt: skip
    print(f"\n[{name}] status={summ['status']} lang={summ['language']} "
          f"duration={summ['duration_s']} engine={summ['engine']}:{summ['model']}")
    print(f"[{name}] transcript={transcript!r}")
    print(f"[{name}] hits={hits} level={a['risk_level']} score={a['risk_score']} "
          f"text_score={a['score_components']['text_score']} "
          f"transcript_score={a['score_components']['transcript_score']}")
    print(f"[{name}] transcript indicators={tr_ind}")
    assert summ["status"] == "transcribed", summ
    assert len(hits) >= min_hits, (hits, transcript)
    located = [st for code, st, _end in tr_ind if code != "SCRIPT_CLASSIFIER_MATCH"]
    assert located and all(st is not None for st in located), tr_ind
    assert a["risk_level"] in ("HIGH", "CRITICAL"), a["risk_level"]
    # reuse: the second run does not transcribe again
    a2 = (await c.post(f"/complaints/{comp['id']}/analysis", headers=tok("a"))).json()
    assert a2["audio_transcription"][0]["reused"] is True
