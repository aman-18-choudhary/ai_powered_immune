"""Environment-driven settings for the live-audio feature (read once at app creation)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from .wav import MAX_CHUNK_BYTES


def _f(env: Mapping[str, str], name: str, default: float, lo: float, hi: float) -> float:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        v = float(raw)
    except ValueError as e:
        raise ValueError(f"{name} must be a number") from e
    if not lo <= v <= hi:
        raise ValueError(f"{name} must be between {lo} and {hi}")
    return v


@dataclass
class LiveAudioConfig:
    jwt_secret: str | None = None  # standalone app only; the demo server uses its own auth
    stt_engine: str = "faster_whisper"  # faster_whisper | whisper_cpp | none
    stt_model: str = "base"
    stt_timeout_s: float = 30.0
    stt_concurrency: int = 1
    whisper_cpp_url: str | None = None
    chunk_ms: int = 4000
    max_chunk_ms: int = 15000
    max_chunk_bytes: int = MAX_CHUNK_BYTES
    tail_s: float = 0.5
    max_sessions: int = 50
    max_sessions_per_user: int = 2
    session_idle_ttl_s: float = 600.0
    chunks_per_minute: int = 40
    sessions_per_minute: int = 10
    reads_per_minute: int = 120
    max_seq: int = 100_000
    max_seq_ahead: int = 64
    max_segments: int = 2000
    max_transcript_chars: int = 200_000
    ollama_url: str | None = None
    ollama_model: str = "qwen2.5:3b"
    ollama_timeout_s: float = 20.0
    llm_min_interval_s: float = 20.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LiveAudioConfig:
        e = dict(os.environ if env is None else env)
        return cls(
            jwt_secret=e.get("LIVE_AUDIO_JWT_SECRET") or e.get("DEMO_JWT_SECRET") or None,
            stt_engine=(e.get("LIVE_STT_ENGINE") or "faster_whisper").strip().lower(),
            stt_model=e.get("LIVE_STT_MODEL") or "base",
            stt_timeout_s=_f(e, "LIVE_STT_TIMEOUT_S", 30.0, 1.0, 300.0),
            stt_concurrency=int(_f(e, "LIVE_STT_CONCURRENCY", 1, 1, 8)),
            whisper_cpp_url=e.get("WHISPER_CPP_URL") or None,
            chunk_ms=int(_f(e, "LIVE_CHUNK_MS", 4000, 1000, 10000)),
            chunks_per_minute=int(_f(e, "LIVE_CHUNKS_PER_MINUTE", 40, 1, 600)),
            ollama_url=e.get("OLLAMA_URL") or None,
            ollama_model=e.get("OLLAMA_MODEL") or "qwen2.5:3b",
            ollama_timeout_s=_f(e, "OLLAMA_TIMEOUT_S", 20.0, 1.0, 120.0),
        )

    def env_for_engine(self) -> dict[str, str]:
        out = {"LIVE_STT_ENGINE": self.stt_engine, "LIVE_STT_MODEL": self.stt_model}
        if self.whisper_cpp_url:
            out["WHISPER_CPP_URL"] = self.whisper_cpp_url
        for k in ("LIVE_STT_BEAM", "LIVE_STT_CPU_THREADS"):
            if os.environ.get(k):
                out[k] = os.environ[k]
        return out
