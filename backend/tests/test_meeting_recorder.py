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
CFG = {"sttBackend": "proxy", "sttModel": "whisper-1"}


class transcribes_as:
    """Swap `stt.transcribe` for a stub and record every call.

    The real one POSTs to a route nothing serves, so a worker test that let it through would be
    asserting on a 120s timeout.
    """

    def __init__(self, *results: dict):
        self.results = list(results)
        self.calls: list[dict] = []

    def __enter__(self) -> transcribes_as:
        self.real = meeting_recorder.stt.transcribe
        meeting_recorder.stt.transcribe = self._transcribe  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: object) -> None:
        meeting_recorder.stt.transcribe = self.real  # type: ignore[assignment]

    def _transcribe(self, path: Path, *, settings: dict, cfg: dict, data_dir: Path,
                    prompt: str = "") -> dict:
        self.calls.append({"path": path, "prompt": prompt, "model": cfg.get("sttModel")})
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

    def __init__(self, out_dir: Path, **kw: object):
        self.out_dir = out_dir
        self.results: list[tuple[str, int, dict]] = []
        self.halt = threading.Event()
        self.q: queue.Queue = queue.Queue()
        self.worker = meeting_recorder.TranscribeWorker(
            "m1", self.q, self.halt, out_dir=out_dir, settings_fn=lambda: dict(SETTINGS),
            config_fn=lambda: dict(CFG), data_dir=out_dir.parent,
            on_result=lambda c, s, p, r: self.results.append((c, s, dict(r))), **kw)

    def __enter__(self) -> _Worker:
        self.worker.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.halt.set()
        self.worker.join(timeout=10)

    def feed(self, seq: int, path: Path) -> None:
        self.q.put(("mic", seq, path))

    def wait(self, count: int, timeout: float = 30.0) -> None:
        end = time.time() + timeout
        while time.time() < end and len(self.results) < count:
            time.sleep(0.05)
        assert len(self.results) >= count, f"only {len(self.results)} results: {self.results}"


# ---------------------------------------------------------------- the capture loop


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
    with transcribes_as({"text": "private"}) as stub, _Worker(out, is_paused=lambda: True) as w:
        w.feed(0, path)
        w.wait(1)
    assert w.results[0][2]["state"] == "discarded"
    assert stub.calls == [], "paused audio must not be sent anywhere"
    assert not path.exists()


# ---------------------------------------------------------------- session and pool


def test_a_session_records_transcribes_and_reports_stats() -> None:
    if not audiocap.ffmpeg_path():
        print("  note  no ffmpeg on PATH, skipping the session")
        return
    data_dir = _tmp()
    segments: list[dict] = []
    results: list[dict] = []
    pool = meeting_recorder.RecorderPool(data_dir, lambda: dict(SETTINGS), lambda: dict(CFG))
    with transcribes_as({"text": "a tone"}):
        session = pool.start(
            "mtg-1", {"mic": audiocap.synthetic_input()},
            on_segment=lambda c, s, p, i: segments.append({"channel": c, "seq": s, **i}),
            on_result=lambda c, s, p, r: results.append({"channel": c, "seq": s, **r}),
            segment_seconds=1, max_seconds=4, drain_seconds=20)
        assert pool.live() is session
        try:
            pool.start("mtg-2", {"mic": audiocap.synthetic_input()},
                       on_segment=lambda *a: None, on_result=lambda *a: None)
            raise AssertionError("two meetings recorded at once")
        except meeting_recorder.RecorderBusy as e:
            assert e.meeting_id == "mtg-1"
        end = time.time() + 20
        while time.time() < end and len(segments) < 3:
            time.sleep(0.1)
        time.sleep(0.6)   # see the capture-loop test: the open segment needs some samples in it
        stats = pool.stop("mtg-1")

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
    assert pool.stop("nope") == {}
    pool.stop_all()   # must never raise


def test_a_session_needs_a_channel() -> None:
    try:
        meeting_recorder.RecordingSession(
            "m", _tmp(), {}, settings_fn=dict, config_fn=dict, data_dir=_tmp(),
            on_segment=lambda *a: None, on_result=lambda *a: None)
        raise AssertionError("a recording with no channels was accepted")
    except ValueError as e:
        assert "channel" in str(e)


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
