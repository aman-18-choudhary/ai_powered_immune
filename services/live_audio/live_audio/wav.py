"""Strict in-memory WAV parsing: 16 kHz, mono, 16-bit little-endian PCM only.

Nothing is written to disk. Errors carry a short machine code only (never any of the input).
"""

from __future__ import annotations

import struct

SAMPLE_RATE = 16000
CHANNELS = 1
BITS = 16
BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * BITS // 8
MAX_CHUNK_SECONDS = 15.0
# 15 s of audio is 480,000 bytes; the cap leaves room for headers and small metadata chunks
# (the duration check is what limits audio length)
MAX_CHUNK_BYTES = 512 * 1024


class WavError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def parse_wav(
    data: bytes, *, max_seconds: float = MAX_CHUNK_SECONDS, max_bytes: int = MAX_CHUNK_BYTES
) -> bytes:
    """Validate ``data`` and return its PCM16 sample bytes."""
    if not data:
        raise WavError("empty")
    if len(data) > max_bytes:
        raise WavError("too_large")
    if len(data) < 12 or data[:4] != b"RIFF":
        raise WavError("not_riff")
    if data[8:12] != b"WAVE":
        raise WavError("not_wave")
    pos = 12
    fmt: tuple[int, int, int, int, int, int] | None = None
    pcm: bytes | None = None
    while pos + 8 <= len(data):
        cid = data[pos : pos + 4]
        (size,) = struct.unpack("<I", data[pos + 4 : pos + 8])
        body_start = pos + 8
        body_end = body_start + size
        if body_end > len(data):
            raise WavError("bad_header")
        if cid == b"fmt ":
            if size < 16:
                raise WavError("bad_header")
            fmt = struct.unpack("<HHIIHH", data[body_start : body_start + 16])
        elif cid == b"data":
            if fmt is None:
                raise WavError("bad_header")
            pcm = data[body_start:body_end]
            break
        pos = body_end + (size & 1)  # chunks are word aligned
    if fmt is None or pcm is None:
        raise WavError("bad_header")
    audio_format, channels, rate, _byte_rate, block_align, bits = fmt
    if audio_format != 1:
        raise WavError("unsupported_format")
    if channels != CHANNELS:
        raise WavError("unsupported_channels")
    if rate != SAMPLE_RATE:
        raise WavError("unsupported_rate")
    if bits != BITS or block_align != CHANNELS * BITS // 8:
        raise WavError("unsupported_bits")
    if len(pcm) % 2:
        raise WavError("bad_header")
    if not pcm:
        raise WavError("no_audio")
    if len(pcm) / BYTES_PER_SECOND > max_seconds:
        raise WavError("too_long")
    return pcm


def encode_wav(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    """Minimal 44-byte-header mono PCM16 WAV (used for the whisper.cpp HTTP engine and tests)."""
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data"
    body += struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", len(body)) + body


def duration_s(pcm: bytes) -> float:
    return len(pcm) / BYTES_PER_SECOND
