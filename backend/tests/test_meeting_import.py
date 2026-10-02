"""Importing an audio file into a meeting: segments, transcript, retention, failures and refusals.

Needs ffmpeg/ffprobe (they cut the file); skips with a printed note when they are absent. STT and
the enhance LLM are stubs, the database is a throwaway, and nothing touches the network.

Runs under pytest, or directly: python backend/tests/test_meeting_import.py
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import audiocap, meeting_import, meeting_recorder, meetings, stt  # noqa: E402
from personal_os.db import Database  # noqa: E402

SETTINGS = {"baseUrl": "http://localhost:4000", "apiKey": "", "defaultModel": "test-model", "extractionModel": ""}
REPLY = json.dumps({"enhanced_markdown": "# Notes\n- shipped", "decisions": [], "action_items": [],
                    "topics": [], "headline": "Shipped"})
HAVE_FFMPEG = bool(audiocap.ffmpeg_path() and audiocap.ffprobe_path())


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="mimport-"))


def _sine(path: Path, seconds: float) -> Path:
    subprocess.run([audiocap.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=16000", "-t", str(seconds), "-y", str(path)], check=True)
    return path


def _svc(consent: bool = True, **cfg: object):
    tmp = _tmp()

    async def complete(settings, model, messages, kind="learn"):
        return REPLY

    db = Database(tmp)
    repo = meetings.Meetings(db)
    svc = meetings.MeetingService(db, lambda: dict(SETTINGS), complete, repo)
    # The fixture is a steady tone, which the VAD would call room noise; the gate has its own tests.
    svc.set_config({"vadGate": False, "hallucinationFilter": False,
                    "consentedAt": time.time() if consent else 0.0, "enhanceOnStop": False, **cfg})
    return repo, svc, tmp


class stt_is:
    def __init__(self, fail_call: int | None = None):
        self.n = 0
        self.fail_call = fail_call

    def __enter__(self) -> stt_is:
        self.real = stt.transcribe
        self.real_backoff = meeting_recorder.RETRY_BACKOFF
        meeting_recorder.RETRY_BACKOFF = (0.01,)
        stt.transcribe = self._t  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: object) -> None:
        stt.transcribe = self.real  # type: ignore[assignment]
        meeting_recorder.RETRY_BACKOFF = self.real_backoff

    def _t(self, path, *, settings, cfg, data_dir, prompt=""):
        self.n += 1
        # Segments are drained in order; a failing segment is retried, so key off the file name.
        k = int(Path(path).stem.split("-")[1])
        if self.fail_call is not None and k == self.fail_call:
            return {"text": "", "detail": {}, "backend": "proxy", "error": "boom", "ms": 1}
        return {"text": f"seg {k}", "detail": {}, "backend": "proxy", "error": "", "ms": 1}


def _run(svc, mid: str, src: Path):
    return asyncio.run(meeting_import.run(svc, mid, src))


def test_a_fifty_second_file_becomes_three_import_segments() -> None:
    if not HAVE_FFMPEG:
        print("  skip: ffmpeg missing")
        return
    repo, svc, tmp = _svc()
    m = repo.create(title="Call")
    src = _sine(tmp / "in.wav", 50)
    with stt_is():
        out = _run(svc, m["id"], src)
    segs = repo.segments(m["id"])
    assert [(s["channel"], s["t_start"], s["state"]) for s in segs] == [
        ("import", 0, "done"), ("import", 20, "done"), ("import", 40, "done")]
    assert out["status"] == "ready" and out["sources"] == ["import"]
    lines = out["transcript"].splitlines()
    assert len(lines) == 1 and lines[0].startswith("00:00 [them] seg 0 seg 1 seg 2"), out["transcript"]
    assert 49000 <= out["duration_ms"] <= 51500
    assert not list((meeting_recorder.recording_dir(tmp, m["id"]) / "import").glob("*.wav")), "wavs should be deleted"
    assert src.exists(), "run() leaves the source alone unless asked"


def test_keep_audio_retains_the_wavs() -> None:
    if not HAVE_FFMPEG:
        return
    repo, svc, tmp = _svc(keepAudio=True)
    m = repo.create(title="Call")
    with stt_is():
        _run(svc, m["id"], _sine(tmp / "in.wav", 30))
    assert len(list((meeting_recorder.recording_dir(tmp, m["id"]) / "import").glob("*.wav"))) == 2


def test_a_failed_segment_keeps_its_wav_and_the_rest_finish() -> None:
    if not HAVE_FFMPEG:
        return
    repo, svc, tmp = _svc()
    m = repo.create(title="Call")
    with stt_is(fail_call=1):
        out = _run(svc, m["id"], _sine(tmp / "in.wav", 50))
    by = {s["seq"]: s for s in repo.segments(m["id"])}
    assert by[0]["state"] == "done" and by[2]["state"] == "done"
    assert by[1]["state"] == "failed" and by[1]["wav_path"] and Path(by[1]["wav_path"]).exists()
    assert out["status"] == "ready" and "seg 0" in out["transcript"] and "seg 2" in out["transcript"]


def test_enhance_is_queued_when_enabled() -> None:
    if not HAVE_FFMPEG:
        return
    repo, svc, tmp = _svc(enhanceOnStop=True)
    m = repo.create(title="Call")
    with stt_is():
        _run(svc, m["id"], _sine(tmp / "in.wav", 10))
    assert repo.revisions(m["id"]), "an enhance proposal should be pending"


def test_oversize_duration_is_rejected() -> None:
    if not HAVE_FFMPEG:
        return
    repo, svc, tmp = _svc(maxImportSeconds=5)
    m = repo.create(title="Call")
    try:
        _run(svc, m["id"], _sine(tmp / "in.wav", 10))
    except ValueError as e:
        assert "limit" in str(e)
    else:
        raise AssertionError("expected ValueError")
    assert repo.segments(m["id"]) == []


def test_unreadable_file_is_a_value_error() -> None:
    if not HAVE_FFMPEG:
        return
    repo, svc, tmp = _svc()
    m = repo.create(title="Call")
    bad = tmp / "bad.wav"
    bad.write_bytes(b"nope")
    try:
        _run(svc, m["id"], bad)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_a_meeting_with_live_segments_refuses_the_import() -> None:
    repo, svc, tmp = _svc()
    m = repo.create(title="Call")
    repo.add_segment(m["id"], "mic", 0, 0, 20, time.time(), "", 0, state="done")
    try:
        meeting_import.check(svc, m["id"])
    except meeting_import.ImportRefused:
        pass
    else:
        raise AssertionError("expected ImportRefused")
    repo2, svc2, _ = _svc()
    m2 = repo2.create(title="Live")
    repo2.patch(m2["id"], {"status": "recording"})
    try:
        meeting_import.check(svc2, m2["id"])
    except meeting_import.ImportRefused:
        return
    raise AssertionError("a recording meeting must refuse")


def test_missing_consent_is_blocked() -> None:
    repo, svc, tmp = _svc(consent=False)
    m = repo.create(title="Call")
    try:
        meeting_import.check(svc, m["id"])
    except meetings.MeetingBlocked as e:
        assert e.blockers[0]["id"] == "consent"
        return
    raise AssertionError("expected MeetingBlocked")


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
