"""Import an existing audio/video file into a meeting, through the live pipeline's own path.

The file is cut by ffmpeg into the same 20 s 16 kHz wavs a live capture writes, recorded as
`import`-channel segments on the file's own clock, and drained by the same TranscribeWorker
(VAD gate, hallucination filter, retries, kept wav on failure), so a failed segment is replayable
with Retranscribe exactly like a live one. Then the transcript is rolled up, the meeting closes
`ready`, and enhance is queued if `enhanceOnStop` is on.

Nothing here is macOS-only: this is the way in on a machine with no loopback device.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import queue
import re
import subprocess
import threading
import wave
from pathlib import Path
from typing import Any

from . import audiocap, diarize, meeting_recorder

log = logging.getLogger("personal_os.meeting_import")

DEFAULT_MAX_IMPORT_SECONDS = 14400
FFMPEG_TIMEOUT = 3600


class ImportRefused(RuntimeError):
    """The meeting cannot take an import right now. The route turns this into a 409."""


def check(svc: Any, meeting_id: str) -> dict[str, Any]:
    """Raise unless this meeting may take an import. Returns the meeting row."""
    from .meetings import MeetingBlocked  # late: meetings imports this module's siblings

    m = svc.meetings.get(meeting_id)
    if m is None:
        raise LookupError(meeting_id)
    cfg = svc.config()
    if float(cfg.get("consentedAt") or 0) <= 0:
        # The same acknowledgement live recording needs: this is third-party speech, and the proxy
        # backend uploads the audio exactly as a live capture does.
        raise MeetingBlocked([{
            "id": "consent", "label": "Recording consent", "ok": False,
            "detail": "Nobody has acknowledged that the people in this audio were told it would be transcribed.",
            "fix": "Open Meetings settings and accept the recording notice once.",
        }])
    if svc.pool.get(meeting_id) is not None or m["status"] in ("recording", "transcribing", "enhancing"):
        raise ImportRefused(f"This meeting is {m['status']}; wait for it to finish first.")
    if svc.meetings.segments(meeting_id, limit=1):
        raise ImportRefused("This meeting already has transcribed audio, so an import would mix two recordings. "
                            "Import into a new meeting instead.")
    return m


def probe(src: Path, cfg: dict[str, Any]) -> float:
    """Duration in seconds, or ValueError (a 400) for unreadable or over-long audio."""
    secs, err = audiocap._probe_duration(src)
    if secs <= 0:
        raise ValueError(f"could not read that file as audio: {err}")
    limit = float(cfg.get("maxImportSeconds") or DEFAULT_MAX_IMPORT_SECONDS)
    if secs > limit:
        raise ValueError(f"that file is {int(secs)}s long; the import limit is {int(limit)}s")
    return secs


def _wav_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 16000)
    except Exception:  # noqa: BLE001 - fall back to a probe
        return audiocap._probe_duration(path)[0]


def _cut(src: Path, out_dir: Path, seconds: int) -> list[Path]:
    ff = audiocap.ffmpeg_path()
    if not ff:
        raise RuntimeError("ffmpeg not found on PATH (brew install ffmpeg)")
    out_dir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(audiocap.resegment_argv(ff, str(src), str(out_dir / "import-%05d.wav"), seconds),
                       capture_output=True, text=True, timeout=FFMPEG_TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError(audiocap._first_line(r.stderr) or f"ffmpeg exited {r.returncode}")
    return sorted(out_dir.glob("import-*.wav"))


def _transcribe_all(svc: Any, meeting_id: str, out_dir: Path, items: list[tuple[int, Path]],
                    cfg: dict[str, Any]) -> None:
    """Drain the segments through the live TranscribeWorker, on this (worker) thread's behalf."""
    q: queue.Queue = queue.Queue()
    halt = threading.Event()
    worker = meeting_recorder.TranscribeWorker(
        meeting_id, q, halt, out_dir=out_dir, settings_fn=svc.settings, config_fn=svc.config,
        data_dir=svc.data_dir,
        on_result=lambda ch, seq, path, res: svc._on_result(meeting_id, ch, seq, path, res),
        keep_audio=bool(cfg.get("keepAudio")), max_audio_bytes=int(cfg.get("maxAudioBytes") or 0))
    worker.start()
    try:
        for seq, path in items:
            q.put(("import", seq, path, False))
        q.join()
    finally:
        halt.set()
        worker.join(timeout=10)


