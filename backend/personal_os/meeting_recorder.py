"""Meeting capture: one long-lived capture per channel, and a worker that turns wavs into text.

The only macOS-dependent file in the subsystem, and the only one that owns processes and threads.
It knows nothing about the database: every row it would write leaves through an injected callable,
which is also what lets the whole file be tested against `audiocap.native_sine_input()` with no
microphone, no loopback driver and no permission grant.

Native capture (AVAudioEngine / Core Audio tap) is the default. ffmpeg avfoundation remains the
fallback when `input_spec` is an argv fragment rather than `['native', ...]`.

Why the segment loop instead of AudioCollector's blocking chunk: activity.py records for `chunk`
seconds then waits on transcription, so everything said while the previous chunk is in flight is
never captured. Here the capture runs continuously and closes a finished wav every
`segment_seconds`; transcription happens on a second thread draining a queue, so falling behind
costs latency instead of audio.

ffmpeg teardown is `q\\n` on stdin, never a signal: that exits 0 and flushes a valid final
segment, where a kill leaves a 0-byte file that fails the wav probe outright. Native teardown
stops the engine and writes whatever samples are still in the ring.
"""
from __future__ import annotations

import collections
import contextlib
import logging
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import audiocap, meeting_vad, stt

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
STDERR_TAIL_LINES = 50      # ffmpeg stderr lines kept in memory while it runs
WORKER_JOIN_SECONDS = 5.0   # how long stop() waits for the transcribe thread to exit

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

# cut_on_silence reads the native capture in steps this long, and looks for a pause this long.
CUT_STEP_SECONDS = 0.25
CUT_PAUSE_SECONDS = 0.5


def recording_dir(data_dir: Path, meeting_id: str) -> Path:
    """Where one meeting's wavs live. The value of meetings.audio_dir."""
    return data_dir / RECORDINGS_DIRNAME / meeting_id


