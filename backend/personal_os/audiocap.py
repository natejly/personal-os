"""Audio plumbing: devices, argv fragments, and whether a wav is real.

Stateless and policy-free. Nothing here knows about meetings or the activity monitor, nothing
here decides whether capture is allowed, and nothing here keeps a thread or a process alive -
callers own all of that. activity.py re-exports IS_MAC, ffmpeg_path, audio_devices and
looks_like_loopback from this module so there is exactly one copy of each helper.

Capture prefers the native path in native_audio.py (AVAudioEngine / Core Audio tap). ffmpeg
avfoundation is the fallback when that cannot start, and the seam is still an argv fragment:
`native_mic_input()` versus `device_input(index)`. Tests keep `synthetic_input()` as a lavfi
tone so the ffmpeg loop can be driven without a microphone.

Every function degrades instead of raising, the same contract the macOS probes in activity.py
follow: a missing binary, a device that was unplugged or a file truncated by a crash comes back
as a neutral value, never an exception into a capture thread.
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import wave
from pathlib import Path

from .db import now

log = logging.getLogger("personal_os.audiocap")

IS_MAC = sys.platform == "darwin"

# A 16k mono wav header plus a few samples. Below this there is no audio at all - but see
# validate_wav: size alone says nothing about whether the samples are readable.
MIN_WAV_BYTES = 2048


def ffmpeg_path() -> str:
    return shutil.which("ffmpeg") or ""


def ffprobe_path() -> str:
    return shutil.which("ffprobe") or ""


def _list_devices() -> list[dict[str, str]]:
    """Audio inputs this machine can record, as [{index, name}].

    Native uniqueIDs first (they survive unplug/replug); ffmpeg's avfoundation numbering
    only when AVFoundation is missing, so a machine that still has brew ffmpeg keeps working.
    """
    native: list[dict[str, str]] = []
    with contextlib.suppress(Exception):
        from . import native_audio
        native = native_audio.list_inputs()
    if native:
        return native
    return _list_ffmpeg_devices()


def _list_ffmpeg_devices() -> list[dict[str, str]]:
    """avfoundation audio inputs ffmpeg can see, as [{index, name}]."""
    ff = ffmpeg_path()
    if not ff or not IS_MAC:
        return []
    try:
        r = subprocess.run([ff, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                           capture_output=True, text=True, timeout=15)
    except Exception:  # noqa: BLE001
        return []
    out: list[dict[str, str]] = []
    in_audio = False
    for line in (r.stderr or "").splitlines():
        if "AVFoundation audio devices" in line:
            in_audio = True
            continue
        if "AVFoundation video devices" in line:
            in_audio = False
            continue
        m = re.search(r"\[(\d+)\]\s+(.+?)\s*$", line)
        if in_audio and m:
            out.append({"index": m.group(1), "name": m.group(2)})
    return out


# Loopback drivers that can carry system output back in as an input device. Without one of these
# macOS gives no way to record what the speakers played.
LOOPBACK_HINTS = ("blackhole", "loopback", "soundflower", "aggregate", "multi-output", "existential audio")


def looks_like_loopback(name: str) -> bool:
    n = (name or "").lower()
    return any(h in n for h in LOOPBACK_HINTS)


# Listing devices is a 15s-timeout subprocess, and the callers ask for it in pairs:
# activity.status() probes capabilities(), which lists devices (activity.py:363), and then lists
# them again itself (activity.py:1090). At a 5s UI poll that is two processes every tick.
_dev_cache: tuple[float, list[dict[str, str]]] | None = None
_dev_lock = threading.Lock()


def audio_devices(ttl: float = 20.0) -> list[dict[str, str]]:
    """Cached avfoundation input list. Pass ttl=0 to force a fresh probe."""
    global _dev_cache
    with _dev_lock:
        cached = _dev_cache
    if cached and ttl > 0 and 0 <= now() - cached[0] < ttl:
        return cached[1]
    devices = _list_devices()
    with _dev_lock:
        _dev_cache = (now(), devices)
    return devices


def resolve_device(index: str, name: str = "", ttl: float = 20.0) -> tuple[str, str]:
    """Map a stored device onto the live list, as (index, note).

    The name is the stable key and the index is only a fallback: avfoundation renumbers its
    inputs whenever an interface is plugged in, and activity.py:843 interpolates a bare stored
    index straight into the argv with nothing revalidating it, so a reshuffle silently records
    the wrong device. An empty index means do not start - the note says why.
    """
    devices = audio_devices(ttl)
    if not devices:
        return "", "no audio inputs visible"
    idx = str(index or "").strip()
    want = (name or "").strip().lower()
    if want:
        for d in devices:
            if d["name"].strip().lower() == want:
                moved = d["index"] != idx
                return d["index"], f"device moved to index {d['index']}" if moved else ""
    if idx and any(d["index"] == idx for d in devices):
        return idx, f"no input named {name!r}, using index {idx}" if want else ""
    return "", f"audio input {name or idx or '(unset)'} is no longer present"


def write_pcm16_wav(path: Path, pcm: bytes, rate: int = 16000, channels: int = 1) -> bool:
    """Write 16-bit PCM as a wav. Used by native capture and the self-test; no ffmpeg."""
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
    # validate_wav rejects files under MIN_WAV_BYTES; pad a too-short request rather than
    # returning a "success" the self-test will then call empty.
    if n + 44 < MIN_WAV_BYTES:
        n = MIN_WAV_BYTES - 44
    return write_pcm16_wav(path, b"\0" * n)


def validate_wav(path: Path, repair: bool = True) -> tuple[bool, str]:
    """Is this file decodable audio? Returns (ok, note).

    Size is not the test. A `-f segment` run killed mid-file leaves a 0-byte wav, but a file
    that is big enough to look finished can still carry no readable stream, so the only honest
    check is a probe. A file ffmpeg can still read but whose header never got its final sizes
    written is repairable by rewriting the container, which is why repair defaults on: the
    alternative is throwing away the last few seconds of every meeting that crashed.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return False, "empty"
    if size < MIN_WAV_BYTES:
        return False, "empty"
    # Native capture writes a finished header, so the stdlib wave module is enough and we
    # do not need ffprobe on PATH. Garbage that is merely large still falls through.
    if _wav_is_pcm16(path):
        return True, ""
    if not ffprobe_path():
        # No probe available: fall back to the size check rather than discarding every segment.
        return True, "unverified"
    secs, err = _probe_duration(path)
    if secs > 0:
        return True, ""
    if repair and ffmpeg_path():
        ok, remux_err = _remux(path)
        if ok:
            return True, "remuxed"
        err = err or remux_err
    return False, (err or "unreadable")[:160]


