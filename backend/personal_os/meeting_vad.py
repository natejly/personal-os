"""A silence gate for meeting segments, in the standard library only.

Whisper invents text on silence and room noise ("Thank you.", "Thanks for watching", loops), and
every 20 s segment of a quiet mic is a billed round trip that rolls that invention into the
transcript, the FTS index and the enhance prompt. faster-whisper and whisper.cpp both
gate on a VAD for this reason. This one is a deliberately small adaptive energy detector (no
numpy, no model): the noise floor is the 10th percentile of frame RMS, so a segment that is
uniformly loud room noise still reads as "no speech" while a tone or voice well above it does not.

It only ever answers "is there anything here worth transcribing". An unreadable or non-16-bit
wav returns ok=False and the caller transcribes anyway: audio is never dropped on a parse failure.
"""
from __future__ import annotations

import array
import math
import operator
import wave
from pathlib import Path
from typing import Any

MERGE_GAP_S = 0.3


def analyze(path: Path | str, frame_ms: int = 30, min_speech_ms: int = 250, margin: float = 2.5,
            abs_floor: float = 60.0) -> dict[str, Any]:
    out: dict[str, Any] = {"speech_ratio": 0.0, "speech_seconds": 0.0, "spans": [],
                           "noise_floor": 0.0, "peak": 0.0, "ok": False}
    try:
        with wave.open(str(path), "rb") as w:
            if w.getsampwidth() != 2:
                return out
            channels, rate, n = w.getnchannels(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
        if rate <= 0 or channels <= 0:
            return out
        samples = array.array("h")
        samples.frombytes(raw[: len(raw) - len(raw) % 2])
        if channels > 1:
            samples = samples[::channels]
    except Exception:  # noqa: BLE001 - anything unreadable means "unknown", not "silent"
        return out
    step = max(1, int(rate * frame_ms / 1000))
    rms: list[float] = []
    for i in range(0, len(samples) - step + 1, step):
        chunk = samples[i:i + step]
        rms.append(math.sqrt(sum(s * s for s in chunk) / step))
    if not rms:
        return out
    ordered = sorted(rms)
    floor = ordered[int(0.10 * (len(ordered) - 1))]
    threshold = max(abs_floor, floor * margin)
    frame_s = step / rate
    spans: list[list[float]] = []
    for k, v in enumerate(rms):
        if v <= threshold:
            continue
        start, end = k * frame_s, (k + 1) * frame_s
        if spans and start - spans[-1][1] <= MERGE_GAP_S:
            spans[-1][1] = end
        else:
            spans.append([start, end])
    spans = [[round(a, 3), round(b, 3)] for a, b in spans if (b - a) * 1000 >= min_speech_ms]
    total = len(rms) * frame_s
    speech = sum(b - a for a, b in spans)
    out.update(speech_ratio=speech / total if total else 0.0, speech_seconds=round(speech, 3),
               spans=spans, noise_floor=round(floor, 2), peak=float(max(abs(s) for s in samples)),
               ok=True)
    return out


def find_cut(pcm: bytes, *, min_seconds: float, max_seconds: float, pause_seconds: float = 0.5,
             rate: int = 16000, frame_ms: int = 30, margin: float = 2.5,
             abs_floor: float = 60.0) -> int | None:
    """Where to close a growing segment of 16-bit mono PCM, as a byte length, or None to keep reading.

    The recorder uses this to end a clip at a pause instead of mid-word. A cut happens when:
      * the buffer reached `max_seconds`: cut exactly there (the hard cap), or
      * at least `min_seconds` are in, speech was heard, and the trailing `pause_seconds` are quiet:
        cut at the end of the buffer.
    "Quiet" uses the same adaptive floor as `analyze` (10th percentile of frame RMS times `margin`,
    never below `abs_floor`). Requiring a frame above that threshold earlier in the buffer is what
    keeps a steady tone, or a clip that is silent throughout, from reading as one long pause: both run
    to the cap, and the silence gate downstream stores the quiet one as empty.
    """
    n = len(pcm) // 2
    cap_bytes = int(max_seconds * rate) * 2
    if n * 2 >= cap_bytes:
        return cap_bytes
    if n * 2 < int(min(min_seconds, max_seconds) * rate) * 2:
        return None
    samples = array.array("h")
    samples.frombytes(pcm[: n * 2])
    step = max(1, int(rate * frame_ms / 1000))
    rms = []
    for i in range(0, n - step + 1, step):
        chunk = samples[i:i + step]
        rms.append(math.sqrt(sum(map(operator.mul, chunk, chunk)) / step))
    window = max(1, int(round(pause_seconds * rate / step)))
    if len(rms) <= window:
        return None
    threshold = max(abs_floor, sorted(rms)[int(0.10 * (len(rms) - 1))] * margin)
    if any(v > threshold for v in rms[-window:]):
        return None
    return n * 2 if any(v > threshold for v in rms[:-window]) else None