async def run(svc: Any, meeting_id: str, src_path: Path | str, *, cleanup_src: bool = False) -> dict[str, Any] | None:
    """Import one file. Raises on refusal/bad input; later failures land on the meeting's banner."""
    src = Path(src_path)
    try:
        m = check(svc, meeting_id)
        cfg = svc.config()
        total = await asyncio.to_thread(probe, src, cfg)
        n = max(1, int(cfg.get("segmentSeconds") or meeting_recorder.DEFAULT_SEGMENT_SECONDS))
        out_dir = meeting_recorder.recording_dir(svc.data_dir, meeting_id) / "import"
        from .db import now
        started = now() - total
        svc.meetings.mark_import(meeting_id, str(out_dir), started)
        keep = svc.keeps_audio(meeting_id, cfg)
        if keep and not m.get("keep_audio"):
            svc.meetings.patch(meeting_id, {"keep_audio": True})   # the wavs are kept, so the row says so (playback gates on it)
        prior_status = m["status"]
        try:
            wavs = await asyncio.to_thread(_cut, src, out_dir, n)
            if not wavs:
                raise RuntimeError("ffmpeg produced no audio from that file")
            items: list[tuple[int, Path]] = []
            for k, wav in enumerate(wavs):
                secs = _wav_seconds(wav)
                svc.meetings.add_segment(
                    meeting_id, "import", k, k * n, k * n + secs, started + k * n, str(wav),
                    wav.stat().st_size, duration_ms=int(secs * 1000), state="recorded")
                items.append((k, wav))
            # Diarization needs the wavs after transcription, so they are held until it has run.
            split = bool(cfg.get("diarize")) and diarize.resolve_backend(cfg, svc.data_dir) != "none"
            await asyncio.to_thread(_transcribe_all, svc, meeting_id, out_dir, items,
                                    {**cfg, "keepAudio": keep or split})
            if split:
                await asyncio.to_thread(svc.diarize_segments, meeting_id)
                if not keep:
                    svc.meetings.drop_done_audio(meeting_id)
        except Exception as e:  # noqa: BLE001 - say so on the meeting instead of leaving it 'transcribing'
            log.warning("meetings: import of %s failed: %s", meeting_id, e)
            svc.meetings.patch(meeting_id, {"status": prior_status, "error": f"import failed: {e}"[:1000]})
            raise
        transcript = svc.meetings.build_transcript(meeting_id)
        failed = len(svc.meetings.failed_segments(meeting_id))
        note = ""
        if failed and not transcript:
            note = (f"{failed} segment(s) could not be transcribed, so there is no transcript. "
                    "The notes are untouched - fix the transcription route and retry.")
        out = svc.meetings.finalize(meeting_id, transcript, ended_at=started + total,
                                    status="ready", error=note)
        emit = getattr(svc, "_emit", None)
        if emit:
            emit("status", meeting_id, status="ready")
        if cfg.get("enhanceOnStop"):
            # For a doc-linked row `_enhance_quietly` proposes the summary into the doc instead
            # (and does nothing for dictation), same as `stop`.
            await svc._enhance_quietly(meeting_id)
        return out
    finally:
        if cleanup_src:
            with contextlib.suppress(Exception):
                src.unlink(missing_ok=True)


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(name: str) -> str:
    return _SAFE.sub("_", Path(name or "audio").name)[:80] or "audio"