def segment_argv(ff: str, input_spec: list[str], out_pattern: str, segment_seconds: int,
                 max_seconds: int) -> list[str]:
    """One long-lived ffmpeg that closes a finished wav every segment_seconds.

    -t caps the whole run and the segment muxer honours it (verified: -t 7 with -segment_time 2
    writes four files and exits 0), so a recorder whose supervisor dies cannot fill the disk.
    16k mono is what the transcription endpoints want and what AudioCollector already asked for
    (activity.py:843-844); out_pattern carries ffmpeg's own %05d counter, so segment order
    survives a restart. input_spec is an argv FRAGMENT, not a device - that is what lets the
    recorder be tested against a synthetic tone with no microphone and no permission grant.
    """
    return [ff, "-hide_banner", "-loglevel", "error", *input_spec,
            "-t", str(max_seconds), "-ac", "1", "-ar", "16000",
            "-f", "segment", "-segment_time", str(segment_seconds),
            "-reset_timestamps", "1", out_pattern]


def resegment_argv(ff: str, src: str, out_pattern: str, segment_seconds: int) -> list[str]:
    """Cut an existing audio/video file into the same 16k mono wav segments a live capture writes."""
    return [ff, "-hide_banner", "-loglevel", "error", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
            "-f", "segment", "-segment_time", str(segment_seconds), "-reset_timestamps", "1",
            "-c:a", "pcm_s16le", out_pattern]


def device_input(index: str) -> list[str]:
    """Capture an avfoundation input by index. Resolve it through resolve_device first."""
    return ["-f", "avfoundation", "-i", f":{index}"]


def synthetic_input(freq: int = 440) -> list[str]:
    """A test tone instead of a device. -re paces lavfi at wall-clock speed, so segments close
    on the same schedule a real capture would. ffmpeg path only; native tests use native_sine_input."""
    return ["-re", "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=16000"]


NATIVE_MARK = "native"


def is_native_input(spec: list[str]) -> bool:
    return bool(spec) and spec[0] == NATIVE_MARK


def native_mic_input(uid: str = "") -> list[str]:
    return [NATIVE_MARK, "mic", uid]


def native_output_input() -> list[str]:
    return [NATIVE_MARK, "output"]


def native_sine_input(freq: int = 440) -> list[str]:
    """In-process 16k sine. ChannelCapture can run this with no ffmpeg and no permission grant."""
    return [NATIVE_MARK, "sine", str(int(freq))]


def dir_bytes(path: Path) -> int:
    """Retained wav bytes in one recording directory, for the disk ceiling."""
    total = 0
    try:
        files = list(path.glob("*.wav"))
    except Exception:  # noqa: BLE001
        return 0
    for f in files:
        try:
            total += f.stat().st_size
        except OSError:
            continue
    return total


# ---------------------------------------------------------------- internals


def _wav_is_pcm16(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnchannels() >= 1 and w.getsampwidth() == 2 and w.getnframes() > 0
    except Exception:  # noqa: BLE001 - junk, truncated, or not a wav
        return False


def _probe_duration(path: Path) -> tuple[float, str]:
    """(seconds, error). A non-positive duration always comes with an error to report."""
    fp = ffprobe_path()
    if not fp:
        return 0.0, "ffprobe not found on PATH"
    try:
        r = subprocess.run(
            [fp, "-hide_banner", "-loglevel", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=20,
        )
    except Exception as e:  # noqa: BLE001
        return 0.0, f"{type(e).__name__}: {e}"
    if r.returncode != 0:
        return 0.0, _first_line(r.stderr) or f"ffprobe exited {r.returncode}"
    try:
        secs = float((r.stdout or "").strip().splitlines()[0])
    except Exception:  # noqa: BLE001
        return 0.0, _first_line(r.stderr) or "ffprobe reported no duration"
    return (secs, "") if secs > 0 else (0.0, "zero-length audio")


def _remux(path: Path) -> tuple[bool, str]:
    """Rewrite the container around whatever samples ffmpeg can still read, in place."""
    ff = ffmpeg_path()
    tmp = path.parent / f".{path.stem}.remux{path.suffix or '.wav'}"
    try:
        r = subprocess.run(
            [ff, "-hide_banner", "-loglevel", "error", "-i", str(path), "-c", "copy", "-y", str(tmp)],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return False, _first_line(r.stderr) or f"ffmpeg exited {r.returncode}"
        secs, err = _probe_duration(tmp)
        if secs <= 0:
            return False, err
        os.replace(tmp, path)
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    finally:
        with contextlib.suppress(Exception):
            tmp.unlink(missing_ok=True)


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:160]
    return ""
