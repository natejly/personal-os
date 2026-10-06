"""Audio plumbing: where ffmpeg is, and a silent test wav for the transcription self-test.

Stateless and policy-free; every function degrades instead of raising.
"""
from __future__ import annotations

import logging
import shutil
import wave
from pathlib import Path

log = logging.getLogger("personal_os.audiocap")

# A 16k mono wav header plus a few samples. Below this there is no audio at all.
MIN_WAV_BYTES = 2048


def ffmpeg_path() -> str:
    return shutil.which("ffmpeg") or ""


def write_pcm16_wav(path: Path, pcm: bytes, rate: int = 16000, channels: int = 1) -> bool:
    """Write 16-bit PCM as a wav; no ffmpeg."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(max(1, int(channels)))
            w.setsampwidth(2)
            w.setframerate(max(1, int(rate)))
            w.writeframes(pcm)
        return path.exists() and path.stat().st_size >= MIN_WAV_BYTES
    except Exception as e:  # noqa: BLE001
        log.debug("write_pcm16_wav failed: %s", e)
        return False


def silence_wav(path: Path, seconds: float = 0.4) -> bool:
    """Write a short silent 16k mono wav - real audio carrying no speech, for the stt self-test."""
    n = max(0, int(16000 * 2 * float(seconds)))
    # Pad a too-short request rather than returning a "success" the self-test will then call empty.
    if n + 44 < MIN_WAV_BYTES:
        n = MIN_WAV_BYTES - 44
    return write_pcm16_wav(path, b"\0" * n)
