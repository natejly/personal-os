"""Live preview of dictation: volatile and final text while someone is still speaking.

The durable transcript is still the clips the recorder cuts and transcribes; this only adds a
short-lived channel next to it, `{session, kind: 'volatile'|'final', text, t0, t1}`. Nothing here
writes to a document or a row, and nothing runs unless a recording exists (the engine lives and
dies with its session) and the `livePreview` meetings setting is on.

An engine is anything with `feed(buffer)`, `start()` and `stop()` that reports through `emit`; the
first one is the on-device Speech framework's streaming request. Its pyobjc imports are lazy so this
module imports (and is tested) anywhere.
"""
from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Any, Callable

log = logging.getLogger("personal_os.stt_stream")

# A streaming request is capped at about a minute, so it is rolled early: once it is this old and
# the partials have stopped changing (a pause), and unconditionally at the hard limit.
ROLL_SECONDS = 50.0
HARD_ROLL_SECONDS = 57.0
QUIET_SECONDS = 0.8


class PreviewEngine:
    """Base: holds the session id and turns `emit` into a published event. Subclasses fill in audio."""

    def __init__(self, session: str, publish: Callable[[dict[str, Any]], None]):
        self.session = session
        self._publish = publish
        self.muted = False  # paused: the recorder keeps the tap running but discards the audio
        self.started = False

    def emit(self, kind: str, text: str, t0: float, t1: float) -> None:
        text = (text or "").strip()
        if kind == "volatile" and not text:
            return
        try:
            self._publish({"session": self.session, "kind": kind, "text": text,
                           "t0": round(t0, 2), "t1": round(t1, 2)})
        except Exception as e:  # noqa: BLE001 - a dead listener must never touch the recording
            log.warning("preview: event not published: %s", e)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def feed(self, buffer: Any) -> None:  # called from the audio tap thread
        raise NotImplementedError


class SpeechStream(PreviewEngine):
    """On-device streaming recognition fed from the capture's AVAudioEngine tap."""

    def __init__(self, session: str, publish: Callable[[dict[str, Any]], None], *,
                 clock: Callable[[], float] = time.monotonic):
        super().__init__(session, publish)
        self._clock = clock
        self._lock = threading.Lock()
        self._t_start = 0.0
        self._req: Any = None
        self._req_t0 = 0.0
        self._last_change = 0.0
        self._last_text = ""
        self._recognizer: Any = None

    def start(self) -> None:
        self._t_start = self._clock()
        super().start()

    def stop(self) -> None:
        super().stop()
        with self._lock:
            self._close()

    def feed(self, buffer: Any) -> None:
        if not self.started or self.muted:
            return
        try:
            with self._lock:
                if self._req is None:
                    self._open()
                self._append(buffer)
                now = self._clock()
                age = now - self._req_t0
                quiet = now - self._last_change
                if age >= HARD_ROLL_SECONDS or (age >= ROLL_SECONDS and quiet >= QUIET_SECONDS):
                    self._close()  # its final arrives through the handler; the next buffer opens a new one
        except Exception as e:  # noqa: BLE001 - realtime callback
            log.warning("preview: stopping after %s: %s", type(e).__name__, e)
            self.started = False

    # ---- result handling (testable without the framework) ----

    def _on_result(self, t0: float, text: str, final: bool) -> None:
        now = self._clock() - self._t_start
        if final:
            self.emit("final", text, t0, now)
            return
        if text != self._last_text:
            self._last_text = text
            self._last_change = self._clock()
            self.emit("volatile", text, t0, now)

    # ---- framework seam ----

    def _open(self) -> None:
        from Foundation import NSLocale, NSOperationQueue  # type: ignore[import-not-found]
        from Speech import SFSpeechAudioBufferRecognitionRequest, SFSpeechRecognizer  # type: ignore[import-not-found]
        if self._recognizer is None:
            rec = SFSpeechRecognizer.alloc().initWithLocale_(NSLocale.currentLocale())
            if rec is None or not rec.supportsOnDeviceRecognition():
                raise RuntimeError("no on-device speech model for this locale")
            # Results on a private queue: nothing in the backend pumps the main run loop.
            rec.setQueue_(NSOperationQueue.alloc().init())
            self._recognizer = rec
        req = SFSpeechAudioBufferRecognitionRequest.alloc().init()
        req.setRequiresOnDeviceRecognition_(True)
        req.setShouldReportPartialResults_(True)
        t0 = self._clock() - self._t_start

        def handler(result: Any, error: Any) -> None:
            if result is not None:
                with contextlib.suppress(Exception):
                    self._on_result(t0, str(result.bestTranscription().formattedString() or ""),
                                    bool(result.isFinal()))

        self._recognizer.recognitionTaskWithRequest_resultHandler_(req, handler)
        self._req, self._req_t0, self._last_text = req, self._clock(), ""
        self._last_change = self._clock()

    def _append(self, buffer: Any) -> None:
        self._req.appendAudioPCMBuffer_(buffer)

    def _close(self) -> None:
        req, self._req = self._req, None
        if req is not None:
            with contextlib.suppress(Exception):
                req.endAudio()


def make_engine(session: str, cfg: dict[str, Any], publish: Callable[[dict[str, Any]], None] | None, *,
                factory: Callable[..., PreviewEngine] = SpeechStream,
                ready: Callable[[], bool] | None = None) -> PreviewEngine | None:
    """The engine for one recording, or None. Off unless `livePreview` is set, a listener exists and
    Speech Recognition is already granted (this never prompts)."""
    if not cfg.get("livePreview") or publish is None:
        return None
    if ready is None:
        from . import stt
        ready = stt.speech_ready
    if not ready():
        return None
    return factory(session, publish)
