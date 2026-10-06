"""Live dictation preview: gating, event order, request rolling, stop. No framework, no microphone."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import stt_stream  # noqa: E402
from personal_os.stt_stream import SpeechStream, make_engine  # noqa: E402


class Fake(SpeechStream):
    """The framework seam replaced: opening a request just counts, closing records it."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.opened = 0
        self.closed = 0
        self.buffers: list = []

    def _open(self) -> None:
        self.opened += 1
        self._req = object()
        self._req_t0 = self._clock()
        self._last_change = self._clock()
        self._last_text = ""

    def _append(self, buffer) -> None:
        self.buffers.append(buffer)

    def _close(self) -> None:
        if self._req is not None:
            self.closed += 1
        self._req = None


def _engine(now: list[float]) -> tuple[Fake, list[dict]]:
    out: list[dict] = []
    e = Fake("m1", out.append, clock=lambda: now[0])
    e.start()
    return e, out


def test_off_means_no_stream_object() -> None:
    made: list = []
    factory = lambda *a: made.append(a)  # noqa: E731
    assert make_engine("m1", {"livePreview": False}, print, factory=factory, ready=lambda: True) is None
    assert make_engine("m1", {}, print, factory=factory, ready=lambda: True) is None
    assert made == []


def test_needs_a_listener_and_a_speech_grant() -> None:
    assert make_engine("m1", {"livePreview": True}, None, ready=lambda: True) is None
    assert make_engine("m1", {"livePreview": True}, print, ready=lambda: False) is None
    assert isinstance(make_engine("m1", {"livePreview": True}, print, ready=lambda: True), SpeechStream)


def test_volatile_volatile_final_in_order() -> None:
    now = [100.0]
    e, out = _engine(now)
    now[0] = 101.0
    e._on_result(0.0, "hel", False)
    now[0] = 102.0
    e._on_result(0.0, "hello wor", False)
    e._on_result(0.0, "hello wor", False)  # unchanged partial is not re-sent
    now[0] = 103.0
    e._on_result(0.0, "hello world", True)
    assert [(x["kind"], x["text"]) for x in out] == [("volatile", "hel"), ("volatile", "hello wor"), ("final", "hello world")]
    assert all(x["session"] == "m1" for x in out)
    assert out[-1]["t1"] == 3.0


def test_request_rolls_at_a_pause_after_fifty_seconds_and_at_the_hard_limit() -> None:
    now = [0.0]
    e, _ = _engine(now)
    e.feed("b")
    assert e.opened == 1
    now[0] = 51.0
    e._on_result(0.0, "still talking", False)  # a change just now: not quiet
    e.feed("b")
    assert e.closed == 0
    now[0] = 52.0  # quiet for over 0.8s
    e.feed("b")
    assert e.closed == 1
    e.feed("b")
    assert e.opened == 2
    now[0] = 52.0 + stt_stream.HARD_ROLL_SECONDS
    e._on_result(0.0, "nonstop", False)
    e.feed("b")
    assert e.closed == 2


def test_stop_closes_and_ignores_later_audio_and_pause_mutes() -> None:
    now = [0.0]
    e, _ = _engine(now)
    e.muted = True
    e.feed("b")
    assert e.opened == 0
    e.muted = False
    e.feed("b")
    e.stop()
    assert e.closed == 1
    e.feed("b")
    assert e.opened == 1


def test_a_failing_open_stops_the_stream_without_raising() -> None:
    e = SpeechStream("m1", lambda _x: None)
    e.start()
    e._open = lambda: (_ for _ in ()).throw(RuntimeError("no model"))  # type: ignore[method-assign]
    e.feed("b")
    assert e.started is False


def test_importing_needs_no_pyobjc() -> None:
    assert "Speech" not in sys.modules or sys.platform == "darwin"
