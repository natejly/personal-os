"""Meeting capture: the segment loop, the graceful stop, and the transcription worker.

Nothing here needs a microphone or a granted permission. ChannelCapture is driven by
`audiocap.synthetic_input()` - a real ffmpeg writing real wavs from a sine generator - and the
worker runs against a stubbed `stt.transcribe`, since nothing on this machine answers
/v1/audio/transcriptions. The ffmpeg tests skip with a printed note rather than failing when
there is no binary, because the recorder is supposed to degrade on such a machine.

Runs under pytest, or directly: python backend/tests/test_meeting_recorder.py
"""
from __future__ import annotations

import queue
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import audiocap, meeting_recorder  # noqa: E402

SETTINGS = {"baseUrl": "http://localhost:4000", "apiKey": ""}
# The fixture wavs are digital silence, so the VAD gate and the filter are off here to keep these
# tests about the worker's queue behaviour; test_meeting_vad.py covers both switched on.
CFG = {"sttBackend": "proxy", "sttModel": "whisper-1", "vadGate": False, "hallucinationFilter": False}


class transcribes_as:
    """Swap `stt.transcribe` for a stub and record every call.

    The real one POSTs to a route nothing serves, so a worker test that let it through would be
    asserting on a 120s timeout.
    """

    def __init__(self, *results: dict, hold: threading.Event | None = None):
        self.results = list(results)
        self.calls: list[dict] = []
        # Set to keep the FIRST call in flight: that is the only way to test what the worker
        # does with a backlog, since the real bugs all live in the gap between enqueue and
        # dequeue.
        self.hold = hold

    def __enter__(self) -> transcribes_as:
        self.real = meeting_recorder.stt.transcribe
        meeting_recorder.stt.transcribe = self._transcribe  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: object) -> None:
        meeting_recorder.stt.transcribe = self.real  # type: ignore[assignment]

    def _transcribe(self, path: Path, *, settings: dict, cfg: dict, data_dir: Path,
                    prompt: str = "") -> dict:
        self.calls.append({"path": path, "prompt": prompt, "model": cfg.get("sttModel")})
        if self.hold is not None and len(self.calls) == 1:
            self.hold.wait(20)
        i = min(len(self.calls) - 1, len(self.results) - 1)
        res = dict(self.results[i]) if self.results else {}
        res.setdefault("text", "")
        res.setdefault("detail", {})
        res.setdefault("backend", "proxy")
        res.setdefault("error", "")
        res.setdefault("ms", 1)
        return res


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="meetrec-"))


