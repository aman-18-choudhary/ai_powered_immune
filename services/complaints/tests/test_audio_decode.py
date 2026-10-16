"""Decoding stored audio evidence to 16 kHz mono PCM16 (real decoders, no speech-to-text).

WAV always runs (stdlib ``wave`` + numpy). MP3, OGG/Opus, M4A and float WAV need PyAV, which
ships with faster-whisper (the optional 'stt' extra); those tests use ``pytest.importorskip("av")``
and are skipped in CI, where the extra is not installed. Their fixtures are encoded with PyAV."""

import struct
from fractions import Fraction
from pathlib import Path

import pytest
from stt_fakes import make_wav

from complaints.transcribe import DecodeError, decode_audio

pytest.importorskip("numpy")


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


@pytest.mark.parametrize(
    ("rate", "channels", "width"),
    [(16000, 1, 2), (8000, 2, 2), (44100, 2, 2), (22050, 1, 1), (48000, 1, 3), (16000, 2, 4)],
)
def test_wav_shapes_decode_to_16k_mono(tmp_path, rate, channels, width):
    p = write(tmp_path, "a.wav", make_wav(2.0, rate, channels, width))
    d = decode_audio(p, "audio/wav", 180)
    assert abs(len(d.pcm16) - 2.0 * 16000 * 2) <= 4
    assert d.duration_s == pytest.approx(2.0) and d.transcribed_s == pytest.approx(2.0, abs=1e-3)
    assert d.truncated is False
    import numpy as np

    peak = np.abs(np.frombuffer(d.pcm16, dtype="<i2")).max()
    assert 12000 < peak < 18000  # the 0.5-amplitude sine survives mixing and resampling


def test_wav_is_truncated_at_the_limit(tmp_path):
    p = write(tmp_path, "long.wav", make_wav(12.0, 16000, 1, 2))
    d = decode_audio(p, "audio/wav", 5)
    assert len(d.pcm16) == 5 * 16000 * 2
    assert d.truncated is True and d.duration_s == pytest.approx(12.0) and d.transcribed_s == 5


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 24, "wav_unreadable"),
        (b"RIFF\x00\x00\x00\x00WAVE", "wav_unreadable"),
        (make_wav(0.0, 16000, 1, 2), "empty_audio"),
    ],
)
def test_bad_wav_is_decode_failed(tmp_path, data, reason):
    with pytest.raises(DecodeError) as e:
        decode_audio(write(tmp_path, "bad.wav", data), "audio/wav", 180)
    assert (e.value.status, e.value.reason) == ("decode_failed", reason)


def test_missing_file_and_unsupported_type(tmp_path):
    with pytest.raises(DecodeError) as e:
        decode_audio(tmp_path / "nope.wav", "audio/wav", 180)
    assert (e.value.status, e.value.reason) == ("decode_failed", "file_missing")
    with pytest.raises(DecodeError) as e:
        decode_audio(write(tmp_path, "x.flac", b"fLaC"), "audio/flac", 180)
    assert e.value.status == "unsupported_format"


def test_pyav_formats_without_pyav_are_decode_unavailable(tmp_path, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "av", None)
    for mime in ("audio/mpeg", "audio/ogg", "audio/mp4"):
        with pytest.raises(DecodeError) as e:
            decode_audio(write(tmp_path, "x.bin", b"ID3\x04" + b"\x00" * 64), mime, 180)
        assert (e.value.status, e.value.reason) == ("decode_unavailable", "pyav_not_installed")


# ------------------------------------------------------------------ PyAV (skipped in CI)
def _encode(path: Path, fmt: str, codec: str, seconds: float, rate: int = 44100) -> Path:
    av = pytest.importorskip("av")
    import numpy as np

    n = int(seconds * rate)
    t = np.arange(n) / rate
    x = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    with av.open(str(path), "w", format=fmt) as out:
        st = out.add_stream(codec, rate=rate)
        st.layout = "mono"
        fr_size = st.codec_context.frame_size or 1024
        sample_fmt = st.codec_context.format.name
        pts = 0
        for i in range(0, n, fr_size):
            chunk = x[i : i + fr_size]
            if sample_fmt.startswith("s16"):
                arr = (chunk * 32767).astype("<i2").reshape(1, -1)
            elif sample_fmt.startswith("s32"):
                arr = (chunk * 2147483647).astype("<i4").reshape(1, -1)
            else:
                arr = chunk.reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(arr, format=sample_fmt, layout="mono")
            frame.sample_rate = rate
            frame.pts = pts
            frame.time_base = Fraction(1, rate)
            pts += len(chunk)
            for pkt in st.encode(frame):
                out.mux(pkt)
        for pkt in st.encode(None):
            out.mux(pkt)
    return path


@pytest.mark.parametrize(
    ("name", "fmt", "codec", "mime", "rate"),
    [
        ("a.mp3", "mp3", "libmp3lame", "audio/mpeg", 44100),
        ("a.ogg", "ogg", "libopus", "audio/ogg", 48000),
        ("a.m4a", "ipod", "aac", "audio/mp4", 44100),
    ],
)
def test_pyav_formats_decode(tmp_path, name, fmt, codec, mime, rate):
    p = _encode(tmp_path / name, fmt, codec, 3.0, rate)
    d = decode_audio(p, mime, 180)
    assert d.duration_s == pytest.approx(3.0, abs=0.15)
    assert len(d.pcm16) / 32000 == pytest.approx(3.0, abs=0.15) and not d.truncated
    d2 = decode_audio(p, mime, 1.0)
    assert len(d2.pcm16) == 32000 and d2.truncated is True
    assert d2.duration_s == pytest.approx(3.0, abs=0.15)


def test_float_wav_falls_back_to_pyav(tmp_path):
    pytest.importorskip("av")
    import numpy as np

    rate, n = 16000, 16000
    data = (0.5 * np.sin(2 * np.pi * 440 * np.arange(n) / rate)).astype("<f4").tobytes()
    fmt = struct.pack("<HHIIHH", 3, 1, rate, rate * 4, 4, 32)
    wav = (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(data)) + b"WAVE"
           + b"fmt " + struct.pack("<I", len(fmt)) + fmt
           + b"data" + struct.pack("<I", len(data)) + data)  # fmt: skip
    d = decode_audio(write(tmp_path, "f.wav", wav), "audio/wav", 180)
    assert len(d.pcm16) == pytest.approx(32000, abs=64) and d.duration_s == pytest.approx(1.0)


def test_corrupt_mp3_is_decode_failed(tmp_path):
    pytest.importorskip("av")
    with pytest.raises(DecodeError) as e:
        decode_audio(write(tmp_path, "bad.mp3", b"ID3\x04\x00" + b"\x00" * 200), "audio/mpeg",
                     180)  # fmt: skip
    assert e.value.status == "decode_failed"
