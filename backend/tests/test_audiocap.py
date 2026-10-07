"""Audio plumbing: the silent test wav the transcription self-test uses, and ffmpeg lookup.

Runs under pytest, or directly: python backend/tests/test_audiocap.py
"""
from __future__ import annotations

import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import audiocap  # noqa: E402


def test_silence_wav_round_trip() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "nested" / "silence.wav"
        assert audiocap.silence_wav(path, 0.4) is True
        assert path.stat().st_size >= audiocap.MIN_WAV_BYTES
        with wave.open(str(path), "rb") as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)


def test_a_too_short_request_is_padded_to_real_audio() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "tiny.wav"
        assert audiocap.silence_wav(path, 0.0) is True
        assert path.stat().st_size >= audiocap.MIN_WAV_BYTES


def test_write_fails_soft_when_the_path_is_unwritable() -> None:
    assert audiocap.write_pcm16_wav(Path("/dev/null/x.wav"), b"\0" * 4096) is False


def test_ffmpeg_path_is_a_string() -> None:
    assert isinstance(audiocap.ffmpeg_path(), str)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