def _wav(path: Path, seconds: float = 0.5) -> Path:
    """A real, probe-valid 16k mono s16le wav, written without ffmpeg."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0" * int(16000 * 2 * seconds))
    return path


class _Worker:
    """A TranscribeWorker on its own queue, with the results it reported."""

    def __init__(self, out_dir: Path, cfg: dict | None = None, **kw: object):
        self.out_dir = out_dir
        self.cfg = {**CFG, **(cfg or {})}
        self.results: list[tuple[str, int, dict]] = []
        self.halt = threading.Event()
        self.q: queue.Queue = queue.Queue()
        self.worker = meeting_recorder.TranscribeWorker(
            "m1", self.q, self.halt, out_dir=out_dir, settings_fn=lambda: dict(SETTINGS),
            config_fn=lambda: dict(self.cfg), data_dir=out_dir.parent,
            on_result=lambda c, s, p, r: self.results.append((c, s, dict(r))), **kw)

    def __enter__(self) -> _Worker:
        self.worker.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.halt.set()
        self.worker.join(timeout=10)

    def feed(self, seq: int, path: Path, paused: bool = False) -> None:
        # (channel, seq, path, paused): `paused` is the state when the audio was RECORDED and
        # travels with the item, because the worker must not re-derive it at dequeue time.
        self.q.put(("mic", seq, path, paused))

    def wait(self, count: int, timeout: float = 30.0) -> None:
        end = time.time() + timeout
        while time.time() < end and len(self.results) < count:
            time.sleep(0.05)
        assert len(self.results) >= count, f"only {len(self.results)} results: {self.results}"


# ---------------------------------------------------------------- the capture loop


def test_native_sine_emits_ordered_segments_and_stops_cleanly() -> None:
    out = _tmp()
    seen: list[tuple[str, int, Path]] = []
    halt = threading.Event()
    cap = meeting_recorder.ChannelCapture(
        "mic", audiocap.native_sine_input(), out, 1, 4, halt,
        lambda c, s, p: seen.append((c, s, p)))
    cap.start()
    end = time.time() + 20
    while time.time() < end and len(seen) < 3:
        time.sleep(0.1)
    time.sleep(0.3)
    cap.stop()
    cap.join(timeout=10)

    assert len(seen) >= 3, f"only {len(seen)} segments: {seen}"
    assert [s for _, s, _ in seen] == list(range(len(seen))), seen
    assert all(c == "mic" for c, _, _ in seen)
    for _, seq, path in seen:
        assert path.name == f"mic-{seq:05d}.wav"
        ok, note = audiocap.validate_wav(path)
        assert ok, f"{path.name} did not validate: {note}"
    assert cap.proc is None
    assert cap.error == "", cap.error
    assert not cap.is_alive()


def test_native_sine_stops_itself_at_max_seconds() -> None:
    out = _tmp()
    seen: list[int] = []
    cap = meeting_recorder.ChannelCapture(
        "mic", audiocap.native_sine_input(), out, 1, 3, threading.Event(),
        lambda c, s, p: seen.append(s))
    cap.start()
    cap.join(timeout=20)
    assert not cap.is_alive(), "the capture outlived its own ceiling"
    assert cap.error == "", cap.error
    assert len(seen) >= 2, seen
    assert cap.restarts == 0, "a clean exit must not look like a crash"


def test_cut_on_silence_with_no_pauses_cuts_at_the_cap_with_contiguous_offsets() -> None:
    # A sine has no pauses, so every segment must close at segment_seconds, and the offsets the
    # capture measured have to tile the recording clock without gaps.
    out = _tmp()
    seen: list[int] = []
    cap = meeting_recorder.ChannelCapture(
        "mic", audiocap.native_sine_input(), out, 2, 7, threading.Event(),
        lambda c, s, p: seen.append(s), cut_on_silence=True, min_segment_seconds=1.0)
    offsets: dict[int, tuple[float, float]] = {}
    real = cap.on_segment
    cap.on_segment = lambda c, s, p: (offsets.__setitem__(s, cap.offsets[s]), real(c, s, p))  # type: ignore[assignment]
    cap.start()
    cap.join(timeout=25)
    assert not cap.is_alive() and cap.error == "", cap.error
    assert len(seen) >= 2, seen
    prev_end = 0.0
    for seq in sorted(offsets):
        a, b = offsets[seq]
        assert abs(a - prev_end) < 1e-6, offsets
        if seq <= max(offsets) - 2 or seq < 2:  # the tail near max_seconds may be short
            assert abs((b - a) - 2.0) < 0.05, f"segment {seq} was {b - a}s, not the 2 s cap"
        prev_end = b


def test_the_session_reports_the_measured_offsets_when_cutting_on_silence() -> None:
    session = meeting_recorder.RecordingSession(
        "mtg-cut", _tmp(), {"mic": ["x"]}, settings_fn=lambda: dict(SETTINGS), config_fn=lambda: dict(CFG),
        data_dir=_tmp(), on_segment=lambda *a: None, on_result=lambda *a: None, segment_seconds=6,
        cut_on_silence=True)
    cap = meeting_recorder.ChannelCapture(
        "mic", ["x"], _tmp(), 6, 60, threading.Event(), lambda *a: None, cut_on_silence=True)
    session.captures["mic"] = cap
    cap.offsets[1] = (3.25, 7.5)
    infos: list[dict] = []
    session.on_segment = lambda c, s, p, i: infos.append(i)
    session._segment("mic", 1, _wav(_tmp() / "mic-00001.wav", 4.25))
    assert infos[0]["t_start"] == 3.25 and infos[0]["t_end"] == 7.5, infos[0]
    # Without recorded offsets (the default mode) the fixed grid is what it always was.
    session._segment("mic", 2, _wav(_tmp() / "mic-00002.wav", 1.0))
    assert infos[1]["t_start"] == 12.0, infos[1]


def test_channel_capture_emits_ordered_segments_and_stops_cleanly() -> None:
    if not audiocap.ffmpeg_path():
        print("  note  no ffmpeg on PATH, skipping the capture loop")
        return
    out = _tmp()
    seen: list[tuple[str, int, Path]] = []
    halt = threading.Event()
    cap = meeting_recorder.ChannelCapture(
        "mic", audiocap.synthetic_input(), out, 1, 4, halt,
        lambda c, s, p: seen.append((c, s, p)))
    cap.start()
    end = time.time() + 20
    while time.time() < end and len(seen) < 3:
        time.sleep(0.1)
    # Segment N is reported the moment N+1 is opened, so N+1 is still empty here. Let it collect
    # more than MIN_WAV_BYTES before the graceful stop flushes it, or the last wav is legitimately
    # too short for validate_wav to accept.
    time.sleep(0.6)
    cap.stop()
    cap.join(timeout=10)

    assert len(seen) >= 3, f"only {len(seen)} segments: {seen}"
    assert [s for _, s, _ in seen] == list(range(len(seen))), seen   # monotonic, no gaps
    assert all(c == "mic" for c, _, _ in seen)
    for _, seq, path in seen:
        assert path.name == f"mic-{seq:05d}.wav"
        ok, note = audiocap.validate_wav(path)
        assert ok, f"{path.name} did not validate: {note}"
    assert cap.proc is not None and cap.proc.poll() is not None, "ffmpeg is still running"
    assert cap.error == "", cap.error
    assert not cap.is_alive()


def test_channel_capture_stops_itself_at_max_seconds() -> None:
    if not audiocap.ffmpeg_path():
        print("  note  no ffmpeg on PATH, skipping the -t ceiling")
        return
    out = _tmp()
    seen: list[int] = []
    cap = meeting_recorder.ChannelCapture(
        "mic", audiocap.synthetic_input(), out, 1, 3, threading.Event(),
        lambda c, s, p: seen.append(s))
    cap.start()
    cap.join(timeout=20)
    # -t caps the run even under -f segment, so the thread ends with no stop() behind it.
    assert not cap.is_alive(), "the capture outlived its own -t ceiling"
    assert cap.error == "", cap.error
    assert len(seen) >= 2, seen
    assert cap.restarts == 0, "a clean exit must not look like a crash"


def test_a_restart_continues_the_seq_numbering() -> None:
    if not audiocap.ffmpeg_path():
        print("  note  no ffmpeg on PATH, skipping the restart numbering")
        return
    out = _tmp()
    seen: list[int] = []
    cap = meeting_recorder.ChannelCapture(
        "mic", audiocap.synthetic_input(), out, 1, 3, threading.Event(),
        lambda c, s, p: seen.append(s))
    cap._next = 2   # as if two segments had already been written before an ffmpeg crash
    cap.start()
    cap.join(timeout=20)
    # segment_argv has no start-number flag; without the one we splice in, ffmpeg would reopen
    # mic-00000.wav and overwrite the start of the meeting.
    assert seen[:1] == [2], seen
    assert (out / "mic-00002.wav").exists()
    assert not (out / "mic-00000.wav").exists()


def test_a_channel_that_keeps_crashing_gives_up_with_a_reason() -> None:
    if not audiocap.ffmpeg_path():
        print("  note  no ffmpeg on PATH, skipping the restart ceiling")
        return
    cap = meeting_recorder.ChannelCapture(
        "mic", ["-f", "lavfi", "-i", "no_such_filter=1"], _tmp(), 1, 4, threading.Event(),
        lambda c, s, p: None)
    cap.start()
    cap.join(timeout=30)
    assert cap.restarts == meeting_recorder.MAX_RESTARTS, cap.restarts
    assert "gave up after 3 restarts" in cap.error, cap.error
    assert cap.error.strip() != "gave up after 3 restarts", "ffmpeg's own complaint is missing"


def test_channel_capture_without_ffmpeg_reports_the_fix() -> None:
    real = audiocap.ffmpeg_path
    audiocap.ffmpeg_path = lambda: ""  # type: ignore[assignment]
    try:
        cap = meeting_recorder.ChannelCapture(
            "mic", audiocap.synthetic_input(), _tmp(), 1, 4, threading.Event(),
            lambda c, s, p: None)
        cap.start()
        cap.join(timeout=5)
    finally:
        audiocap.ffmpeg_path = real  # type: ignore[assignment]
    assert "brew install ffmpeg" in cap.error
    assert cap.proc is None


def test_a_second_run_never_reports_or_overwrites_the_first_runs_wavs() -> None:
    """The regression: a meeting recorded, stopped and started again on the same id.

    Its failed segments keep their wavs on purpose (retranscribe replays them) and
    mark_started reuses audio_dir, so a capture that numbered from 0 emitted the previous run's
    files as this run's first segments and then let ffmpeg overwrite them underneath the worker
    - losing the old audio and the new meeting's first segments at once.
    """
    out = _tmp()
    leftovers = [_wav(out / f"mic-{i:05d}.wav", seconds=0.3) for i in range(6)]
    sizes = [p.stat().st_size for p in leftovers]
    seen: list[int] = []
    cap = meeting_recorder.ChannelCapture(
        "mic", audiocap.native_sine_input(), out, 1, 3, threading.Event(),
        lambda c, s, p: seen.append(s))
    assert cap._next == 6, f"numbering restarted over 6 wavs already on disk: {cap._next}"
    cap._emit_ready(exited=True)
    assert seen == [], f"a previous run's wavs were reported as this run's segments: {seen}"

    cap.start()
    cap.join(timeout=20)
    assert seen and seen[0] == 6, seen
    assert [p.stat().st_size for p in leftovers] == sizes, "the first run's audio was overwritten"
    assert (out / "mic-00006.wav").exists()
    assert cap.error == "", cap.error


def test_a_chatty_capture_never_wedges_on_a_full_stderr_pipe() -> None:
    """ffmpeg's stderr is read WHILE it runs, so it can never block in write(2).

    Nothing drained the pipe during the run before: past ~64 KiB of complaints (one line per
    dropped input buffer is enough over a four-hour ceiling) ffmpeg blocked forever, poll()
    kept returning None, and the capture went on looking alive with no error and no segments.
    Driven through a stand-in for ffmpeg that says 120 KiB worth before exiting 0.
    """
    noise = ("import sys\n"
             "for i in range(3000):\n"
             "    sys.stderr.write('[avfoundation @ 0x1] input buffer overrun %04d\\n' % i)\n"
             "sys.stderr.write('[avfoundation @ 0x1] the last complaint\\n')\n"
             "sys.stderr.flush()\n")
    out = _tmp()
    cap = meeting_recorder.ChannelCapture(
        "mic", [], out, 1, 5, threading.Event(), lambda c, s, p: None)
    got: list[tuple[int, str]] = []
    real = meeting_recorder.audiocap.segment_argv
    meeting_recorder.audiocap.segment_argv = (  # type: ignore[assignment]
        lambda *a, **k: [sys.executable, "-c", noise, "OUT"])
    try:
        runner = threading.Thread(target=lambda: got.append(cap._run_once("ffmpeg")), daemon=True)
        runner.start()
        runner.join(timeout=20)
        assert got, "_run_once never returned: the capture wedged on its unread stderr pipe"
        rc, err = got[0]
        assert rc == 0, rc
        # The tail still reaches the caller - that is what the old post-exit read was for.
        assert err == "[avfoundation @ 0x1] the last complaint", err
    finally:
        meeting_recorder.audiocap.segment_argv = real  # type: ignore[assignment]
        cap.stopping = True
        cap.halt.set()


def _tone_wav(path: Path, seconds: float = 4.0) -> Path:
    import math
    import struct
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        # A third of near-silence first: the VAD's floor is the quiet tail of the segment, so a
        # tone that never stops would (correctly) read as steady room noise.
        quiet = int(16000 * seconds / 3)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 16000)) if i >= quiet else 3)
                               for i in range(int(16000 * seconds))))
    return path


def test_the_vad_gate_skips_a_silent_segment_without_an_stt_call() -> None:
    out = _tmp()
    path = _wav(out / "mic-00000.wav", 2.0)
    with transcribes_as({"text": "Thank you."}) as stub, _Worker(out, {"vadGate": True}) as w:
        w.feed(0, path)
        w.wait(1)
    res = w.results[0][2]
    assert res["state"] == "empty" and res["text"] == ""
    assert stub.calls == [], "silence reached the billed STT call"
    assert not path.exists()


def test_the_vad_gate_lets_a_tone_through_once() -> None:
    out = _tmp()
    path = _tone_wav(out / "mic-00000.wav")
    with transcribes_as({"text": "hello there"}) as stub, _Worker(out, {"vadGate": True}) as w:
        w.feed(0, path)
        w.wait(1)
    assert len(stub.calls) == 1
    assert w.results[0][2]["state"] == "done" and w.results[0][2]["text"] == "hello there"


def test_the_hallucination_filter_empties_a_quiet_thank_you() -> None:
    out = _tmp()
    path = _tone_wav(out / "mic-00000.wav")  # speech ratio is high, so the phrase rule must not fire
    with transcribes_as({"text": "Thank you."}) as stub, _Worker(out, {"vadGate": True, "hallucinationFilter": True}) as w:
        w.feed(0, path)
        w.wait(1)
    assert w.results[0][2]["text"] == "Thank you."
    out2 = _tmp()
    seg = out2 / "mic-00000.wav"
    _tone_wav(seg)
    bad = {"text": "x", "detail": {"segments": [{"text": "x", "no_speech_prob": 0.9, "avg_logprob": -1.5}]}}
    with transcribes_as(bad) as stub, _Worker(out2, {"vadGate": True, "hallucinationFilter": True}) as w:
        w.feed(0, seg)
        w.wait(1)
    res = w.results[0][2]
    assert res["state"] == "empty" and res["detail"]["filtered"] == ["x"]


# ---------------------------------------------------------------- the worker


def test_a_transcribed_segment_loses_its_wav() -> None:
    out = _tmp()
    path = _wav(out / "mic-00000.wav")
    with transcribes_as({"text": "ship it"}) as stub, _Worker(out) as w:
        w.feed(0, path)
        w.wait(1)
    channel, seq, res = w.results[0]
    assert (channel, seq) == ("mic", 0)
    assert res["state"] == "done" and res["text"] == "ship it"
    assert res["attempts"] == 1 and res["error"] == ""
    assert res["wav_path"] == "" and res["wav_bytes"] == 0
    assert not path.exists(), "a transcribed wav is not worth keeping"
    assert len(stub.calls) == 1


def test_a_short_result_is_never_dropped() -> None:
    # activity.py:860-861 throws away any transcript under minChars, which on a call deletes
    # "yes" and "no" - the two most load-bearing words in a meeting.
    out = _tmp()
    with transcribes_as({"text": "no."}), _Worker(out) as w:
        w.feed(0, _wav(out / "mic-00000.wav"))
        w.wait(1)
    assert w.results[0][2]["text"] == "no."
    assert w.results[0][2]["state"] == "done"


def test_silence_is_empty_not_failed() -> None:
    out = _tmp()
    with transcribes_as({"text": "   "}), _Worker(out) as w:
        w.feed(0, _wav(out / "mic-00000.wav"))
        w.wait(1)
    res = w.results[0][2]
    assert res["state"] == "empty" and res["error"] == ""


def test_a_failure_retries_keeps_the_wav_and_counts_attempts() -> None:
    out = _tmp()
    path = _wav(out / "mic-00000.wav")
    with transcribes_as({"error": "transcription 404: no route"}) as stub, \
            _Worker(out, max_attempts=3) as w:
        w.feed(0, path)
        w.wait(1, timeout=60)
    res = w.results[0][2]
    assert len(stub.calls) == 3, f"retried {len(stub.calls)} times"
    assert res["attempts"] == 3 and res["state"] == "failed"
    assert res["error"] == "transcription 404: no route"
    # Kept so retranscribe can replay it once the user has an STT route at all.
    assert path.exists() and res["wav_path"] == str(path) and res["wav_bytes"] > 0


def test_a_second_attempt_that_succeeds_is_a_success() -> None:
    out = _tmp()
    path = _wav(out / "mic-00000.wav")
    with transcribes_as({"error": "transcription 500: busy"}, {"text": "second time"}), \
            _Worker(out, max_attempts=3) as w:
        w.feed(0, path)
        w.wait(1, timeout=60)
    res = w.results[0][2]
    assert res["state"] == "done" and res["text"] == "second time" and res["attempts"] == 2
    assert not path.exists()


def test_an_unusable_segment_is_reported_not_swallowed() -> None:
    out = _tmp()
    orphan = out / "mic-00000.wav"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"")          # what a SIGKILLed ffmpeg actually leaves
    with transcribes_as({"text": "never asked"}) as stub, _Worker(out) as w:
        w.feed(0, orphan)
        w.wait(1)
    res = w.results[0][2]
    assert res["state"] == "empty"
    assert res["error"] == "unusable segment: empty", res["error"]
    assert stub.calls == [], "a 0-byte wav must never reach the transcription route"
    assert not orphan.exists()


def test_the_previous_tail_is_sent_as_the_prompt() -> None:
    out = _tmp()
    tail = "a" * 400
    with transcribes_as({"text": tail}, {"text": "and then"}) as stub, _Worker(out) as w:
        w.feed(0, _wav(out / "mic-00000.wav"))
        w.wait(1)
        w.feed(1, _wav(out / "mic-00001.wav"))
        w.wait(2)
    assert stub.calls[0]["prompt"] == "", "there is no previous segment to condition on"
    assert stub.calls[1]["prompt"] == tail[-meeting_recorder.TAIL_CHARS:]
    assert len(stub.calls[1]["prompt"]) == meeting_recorder.TAIL_CHARS


def test_the_disk_ceiling_evicts_the_oldest_failed_wav() -> None:
    out = _tmp()
    first = _wav(out / "mic-00000.wav")
    second = _wav(out / "mic-00001.wav")
    one = first.stat().st_size
    with transcribes_as({"error": "transcription 404: no route"}), \
            _Worker(out, max_attempts=1, max_audio_bytes=one + 1) as w:
        w.feed(0, first)
        w.wait(1)
        assert first.exists(), "nothing older to evict yet"
        w.feed(1, second)
        w.wait(3)   # the second segment, plus the eviction update for the first

    assert not first.exists(), "the oldest failed wav survived the ceiling"
    assert second.exists(), "the newest failed wav was evicted instead of the oldest"
    evicted = [r for _, _, r in w.results if r.get("evicted")]
    assert len(evicted) == 1 and evicted[0]["wav_path"] == ""
    assert "disk ceiling" in evicted[0]["error"]
    current = [r for c, s, r in w.results if s == 1 and not r.get("evicted")][0]
    assert "ceiling" in current["error"], current["error"]


def test_keep_audio_keeps_a_transcribed_wav() -> None:
    out = _tmp()
    path = _wav(out / "mic-00000.wav")
    with transcribes_as({"text": "ship it"}), _Worker(out, keep_audio=True) as w:
        w.feed(0, path)
        w.wait(1)
    assert path.exists()
    assert w.results[0][2]["wav_path"] == str(path)


def test_a_paused_segment_is_discarded_without_transcribing() -> None:
    out = _tmp()
    path = _wav(out / "mic-00000.wav")
    with transcribes_as({"text": "private"}) as stub, _Worker(out) as w:
        w.feed(0, path, paused=True)
        w.wait(1)
    assert w.results[0][2]["state"] == "discarded"
    assert stub.calls == [], "paused audio must not be sent anywhere"
    assert not path.exists()


def test_the_pause_decision_is_the_one_made_when_the_audio_was_recorded() -> None:
    """Both directions of the pause race, which a dequeue-time flag got wrong both ways.

    seq 0 was recorded during a paused aside and dequeued after the user resumed: it must still
    be thrown away. seq 1 was recorded while live and dequeued after the user paused: it must
    still be transcribed, because `_report` DELETES the wav of a discarded segment.
    """
    out = _tmp()
    aside = _wav(out / "mic-00000.wav")
    wanted = _wav(out / "mic-00001.wav")
    with transcribes_as({"text": "wanted"}) as stub, _Worker(out) as w:
        w.feed(0, aside, paused=True)
        w.feed(1, wanted, paused=False)
        w.wait(2)
    by_seq = {seq: res for _, seq, res in w.results}
    assert by_seq[0]["state"] == "discarded" and by_seq[0]["text"] == "", by_seq[0]
    assert by_seq[1]["state"] == "done" and by_seq[1]["text"] == "wanted", by_seq[1]
    assert [c["path"] for c in stub.calls] == [wanted], "paused audio reached the stt route"
    # One was discarded and one was transcribed, so neither wav is worth keeping - but the
    # words of the wanted one survived in the row, which is the whole point.
    assert not aside.exists() and not wanted.exists()


def test_a_halted_worker_abandons_its_backlog_instead_of_transcribing_it() -> None:
    """stop() must not leave a thread transcribing a meeting that is already finalized.

    One attempt can block for two minutes (stt.py:209), so a six-segment backlog kept the
    worker alive for hours past the 5s join - writing text into a transcript that was rolled up
    when stop() returned, while retranscribe re-queued the same wavs from the other side.
    """
    out = _tmp()
    hold = threading.Event()
    paths = [_wav(out / f"mic-{i:05d}.wav") for i in range(6)]
    with transcribes_as({"text": "ship it"}, hold=hold) as stub, _Worker(out) as w:
        for i, path in enumerate(paths):
            w.feed(i, path)
        end = time.time() + 5
        while time.time() < end and not stub.calls:
            time.sleep(0.02)
        assert stub.calls, "the worker never picked up the first segment"
        w.halt.set()        # what RecordingSession.stop does
        hold.set()          # the in-flight call finally comes back
        w.wait(6, timeout=15)
    assert not w.worker.is_alive(), "the worker outlived its halt"
    assert len(stub.calls) == 1, [c["path"].name for c in stub.calls]
    backlog = [res for _, seq, res in w.results if seq > 0]
    assert len(backlog) == 5, w.results
    assert all(r["state"] == "failed" for r in backlog), backlog
    assert all("could not be transcribed before the recording stopped" in r["error"] for r in backlog)
    # The message carries a TRANSCRIPT_BANNERS marker and no "; ", so MeetingService can clear it
    # clause by clause once a replay makes it untrue.
    assert all("could not be transcribed" in r["error"] and "; " not in r["error"] for r in backlog)
    # Abandoned, not lost: the wav is kept, so retranscribe can still finish the job.
    assert all(p.exists() for p in paths[1:])
    assert all(r["wav_path"] == str(p) for r, p in zip(backlog, paths[1:]))


# ---------------------------------------------------------------- session and pool


def test_a_session_records_transcribes_and_reports_stats() -> None:
    data_dir = _tmp()
    segments: list[dict] = []
    results: list[dict] = []
    pool = meeting_recorder.RecorderPool(data_dir, lambda: dict(SETTINGS), lambda: dict(CFG))
    with transcribes_as({"text": "a tone"}):
        session = pool.start(
            "mtg-1", {"mic": audiocap.native_sine_input()},
            on_segment=lambda c, s, p, i: segments.append({"channel": c, "seq": s, **i}),
            on_result=lambda c, s, p, r: results.append({"channel": c, "seq": s, **r}),
            segment_seconds=1, max_seconds=4, drain_seconds=20)
        assert pool.live() is session
        try:
            pool.start("mtg-2", {"mic": audiocap.native_sine_input()},
                       on_segment=lambda *a: None, on_result=lambda *a: None)
            raise AssertionError("two meetings recorded at once")
        except meeting_recorder.RecorderBusy as e:
            assert e.meeting_id == "mtg-1"
        end = time.time() + 20
        while time.time() < end and len(segments) < 3:
            time.sleep(0.1)
        time.sleep(0.3)
        stopped = pool.stop("mtg-1")

    # The stop contract: the caller finalizes the meeting the moment this returns, so it has to
    # be told whether the worker really got through its backlog.
    assert set(stopped) == {"drained", "pending", "stats"}, stopped
    assert stopped["drained"] is True and stopped["pending"] == 0, stopped
    stats = stopped["stats"]
    assert len(segments) >= 3, segments
    assert [s["seq"] for s in segments] == list(range(len(segments)))
    # t_start comes from the recording clock, so it is exact multiples of segment_seconds.
    assert [s["t_start"] for s in segments[:3]] == [0.0, 1.0, 2.0]
    assert all(0.9 < s["duration_ms"] / 1000 < 1.1 for s in segments[:3]), segments[:3]
    assert all(s["started_at"] >= session.started_at for s in segments)
    assert stats["segments_done"] >= 3 and stats["segments_pending"] == 0
    assert stats["queued"] == 0 and stats["elapsed_ms"] > 0
    assert stats["channels"] == [{"channel": "mic", "alive": False, "error": ""}]
    assert len(results) == len(segments)
    assert all(r["state"] == "done" and r["text"] == "a tone" for r in results)
    assert pool.get("mtg-1") is None and pool.live() is None
    assert not session.alive
    # Everything transcribed, so nothing is left on disk - but the directory is under
    # recordings/, never tmp/, which app.py:1113 deletes on every shutdown.
    assert session.out_dir == data_dir / "recordings" / "mtg-1"
    assert audiocap.dir_bytes(session.out_dir) == 0


def test_recordings_never_live_under_tmp() -> None:
    d = meeting_recorder.recording_dir(Path("/data"), "abc")
    assert d == Path("/data/recordings/abc")
    assert "tmp" not in d.parts


def test_stop_all_is_safe_with_nothing_running() -> None:
    pool = meeting_recorder.RecorderPool(_tmp(), lambda: dict(SETTINGS), lambda: dict(CFG))
    assert pool.live() is None
    # Nothing was recording, so nothing is outstanding - but the keys are the same every time,
    # because the caller reads them unconditionally.
    assert pool.stop("nope") == {"drained": True, "pending": 0, "stats": {}}
    pool.stop_all()   # must never raise


def test_a_session_needs_a_channel() -> None:
    try:
        meeting_recorder.RecordingSession(
            "m", _tmp(), {}, settings_fn=dict, config_fn=dict, data_dir=_tmp(),
            on_segment=lambda *a: None, on_result=lambda *a: None)
        raise AssertionError("a recording with no channels was accepted")
    except ValueError as e:
        assert "channel" in str(e)


def _nothing_session(pool: "meeting_recorder.RecorderPool", meeting_id: str,
                     on: dict) -> None:
    """Start a session through the pool with a stand-in start(), recording the outcome."""
    try:
        session = pool.start(meeting_id, {"mic": ["-f", "lavfi", "-i", "anullsrc"]},
                             on_segment=lambda *a: None, on_result=lambda *a: None)
        on.setdefault("started", []).append((meeting_id, session))
    except meeting_recorder.RecorderBusy as e:
        on.setdefault("busy", []).append((meeting_id, e.meeting_id))


class _slow_claim:
    """Replace RecordingSession.start with a 0.3s no-op, widening the claim window.

    No thread, no ffmpeg: the point is exactly the state the pool used to be blind to - a
    session that is registered but has nothing running yet.
    """

    @staticmethod
    def _claim(session: object) -> None:
        time.sleep(0.3)      # the window the pool used to leave open

    def __enter__(self) -> None:
        self.real = meeting_recorder.RecordingSession.start
        meeting_recorder.RecordingSession.start = self._claim  # type: ignore[method-assign]

    def __exit__(self, *exc: object) -> None:
        meeting_recorder.RecordingSession.start = self.real  # type: ignore[method-assign]


def _race(ids: list[str], pool: "meeting_recorder.RecorderPool") -> dict:
    out: dict = {}
    gate = threading.Barrier(len(ids))

    def go(meeting_id: str) -> None:
        gate.wait(5)
        _nothing_session(pool, meeting_id, out)

    threads = [threading.Thread(target=go, args=(i,)) for i in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads), "a start never returned"
    return out


def test_two_concurrent_starts_for_one_meeting_admit_exactly_one() -> None:
    """A double-clicked Start, or the 45s nudge racing a manual one - both via asyncio.to_thread.

    Registering the session and starting it used to happen on either side of the lock, so both
    threads passed the busy check and the second one's dict entry ORPHANED the first: nothing
    could reach its stop_event again, and its ffmpeg would have run to the four-hour backstop
    writing the same mic-%05d.wav files as the winner.
    """
    pool = meeting_recorder.RecorderPool(_tmp(), lambda: dict(SETTINGS), lambda: dict(CFG))
    with _slow_claim():
        out = _race(["mtg-1", "mtg-1"], pool)
    started = out.get("started", [])
    busy = out.get("busy", [])
    assert len(started) == 1, f"{len(started)} starts were admitted for one meeting"
    assert len(busy) == 1 and busy[0][1] == "mtg-1", busy
    assert list(pool.sessions) == ["mtg-1"]
    assert pool.sessions["mtg-1"] is started[0][1], "the live session was orphaned from the pool"


def test_a_registered_session_is_busy_before_its_threads_are_up() -> None:
    """Two different meetings racing: `alive` cannot mean "a thread is running" alone.

    Between the pool registering a session and its threads coming up there is nothing to see,
    but the microphone is already claimed - so the second meeting has to be refused.
    """
    pool = meeting_recorder.RecorderPool(_tmp(), lambda: dict(SETTINGS), lambda: dict(CFG))
    with _slow_claim():
        out = _race(["mtg-a", "mtg-b"], pool)
    assert len(out.get("started", [])) == 1, out
    assert len(out.get("busy", [])) == 1, out
    assert len(pool.sessions) == 1, list(pool.sessions)
    claimed = out["started"][0][1]
    assert claimed.alive and claimed.starting, "a registered session must count as live"
    assert pool.live() is claimed


def test_a_segment_carries_the_pause_state_it_was_recorded_with() -> None:
    out = _tmp()
    recorded: list[dict] = []
    session = meeting_recorder.RecordingSession(
        "m1", out, {"mic": audiocap.synthetic_input()}, settings_fn=lambda: dict(SETTINGS),
        config_fn=lambda: dict(CFG), data_dir=out.parent,
        on_segment=lambda c, s, p, i: recorded.append(dict(i)), on_result=lambda *a: None,
        segment_seconds=1)
    first = _wav(out / "mic-00000.wav")
    second = _wav(out / "mic-00001.wav")
    session.captures["mic"] = meeting_recorder.ChannelCapture(
        "mic", [], out, 1, 4, session.stop_event, session._segment)
    session.pause(True)
    session._segment("mic", 0, first)
    session.pause(False)
    session._segment("mic", 1, second)

    assert [r["state"] for r in recorded] == ["discarded", "recorded"], recorded
    # The row's state and the worker's decision are the same decision, carried together.
    assert session.q.get_nowait() == ("mic", 0, first, True)
    assert session.q.get_nowait() == ("mic", 1, second, False)


def test_stop_says_so_when_the_worker_does_not_finish_in_time() -> None:
    """The other half of the stop contract: an undrained stop must not look like a clean one."""
    data_dir = _tmp()
    out = meeting_recorder.recording_dir(data_dir, "mtg-x")
    out.mkdir(parents=True, exist_ok=True)
    path = _wav(out / "mic-00000.wav")
    session = meeting_recorder.RecordingSession(
        "mtg-x", out, {"mic": audiocap.synthetic_input()}, settings_fn=lambda: dict(SETTINGS),
        config_fn=lambda: dict(CFG), data_dir=data_dir,
        on_segment=lambda *a: None, on_result=lambda *a: None, segment_seconds=1)
    # A capture that is constructed but never started: stop() must not trip over it, and
    # _emit_ready must not re-report the wav this test queues by hand.
    session.captures["mic"] = meeting_recorder.ChannelCapture(
        "mic", [], out, 1, 4, session.stop_event, session._segment)
    hold = threading.Event()
    with transcribes_as({"text": "too late"}, hold=hold) as stub:
        session.worker.start()
        session._segment("mic", 0, path)
        end = time.time() + 5
        while time.time() < end and not stub.calls:
            time.sleep(0.02)
        assert stub.calls, "the worker never picked up the segment"
        stopped = session.stop(drain_seconds=0.2, join_seconds=0.3)
        assert stopped["drained"] is False, stopped
        assert stopped["pending"] == 1, stopped
        assert stopped["stats"]["segments_pending"] == 1, stopped["stats"]
        assert "still running" in session.errors().get("transcribe", ""), session.errors()
        hold.set()
    session.worker.join(timeout=15)
    assert not session.worker.is_alive()


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
