from __future__ import annotations

import math
import struct
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import jwt
import pytest
from fastapi.testclient import TestClient

from live_audio.api import create_app
from live_audio.config import LiveAudioConfig
from live_audio.stt import Segment, SttResult, SttUnavailable

SECRET = "live-audio-unit-test-secret-" + "x" * 32
RATE = 16000


def pcm_tone(seconds: float, freq: float = 220.0, amp: float = 0.3, rate: int = RATE) -> bytes:
    n = int(seconds * rate)
    return b"".join(
        struct.pack("<h", int(amp * 32767 * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(n)
    )


def wav_bytes(
    pcm: bytes | None = None,
    *,
    seconds: float = 1.0,
    rate: int = RATE,
    channels: int = 1,
    bits: int = 16,
    fmt: int = 1,
) -> bytes:
    if pcm is None:
        pcm = b"\x00\x00" * int(seconds * rate) * channels
    block = channels * bits // 8
    fmt_chunk = struct.pack("<HHIIHH", fmt, channels, rate, rate * block, block, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt_chunk
    body += b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", len(body)) + body


def token(sub: str = "user-a", role: str = "citizen", secret: str = SECRET) -> str:
    now = int(time.time())
    claims = {"sub": sub, "role": role, "iat": now, "exp": now + 3600, "jti": sub + "-j"}
    return jwt.encode(claims, secret, algorithm="HS256")


def auth(sub: str = "user-a", role: str = "citizen") -> dict[str, str]:
    return {"Authorization": "Bearer " + token(sub, role)}


@dataclass
class FakeEngine:
    """Scripted STT engine: each call pops the next script entry.

    An entry is a list of (start_s, end_s, text) tuples, or an Exception to raise.
    """

    script: list = field(default_factory=list)
    name: str = "fake"
    model: str = "fake-model"
    calls: list[tuple[int, int, str]] = field(default_factory=list)
    default: list = field(default_factory=list)

    def status(self) -> dict:
        return {"status": "ready", "reason": None}

    def preload(self) -> None:
        return None

    def transcribe(self, pcm16: bytes, sample_rate: int, language: str) -> SttResult:
        self.calls.append((len(pcm16), sample_rate, language))
        item = self.script.pop(0) if self.script else self.default
        if isinstance(item, Exception):
            raise item
        lang = "en" if language == "auto" else language
        segs = [Segment(s, e, t, lang, -0.2, 0.01) for s, e, t in item]
        return SttResult(segments=segs, engine=self.name, model=self.model, language=lang)


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_client(engine: FakeEngine, clock: FakeClock) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def make(**kw) -> TestClient:
        cfg_kw = dict(jwt_secret=SECRET, stt_engine="fake")
        cfg_kw.update(kw.pop("cfg", {}))
        app = create_app(
            LiveAudioConfig(**cfg_kw),
            engine=kw.pop("engine", engine),
            clock=kw.pop("clock", clock),
            **kw,
        )
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


def start_session(c: TestClient, h: dict[str, str] | None = None, language: str = "auto") -> str:
    r = c.post("/api/live-audio/sessions", json={"language": language}, headers=h or auth())
    assert r.status_code == 201, r.text
    return r.json()["session_id"]


def send_chunk(
    c: TestClient,
    sid: str,
    seq: int,
    *,
    seconds: float = 4.0,
    h: dict[str, str] | None = None,
    start_ms: int | None = None,
    body: bytes | None = None,
):
    q = f"?seq={seq}" + (f"&start_ms={start_ms}" if start_ms is not None else "")
    return c.post(
        f"/api/live-audio/sessions/{sid}/chunks{q}",
        content=body if body is not None else wav_bytes(seconds=seconds),
        headers={**(h or auth()), "Content-Type": "audio/wav"},
    )


__all__ = ["SttUnavailable"]
