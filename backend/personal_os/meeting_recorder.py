"""Meeting capture: one long-lived ffmpeg per channel, and a worker that turns wavs into text.

The only macOS-dependent file in the subsystem, and the only one that owns processes and threads.
It knows nothing about the database: every row it would write leaves through an injected callable,
which is also what lets the whole file be tested against `audiocap.synthetic_input()` with no
microphone, no loopback driver and no permission grant.

Why the segment muxer instead of AudioCollector's loop: activity.py:841-851 records for `chunk`
seconds with subprocess.run and then blocks on a transcription that can take two minutes, so
everything said while the previous chunk is in flight is simply never captured. Here ffmpeg runs
continuously and closes a finished wav every `segment_seconds`; transcription happens on a second
thread draining a queue, so falling behind costs latency instead of audio.

Teardown is `q\\n` on ffmpeg's stdin, never a signal: that exits 0 and flushes a valid final
segment, where a kill leaves a 0-byte file that fails ffprobe outright.
"""
from __future__ import annotations

import contextlib
import logging
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import audiocap, stt

log = logging.getLogger("personal_os.meeting_recorder")

# Wavs live under <data_dir>/recordings/<meeting_id>/ and never under tmp/, which app.py:1113
# rmtree's on every shutdown - a meeting that outlives one app launch must keep its audio.
RECORDINGS_DIRNAME = "recordings"

POLL_SECONDS = 0.5          # how often a capture looks for a newly closed segment
MAX_RESTARTS = 3            # ffmpeg crashes we will ride out before giving up on a channel
RESTART_GAP = 2.0
MAX_ATTEMPTS = 3            # transcription attempts per segment
RETRY_BACKOFF = (2.0, 4.0, 8.0)
TAIL_CHARS = 180            # of the previous transcript, sent as whisper's prompt

DEFAULT_SEGMENT_SECONDS = 20
# -t caps the whole run even under -f segment (verified), so a recorder whose supervisor dies
# cannot fill the disk; it is the backstop behind the UI's own auto-stop.
DEFAULT_MAX_SECONDS = 4 * 60 * 60
DEFAULT_MAX_AUDIO_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_DRAIN_SECONDS = 120.0

# segment_argv always asks for 16k mono, and ffmpeg writes that as pcm_s16le, so a segment's
# length is its size - the header. Cheaper than an ffprobe per segment and off by only the few
# bytes of any extra header chunk (~1ms).
WAV_BYTES_PER_SECOND = 16000 * 2
WAV_HEADER_BYTES = 44


def recording_dir(data_dir: Path, meeting_id: str) -> Path:
    """Where one meeting's wavs live. The value of meetings.audio_dir."""
    return data_dir / RECORDINGS_DIRNAME / meeting_id


class RecorderBusy(RuntimeError):
    """Something is already recording. The route turns this into a 409."""

    def __init__(self, meeting_id: str):
        self.meeting_id = meeting_id
        super().__init__(f"a recording is already running for meeting {meeting_id}")


