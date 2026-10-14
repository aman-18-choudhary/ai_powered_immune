import asyncio
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from live_audio.stt import (
    HI_PROMPT,
    FasterWhisperEngine,
    SttRunner,
    SttTimeout,
    SttUnavailable,
    UnavailableEngine,
    WhisperCppHttpEngine,
    engine_from_env,
)

from .conftest import FakeEngine, pcm_tone


class FakeModel:
    def __init__(self, probs=(("en", 0.9), ("hi", 0.05)), segs=None):
        self.probs = list(probs)
        self.segs = segs
        self.calls = []

    def detect_language(self, audio, vad_filter=False, **kw):
        return self.probs[0][0], self.probs[0][1], self.probs

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        segs = self.segs or [
            SimpleNamespace(
                start=0.0, end=1.0, text=" hello there", avg_logprob=-0.2, no_speech_prob=0.01
            )
        ]
        return iter(segs), SimpleNamespace(language=kw.get("language"), language_probability=0.9)


def make_engine(model, **kw):
    seen = {}

    def factory(name, **fkw):
        seen.update(fkw, name=name)
        return model

    e = FasterWhisperEngine("base", model_factory=factory, **kw)
    return e, seen


def test_faster_whisper_loads_offline_only():
    m = FakeModel()
    e, seen = make_engine(m)
    assert e.status()["status"] == "not_loaded"
    r = e.transcribe(pcm_tone(1.0), 16000, "en")
    assert seen["local_files_only"] is True
    assert seen["device"] == "cpu" and seen["compute_type"] == "int8"
    assert e.status()["status"] == "ready"
    kw = m.calls[0]
    assert kw["vad_filter"] is True and kw["condition_on_previous_text"] is False
    assert kw["language"] == "en" and kw["initial_prompt"] is None
    assert r.segments[0].text == "hello there"
    assert r.engine == "faster_whisper" and r.model == "base"


def test_auto_picks_hindi_and_uses_romanised_prompt():
    m = FakeModel(probs=[("ur", 0.5), ("hi", 0.3), ("en", 0.1)])
    e, _ = make_engine(m)
    r = e.transcribe(pcm_tone(1.0), 16000, "auto")
    assert m.calls[0]["language"] == "hi"
    assert m.calls[0]["initial_prompt"] == HI_PROMPT
    assert r.language == "hi"


def test_auto_restricted_to_en_or_hi():
    m = FakeModel(probs=[("pa", 0.6), ("en", 0.3), ("hi", 0.05)])
    e, _ = make_engine(m)
    e.transcribe(pcm_tone(1.0), 16000, "auto")
    assert m.calls[0]["language"] == "en"


def test_filters_no_speech_and_prompt_echo():
    segs = [
        SimpleNamespace(start=0.0, end=1.0, text=" ", avg_logprob=-0.1, no_speech_prob=0.0),
        SimpleNamespace(start=1.0, end=2.0, text=" mumble", avg_logprob=-1.5, no_speech_prob=0.9),
        SimpleNamespace(
            start=2.0, end=3.0, text=" " + HI_PROMPT, avg_logprob=-0.1, no_speech_prob=0.1
        ),
        SimpleNamespace(
            start=3.0,
            end=4.0,
            text=" Aap digital arrest mein ho",
            avg_logprob=-0.3,
            no_speech_prob=0.1,
        ),
    ]
    e, _ = make_engine(FakeModel(segs=segs))
    r = e.transcribe(pcm_tone(1.0), 16000, "hi")
    assert [s.text for s in r.segments] == ["Aap digital arrest mein ho"]


def test_model_missing_is_unavailable():
    def factory(name, **kw):
        raise OSError("not cached")

    e = FasterWhisperEngine("base", model_factory=factory)
    with pytest.raises(SttUnavailable) as ei:
        e.transcribe(pcm_tone(0.5), 16000, "en")
    assert ei.value.reason == "model_not_available"
    assert e.status()["status"] == "unavailable"


def test_unavailable_engine():
    e = UnavailableEngine("faster_whisper_not_installed")
    assert e.status() == {"status": "unavailable", "reason": "faster_whisper_not_installed"}
    with pytest.raises(SttUnavailable):
        e.transcribe(b"\x00\x00", 16000, "en")


def test_engine_from_env():
    assert engine_from_env({"LIVE_STT_ENGINE": "none"}).status()["status"] == "unavailable"
    e = engine_from_env({"LIVE_STT_ENGINE": "faster_whisper", "LIVE_STT_MODEL": "tiny"})
    assert e.name in ("faster_whisper", "unavailable")
    assert engine_from_env({"LIVE_STT_ENGINE": "whisper_cpp"}).status()["reason"] == (
        "whisper_cpp_url_not_set"
    )
    assert engine_from_env({"LIVE_STT_ENGINE": "bogus"}).status()["reason"] == "unknown_engine"
    with pytest.raises(ValueError):
        engine_from_env({"LIVE_STT_ENGINE": "faster_whisper", "LIVE_STT_MODEL": "../../etc"})


def test_whisper_cpp_engine_with_fake_transport():
    def h(req: httpx.Request):
        assert req.url.path == "/inference"
        assert b"RIFF" in req.content
        return httpx.Response(
            200,
            json={
                "segments": [{"start": 0.0, "end": 1.2, "text": " share the OTP"}],
                "language": "en",
            },
        )

    e = WhisperCppHttpEngine("http://127.0.0.1:8178", transport=httpx.MockTransport(h))
    r = e.transcribe(pcm_tone(1.0), 16000, "en")
    assert [s.text for s in r.segments] == ["share the OTP"]

    def down(req):
        raise httpx.ConnectError("refused")

    e2 = WhisperCppHttpEngine("http://127.0.0.1:8178", transport=httpx.MockTransport(down))
    with pytest.raises(SttUnavailable) as ei:
        e2.transcribe(pcm_tone(0.5), 16000, "en")
    assert ei.value.reason == "unreachable"


async def test_runner_timeout_and_concurrency():
    gate = threading.Event()
    active = []
    peak = []

    class Slow(FakeEngine):
        def transcribe(self, pcm16, sample_rate, language):
            active.append(1)
            peak.append(len(active))
            gate.wait(2.0)
            active.pop()
            return super().transcribe(pcm16, sample_rate, language)

    r = SttRunner(Slow(), max_concurrent=1, timeout_s=0.2)
    t0 = time.monotonic()
    res = await asyncio.gather(
        r.transcribe(b"\x00\x00", 16000, "en"),
        r.transcribe(b"\x00\x00", 16000, "en"),
        return_exceptions=True,
    )
    assert all(isinstance(x, SttTimeout) for x in res)
    assert time.monotonic() - t0 < 1.5
    gate.set()
    await asyncio.sleep(0.1)
    assert max(peak) == 1
    ok = await r.transcribe(b"\x00\x00", 16000, "en")
    assert ok.segments == []
