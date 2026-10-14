"""Speech-to-text engines behind one small protocol.

``SttEngine.transcribe(pcm16, sample_rate, language) -> SttResult`` is synchronous and blocking;
``SttRunner`` runs it in a worker thread with a concurrency limit and a per-chunk timeout.

Engines
-------
* ``FasterWhisperEngine`` (default): CTranslate2 Whisper on CPU, int8, loaded lazily in a worker
  thread with ``local_files_only=True`` (a model that is not already cached is reported as
  ``model_not_available``; nothing is ever downloaded at runtime). ``vad_filter=True`` drops
  silence, ``condition_on_previous_text=False`` limits run-on hallucination.
* ``WhisperCppHttpEngine``: a local whisper.cpp ``server`` (``WHISPER_CPP_URL``), POST
  ``/inference``. Tested only against a fake transport in this repository.
* ``UnavailableEngine``: always raises ``SttUnavailable(reason)``.

Languages: ``auto`` | ``hi`` | ``en``. ``auto`` is restricted to English or Hindi: Whisper's
language-id probabilities are compared as ``p(en)`` versus ``p(hi) + p(ur)`` (spoken Hindi and
Urdu are close and Whisper often labels Hindi speech ``ur``).

Hindi decoding (measured deviation, see README): the multilingual base/tiny models write Hindi
speech in Perso-Arabic (Urdu) script or garbled Devanagari. With ``language="hi"`` and the
romanised-Hinglish ``HI_PROMPT`` as ``initial_prompt`` they write usable romanised Hindi, which
call-guard's romanised-Hindi rules understand. Segments that merely echo the prompt are dropped.

Never invented text: no-speech chunks return zero segments, and segments that Whisper itself
marks as probably silence (``no_speech_prob > 0.6`` with ``avg_logprob < -1``) are dropped.
No transcript text is logged.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

log = logging.getLogger("live_audio.stt")

Language = Literal["auto", "hi", "en"]
LANGUAGES: tuple[str, ...] = ("auto", "hi", "en")
HI_PROMPT = "Namaste, main Hindi aur English mein baat kar raha hoon."
MODEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
NO_SPEECH_PROB = 0.6
LOW_LOGPROB = -1.0


@dataclass(frozen=True)
class Segment:
    start_s: float
    end_s: float
    text: str
    language: str
    avg_logprob: float | None = None
    no_speech_prob: float | None = None


@dataclass(frozen=True)
class SttResult:
    segments: list[Segment]
    engine: str
    model: str
    language: str | None = None


class SttUnavailable(Exception):
    """STT could not run for this chunk. ``reason`` is a short machine code."""

    status = "unavailable"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SttTimeout(SttUnavailable):
    status = "timeout"


class SttEngine(Protocol):
    name: str
    model: str

    def status(self) -> dict[str, Any]: ...

    def preload(self) -> None: ...

    def transcribe(self, pcm16: bytes, sample_rate: int, language: str) -> SttResult: ...


def _clean(text: str) -> str:
    return " ".join(text.split())


def _keep(text: str, avg_logprob: float | None, no_speech: float | None) -> bool:
    if not text:
        return False
    if (
        no_speech is not None
        and avg_logprob is not None
        and no_speech > NO_SPEECH_PROB
        and avg_logprob < LOW_LOGPROB
    ):
        return False
    t = text.strip(" .,!?").lower()
    return not (len(t) > 3 and t in HI_PROMPT.lower())  # prompt echo (hallucination)


# --------------------------------------------------------------------------- faster-whisper
class FasterWhisperEngine:
    name = "faster_whisper"

    def __init__(
        self,
        model: str = "base",
        *,
        device: str = "cpu",
        compute_type: str = "int8",
        beam_size: int = 1,
        cpu_threads: int = 0,
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not MODEL_RE.fullmatch(model):
            raise ValueError("invalid model name")
        self.model = model
        self._device, self._compute, self._beam = device, compute_type, beam_size
        self._threads = cpu_threads
        self._factory = model_factory
        self._lock = threading.Lock()
        self._model: Any = None
        self._failed: str | None = None

    def status(self) -> dict[str, Any]:
        if self._model is not None:
            return {"status": "ready", "reason": None}
        if self._failed:
            return {"status": "unavailable", "reason": self._failed}
        return {"status": "not_loaded", "reason": None}

    def _load(self) -> Any:
        with self._lock:
            if self._model is not None:
                return self._model
            if self._failed:
                raise SttUnavailable(self._failed)
            factory = self._factory
            if factory is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError:
                    self._failed = "faster_whisper_not_installed"
                    raise SttUnavailable(self._failed) from None
                factory = WhisperModel
            try:
                self._model = factory(
                    self.model, device=self._device, compute_type=self._compute,
                    cpu_threads=self._threads, local_files_only=True,
                )  # fmt: skip
            except Exception as e:  # not cached, corrupt, unsupported CPU...
                self._failed = "model_not_available"
                log.warning("faster-whisper model %s unavailable (%s)", self.model, type(e).__name__)
                raise SttUnavailable(self._failed) from None
            log.info("faster-whisper model %s loaded", self.model)
            return self._model

    def preload(self) -> None:
        try:
            self._load()
        except SttUnavailable:
            pass

    def _pick_language(self, model: Any, audio: Any) -> str:
        try:
            _, _, probs = model.detect_language(audio, vad_filter=True)
        except Exception:  # no speech after VAD (empty concatenation) or engine quirk
            return "en"
        p = dict(probs or [])
        return "hi" if p.get("hi", 0.0) + p.get("ur", 0.0) > p.get("en", 0.0) else "en"

    def transcribe(self, pcm16: bytes, sample_rate: int, language: str) -> SttResult:
        import numpy as np

        if sample_rate != 16000:
            raise SttUnavailable("unsupported_rate")
        model = self._load()
        audio = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
        lang = self._pick_language(model, audio) if language == "auto" else language
        segs, _info = model.transcribe(
            audio, language=lang, beam_size=self._beam, vad_filter=True,
            condition_on_previous_text=False,
            initial_prompt=HI_PROMPT if lang == "hi" else None,
        )  # fmt: skip
        out = []
        for s in segs:
            text = _clean(s.text)
            lp, ns = getattr(s, "avg_logprob", None), getattr(s, "no_speech_prob", None)
            if _keep(text, lp, ns):
                out.append(Segment(float(s.start), float(s.end), text, lang, lp, ns))
        return SttResult(segments=out, engine=self.name, model=self.model, language=lang)


# ------------------------------------------------------------------------- whisper.cpp
class WhisperCppHttpEngine:
    name = "whisper_cpp"

    def __init__(
        self,
        url: str,
        *,
        timeout_s: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.model = "whisper.cpp-server"
        self._timeout = timeout_s
        self._transport = transport

    def status(self) -> dict[str, Any]:
        return {"status": "configured", "reason": None}

    def preload(self) -> None:
        return None

    def transcribe(self, pcm16: bytes, sample_rate: int, language: str) -> SttResult:
        from .wav import duration_s, encode_wav

        data = {"response_format": "verbose_json", "temperature": "0"}
        if language != "auto":
            data["language"] = language
            if language == "hi":
                data["prompt"] = HI_PROMPT
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout) as c:
                r = c.post(
                    f"{self.url}/inference",
                    files={"file": ("chunk.wav", encode_wav(pcm16, sample_rate), "audio/wav")},
                    data=data,
                )
        except httpx.TimeoutException:
            raise SttTimeout("timeout") from None
        except httpx.HTTPError:
            raise SttUnavailable("unreachable") from None
        if r.status_code != 200:
            raise SttUnavailable("engine_error")
        try:
            j = r.json()
        except ValueError:
            raise SttUnavailable("bad_response") from None
        lang = str(j.get("language") or (language if language != "auto" else "en"))[:8]
        out: list[Segment] = []
        segs = j.get("segments")
        if isinstance(segs, list):
            for s in segs:
                if not isinstance(s, dict):
                    continue
                text = _clean(str(s.get("text", "")))
                if _keep(text, None, None):
                    start = float(s.get("start", 0.0) or 0.0)
                    end = float(s.get("end", start) or start)
                    out.append(Segment(start, end, text, lang))
        elif isinstance(j.get("text"), str) and _keep(_clean(j["text"]), None, None):
            out.append(Segment(0.0, duration_s(pcm16), _clean(j["text"]), lang))
        return SttResult(segments=out, engine=self.name, model=self.model, language=lang)


# ------------------------------------------------------------------------- unavailable
@dataclass
class UnavailableEngine:
    reason: str
    name: str = "unavailable"
    model: str = "none"

    def status(self) -> dict[str, Any]:
        return {"status": "unavailable", "reason": self.reason}

    def preload(self) -> None:
        return None

    def transcribe(self, pcm16: bytes, sample_rate: int, language: str) -> SttResult:
        raise SttUnavailable(self.reason)


def engine_from_env(env: Mapping[str, str]) -> SttEngine:
    kind = (env.get("LIVE_STT_ENGINE") or "faster_whisper").strip().lower()
    if kind == "none":
        return UnavailableEngine("engine_disabled")
    if kind == "faster_whisper":
        model = env.get("LIVE_STT_MODEL") or "base"
        eng = FasterWhisperEngine(
            model,
            beam_size=int(env.get("LIVE_STT_BEAM") or 1),
            cpu_threads=int(env.get("LIVE_STT_CPU_THREADS") or 0),
        )
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return UnavailableEngine("faster_whisper_not_installed")
        return eng
    if kind == "whisper_cpp":
        url = env.get("WHISPER_CPP_URL")
        if not url:
            return UnavailableEngine("whisper_cpp_url_not_set")
        return WhisperCppHttpEngine(url)
    return UnavailableEngine("unknown_engine")


# ------------------------------------------------------------------------------ runner
@dataclass
class SttRunner:
    """Async wrapper: at most ``max_concurrent`` transcriptions run; each call (waiting for a
    slot included) is bounded by ``timeout_s``. A timed-out transcription keeps its slot until
    its worker thread really finishes, so CPU is never oversubscribed."""

    engine: SttEngine
    max_concurrent: int = 1
    timeout_s: float = 30.0
    _sem: asyncio.Semaphore | None = field(default=None, init=False, repr=False)

    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.max_concurrent)
        return self._sem

    async def transcribe(self, pcm16: bytes, sample_rate: int, language: str) -> SttResult:
        sem = self._semaphore()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_s
        try:
            await asyncio.wait_for(sem.acquire(), self.timeout_s)
        except TimeoutError:
            raise SttTimeout("busy_timeout") from None
        fut = loop.run_in_executor(None, self.engine.transcribe, pcm16, sample_rate, language)
        fut.add_done_callback(lambda _f: sem.release())
        try:
            return await asyncio.wait_for(asyncio.shield(fut), max(0.01, deadline - loop.time()))
        except TimeoutError:
            fut.add_done_callback(lambda f: f.cancelled() or f.exception())
            raise SttTimeout("timeout") from None
