"""MOCKED speech-to-text for the complaints tests: a scripted engine (no Whisper, no model) with
the same interface as ``live_audio.stt`` engines, and real WAV files built with ``wave``."""

import io
import math
import struct
import time
import wave
from dataclasses import dataclass


@dataclass(frozen=True)
class Seg:
    start_s: float
    end_s: float
    text: str


@dataclass
class Res:
    segments: list
    engine: str = "fake_stt"
    model: str = "fake-1"
    language: str = "en"


class SttError(Exception):
    """Like ``live_audio.stt.SttUnavailable``: a ``reason`` and a ``status``."""

    def __init__(self, reason: str, status: str = "unavailable") -> None:
        super().__init__(reason)
        self.reason, self.status = reason, status


SCAM_SEGMENTS = [
    (0.0, 3.0, "Hello, this is officer Sharma calling from the CBI."),
    (3.0, 6.5, "You are under digital arrest."),
    (6.5, 9.0, "Do not tell anyone about this call."),
]


class FakeStt:
    name = "fake_stt"
    model = "fake-1"

    def __init__(self, segments=SCAM_SEGMENTS, *, delay=0.0, error=None, state="ready",
                 reason=None, language="en"):  # fmt: skip
        self.segments = [Seg(*s) for s in segments]
        self.delay, self.error, self.state, self.reason = delay, error, state, reason
        self.language = language
        self.calls: list[tuple[int, int, str]] = []

    def status(self):
        return {"status": self.state, "reason": self.reason}

    def preload(self):
        return None

    def transcribe(self, pcm16: bytes, sample_rate: int, language: str):
        self.calls.append((len(pcm16), sample_rate, language))
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return Res(list(self.segments), language=self.language)


def make_wav(seconds: float = 1.0, rate: int = 8000, channels: int = 2, width: int = 2,
             freq: float = 440.0) -> bytes:  # fmt: skip
    """A real PCM WAV (sine tone) of the given shape."""
    n = int(seconds * rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            v = math.sin(2 * math.pi * freq * i / rate) * 0.5
            if width == 1:
                sample = struct.pack("<B", int(128 + v * 127))
            elif width == 2:
                sample = struct.pack("<h", int(v * 32767))
            elif width == 3:
                sample = int(v * 8388607).to_bytes(3, "little", signed=True)
            else:
                sample = struct.pack("<i", int(v * 2147483647))
            frames += sample * channels
        w.writeframes(bytes(frames))
    return buf.getvalue()