# Consecutive empty native reads before the channel reports that nothing is arriving.
EMPTY_READS_BEFORE_WARNING = 3
NO_AUDIO = "no audio is arriving from the capture device"


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
    change tear down a live recording in the middle of someone's call. `activity_pause` asks first,
    but it still pauses the monitor, so it must not share this base class.
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
    """One long-lived capture writing `channel-00000.wav`, `channel-00001.wav`, ...

    `input_spec` is either a native descriptor (`audiocap.native_mic_input()`,
    `native_output_input()`, `native_sine_input()`) or an ffmpeg argv fragment
    (`audiocap.device_input(index)` / `synthetic_input()`). That is the seam: with it the
    capture loop, the restart path and the graceful stop are all testable on a machine with one
    microphone and no loopback driver.
    """

    def __init__(self, channel: str, input_spec: list[str], out_dir: Path, segment_seconds: int,
                 max_seconds: int, halt: threading.Event,
                 on_segment: Callable[[str, int, Path], None], *,
                 cut_on_silence: bool = False, min_segment_seconds: float = 2.0):
        super().__init__(f"capture-{channel}", halt)
        self.channel = channel
        self.input_spec = list(input_spec)
        self.out_dir = out_dir
        self.segment_seconds = max(1, int(segment_seconds))
        self.max_seconds = max(1, int(max_seconds))
        self.on_segment = on_segment
        # Opt-in, native capture only: close a segment at the first pause once `min_segment_seconds`
        # are in, with `segment_seconds` as the hard cap. The ffmpeg path below always cuts on its
        # fixed grid and ignores both, since its segment muxer owns the boundaries.
        self.cut_on_silence = bool(cut_on_silence)
        self.min_segment_seconds = max(0.5, float(min_segment_seconds))
        # seq -> (t_start, t_end) on this capture's own clock, filled only when cutting on silence
        # (segments are then variable length, so `seq * segment_seconds` would lie). The session
        # reads it in _segment; empty means "use the fixed grid", exactly as before.
        self.offsets: dict[int, tuple[float, float]] = {}
        self._pos_samples = 0
        self.proc: subprocess.Popen[bytes] | None = None
        self._native: Any = None
        self.session_start = 0.0
        self.stopping = False
        self.restarts = 0
        # The lowest seq not yet handed to on_segment, and never 0 over a directory that
        # already holds wavs. A meeting that is stopped and started again reuses its audio_dir
        # (meetings.mark_started keeps it) and a failed segment's wav is kept on purpose for
        # retranscribe, so numbering from 0 would make _emit_ready report the PREVIOUS run's
        # files as this run's first segments while the new ffmpeg overwrote them underneath.
        self._next = _first_free_seq(out_dir, channel)
        self._emit_lock = threading.Lock()
        self._proc_lock = threading.Lock()

    def work(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        # Anchored once and kept across restarts: seq numbering continues, so a segment's place in
        # the meeting stays correct even if the capture died and came back.
        self.session_start = time.time()
        if audiocap.is_native_input(self.input_spec):
            self._work_native()
            return
        ff = audiocap.ffmpeg_path()
        if not ff:
            self.error = "ffmpeg not found on PATH (brew install ffmpeg)"
            return
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

        Native: stop the engine and flush the ring. ffmpeg: `q\\n` on stdin finishes the
        segment it is writing and exits 0; SIGKILL leaves a 0-byte file that fails the wav
        probe, which is the last few seconds of the meeting gone.
        """
        self.stopping = True
        native = self._native
        if native is not None:
            with contextlib.suppress(Exception):
                native.stop()
            return
        self._shutdown_proc()
        self._emit_ready(exited=True)

    # ------------------------------------------------------------ native

    def _work_native(self) -> None:
        from .native_audio import Capture

        while not self.halt.is_set() and not self.stopping:
            try:
                cap = Capture.from_spec(self.input_spec)
                self._native = cap
                cap.start()
            except Exception as e:  # noqa: BLE001
                self.error = f"native capture: {e}"[:200]
                self.restarts += 1
                if self.restarts >= MAX_RESTARTS or self.stopping or self.halt.is_set():
                    if self.restarts >= MAX_RESTARTS:
                        self.error = f"{self.error} (gave up after {self.restarts} restarts)"
                    return
                self.sleep(RESTART_GAP)
                continue
            try:
                self._native_loop(cap)
                return
            except Exception as e:  # noqa: BLE001
                self.error = f"native capture: {e}"[:200]
            finally:
                with contextlib.suppress(Exception):
                    cap.stop()
                self._native = None
            if self.stopping or self.halt.is_set():
                return
            self.restarts += 1
            if self.restarts >= MAX_RESTARTS:
                self.error = f"{self.error} (gave up after {self.restarts} restarts)"
                log.warning("meeting: %s gave up: %s", self.name_id, self.error)
                return
            log.warning("meeting: %s restarting after %s", self.name_id, self.error)
            self.sleep(RESTART_GAP)

    def _native_loop(self, cap: Any) -> None:
        """Read segment-sized PCM chunks, write finished wavs, emit immediately.

        Native writes a complete file before reporting it, unlike ffmpeg's segment muxer
        which we can only trust once the next file has been opened.
        """
        seq = self._next
        empty_reads = 0
        while not self.halt.is_set() and not self.stopping:
            elapsed = time.time() - self.session_start
            remaining = self.max_seconds - elapsed
            if remaining <= 0.05:
                break
            secs = min(float(self.segment_seconds), remaining)
            if self.cut_on_silence:
                pcm = self._read_until_pause(cap, secs)
            else:
                pcm = cap.read_seconds(secs, self.halt)
            if self.stopping or self.halt.is_set():
                extra = cap.drain()
                if extra:
                    pcm = pcm + extra
            if cap.error and not pcm:
                raise RuntimeError(cap.error)
            # A halt mid-segment can leave a stub. Drop anything under ~100ms unless it is the
            # last chunk of a real take - then keep it so the end of the meeting is not silent.
            min_pcm = 16000 * 2 // 10
            if len(pcm) < min_pcm:
                self._pos_samples += len(pcm) // 2  # dropped audio still happened on the clock
                if self.stopping or self.halt.is_set() or remaining <= 0.05:
                    break
                # A device that vanished mid-capture returns empty reads forever with no error.
                # Say so in the status, but keep reading: tearing the channel down here would also
                # end a recording whose source simply had nothing to deliver for a while.
                empty_reads += 1
                if empty_reads >= EMPTY_READS_BEFORE_WARNING and not self.error:
                    self.error = NO_AUDIO
                continue
            empty_reads = 0
            if self.error == NO_AUDIO:
                self.error = ""
            path = self.out_dir / f"{self.channel}-{seq:05d}.wav"
            if not audiocap.write_pcm16_wav(path, pcm):
                raise RuntimeError("could not write segment wav")
            self._note_offsets(seq, len(pcm))
            self._emit_direct(seq, path)
            seq += 1
        extra = cap.drain()
        if extra and len(extra) >= 16000 * 2 // 10 and not self.halt.is_set():
            path = self.out_dir / f"{self.channel}-{seq:05d}.wav"
            if audiocap.write_pcm16_wav(path, extra):
                self._note_offsets(seq, len(extra))
                self._emit_direct(seq, path)

    def _read_until_pause(self, cap: Any, cap_seconds: float) -> bytes:
        """Read small steps until `meeting_vad.find_cut` says to close, or the audio stops coming.

        A halt or stop returns whatever has accumulated, like the fixed-length read does, so the
        final partial flush behaves the same."""
        buf = bytearray()
        cap_bytes = int(cap_seconds * 16000) * 2
        while not self.halt.is_set() and not self.stopping:
            step = min(CUT_STEP_SECONDS, (cap_bytes - len(buf)) / 32000)
            if step <= 0:
                break
            chunk = cap.read_seconds(step, self.halt)
            buf.extend(chunk)
            if cap.error and not chunk:
                break
            cut = meeting_vad.find_cut(
                bytes(buf), min_seconds=self.min_segment_seconds, max_seconds=cap_seconds,
                pause_seconds=CUT_PAUSE_SECONDS)
            if cut is not None:
                return bytes(buf[:cut])
        return bytes(buf)

    def _note_offsets(self, seq: int, pcm_bytes: int) -> None:
        """Advance the recording clock by a written segment's samples. Only when cutting on silence."""
        start = self._pos_samples
        self._pos_samples += pcm_bytes // 2
        if self.cut_on_silence:
            self.offsets[seq] = (start / 16000.0, self._pos_samples / 16000.0)

    def _emit_direct(self, seq: int, path: Path) -> None:
        with self._emit_lock:
            self._next = seq + 1
        try:
            self.on_segment(self.channel, seq, path)
        except Exception as e:  # noqa: BLE001 - a bad callback must not stop the capture
            self.error = f"{type(e).__name__}: {e}"
            log.warning("meeting: %s could not report seq %s: %s", self.name_id, seq, e)

    # ------------------------------------------------------------ internals

    def _run_once(self, ff: str) -> tuple[int, str]:
        """Launch ffmpeg and watch it until it exits or we are told to stop. (rc, stderr)."""
        start = self._next
        remaining = max(1, self.max_seconds - int(time.time() - self.session_start))
        argv = audiocap.segment_argv(ff, self.input_spec,
                                     str(self.out_dir / f"{self.channel}-%05d.wav"),
                                     self.segment_seconds, remaining)
        # segment_argv has no start-number flag, and without one ffmpeg reopens
        # channel-00000.wav - overwriting the beginning of the meeting after a restart, or the
        # kept audio of an earlier run of the same meeting. Always passed, including the 0 that
        # is already ffmpeg's default, so the numbering is never implicit.
        argv[-1:-1] = ["-segment_start_number", str(start)]
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
        self.proc = proc
        # stderr has to be read WHILE ffmpeg runs. -loglevel error still prints a line per
        # dropped input buffer, and once the ~64 KiB pipe buffer fills ffmpeg blocks in
        # write(2) for good: it never exits, so poll() stays None, the capture looks alive with
        # no error, and not one more segment is ever closed. The reader keeps only the tail,
        # which is the only part _last_line ever wanted.
        tail: collections.deque[bytes] = collections.deque(maxlen=STDERR_TAIL_LINES)
        reader = threading.Thread(target=_pump_stderr, args=(proc, tail),
                                  name=f"{self.name}-stderr", daemon=True)
        reader.start()
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
        # Bounded: the pipe is at EOF once the process is down. Never a blocking read on a live
        # process, which is what hung the capture thread when even `kill` timed out.
        reader.join(timeout=2)
        return (rc if rc is not None else 0), _last_line(b"".join(tail).decode("utf-8", "replace"))

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


class TranscribeWorker(_RecorderThread):
    """Drains (channel, seq, path, paused) off a queue, transcribes each wav, reports the result.

    Every outcome - silence, a broken file, three failed attempts - leaves through `on_result`.
    Nothing is dropped for being short: activity.py:860-861 throws away any transcript under
    `minChars`, which on a call silently deletes "yes, ship it".

    `paused` travels WITH the work item because it is a fact about when the audio was recorded,
    not a question to ask at dequeue time; and once `halt` is set the backlog is settled as
    replayable failures rather than transcribed, because the meeting it belongs to is already
    being finalized.
    """

    def __init__(self, meeting_id: str, q: "queue.Queue[tuple[str, int, Path, bool]]",
                 halt: threading.Event, *, out_dir: Path,
                 settings_fn: Callable[[], dict[str, Any]],
                 config_fn: Callable[[], dict[str, Any]],
                 data_dir: Path,
                 on_result: Callable[[str, int, Path, dict[str, Any]], None],
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
                channel, seq, path, paused = self.q.get(timeout=POLL_SECONDS)
            except queue.Empty:
                if self.halt.is_set():
                    return
                continue
            try:
                if self.halt.is_set():
                    self._abandon(channel, seq, path)
                else:
                    self._transcribe_one(channel, seq, path, paused)
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

    def _abandon(self, channel: str, seq: int, path: Path) -> None:
        """Settle a backlogged segment without a network call, keeping its audio.

        Halt means the session is already stopping, and one attempt can block for two minutes
        (stt.py:209 is httpx.Client(timeout=120)). Working through a backlog here would outlive
        the meeting by hours and write text nobody ever sees - MeetingService.stop rolls the
        transcript up and finalizes as soon as stop() returns - while the 45s tick re-queues the
        same wavs for retranscribe underneath us. A kept wav and a failed row is the honest
        outcome: it is exactly what retranscribe replays.
        """
        # No "; " in this message: MeetingService._settle_transcript splits `meetings.error` on
        # that separator to clear spent clauses, so a semicolon here would survive as two
        # unrecognised clauses forever. It also deliberately carries the "could not be
        # transcribed" marker from TRANSCRIPT_BANNERS, because a replay makes it untrue.
        self._report(channel, seq, path, self._result(
            state="failed", keep=True,
            error="this segment could not be transcribed before the recording stopped, so its "
                  "audio is kept for a retranscribe"))

    def _transcribe_one(self, channel: str, seq: int, path: Path, paused: bool) -> None:
        if paused:
            # `paused` is the state when the audio was RECORDED, handed over on the queue, not
            # the live flag read now. Pause does not kill ffmpeg - the seq numbering has to stay
            # monotonic - so paused segments arrive here and are thrown away on purpose; but the
            # queue is normally a transcription round trip behind, so re-reading the flag here
            # transcribed the aside the user paused for (they resumed before the worker caught
            # up) and deleted audio recorded while live (they paused after it was captured).
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

        cfg = self.config_fn()
        vad: dict[str, Any] = {}
        if cfg.get("vadGate", True):
            # A segment with no speech never reaches the (billed) STT call. An unreadable wav
            # comes back ok=False and is transcribed anyway: never drop audio on a parse failure.
            vad = meeting_vad.analyze(path)
            if vad["ok"] and vad["speech_ratio"] < float(cfg.get("vadMinSpeechRatio", 0.03)):
                self._report(channel, seq, path, self._result(
                    state="empty", detail={"vad": vad}, keep=False))
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
        detail = res.get("detail") or {}
        if text and not error and cfg.get("hallucinationFilter", True):
            ratio = vad["speech_ratio"] if vad.get("ok") else None
            text, detail, _dropped = stt.filter_hallucinations(text, detail, ratio, cfg)
            text = text.strip()
        if text:
            self._tail[channel] = text[-TAIL_CHARS:]
        self._report(channel, seq, path, self._result(
            state="done" if text and not error else ("failed" if error else "empty"),
            text=text, detail=detail, backend=str(res.get("backend") or ""),
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
                 on_disk_check: Callable[[], int] | None = None,
                 cut_on_silence: bool = False, min_segment_seconds: float = 2.0):
        if not channels:
            raise ValueError("a recording needs at least one channel")
        self.meeting_id = meeting_id
        self.out_dir = out_dir
        self.channels = {k: list(v) for k, v in channels.items()}
        self.segment_seconds = max(1, int(segment_seconds))
        self.max_seconds = max(1, int(max_seconds))
        self.keep_audio = keep_audio
        self.cut_on_silence = bool(cut_on_silence)
        self.min_segment_seconds = float(min_segment_seconds)
        self.drain_seconds = float(drain_seconds)
        self.on_segment = on_segment
        self.stop_event = threading.Event()
        self.q: queue.Queue[tuple[str, int, Path, bool]] = queue.Queue()
        self.paused = False
        self.stopping = False
        # True from construction until start() has the threads up. The pool registers a session
        # before it can possibly be alive, and `alive` means "a thread is running", so without
        # this a second concurrent start would find nothing recording and be admitted.
        self.starting = True
        self.started_at = 0.0
        self.ended_at = 0.0
        self.captures: dict[str, ChannelCapture] = {}
        self._counts = {"emitted": 0, "reported": 0, "done": 0, "failed": 0, "discarded": 0}
        self._lock = threading.Lock()
        self.worker = TranscribeWorker(
            meeting_id, self.q, self.stop_event, out_dir=out_dir, settings_fn=settings_fn,
            config_fn=config_fn, data_dir=data_dir, on_result=self._result,
            keep_audio=keep_audio, max_attempts=max_attempts,
            max_audio_bytes=max_audio_bytes, on_disk_check=on_disk_check)
        self._on_result = on_result

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.started_at = time.time()
        try:
            self.worker.start()
            for channel, spec in self.channels.items():
                cap = ChannelCapture(channel, spec, self.out_dir, self.segment_seconds,
                                     self.max_seconds, self.stop_event, self._segment,
                                     cut_on_silence=self.cut_on_silence,
                                     min_segment_seconds=self.min_segment_seconds)
                self.captures[channel] = cap
                cap.start()
        finally:
            # Even a half-started session must stop claiming to be starting, or the pool would
            # refuse every later start for the lifetime of the process.
            self.starting = False

    def pause(self, paused: bool) -> None:
        """Stop keeping audio without stopping ffmpeg, so seq numbering stays monotonic."""
        self.paused = bool(paused)

    def stop(self, drain_seconds: float | None = None, *,
             join_seconds: float = WORKER_JOIN_SECONDS) -> dict[str, Any]:
        """Captures down first, then drain what is already recorded, then the worker.

        Returns the stop contract - {"drained": bool, "pending": int, "stats": dict} - because
        the caller rolls the transcript up and finalizes the meeting the moment this returns.
        `drained` is True only when the worker really finished its backlog and exited inside the
        deadline; on False, `pending` segments are still unsettled and the transcript the caller
        is about to build is NOT the whole meeting.
        """
        self.stopping = True
        self.starting = False
        for cap in self.captures.values():
            with contextlib.suppress(Exception):
                cap.stop()
        for cap in self.captures.values():
            # RuntimeError: a capture that never got started (start() raised part way).
            with contextlib.suppress(RuntimeError):
                cap.join(timeout=5)
        deadline = time.time() + (self.drain_seconds if drain_seconds is None else float(drain_seconds))
        while time.time() < deadline and self.pending > 0:
            # halt.wait rather than sleep: a hard shutdown behind us cuts the drain short.
            self.stop_event.wait(0.2)
        self.stop_event.set()
        if self.worker.ident is not None:
            self.worker.join(timeout=max(0.1, float(join_seconds)))
        pending = self.pending
        drained = not self.worker.is_alive() and pending == 0 and self.q.qsize() == 0
        if not drained and not self.worker.error:
            # The one place that knows the transcript is short. errors() carries it into
            # status(), and the caller gets it in the return value either way.
            self.worker.error = (f"transcription was still running when the recording stopped; "
                                 f"{pending} segment(s) are missing from the transcript - "
                                 "retranscribe them once the route is healthy")
        self.ended_at = time.time()
        return {"drained": drained, "pending": pending, "stats": self.stats()}

    @property
    def alive(self) -> bool:
        # `starting` counts as alive: between the pool registering this session and its threads
        # coming up there is no thread to see, but the microphone is already claimed.
        return (self.starting or any(c.is_alive() for c in self.captures.values())
                or self.worker.is_alive())

    def captures_dead(self) -> bool:
        """True once every channel thread has exited on its own (gave up), not because of a stop."""
        return (not self.starting and not self.stopping and bool(self.captures)
                and not any(c.is_alive() for c in self.captures.values()))

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
        cap = self.captures.get(channel)
        real = cap.offsets.pop(seq, None) if cap is not None else None
        if real is not None:
            # Cut on silence: segments vary in length, so the capture measured where this one sits.
            t_start = real[0]
        else:
            t_start = float(seq * self.segment_seconds)
        # Read once, here, and handed to the worker on the queue: the row's state and the
        # worker's keep-or-discard decision have to be the same decision.
        paused = self.paused
        info = {
            "state": "discarded" if paused else "recorded",
            "t_start": t_start,
            "t_end": real[1] if real is not None else t_start + seconds,
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
        self.q.put((channel, seq, path, paused))

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
              on_disk_check: Callable[[], int] | None = None,
              cut_on_silence: bool = False,
              min_segment_seconds: float = 2.0) -> RecordingSession:
        with self._lock:
            live = self._live()
            if live is not None:
                raise RecorderBusy(live.meeting_id)
            if meeting_id in self.sessions:
                # Overwriting the entry would ORPHAN the session already under this id: nothing
                # could reach its stop_event again, so its worker would block on the queue for
                # good and its ffmpeg would run to its own -t backstop four hours later, both
                # of them writing the same mic-%05d.wav files as the new run.
                raise RecorderBusy(meeting_id)
            session = RecordingSession(
                meeting_id, recording_dir(self.data_dir, meeting_id), channels,
                settings_fn=self.settings_fn, config_fn=self.config_fn, data_dir=self.data_dir,
                on_segment=on_segment, on_result=on_result, segment_seconds=segment_seconds,
                max_seconds=max_seconds, keep_audio=keep_audio, max_attempts=max_attempts,
                max_audio_bytes=max_audio_bytes, drain_seconds=drain_seconds,
                on_disk_check=on_disk_check, cut_on_silence=cut_on_silence,
                min_segment_seconds=min_segment_seconds)
            self.sessions[meeting_id] = session
            # start() under the SAME lock as the busy check. It only spawns threads - ffmpeg is
            # launched inside the capture thread - so the lock is held for microseconds, and
            # releasing it first left a window where `alive` was still False (no thread had run
            # yet) and a second concurrent start - a double-clicked button, or the 45s _nudge
            # racing a manual start, both via asyncio.to_thread - was admitted as well.
            try:
                session.start()
            except Exception:
                self.sessions.pop(meeting_id, None)
                with contextlib.suppress(Exception):
                    session.stop(drain_seconds=0.0, join_seconds=0.5)
                raise
        return session

    def get(self, meeting_id: str) -> RecordingSession | None:
        return self.sessions.get(meeting_id)

    def live(self) -> RecordingSession | None:
        with self._lock:
            return self._live()

    def stop(self, meeting_id: str, drain_seconds: float | None = None) -> dict[str, Any]:
        session = self.sessions.get(meeting_id)
        if session is None:
            # Nothing was recording, so nothing can be outstanding: the caller's transcript is
            # as complete as this pool can make it. Same keys as a real stop, always.
            return {"drained": True, "pending": 0, "stats": {}}
        try:
            return session.stop(drain_seconds)
        finally:
            with self._lock:
                if self.sessions.get(meeting_id) is session:
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


def _first_free_seq(out_dir: Path, channel: str) -> int:
    """One past the highest `channel-%05d.wav` already on disk, so a second run of the same
    meeting never re-emits - or overwrites - the first run's audio."""
    highest = -1
    try:
        for path in out_dir.glob(f"{channel}-*.wav"):
            stem = path.name[len(channel) + 1:-len(".wav")]
            if stem.isdigit():
                highest = max(highest, int(stem))
    except OSError:
        return 0
    return highest + 1


def _pump_stderr(proc: subprocess.Popen[bytes], tail: "collections.deque[bytes]") -> None:
    """Keep ffmpeg's stderr pipe empty while it runs, holding on to the last lines only.

    Nothing drained the pipe during the run before this, so a chatty capture - one line per
    dropped input buffer is enough - filled the ~64 KiB buffer and ffmpeg blocked in write(2)
    permanently: no exit, no further segments, and `alive: True` with an empty error in status.
    """
    stream = proc.stderr
    if stream is None:
        return
    try:
        for line in iter(stream.readline, b""):
            tail.append(line)   # deque(maxlen=...): bounded, and append is atomic
    except Exception:  # noqa: BLE001 - a dead pipe must not crash the reader
        pass
    finally:
        with contextlib.suppress(Exception):
            stream.close()


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
