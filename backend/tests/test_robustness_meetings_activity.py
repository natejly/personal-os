"""Robustness regressions for meeting recording and the activity monitor.

Each test pins one defect a review found: audio lost after a crash, a delete orphaning the
recorder, a double stop inflating a duration, the audio collector writing while paused, a
malformed insights reply, typed text leaking through a rollup failure, dead captures looking
alive, and the event tap staying off. Nothing here touches a microphone, a screen or a network.

Runs under pytest, or directly: python backend/tests/test_robustness_meetings_activity.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import activity, insights, meeting_recorder, meetings, native_audio  # noqa: E402
from personal_os.db import Database  # noqa: E402

SETTINGS = {"baseUrl": "http://localhost:4000", "apiKey": "", "defaultModel": "test-model", "extractionModel": ""}


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="robust-"))


def _wav(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0" * 16000)
    return path


def _svc(tmp: Path) -> tuple[meetings.Meetings, meetings.MeetingService]:
    async def fake_complete(settings, model, messages, kind="learn"):
        return ""

    db = Database(tmp)
    repo = meetings.Meetings(db)
    return repo, meetings.MeetingService(db, lambda: dict(SETTINGS), fake_complete, repo)


class _Session:
    """A recorder session that records how it was stopped."""

    def __init__(self, meeting_id: str):
        self.meeting_id = meeting_id
        self.started_at = time.time()
        self.stopping = False
        self.alive = True
        self.stopped = False

    def captures_dead(self) -> bool:
        return False

    def errors(self) -> dict:
        return {}


def _live_pool(svc: meetings.MeetingService, meeting_id: str) -> _Session:
    session = _Session(meeting_id)
    svc.pool.sessions = {meeting_id: session}

    def stop(mid: str, drain_seconds: float | None = None) -> dict:
        session.stopped = True
        session.stopping = True
        svc.pool.sessions.pop(mid, None)
        return {"drained": True, "pending": 0, "stats": {}}

    svc.pool.stop = stop  # type: ignore[method-assign]
    return session


# ---------------------------------------------------------------- 1. crash recovery keeps audio


def test_recover_turns_interrupted_segments_into_replayable_failures() -> None:
    tmp = _tmp()
    repo, svc = _svc(tmp)
    mid = repo.create(title="Call")["id"]
    with repo.db.tx() as c:
        c.execute("UPDATE meetings SET status='recording', started_at=? WHERE id=?", (time.time() - 60, mid))
    wav = _wav(tmp / "rec" / "mic-00000.wav")
    repo.add_segment(mid, "mic", 0, 0.0, 1.0, time.time() - 60, str(wav), wav.stat().st_size)
    repo.add_segment(mid, "mic", 1, 1.0, 2.0, time.time() - 59, str(tmp / "missing.wav"), 10)

    assert svc.recover() == [mid]
    states = {s["seq"]: s["state"] for s in repo.segments(mid)}
    assert states[0] == "failed"                        # replayable: retranscribe reads state='failed'
    assert states[1] == "recorded"                         # no wav, nothing to replay
    assert "interrupted" in next(s for s in repo.segments(mid) if s["seq"] == 0)["error"]
    # ... and the sweep keeps that wav instead of deleting it on the next tick
    with repo.db.tx() as c:
        c.execute("UPDATE meetings SET audio_dir=? WHERE id=?", (str(tmp / "rec"), mid))
    svc._sweep_audio({**svc.config(), "keepAudio": False})
    assert wav.exists()


# ---------------------------------------------------------------- 2. delete while recording


def test_delete_while_recording_stops_the_recorder_first() -> None:
    tmp = _tmp()
    repo, svc = _svc(tmp)
    mid = repo.create(title="Call")["id"]
    session = _live_pool(svc, mid)
    repo.delete(mid)
    assert session.stopped and repo.get(mid) is None
    assert svc.pool.live() is None


def test_delete_audio_while_recording_stops_and_closes_the_meeting() -> None:
    tmp = _tmp()
    repo, svc = _svc(tmp)
    mid = repo.create(title="Call")["id"]
    with repo.db.tx() as c:
        c.execute("UPDATE meetings SET status='recording', started_at=? WHERE id=?", (time.time() - 5, mid))
    session = _live_pool(svc, mid)
    out = repo.delete_audio(mid)
    assert session.stopped and out["status"] == "ready"


def test_stop_after_the_row_is_gone_still_releases_the_pool() -> None:
    tmp = _tmp()
    repo, svc = _svc(tmp)
    session = _live_pool(svc, "ghost")
    assert asyncio.run(svc.stop("ghost")) is None
    assert session.stopped


# ---------------------------------------------------------------- 3. stop on a finished meeting


def test_stop_on_a_finished_meeting_keeps_its_duration() -> None:
    tmp = _tmp()
    repo, svc = _svc(tmp)
    mid = repo.create(title="Call")["id"]
    start = time.time() - 7200
    repo.finalize(mid, "hello", ended_at=start + 200, status="ready")
    with repo.db.tx() as c:
        c.execute("UPDATE meetings SET started_at=? WHERE id=?", (start, mid))
    repo.finalize(mid, "hello", ended_at=start + 200, status="ready")
    out = asyncio.run(svc.stop(mid))
    assert out["duration_ms"] == 200_000


def test_stop_on_a_scheduled_meeting_does_not_flip_it_to_ready() -> None:
    tmp = _tmp()
    repo, svc = _svc(tmp)
    mid = repo.create(title="Later", status="scheduled")["id"]
    out = asyncio.run(svc.stop(mid))
    assert out["status"] == "scheduled"


# ---------------------------------------------------------------- 4. audio collector


def _audio_collector(tmp: Path, monkeypatch_native: bool = True):
    async def fake_complete(settings, model, messages, kind="learn"):
        return ""

    m = activity.Monitor(Database(tmp), lambda: dict(SETTINGS), fake_complete)
    m.set_config({"signals": {"micAudio": True}})
    m.stop()
    m.running = True
    m.paused = False
    m.stop_event = threading.Event()
    return m, activity.AudioCollector(m, "mic")


def test_audio_collector_does_not_transcribe_or_store_once_paused() -> None:
    m, col = _audio_collector(_tmp())
    real = (native_audio.can_capture, native_audio.record_seconds, activity.frontmost_app)
    seen = {"transcribed": 0}

    def record(kind, seconds, uid="", freq=440):
        m.paused = True                                   # Pause lands during the capture
        return b"\x01\x00" * 4000

    native_audio.can_capture = lambda kind: True          # type: ignore[assignment]
    native_audio.record_seconds = record                   # type: ignore[assignment]
    col._transcribe = lambda path, model: seen.__setitem__("transcribed", seen["transcribed"] + 1) or "x" * 40  # type: ignore[method-assign]
    col.sleep = lambda s: m.stop_event.set()              # type: ignore[method-assign]
    try:
        col.work()
    finally:
        native_audio.can_capture, native_audio.record_seconds, activity.frontmost_app = real  # type: ignore[assignment]
    assert seen["transcribed"] == 0
    assert m.store.counts()["events"] == 0


def test_audio_collector_backs_off_when_capture_raises() -> None:
    m, col = _audio_collector(_tmp())
    real = (native_audio.can_capture, native_audio.record_seconds)
    sleeps: list[float] = []

    def record(kind, seconds, uid="", freq=440):
        raise RuntimeError("microphone permission denied")

    def sleep(s: float) -> None:
        sleeps.append(s)
        m.stop_event.set()

    native_audio.can_capture = lambda kind: True          # type: ignore[assignment]
    native_audio.record_seconds = record                  # type: ignore[assignment]
    col.sleep = sleep                                     # type: ignore[method-assign]
    try:
        col.work()
    finally:
        native_audio.can_capture, native_audio.record_seconds = real  # type: ignore[assignment]
    assert sleeps and "permission denied" in col.error


# ---------------------------------------------------------------- 5. malformed insights reply


def _monitor(reply: str) -> activity.Monitor:
    calls: list[int] = []

    async def fake_complete(settings, model, messages, kind="learn"):
        calls.append(1)
        return reply

    m = activity.Monitor(Database(_tmp()), lambda: dict(SETTINGS), fake_complete)
    m.llm_calls = calls  # type: ignore[attr-defined]
    return m


def test_insights_tolerates_bad_items_and_duplicate_keys() -> None:
    m = _monitor("")
    habits = [
        "not a dict",
        {"key": "habit-a", "statement": "Works in the editor every morning.", "confidence": "high"},
        {"key": "habit-a", "statement": "A second claim with the same key.", "confidence": 0.9},
        {"key": "habit-b", "statement": "Reviews mail after lunch each day.", "confidence": 0.9, "evidence": 5},
    ]
    out = m.insights._persist_habits(habits, True, 0.1)
    assert sorted(h["key"] for h in out) == ["habit-a", "habit-b"]
    owned = {h["memory_id"] for h in m.insights.list_habits()}
    mems = [x for x in m.insights.memories.list(None) if x.get("source") == insights.MEMORY_SOURCE]
    assert len(mems) == len(owned) == 2                   # no memory without a habit behind it
    sugs = m.insights._persist_suggestions(
        [None, {"title": "Do the thing", "key": "sug-x", "confidence": "high", "evidence": 3},
         {"title": "Again", "key": "sug-x"}], 5)
    assert len(sugs) == 1


def test_refresh_sets_last_run_even_when_persisting_fails() -> None:
    m = _monitor('{"habits": [{"key": "habit-z", "statement": "Reads mail after lunch daily."}]}')
    m.insights.mine_now = lambda: {"patterns": [insights._pattern("k", "t", "d", support=1, days=1, window=1, evidence={})]}  # type: ignore[method-assign]

    def boom(*a, **k):
        raise RuntimeError("db")

    m.insights._persist_habits = boom  # type: ignore[method-assign]
    try:
        asyncio.run(m.insights.refresh(force=True))
    except RuntimeError:
        pass
    assert m.insights.last_run > 0


# ---------------------------------------------------------------- 6. rollup failure paths


def test_rollup_fallback_carries_no_typed_or_heard_text() -> None:
    m = _monitor("__never__")

    async def boom(*a, **k):
        raise RuntimeError("proxy down")

    m._complete = boom
    now = time.time()
    m.store.add("focus", app="Cursor", title="secret-plans.md", duration_ms=600_000, ts=now - 600)
    m.store.add("input", app="Cursor", text="my password is hunter2", meta={"keys": 40}, ts=now - 300)
    m.store.add("audio", app="Cursor", text="wire the money to account 12345", meta={"channel": "mic"}, ts=now - 200)
    s = asyncio.run(m.rollup_once(force=True))
    assert "hunter2" not in s["body"] and "12345" not in s["body"] and "secret-plans" not in s["body"]
    assert "Cursor" in s["body"]


def test_rollup_with_a_non_json_reply_keeps_events_pending() -> None:
    m = _monitor("Sorry, I cannot help with that.")
    m.store.add("focus", app="Cursor", duration_ms=600_000, ts=time.time() - 600)
    assert asyncio.run(m.rollup_once(force=True)) is None
    assert m.store.counts()["pending"] == 1 and m.store.counts()["summaries"] == 0
    assert "rollup failed" in m.last_error


# ---------------------------------------------------------------- 7. dead captures


class _DeadCapture:
    error = "gave up after 3 restarts"

    def is_alive(self) -> bool:
        return False


def test_session_with_every_capture_dead_is_reported_dead() -> None:
    tmp = _tmp()
    s = meeting_recorder.RecordingSession(
        "m1", tmp, {"mic": ["native", "sine"]}, settings_fn=dict, config_fn=dict, data_dir=tmp,
        on_segment=lambda *a: None, on_result=lambda *a: None)
    s.starting = False
    s.captures = {"mic": _DeadCapture()}  # type: ignore[dict-item]
    assert s.captures_dead()
    s.stopping = True
    assert not s.captures_dead()


def test_auto_stop_closes_a_session_whose_captures_died() -> None:
    tmp = _tmp()
    repo, svc = _svc(tmp)
    mid = repo.create(title="Call")["id"]
    with repo.db.tx() as c:
        c.execute("UPDATE meetings SET status='recording', started_at=? WHERE id=?", (time.time() - 30, mid))
    svc.set_config({"enhanceOnStop": False})
    session = _live_pool(svc, mid)
    session.captures_dead = lambda: True                  # type: ignore[method-assign]
    session.errors = lambda: {"mic": "no audio from the capture device"}  # type: ignore[method-assign]
    asyncio.run(svc._auto_stop(svc.config()))
    row = repo.get(mid)
    assert session.stopped and row["status"] == "ready"
    assert "no audio" in row["error"]


def test_native_loop_reports_empty_reads_without_ending_the_channel() -> None:
    tmp = _tmp()
    halt = threading.Event()
    cap = meeting_recorder.ChannelCapture("mic", ["native", "sine"], tmp, 1, 600, halt, lambda *a: None)
    cap.session_start = time.time()
    reads: list[int] = []

    class Quiet:
        """Nothing for a while, then audio again: a source with nothing to deliver, not a dead one."""
        error = ""

        def read_seconds(self, secs, halt_):
            reads.append(1)
            if len(reads) == meeting_recorder.EMPTY_READS_BEFORE_WARNING + 1:
                assert cap.error == meeting_recorder.NO_AUDIO  # said so while it was quiet
                return b"\x01\x00" * 16000
            if len(reads) > meeting_recorder.EMPTY_READS_BEFORE_WARNING + 1:
                halt.set()
            return b""

        def drain(self):
            return b""

    cap._native_loop(Quiet())  # must not raise: silence is not a reason to tear the channel down

    assert cap.error == ""  # cleared once audio arrived again
    assert list(tmp.glob("mic-*.wav"))


def test_read_seconds_surfaces_a_tap_callback_error() -> None:
    cap = native_audio.Capture("sine")
    cap.sink.error = "ValueError: bad buffer"
    cap.read_seconds(1.0, threading.Event())
    assert cap.error == "ValueError: bad buffer"


# ---------------------------------------------------------------- 8. event tap re-enable


def test_tap_disabled_events_reenable_the_tap_and_count_nothing() -> None:
    m = _monitor("")
    col = activity.InputCollector(m)
    enabled: list[tuple] = []
    activity._pyobjc["Quartz"] = SimpleNamespace(
        CGEventTapEnable=lambda tap, on: enabled.append((tap, on)),
        kCGEventKeyDown=10, kCGEventScrollWheel=22)
    col.tap = "TAP"
    m.running = True
    col._on_event(None, 0xFFFFFFFE, object(), None)
    col._on_event(None, 0xFFFFFFFF, object(), None)
    assert enabled == [("TAP", True), ("TAP", True)]
    assert col.clicks == 0


# ---------------------------------------------------------------- 9. patterns and titles


def test_purge_drops_the_pattern_snapshot_with_its_events() -> None:
    m = _monitor("")
    m.insights._save_patterns({"patterns": [{"title": 'Keeps returning to "quarterly-plan.xlsx"'}]})
    m.purge("events")
    assert not m.insights.patterns()
    m.insights._save_patterns({"patterns": [{"title": "x"}]})
    m.purge("expired")                                    # fresh snapshot, inside retention: kept
    assert m.insights.patterns()
    with m.db.tx() as c:
        c.execute("UPDATE activity_patterns SET updated_at=?", (time.time() - 100 * 3600,))
    m.purge("expired")
    assert not m.insights.patterns()


# ---------------------------------------------------------------- 10. palantir restore


def test_exclusion_edits_during_palantir_survive_turning_it_off() -> None:
    m = _monitor("")
    m.set_config({"excludeApps": ["OldApp"]})
    m.set_palantir(True)
    m.set_config({"excludeApps": ["NewApp"], "redact": True})
    assert m.config()["excludeApps"] == []                # the mode is still flat
    m.set_palantir(False)
    assert m.config()["excludeApps"] == ["NewApp"] and m.config()["redact"] is True
    m.stop()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("ok  ", fn.__name__)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("FAIL", fn.__name__, type(e).__name__, e)
    sys.exit(1 if failed else 0)
