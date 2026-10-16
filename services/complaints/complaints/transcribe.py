"""Transcription of uploaded audio evidence (MP3, WAV, OGG/Opus, M4A) with the local speech-to-text
engine of ``services/live_audio`` (faster-whisper; optional extra ``complaints[stt]``).

Flow (``Transcriber.prepare``, called by ``POST /complaints/{id}/analysis``)
* Each stored audio file is transcribed at most once: a stored transcript is reused by every
  later analysis. Only definitive outcomes are stored (``transcribed``, ``truncated``,
  ``decode_failed``, ``unsupported_format``); transient ones (engine missing, model not cached,
  timeout, budget, per-run cap) are reported for this run and retried by the next one.
* Limits per analysis run (env, see ``config.Settings``): at most ``max_files`` new files
  (``COMPLAINTS_STT_MAX_FILES_PER_RUN``, default 2; the rest are ``not_attempted_limit``), at most
  ``max_seconds`` of audio per file (``COMPLAINTS_STT_MAX_SECONDS_PER_FILE``, default 180; longer
  files are transcribed up to the limit and marked ``truncated``) and an overall time budget
  (``COMPLAINTS_STT_RUN_BUDGET_S``, default 120 s; a file that cannot start in time is
  ``budget_exhausted``, one that does not finish in time is ``timeout``).
* Decoding to 16 kHz mono PCM16: WAV with the standard-library ``wave`` module (integer PCM; numpy
  for channel mixing and linear resampling); MP3, OGG/Opus, M4A (and WAV that ``wave`` cannot
  read, e.g. float samples) with PyAV, which ships with faster-whisper. Without PyAV those files
  are ``decode_unavailable``. A file that cannot be parsed is ``decode_failed``.
* The engine call runs in a worker thread, one at a time per process (its own semaphore; the
  demo server shares the already-loaded live-audio engine object, so one model is in memory, but a
  live chunk and an evidence file can be transcribed at the same time). A timed-out call keeps
  its slot until the thread really finishes. Language ``auto`` with the live-audio Hindi /
  Hinglish handling (English vs Hindi+Urdu language id, romanised-Hindi prompt).

Privacy: transcript text, file names and ids are never logged (status codes, counts and
durations only). The engine never downloads a model (``local_files_only``).

Status vocabulary (per file; ``STATUS_TEXT`` has the plain-language reason): ``transcribed``,
``truncated``, ``engine_not_configured``, ``model_not_available_offline``, ``timeout``,
``decode_failed``, ``decode_unavailable``, ``unsupported_format``, ``budget_exhausted``,
``not_attempted_limit``, ``engine_error`` (the engine raised), ``not_attempted`` (the caller did
not ask for transcription, e.g. an analysis service used outside the API).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import wave
import weakref
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("complaints.transcribe")

SAMPLE_RATE = 16000
LANGUAGE = "auto"
MAX_SEGMENTS = 2000
MAX_SEGMENT_CHARS = 1000
HARD_SCAN_SECONDS = 4 * 3600  # never decode more than this to measure a duration

TRANSCRIBED = frozenset({"transcribed", "truncated"})
PERSISTED = frozenset({"transcribed", "truncated", "decode_failed", "unsupported_format"})
STATUSES = (
    "transcribed", "truncated", "engine_not_configured", "model_not_available_offline",
    "timeout", "decode_failed", "decode_unavailable", "unsupported_format", "budget_exhausted",
    "not_attempted_limit", "engine_error", "not_attempted",
)  # fmt: skip
STATUS_TEXT: dict[str, str] = {
    "transcribed": "transcribed locally",
    "truncated": "transcribed locally up to the per-file limit; the rest was not analysed",
    "engine_not_configured": "no speech-to-text engine is configured for evidence on this server "
    "(the optional 'stt' extra is not installed or evidence transcription is disabled)",
    "model_not_available_offline": "the speech-to-text model is not available offline on this "
    "server (models are never downloaded at runtime)",
    "timeout": "transcription did not finish within this analysis run's time budget",
    "decode_failed": "the audio file could not be decoded",
    "decode_unavailable": "no decoder for this audio format is installed (PyAV is needed for "
    "MP3, OGG and M4A)",
    "unsupported_format": "this audio format is not supported for transcription",
    "budget_exhausted": "this analysis run's transcription time budget was used up before this "
    "file",
    "not_attempted_limit": "not transcribed in this run: the per-run file limit was reached; "
    "run the analysis again to continue",
    "engine_error": "the speech-to-text engine failed on this file",
    "not_attempted": "transcription was not requested for this analysis",
}
# engine-level reasons (live_audio.stt / this module) that mean "no engine for evidence"
_NOT_CONFIGURED = frozenset({
    "engine_disabled", "faster_whisper_not_installed", "whisper_cpp_url_not_set",
    "unknown_engine", "live_audio_not_installed", "disabled_by_config",
})  # fmt: skip
_PYAV_MIMES = frozenset({"audio/mpeg", "audio/ogg", "audio/mp4", "audio/wav"})
# WAV format tags the stdlib cannot read but PyAV can (float, A-law, mu-law, extensible)
_WAV_PYAV_TAGS = frozenset({3, 6, 7, 0xFFFE})


def status_of_engine_reason(reason: str | None, timeout: bool = False) -> str:
    if timeout:
        return "timeout"
    if reason in _NOT_CONFIGURED:
        return "engine_not_configured"
    if reason in ("model_not_available", "model_not_available_offline"):
        return "model_not_available_offline"
    if reason in ("timeout", "busy_timeout"):
        return "timeout"
    return "engine_error"


# ------------------------------------------------------------------------------- decoding
class DecodeError(Exception):
    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status, self.reason = status, reason


@dataclass(frozen=True)
class DecodedAudio:
    pcm16: bytes  # 16 kHz mono little-endian int16
    duration_s: float  # of the whole file
    transcribed_s: float  # of ``pcm16``
    truncated: bool


def _to_pcm16(samples: Any, rate: int) -> bytes:
    """float32 mono samples in [-1, 1] at ``rate`` -> 16 kHz int16 bytes (linear resampling)."""
    import numpy as np

    if rate != SAMPLE_RATE and len(samples):
        n = max(1, round(len(samples) * SAMPLE_RATE / rate))
        x_new = np.arange(n, dtype=np.float64) * (rate / SAMPLE_RATE)
        samples = np.interp(x_new, np.arange(len(samples), dtype=np.float64), samples)
    out = np.clip(np.asarray(samples) * 32768.0, -32768, 32767).astype("<i2")
    return out.tobytes()


def _decode_wav(path: Path, max_seconds: float) -> DecodedAudio | None:
    """Integer-PCM WAV with the stdlib. ``None`` when the format needs PyAV."""
    try:
        import numpy as np
    except ImportError:
        raise DecodeError("decode_unavailable", "numpy_not_installed") from None
    try:
        with wave.open(str(path), "rb") as w:
            ch, width, rate, nframes = (
                w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            )  # fmt: skip
            if w.getcomptype() != "NONE":
                return None
            if not (1 <= ch <= 8 and width in (1, 2, 3, 4) and 1000 <= rate <= 192000):
                raise DecodeError("decode_failed", "wav_parameters")
            take = min(nframes, int(max_seconds * rate))
            raw = w.readframes(take)
    except wave.Error as exc:
        msg = str(exc)
        if msg.startswith("unknown format:"):
            try:
                tag = int(msg.split(":", 1)[1])
            except ValueError:
                tag = -1
            if tag in _WAV_PYAV_TAGS:
                return None
        raise DecodeError("decode_failed", "wav_unreadable") from None
    except (EOFError, OSError) as exc:
        if isinstance(exc, FileNotFoundError):
            raise DecodeError("decode_failed", "file_missing") from None
        raise DecodeError("decode_failed", "wav_unreadable") from None
    frames = len(raw) // (width * ch)
    if frames == 0:
        raise DecodeError("decode_failed", "empty_audio")
    raw = raw[: frames * width * ch]
    if width == 1:
        a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        a = v.astype(np.float32) / float(1 << 23)
    else:
        a = np.frombuffer(raw, dtype="<i4").astype(np.float32) / float(1 << 31)
    mono = a.reshape(-1, ch).mean(axis=1)
    duration = nframes / rate
    return DecodedAudio(
        _to_pcm16(mono, rate), duration, frames / rate, duration > max_seconds + 1e-6
    )


def _decode_pyav(path: Path, max_seconds: float) -> DecodedAudio:
    try:
        import av
    except ImportError:
        raise DecodeError("decode_unavailable", "pyav_not_installed") from None
    limit = int(max_seconds * SAMPLE_RATE) * 2
    out = bytearray()
    seen = 0.0
    total: float | None = None
    try:
        with av.open(str(path), "r") as container:
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is None:
                raise DecodeError("decode_failed", "no_audio_stream")
            if stream.duration is not None and stream.time_base is not None:
                total = float(stream.duration * stream.time_base)
            elif container.duration is not None:
                total = container.duration / 1_000_000
            resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
            for frame in container.decode(stream):
                if frame.sample_rate:
                    seen += frame.samples / frame.sample_rate
                if len(out) < limit:
                    for f in resampler.resample(frame):
                        out += f.to_ndarray().astype("<i2").tobytes()
                elif total is not None or seen > HARD_SCAN_SECONDS:
                    break  # the duration is known (or the scan cap is hit): stop decoding
            if len(out) < limit:
                for f in resampler.resample(None):
                    out += f.to_ndarray().astype("<i2").tobytes()
    except DecodeError:
        raise
    except FileNotFoundError:
        raise DecodeError("decode_failed", "file_missing") from None
    except Exception as exc:  # av.FFmpegError and friends: corrupt or unreadable content
        log.info("audio decode failed: %s", type(exc).__name__)
        raise DecodeError("decode_failed", "unreadable_audio") from None
    pcm = bytes(out[:limit])
    if not pcm:
        raise DecodeError("decode_failed", "empty_audio")
    duration = total if total is not None and total > 0 else seen
    transcribed = len(pcm) / (2 * SAMPLE_RATE)
    truncated = len(out) >= limit and duration > max_seconds + 0.05
    return DecodedAudio(pcm, max(duration, transcribed), transcribed, truncated)


def decode_audio(path: Path, mime: str, max_seconds: float) -> DecodedAudio:
    """Decode a stored evidence file to 16 kHz mono PCM16 (at most ``max_seconds``)."""
    if mime not in _PYAV_MIMES:
        raise DecodeError("unsupported_format", "unsupported_mime_type")
    if mime == "audio/wav":
        got = _decode_wav(path, max_seconds)
        if got is not None:
            return got
    return _decode_pyav(path, max_seconds)


def pyav_available() -> bool:
    try:
        import av  # noqa: F401
    except ImportError:
        return False
    return True


# ------------------------------------------------------------------------------- engines
@dataclass
class NoEngine:
    """Stands in when no engine is configured for evidence (``reason`` says why)."""

    reason: str
    name: str = "none"
    model: str = "none"

    def status(self) -> dict[str, Any]:
        return {"status": "unavailable", "reason": self.reason}

    def preload(self) -> None:
        return None

    def transcribe(self, pcm16: bytes, sample_rate: int, language: str) -> Any:
        raise _EngineUnavailable(self.reason)


class _EngineUnavailable(Exception):
    status = "unavailable"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def engine_from_env(env: Mapping[str, str] | None = None) -> Any:
    """The live-audio engine selected by ``LIVE_STT_ENGINE`` / ``LIVE_STT_MODEL`` (default
    faster-whisper ``base``), or a ``NoEngine`` when live_audio is not installed."""
    try:
        from live_audio.stt import engine_from_env as la_engine_from_env
    except ImportError:
        return NoEngine("live_audio_not_installed")
    return la_engine_from_env(dict(os.environ if env is None else env))


def _model_cached(model: str) -> bool:
    try:
        from faster_whisper.utils import download_model

        download_model(model, local_files_only=True)  # no network: cache lookup only
        return True
    except Exception:
        return False


async def engine_health(engine: Any, enabled: bool = True) -> dict[str, Any]:
    """Truthful engine state for ``/admin/health`` and ``/models`` without loading a model:
    ``ready`` (loaded), ``available_offline`` (faster-whisper model in the local cache, loaded on
    first use), ``model_not_available_offline``, ``engine_not_configured`` or ``configured`` (a
    whisper.cpp server URL is set; not probed)."""
    name, model = getattr(engine, "name", "none"), getattr(engine, "model", "none")
    base = {"engine": name, "model": model if name != "none" else None, "local": True,
            "evidence_transcription": "enabled" if enabled else "disabled"}  # fmt: skip
    st = engine.status() if engine is not None else {"status": "unavailable", "reason": None}
    s, reason = st.get("status"), st.get("reason")
    if s == "ready":
        return {**base, "status": "ready", "reason": None}
    if s == "configured":
        return {**base, "status": "configured", "reason": "not_probed"}
    if s == "not_loaded":
        if name == "faster_whisper":
            cached = await asyncio.to_thread(_model_cached, model)
            return {**base, "status": "available_offline" if cached else
                    "model_not_available_offline",
                    "reason": None if cached else "model_not_in_local_cache"}  # fmt: skip
        return {**base, "status": "not_loaded", "reason": None}
    return {**base, "status": status_of_engine_reason(reason), "reason": reason}


# ------------------------------------------------------------------------------- transcriber
def file_result(
    evidence_id: str, mime: str, status: str, reason: str | None = None, **kw: Any
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "evidence_id": evidence_id, "mime_type": mime, "status": status,
        "reason": reason or status, "message": STATUS_TEXT.get(status, status),
        "engine": None, "model": None, "language": None, "duration_s": None,
        "transcribed_s": None, "truncated": False, "segments": [], "reused": False,
    }  # fmt: skip
    out.update(kw)
    return out


def result_from_row(row: dict[str, Any], mime: str) -> dict[str, Any]:
    return file_result(
        row["evidence_id"], mime, row["status"], row.get("error_reason") or row["status"],
        engine=row.get("engine"), model=row.get("model"), language=row.get("language"),
        duration_s=row.get("duration_s"), transcribed_s=row.get("transcribed_s"),
        truncated=bool(row.get("truncated")), segments=list(row.get("segments") or []),
        reused=True,
    )  # fmt: skip


class Transcriber:
    def __init__(
        self,
        engine: Any,
        *,
        max_seconds: float = 180.0,
        max_files: int = 2,
        budget_s: float = 120.0,
        enabled: bool = True,
        decoder: Callable[[Path, str, float], DecodedAudio] = decode_audio,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.engine = engine if enabled else NoEngine("disabled_by_config")
        self.enabled = enabled
        self.max_seconds, self.max_files, self.budget_s = max_seconds, max_files, budget_s
        self._decode = decoder
        self._clock = clock
        self._sem: asyncio.Semaphore | None = None
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    async def health(self) -> dict[str, Any]:
        return {
            **await engine_health(self.engine, self.enabled),
            "limits": {"max_seconds_per_file": self.max_seconds,
                       "max_files_per_run": self.max_files, "run_budget_s": self.budget_s},
            "decoders": {"wav": "stdlib", "mp3_ogg_m4a": "pyav" if pyav_available()
                         else "unavailable"},
        }  # fmt: skip

    def _engine_block(self) -> tuple[str, str] | None:
        """(status, reason) when the engine cannot run at all, else None."""
        st = self.engine.status()
        if st.get("status") == "unavailable":
            reason = st.get("reason") or "unavailable"
            return status_of_engine_reason(reason), reason
        return None

    def _lock_for(self, complaint_id: str) -> asyncio.Lock:
        # created and registered synchronously (no await in between): one run per complaint at a
        # time in this process, so two concurrent analyses do not transcribe the same file twice
        lock = self._locks.get(complaint_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[complaint_id] = lock
        return lock

    async def prepare(
        self,
        complaint_id: str,
        files: list[dict[str, Any]],
        path_for: Callable[[dict[str, Any]], Path],
        load_existing: Callable[[], Awaitable[dict[str, dict[str, Any]]]],
        save: Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]],
    ) -> dict[str, dict[str, Any]]:
        """Per stored audio file (by evidence id): its transcript (stored or new) or the precise
        reason it has none. ``files`` are evidence rows; non-audio rows are ignored."""
        audio = [f for f in files if str(f.get("mime_type", "")).startswith("audio/")]
        if not audio:
            return {}
        async with self._lock_for(complaint_id):
            existing = await load_existing()
            deadline = self._clock() + self.budget_s
            attempted = 0
            out: dict[str, dict[str, Any]] = {}
            for f in audio:
                eid, mime = f["id"], f["mime_type"]
                row = existing.get(eid)
                if row is not None:
                    out[eid] = result_from_row(row, mime)
                    continue
                blocked = self._engine_block()
                if blocked is not None:
                    out[eid] = file_result(eid, mime, blocked[0], blocked[1],
                                           engine=self.engine.name)  # fmt: skip
                    continue
                if attempted >= self.max_files:
                    out[eid] = file_result(eid, mime, "not_attempted_limit")
                    continue
                remaining = deadline - self._clock()
                if remaining <= 0:
                    out[eid] = file_result(eid, mime, "budget_exhausted")
                    continue
                attempted += 1
                res = await self._one(eid, mime, path_for(f), deadline)
                if res["status"] in PERSISTED and res["reason"] != "file_missing":
                    stored = await save(res)
                    if stored is not None:  # another process stored it first: use that one
                        res = result_from_row(stored, mime)
                out[eid] = res
                log.info(
                    "evidence transcription status=%s audio_s=%s segments=%d",
                    res["status"], res["transcribed_s"], len(res["segments"]),
                )  # counts only, never text or ids
            return out

    async def _one(self, eid: str, mime: str, path: Path, deadline: float) -> dict[str, Any]:
        try:
            dec = await asyncio.to_thread(self._decode, path, mime, self.max_seconds)
        except DecodeError as e:
            return file_result(eid, mime, e.status, e.reason)
        except Exception as e:  # a decoder bug must not break the analysis
            log.warning("audio decode error: %s", type(e).__name__)
            return file_result(eid, mime, "decode_failed", "decoder_error")
        common = {"duration_s": round(dec.duration_s, 2), "engine": self.engine.name,
                  "model": self.engine.model}  # fmt: skip
        remaining = deadline - self._clock()
        if remaining <= 0:
            return file_result(eid, mime, "timeout", "budget_spent_decoding", **common)
        try:
            r = await self._run_engine(dec.pcm16, remaining)
        except Exception as e:
            reason = getattr(e, "reason", None)
            timeout = getattr(e, "status", None) == "timeout" or isinstance(e, TimeoutError)
            if reason is None and not timeout:
                log.warning("stt engine error: %s", type(e).__name__)
            status = status_of_engine_reason(reason or "engine_error", timeout)
            return file_result(eid, mime, status, reason or status, **common)
        segs = []
        for s in list(getattr(r, "segments", []) or [])[:MAX_SEGMENTS]:
            text = " ".join(str(s.text).split())[:MAX_SEGMENT_CHARS]
            if not text:
                continue
            start = max(0.0, min(float(s.start_s), dec.transcribed_s))
            end = max(start, min(float(s.end_s), dec.transcribed_s))
            segs.append({"start_s": round(start, 2), "end_s": round(end, 2), "text": text})
        return file_result(
            eid, mime, "truncated" if dec.truncated else "transcribed",
            language=(getattr(r, "language", None) or None),
            transcribed_s=round(dec.transcribed_s, 2), truncated=dec.truncated, segments=segs,
            **{**common, "engine": getattr(r, "engine", None) or self.engine.name,
               "model": getattr(r, "model", None) or self.engine.model},
        )  # fmt: skip

    async def _run_engine(self, pcm16: bytes, timeout_s: float) -> Any:
        """The engine call in a worker thread, one at a time; waiting for the slot counts against
        ``timeout_s``. A timed-out call keeps its slot until the thread finishes."""
        if self._sem is None:
            self._sem = asyncio.Semaphore(1)
        sem = self._sem
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        try:
            async with asyncio.timeout(timeout_s):
                await sem.acquire()
        except TimeoutError:
            raise _Timeout("busy_timeout") from None
        try:
            fut = loop.run_in_executor(
                None, self.engine.transcribe, pcm16, SAMPLE_RATE, LANGUAGE
            )
        except BaseException:
            sem.release()
            raise
        fut.add_done_callback(lambda _f: sem.release())
        try:
            return await asyncio.wait_for(asyncio.shield(fut), max(0.01, deadline - loop.time()))
        except TimeoutError:
            fut.add_done_callback(lambda f: f.cancelled() or f.exception())
            raise _Timeout("timeout") from None


class _Timeout(Exception):
    status = "timeout"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
