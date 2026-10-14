"""REAL speech-to-text tests (opt-in): ``LIVE_STT_TESTS=1 pytest -m stt -s``.

Audio is synthesised with macOS ``say`` (Samantha en_US, Rishi en_IN, Lekha hi_IN), converted to
16 kHz mono PCM16 WAV with ffmpeg, cut into 4 s chunks exactly like the browser does, and sent
through the real API with the cached faster-whisper model (offline: ``HF_HUB_OFFLINE=1``,
``local_files_only=True``). Nothing is mocked except the microphone.

Thresholds (fuzzy, documented): a transcript "contains" a key word when a word of the transcript
has a ``difflib`` similarity ratio >= 0.75 with it (case-insensitive); at least ``min_hits`` of
the key words must be found. The scam script must reach >= HIGH; the benign sentence must stay LOW.
Results (transcripts, levels, latency) are printed with ``-s`` for the report.
"""

from __future__ import annotations

import difflib
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from live_audio.api import create_app
from live_audio.config import LiveAudioConfig
from live_audio.wav import parse_wav

from .conftest import SECRET, auth, wav_bytes

pytestmark = [
    pytest.mark.stt,
    pytest.mark.skipif(os.environ.get("LIVE_STT_TESTS") != "1", reason="set LIVE_STT_TESTS=1"),
    pytest.mark.skipif(shutil.which("say") is None, reason="macOS `say` not available"),
]

FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
CHUNK_S = 4.0

SCAM_EN = (
    "Hello, this is officer Sharma calling from the CBI. A parcel in your name was seized with "
    "drugs. You are under digital arrest. Do not tell anyone about this call and do not "
    "disconnect. Share the OTP you just received and transfer the money to the safe account now."
)
BENIGN_EN = (
    "Hi mom, I reached the office safely. The train was a little late today. "
    "I will call you in the evening after dinner."
)
HINDI = "मैं पुलिस से बोल रहा हूँ। आप डिजिटल अरेस्ट में हैं। किसी को मत बताइए। अभी पैसे ट्रांसफर कीजिए।"
HINGLISH = "Main CBI officer bol raha hoon. Aap digital arrest mein ho. Kisi ko mat batana, abhi OTP bataiye."


def synth(tmp: Path, voice: str, text: str, name: str) -> bytes:
    aiff, wav = tmp / f"{name}.aiff", tmp / f"{name}.wav"
    subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
    subprocess.run(
        [FFMPEG, "-loglevel", "error", "-y", "-i", str(aiff), "-ar", "16000", "-ac", "1",
         "-c:a", "pcm_s16le", str(wav)],
        check=True,
    )  # fmt: skip
    return parse_wav(wav.read_bytes())


def fuzzy_hits(transcript: str, words: list[str]) -> list[str]:
    toks = re.findall(r"\w+", transcript.lower())
    found = []
    for w in words:
        if any(difflib.SequenceMatcher(None, w.lower(), t).ratio() >= 0.75 for t in toks):
            found.append(w)
    return found


@pytest.fixture(scope="module")
def client():
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    cfg = LiveAudioConfig(
        jwt_secret=SECRET,
        stt_engine="faster_whisper",
        stt_model=os.environ.get("LIVE_STT_MODEL", "base"),
        chunks_per_minute=1000,
        stt_timeout_s=120,
    )
    with TestClient(create_app(cfg)) as c:
        yield c


def run_audio(c: TestClient, pcm: bytes, language: str) -> dict:
    sid = c.post("/api/live-audio/sessions", json={"language": language}, headers=auth()).json()[
        "session_id"
    ]
    step = int(CHUNK_S * 16000) * 2
    latencies = []
    last = None
    for seq, off in enumerate(range(0, len(pcm), step)):
        part = pcm[off : off + step]
        t0 = time.monotonic()
        r = c.post(
            f"/api/live-audio/sessions/{sid}/chunks?seq={seq}&start_ms={int(off / 32)}",
            content=wav_bytes(part),
            headers={**auth(), "Content-Type": "audio/wav"},
        )
        latencies.append(time.monotonic() - t0)
        assert r.status_code == 200, r.text
        last = r.json()
        assert last["status"]["stt"] == "ok", last
    state = c.get(f"/api/live-audio/sessions/{sid}", headers=auth()).json()
    c.post(f"/api/live-audio/sessions/{sid}/end", headers=auth())
    text = " ".join(s["text"] for s in state["transcript"])
    out = {
        "text": text,
        "level": state["risk"]["level"],
        "score": state["risk"]["score"],
        "codes": sorted(i["code"] for i in state["indicators"]),
        "latency_s": [round(x, 2) for x in latencies],
        "audio_s": round(len(pcm) / 32000, 2),
        "langs": sorted({s["language"] for s in state["transcript"]}),
    }
    print(f"\n[{language}] {out}")
    return out


def test_english_scam_script(client, tmp_path):
    out = run_audio(client, synth(tmp_path, "Samantha", SCAM_EN, "scam"), "auto")
    hits = fuzzy_hits(
        out["text"], ["officer", "CBI", "digital", "arrest", "OTP", "transfer", "account"]
    )
    assert len(hits) >= 6, (hits, out)
    assert out["level"] in ("HIGH", "CRITICAL")
    assert "DIGITAL_ARREST_PHRASE" in out["codes"]


def test_english_benign(client, tmp_path):
    out = run_audio(client, synth(tmp_path, "Samantha", BENIGN_EN, "benign"), "auto")
    assert len(fuzzy_hits(out["text"], ["office", "train", "evening", "dinner"])) >= 3, out
    assert out["level"] == "LOW"


def test_hindi_devanagari_voice(client, tmp_path):
    out = run_audio(client, synth(tmp_path, "Lekha", HINDI, "hindi"), "hi")
    # romanised output (see README: Hindi is decoded with a romanised-Hinglish prompt)
    hits = fuzzy_hits(out["text"], ["police", "digital", "arrest", "kisi", "transfer"])
    assert len(hits) >= 3, (hits, out)
    assert out["level"] in ("HIGH", "CRITICAL"), out


def test_hinglish_mixed(client, tmp_path):
    out = run_audio(client, synth(tmp_path, "Rishi", HINGLISH, "hinglish"), "auto")
    hits = fuzzy_hits(out["text"], ["CBI", "officer", "digital", "arrest", "kisi", "OTP"])
    assert len(hits) >= 4, (hits, out)
    assert out["level"] in ("HIGH", "CRITICAL"), out