class _RecorderThread(threading.Thread):
    """Base for a session's threads: a daemon that respects one stop event.

    A deliberate duplicate of activity.Collector (activity.py:551-583) rather than a subclass of
    it. `Collector.active` consults `monitor.running` and `monitor.paused` (activity.py:564-566)
    and `Monitor.set_config` calls `restart()` whenever the monitor is running
    (activity.py:1004-1006), so sharing the base class would let an unrelated Activity settings
    change - or the `activity_pause` tool, which is registered at danger `writes` and so needs no
    approval card - tear down a live recording in the middle of someone's call.
    """

    def __init__(self, name_id: str, halt: threading.Event):
        super().__init__(name=f"meeting-{name_id}", daemon=True)
        self.name_id = name_id
        self.error = ""
        # Bound once, exactly as Collector does: the session owns this event, and a thread that
        # re-read it would end up watching a later generation's event and never see its own stop.
        self.halt = halt

    def sleep(self, seconds: float) -> None:
        self.halt.wait(max(0.05, seconds))

    def run(self) -> None:
        try:
            self.work()
        except Exception as e:  # noqa: BLE001 - one broken thread must not take the app down
            self.error = f"{type(e).__name__}: {e}"
            log.warning("meeting: %s stopped: %s", self.name_id, self.error)

    def work(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


class ChannelCapture(_RecorderThread):
    """One long-lived ffmpeg writing `channel-00000.wav`, `channel-00001.wav`, ...

    `input_spec` is an argv FRAGMENT - `audiocap.device_input(index)` for a real input or
    `audiocap.synthetic_input()` for a test tone - never a bare device index. That is the seam:
    with it the capture loop, the restart path and the graceful stop are all testable on a machine
    with one microphone and no loopback driver.
    """

    def __init__(self, channel: str, input_spec: list[str], out_dir: Path, segment_seconds: int,
                 max_seconds: int, halt: threading.Event,
                 on_segment: Callable[[str, int, Path], None]):
        super().__init__(f"capture-{channel}", halt)
        self.channel = channel
        self.input_spec = list(input_spec)
        self.out_dir = out_dir
        self.segment_seconds = max(1, int(segment_seconds))
        self.max_seconds = max(1, int(max_seconds))
        self.on_segment = on_segment
        self.proc: subprocess.Popen[bytes] | None = None
        self.session_start = 0.0
        self.stopping = False
        self.restarts = 0
        self._next = 0  # the lowest seq not yet handed to on_segment
        self._emit_lock = threading.Lock()
        self._proc_lock = threading.Lock()

    def work(self) -> None:
        ff = audiocap.ffmpeg_path()
        if not ff:
            self.error = "ffmpeg not found on PATH (brew install ffmpeg)"
            return
        self.out_dir.mkdir(parents=True, exist_ok=True)
        # Anchored once and kept across restarts: seq numbering continues, so a segment's place in
        # the meeting stays correct even if ffmpeg died and came back.
        self.session_start = time.time()
        while not self.halt.is_set() and not self.stopping:
            rc, err = self._run_once(ff)
            self._emit_ready(exited=True)
            if self.stopping or self.halt.is_set() or rc == 0:
                return
            self.restarts += 1
            self.error = (err or f"ffmpeg exited {rc}")[:200]
            if self.restarts >= MAX_RESTARTS:
                self.error = f"{self.error} (gave up after {self.restarts} restarts)"
                log.warning("meeting: %s gave up: %s", self.name_id, self.error)
                return
            log.warning("meeting: %s restarting after %s", self.name_id, self.error)
            self.sleep(RESTART_GAP)

    def stop(self) -> None:
        """The graceful path, and the reason this class exists.

        `q\\n` on stdin makes ffmpeg finish the segment it is writing and exit 0; SIGKILL leaves a
        0-byte file that fails ffprobe, which is the last few seconds of the meeting gone.
        """
        self.stopping = True
        self._shutdown_proc()
        self._emit_ready(exited=True)

    # ------------------------------------------------------------ internals

    def _run_once(self, ff: str) -> tuple[int, str]:
        """Launch ffmpeg and watch it until it exits or we are told to stop. (rc, stderr)."""
        start = self._next
        remaining = max(1, self.max_seconds - int(time.time() - self.session_start))
        argv = audiocap.segment_argv(ff, self.input_spec,
                                     str(self.out_dir / f"{self.channel}-%05d.wav"),
                                     self.segment_seconds, remaining)
        if start:
            # segment_argv has no start-number flag, and without one a restart would reopen
            # channel-00000.wav and overwrite the beginning of the meeting.
            argv[-1:-1] = ["-segment_start_number", str(start)]
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
        self.proc = proc
        while True:
            rc = proc.poll()
            self._emit_ready(exited=rc is not None)
            if rc is not None:
                break
            if self.halt.is_set() or self.stopping:
                break
            self.sleep(POLL_SECONDS)
        if proc.poll() is None:
            # A hard halt with no stop() behind it still owes ffmpeg a chance to flush.
            self._shutdown_proc()
        rc = proc.poll()
        return (rc if rc is not None else 0), self._drain_stderr(proc)

    def _emit_ready(self, exited: bool) -> None:
        """Hand on_segment every segment that is finished and not yet reported.

        A file is finished once its successor exists - ffmpeg only opens the next segment after
        closing this one - or once the process is down and nothing more will be written.
        """
        ready: list[tuple[int, Path]] = []
        with self._emit_lock:
            while True:
                path = self.out_dir / f"{self.channel}-{self._next:05d}.wav"
                if not path.exists():
                    break
                nxt = self.out_dir / f"{self.channel}-{self._next + 1:05d}.wav"
                if not exited and not nxt.exists():
                    break
                ready.append((self._next, path))
                self._next += 1
        for seq, path in ready:
            try:
                self.on_segment(self.channel, seq, path)
            except Exception as e:  # noqa: BLE001 - a bad callback must not stop the capture
                self.error = f"{type(e).__name__}: {e}"
                log.warning("meeting: %s could not report seq %s: %s", self.name_id, seq, e)

    def _shutdown_proc(self) -> None:
        proc = self.proc
        if proc is None:
            return
        with self._proc_lock:
            if proc.poll() is not None:
                return
            with contextlib.suppress(Exception):
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.write(b"q\n")
                    proc.stdin.flush()
            for step, timeout in (("quit", 5), ("terminate", 3), ("kill", 2)):
                if step == "terminate":
                    proc.terminate()
                elif step == "kill":
                    proc.kill()
                try:
                    proc.wait(timeout=timeout)
                    return
                except subprocess.TimeoutExpired:
                    continue

    @staticmethod
    def _drain_stderr(proc: subprocess.Popen[bytes]) -> str:
        """ffmpeg's own complaint, which is the last thing it says before exiting."""
        if proc.stderr is None:
            return ""
        try:
            raw = proc.stderr.read() or b""
        except Exception:  # noqa: BLE001
            return ""
        return _last_line(raw.decode("utf-8", "replace"))


class TranscribeWorker(_RecorderThread):
    """Drains (channel, seq, path) off a queue, transcribes each wav, reports the result.

    Every outcome - silence, a broken file, three failed attempts - leaves through `on_result`.
    Nothing is dropped for being short: activity.py:860-861 throws away any transcript under
    `minChars`, which on a call silently deletes "yes, ship it".
    """

    def __init__(self, meeting_id: str, q: "queue.Queue[tuple[str, int, Path]]",
                 halt: threading.Event, *, out_dir: Path,
                 settings_fn: Callable[[], dict[str, Any]],
                 config_fn: Callable[[], dict[str, Any]],
                 data_dir: Path,
                 on_result: Callable[[str, int, Path, dict[str, Any]], None],
                 is_paused: Callable[[], bool] = lambda: False,
                 keep_audio: bool = False,
                 max_attempts: int = MAX_ATTEMPTS,
                 max_audio_bytes: int = DEFAULT_MAX_AUDIO_BYTES,
                 on_disk_check: Callable[[], int] | None = None):
        super().__init__(f"transcribe-{meeting_id}", halt)
        self.meeting_id = meeting_id
        self.q = q
        self.out_dir = out_dir
        self.settings_fn = settings_fn
        self.config_fn = config_fn
        self.data_dir = data_dir
        self.on_result = on_result
        self.is_paused = is_paused
        self.keep_audio = keep_audio
        self.max_attempts = max(1, int(max_attempts))
        self.max_audio_bytes = max(0, int(max_audio_bytes))
        self.disk_check = on_disk_check or (lambda: audiocap.dir_bytes(out_dir))
        self._tail: dict[str, str] = {}
        # Failed segments whose wav we are holding for retranscribe, oldest first. The disk
        # ceiling evicts from the front of this list, never from a wav the user asked to keep.
        self._kept: list[dict[str, Any]] = []

    def work(self) -> None:
        while True:
            try:
                channel, seq, path = self.q.get(timeout=POLL_SECONDS)
            except queue.Empty:
                if self.halt.is_set():
                    return
                continue
            try:
                self._transcribe_one(channel, seq, path)
            except Exception as e:  # noqa: BLE001 - report the segment, then keep draining
                self.error = f"{type(e).__name__}: {e}"
                log.warning("meeting: %s could not settle %s/%s: %s",
                            self.name_id, channel, seq, self.error)
                with contextlib.suppress(Exception):
                    self._report(channel, seq, path, self._result(
                        state="failed", error=self.error, keep=True))
            finally:
                self.q.task_done()

    # ------------------------------------------------------------ internals

    def _transcribe_one(self, channel: str, seq: int, path: Path) -> None:
        if self.is_paused():
            # Pause does not kill ffmpeg - the seq numbering has to stay monotonic - so the
            # segments recorded while paused arrive here and are thrown away on purpose.
            self._report(channel, seq, path, self._result(state="discarded", keep=False))
            return
        ok, note = audiocap.validate_wav(path)
        if not ok:
            # Probe-based, never a byte count: a killed ffmpeg leaves 0 bytes, but a file big
            # enough to look finished can still hold no readable stream.
            self._report(channel, seq, path, self._result(
                state="empty" if note == "empty" else "failed",
                error=f"unusable segment: {note}", keep=note != "empty"))
            return

        attempts = 0
        res: dict[str, Any] = {}
        while True:
            attempts += 1
            res = stt.transcribe(path, settings=self.settings_fn(), cfg=self.config_fn(),
                                 data_dir=self.data_dir, prompt=self._tail.get(channel, ""))
            if not res.get("error") or attempts >= self.max_attempts or self.halt.is_set():
                break
            self.sleep(RETRY_BACKOFF[min(attempts - 1, len(RETRY_BACKOFF) - 1)])

        text = str(res.get("text") or "").strip()
        error = str(res.get("error") or "")
        if text:
            self._tail[channel] = text[-TAIL_CHARS:]
        self._report(channel, seq, path, self._result(
            state="done" if text and not error else ("failed" if error else "empty"),
            text=text, detail=res.get("detail") or {}, backend=str(res.get("backend") or ""),
            error=error, ms=int(res.get("ms") or 0), attempts=attempts,
            # A failed segment keeps its wav so retranscribe can replay it once the user has
            # fixed their STT route - which today is every segment, since nothing answers
            # /v1/audio/transcriptions.
            keep=self.keep_audio or bool(error)))

    def _result(self, *, state: str, text: str = "", detail: Any = None, backend: str = "",
                error: str = "", ms: int = 0, attempts: int = 0, keep: bool = False) -> dict[str, Any]:
        return {"text": text, "detail": detail if detail is not None else {}, "backend": backend,
                "error": error, "ms": ms, "state": state, "attempts": attempts, "keep": keep,
                "wav_path": "", "wav_bytes": 0}

    def _report(self, channel: str, seq: int, path: Path, res: dict[str, Any]) -> None:
        """Settle the wav (keep it, or delete it), then hand the row to the caller."""
        keep = bool(res.pop("keep", False))
        if keep and res.get("error"):
            evicted = self._evict()
            if evicted:
                mib = self.max_audio_bytes // (1024 * 1024)
                res["error"] = (f"{res['error']} | discarded the audio of {len(evicted)} older "
                                f"failed segment(s) to stay under the {mib} MiB ceiling")[:1000]
        if keep:
            res["wav_path"] = str(path)
            res["wav_bytes"] = _size(path)
            if res.get("error"):
                self._kept.append({"channel": channel, "seq": seq, "path": path, "res": dict(res)})
        else:
            with contextlib.suppress(Exception):
                path.unlink(missing_ok=True)
        self.on_result(channel, seq, path, res)

    def _evict(self) -> list[dict[str, Any]]:
        """Delete the oldest failed wavs until the retained total is back under the ceiling.

        Without this, a broken STT route plus an all-day capture grows without bound - every
        segment fails, every failure keeps its wav, and one channel is ~115 MB an hour.
        """
        total = self.disk_check()
        if total <= self.max_audio_bytes:
            return []
        gone: list[dict[str, Any]] = []
        while self._kept and total > self.max_audio_bytes:
            held = self._kept.pop(0)
            path: Path = held["path"]
            total -= _size(path)
            with contextlib.suppress(Exception):
                path.unlink(missing_ok=True)
            gone.append(held)
        for held in gone:
            # The row stays, only its audio is gone: a failure must never vanish silently, and
            # the UI has to be able to say why retranscribe is no longer possible.
            res = dict(held["res"])
            res["wav_path"] = ""
            res["wav_bytes"] = 0
            res["evicted"] = True  # an update to a row that already exists, not a new segment
            res["error"] = f"{res.get('error') or 'failed'} | audio discarded (disk ceiling)"[:1000]
            with contextlib.suppress(Exception):
                self.on_result(held["channel"], held["seq"], held["path"], res)
        return gone


class RecordingSession:
    """One meeting's capture: its own stop event, its channels, its queue, its worker.

    The session is the only thing that knows the recording clock, so it is what turns a bare
    (channel, seq, path) into a row with a `t_start` measured from the start of the meeting.
    """

    def __init__(self, meeting_id: str, out_dir: Path, channels: dict[str, list[str]], *,
                 settings_fn: Callable[[], dict[str, Any]],
                 config_fn: Callable[[], dict[str, Any]],
                 data_dir: Path,
                 on_segment: Callable[[str, int, Path, dict[str, Any]], None],
                 on_result: Callable[[str, int, Path, dict[str, Any]], None],
                 segment_seconds: int = DEFAULT_SEGMENT_SECONDS,
                 max_seconds: int = DEFAULT_MAX_SECONDS,
                 keep_audio: bool = False,
                 max_attempts: int = MAX_ATTEMPTS,
                 max_audio_bytes: int = DEFAULT_MAX_AUDIO_BYTES,
                 drain_seconds: float = DEFAULT_DRAIN_SECONDS,
                 on_disk_check: Callable[[], int] | None = None):
        if not channels:
            raise ValueError("a recording needs at least one channel")
        self.meeting_id = meeting_id
        self.out_dir = out_dir
        self.channels = {k: list(v) for k, v in channels.items()}
        self.segment_seconds = max(1, int(segment_seconds))
        self.max_seconds = max(1, int(max_seconds))
        self.keep_audio = keep_audio
        self.drain_seconds = float(drain_seconds)
        self.on_segment = on_segment
        self.stop_event = threading.Event()
        self.q: queue.Queue[tuple[str, int, Path]] = queue.Queue()
        self.paused = False
        self.stopping = False
        self.started_at = 0.0
        self.ended_at = 0.0
        self.captures: dict[str, ChannelCapture] = {}
        self._counts = {"emitted": 0, "reported": 0, "done": 0, "failed": 0, "discarded": 0}
        self._lock = threading.Lock()
        self.worker = TranscribeWorker(
            meeting_id, self.q, self.stop_event, out_dir=out_dir, settings_fn=settings_fn,
            config_fn=config_fn, data_dir=data_dir, on_result=self._result,
            is_paused=lambda: self.paused, keep_audio=keep_audio, max_attempts=max_attempts,
            max_audio_bytes=max_audio_bytes, on_disk_check=on_disk_check)
        self._on_result = on_result

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.started_at = time.time()
        self.worker.start()
        for channel, spec in self.channels.items():
            cap = ChannelCapture(channel, spec, self.out_dir, self.segment_seconds,
                                 self.max_seconds, self.stop_event, self._segment)
            self.captures[channel] = cap
            cap.start()

    def pause(self, paused: bool) -> None:
        """Stop keeping audio without stopping ffmpeg, so seq numbering stays monotonic."""
        self.paused = bool(paused)

    def stop(self, drain_seconds: float | None = None) -> dict[str, Any]:
        """Captures down first, then drain what is already recorded, then the worker."""
        self.stopping = True
        for cap in self.captures.values():
            with contextlib.suppress(Exception):
                cap.stop()
        for cap in self.captures.values():
            cap.join(timeout=5)
        deadline = time.time() + (self.drain_seconds if drain_seconds is None else float(drain_seconds))
        while time.time() < deadline and self.pending > 0:
            # halt.wait rather than sleep: a hard shutdown behind us cuts the drain short.
            self.stop_event.wait(0.2)
        self.stop_event.set()
        self.worker.join(timeout=5)
        self.ended_at = time.time()
        return self.stats()

    @property
    def alive(self) -> bool:
        return any(c.is_alive() for c in self.captures.values()) or self.worker.is_alive()

    @property
    def pending(self) -> int:
        with self._lock:
            return max(0, self._counts["emitted"] - self._counts["reported"])

    def errors(self) -> dict[str, str]:
        out = {c: cap.error for c, cap in self.captures.items() if cap.error}
        if self.worker.error:
            out["transcribe"] = self.worker.error
        return out

    def stats(self) -> dict[str, Any]:
        end = self.ended_at or time.time()
        with self._lock:
            counts = dict(self._counts)
        return {
            "elapsed_ms": int(max(0.0, end - self.started_at) * 1000) if self.started_at else 0,
            "segments_done": counts["done"],
            "segments_failed": counts["failed"],
            "segments_pending": max(0, counts["emitted"] - counts["reported"]),
            "queued": self.q.qsize(),
            "paused": self.paused,
            "channels": [{"channel": c, "alive": cap.is_alive(), "error": cap.error}
                         for c, cap in self.captures.items()],
        }

    # ------------------------------------------------------------ internals

    def _segment(self, channel: str, seq: int, path: Path) -> None:
        """A closed wav: record where it sits in the meeting, then queue it for transcription."""
        seconds = _wav_seconds(path)
        t_start = float(seq * self.segment_seconds)
        info = {
            "state": "discarded" if self.paused else "recorded",
            "t_start": t_start,
            "t_end": t_start + seconds,
            # From the RECORDING clock, not from when transcription returned: that is the bug at
            # activity.py:866-870, where store.add is called with no ts= so ts + duration_ms
            # points into the future.
            "started_at": (self.captures[channel].session_start or self.started_at) + t_start,
            "duration_ms": int(seconds * 1000),
            "wav_path": str(path),
            "wav_bytes": _size(path),
        }
        with self._lock:
            self._counts["emitted"] += 1
        with contextlib.suppress(Exception):
            self.on_segment(channel, seq, path, info)
        self.q.put((channel, seq, path))

    def _result(self, channel: str, seq: int, path: Path, res: dict[str, Any]) -> None:
        state = str(res.get("state") or "")
        # An eviction re-reports a segment that was already counted: it updates an existing row
        # rather than settling a new one.
        if not res.get("evicted"):
            with self._lock:
                self._counts["reported"] += 1
                if state in ("done", "failed", "discarded"):
                    self._counts[state] += 1
        self._on_result(channel, seq, path, res)


class RecorderPool:
    """Every live recording, keyed by meeting id - and there is at most one.

    Two concurrent meetings would fight over the same microphone and double the transcription
    bill for one room, so `start` refuses rather than guessing which one the user meant.
    """

    def __init__(self, data_dir: Path, settings_fn: Callable[[], dict[str, Any]],
                 config_fn: Callable[[], dict[str, Any]]):
        self.data_dir = data_dir
        self.settings_fn = settings_fn
        self.config_fn = config_fn
        self.sessions: dict[str, RecordingSession] = {}
        self._lock = threading.Lock()

    def start(self, meeting_id: str, channels: dict[str, list[str]], *,
              on_segment: Callable[[str, int, Path, dict[str, Any]], None],
              on_result: Callable[[str, int, Path, dict[str, Any]], None],
              segment_seconds: int = DEFAULT_SEGMENT_SECONDS,
              max_seconds: int = DEFAULT_MAX_SECONDS,
              keep_audio: bool = False,
              max_attempts: int = MAX_ATTEMPTS,
              max_audio_bytes: int = DEFAULT_MAX_AUDIO_BYTES,
              drain_seconds: float = DEFAULT_DRAIN_SECONDS,
              on_disk_check: Callable[[], int] | None = None) -> RecordingSession:
        with self._lock:
            live = self._live()
            if live is not None:
                raise RecorderBusy(live.meeting_id)
            session = RecordingSession(
                meeting_id, recording_dir(self.data_dir, meeting_id), channels,
                settings_fn=self.settings_fn, config_fn=self.config_fn, data_dir=self.data_dir,
                on_segment=on_segment, on_result=on_result, segment_seconds=segment_seconds,
                max_seconds=max_seconds, keep_audio=keep_audio, max_attempts=max_attempts,
                max_audio_bytes=max_audio_bytes, drain_seconds=drain_seconds,
                on_disk_check=on_disk_check)
            self.sessions[meeting_id] = session
        session.start()
        return session

    def get(self, meeting_id: str) -> RecordingSession | None:
        return self.sessions.get(meeting_id)

    def live(self) -> RecordingSession | None:
        with self._lock:
            return self._live()

    def stop(self, meeting_id: str, drain_seconds: float | None = None) -> dict[str, Any]:
        session = self.sessions.get(meeting_id)
        if session is None:
            return {}
        try:
            return session.stop(drain_seconds)
        finally:
            with self._lock:
                self.sessions.pop(meeting_id, None)

    def stop_all(self) -> None:
        """App shutdown: every live ffmpeg gets its q\\n, so no meeting loses its last segment."""
        for meeting_id in list(self.sessions):
            with contextlib.suppress(Exception):
                self.stop(meeting_id, drain_seconds=5.0)

    def _live(self) -> RecordingSession | None:
        for session in self.sessions.values():
            if not session.stopping and session.alive:
                return session
        return None


# ---------------------------------------------------------------- internals


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _wav_seconds(path: Path) -> float:
    """A 16k mono s16le wav's length from its size - the only format segment_argv produces."""
    return max(0.0, (_size(path) - WAV_HEADER_BYTES) / float(WAV_BYTES_PER_SECOND))


def _last_line(text: str) -> str:
    """ffmpeg's complaint is the last thing it writes, not the first."""
    for line in reversed((text or "").splitlines()):
        if line.strip():
            return line.strip()[:160]
    return ""
