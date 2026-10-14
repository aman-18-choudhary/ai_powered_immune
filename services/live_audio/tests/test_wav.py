import struct

import pytest

from live_audio.wav import MAX_CHUNK_BYTES, WavError, encode_wav, parse_wav

from .conftest import pcm_tone, wav_bytes


def test_valid_mono_16k_roundtrip():
    pcm = pcm_tone(0.5)
    out = parse_wav(wav_bytes(pcm))
    assert out == pcm
    assert parse_wav(encode_wav(pcm)) == pcm


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"", "empty"),
        (b"NOPE" + b"\x00" * 60, "not_riff"),
        (b"RIFF" + struct.pack("<I", 40) + b"AVI " + b"\x00" * 40, "not_wave"),
        (wav_bytes(seconds=0.2, channels=2), "unsupported_channels"),
        (wav_bytes(seconds=0.2, rate=44100), "unsupported_rate"),
        (wav_bytes(seconds=0.2, bits=8), "unsupported_bits"),
        (wav_bytes(seconds=0.2, fmt=3), "unsupported_format"),
        (wav_bytes(seconds=15.5), "too_long"),
        (wav_bytes(b""), "no_audio"),
        (wav_bytes(seconds=0.2)[:30], "bad_header"),
    ],
)
def test_rejects_bad_input(data, code):
    with pytest.raises(WavError) as ei:
        parse_wav(data)
    assert ei.value.code == code


def test_oversize_rejected_before_parsing():
    with pytest.raises(WavError) as ei:
        parse_wav(b"RIFF" + b"\x00" * (MAX_CHUNK_BYTES + 1))
    assert ei.value.code == "too_large"


def test_truncated_data_chunk_rejected():
    w = wav_bytes(seconds=0.5)
    with pytest.raises(WavError) as ei:
        parse_wav(w[:-100])
    assert ei.value.code == "bad_header"


def test_odd_length_rejected():
    w = bytearray(wav_bytes(b"\x01\x02\x03"))
    with pytest.raises(WavError) as ei:
        parse_wav(bytes(w))
    assert ei.value.code == "bad_header"


def test_extra_chunks_skipped():
    pcm = pcm_tone(0.25)
    w = wav_bytes(pcm)
    # insert a LIST chunk between fmt and data
    idx = w.index(b"data")
    extra = b"LIST" + struct.pack("<I", 5) + b"abcde" + b"\x00"  # odd size + pad byte
    body = w[8:idx] + extra + w[idx:]
    w2 = b"RIFF" + struct.pack("<I", len(body)) + body
    assert parse_wav(w2) == pcm


def test_error_message_never_echoes_input():
    with pytest.raises(WavError) as ei:
        parse_wav(b"SECRETSECRETSECRET" * 4)
    assert "SECRET" not in str(ei.value)
