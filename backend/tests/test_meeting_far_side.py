"""Far side captured by default, diarize gated on keepAudio, mic-only diarize, Speech-shaped no-op.

Offline: stubbed capture probes, stub pool, fake diarizer, no mic and no network.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import audiocap, meetings, stt  # noqa: E402
from personal_os.app import MeetingConfigIn  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.docs import Docs  # noqa: E402

SETTINGS = {"baseUrl": "http://localhost:4000", "apiKey": "", "defaultModel": "m", "extractionModel": ""}


def _svc():
    tmp = Path(tempfile.mkdtemp(prefix="far-"))

    async def complete(settings, model, messages, kind="learn"):
        return ""

    db = Database(tmp)
    repo = meetings.Meetings(db)
    svc = meetings.MeetingService(db, lambda: dict(SETTINGS), complete, repo, docs=Docs(db))
    svc.preflight = lambda force=False: {"ok": True, "blockers": []}  # type: ignore[assignment]
    return repo, svc, tmp


class _Pool:
    def __init__(self, tmp):
        self.tmp, self.channels = tmp, {}

    def start(self, meeting_id, channels, **kw):
        self.channels = channels
        return SimpleNamespace(out_dir=self.tmp / "rec", started_at=time.time(), segment_seconds=20,
                               stats=lambda: {}, errors=lambda: {})

    def get(self, meeting_id):
        return None


def _start(system: bool, doc_mode=None):
    repo, svc, tmp = _svc()
    svc.pool = _Pool(tmp)  # type: ignore[assignment]
    real = (meetings.native_audio.mic_available, meetings.native_audio.system_available,
            audiocap.native_mic_input, audiocap.native_output_input, audiocap.audio_devices)
    meetings.native_audio.mic_available = lambda: True  # type: ignore[assignment]
    meetings.native_audio.system_available = lambda: system  # type: ignore[assignment]
    audiocap.native_mic_input = lambda uid="": ["mic"]  # type: ignore[assignment]
    audiocap.native_output_input = lambda: ["out"]  # type: ignore[assignment]
    audiocap.audio_devices = lambda ttl=0: []  # type: ignore[assignment]
    try:
        kw = {}
        if doc_mode:
            kw = {"doc_id": svc.docs.create("D", "# D")["id"], "doc_mode": doc_mode}
        m = repo.create(title="t", status="scheduled", **kw)
        out = svc.start(m["id"])
        return set(svc.pool.channels), out  # type: ignore[attr-defined]
    finally:
        (meetings.native_audio.mic_available, meetings.native_audio.system_available,
         audiocap.native_mic_input, audiocap.native_output_input, audiocap.audio_devices) = real  # type: ignore[assignment]


def test_defaults_and_consent_stay_closed() -> None:
    cfg = meetings.DEFAULT_CONFIG
    assert cfg["diarize"] is False and cfg["autoRecord"] is False
    assert "consentedAt" not in MeetingConfigIn.model_fields


def test_start_adds_far_side_when_available_and_survives_without() -> None:
    chans, _ = _start(True)
    assert chans == {"mic", "output"}
    chans, out = _start(False)
    assert chans == {"mic"} and "output not captured" in out["error"]


def test_dictate_is_mic_only_either_way() -> None:
    assert _start(True, "dictate")[0] == {"mic"}
    assert _start(False, "dictate")[0] == {"mic"}


class _Fake:
    def diarize(self, wav, cfg):
        return [(0, 5, "a"), (5, 10, "b")]


TIMED = {"segments": [{"start": 0, "end": 4, "text": "hello there."}, {"start": 6, "end": 9, "text": "okay"}]}


def _seed(channels: dict):
    repo, svc, tmp = _svc()
    mid = repo.create(title="c")["id"]
    for ch, detail in channels.items():
        wav = tmp / f"{ch}.wav"
        wav.write_bytes(b"x")
        repo.add_segment(mid, ch, 0, 0, 10, time.time(), str(wav), 1, duration_ms=10000, state="recorded")
        repo.finish_segment(repo.segment(mid, ch, 0)["id"], text="hello there. okay", detail=detail,
                            backend="local", state="done", wav_path=str(wav), wav_bytes=1)
    return repo, svc, mid


def _run(svc, mid):
    def concat(w, out):
        out.write_bytes(b"x")
        return True
    res = svc.diarize_segments(mid, _Fake(), concat=concat)
    if res["ok"]:
        svc._settle_transcript(mid)
    return res


def test_far_side_gets_speakers_and_mic_stays_you() -> None:
    repo, svc, mid = _seed({"mic": TIMED, "output": TIMED})
    notes = repo.get(mid)["notes"]
    assert _run(svc, mid)["ok"]
    t = repo.get(mid)["transcript"]
    assert "[S1]" in t and "[S2]" in t and "[you]" in t
    assert repo.segment(mid, "mic", 0)["speaker"] == ""
    assert repo.get(mid)["notes"] == notes


def test_mic_alone_is_diarized() -> None:
    repo, svc, mid = _seed({"mic": TIMED})
    assert _run(svc, mid)["ok"]
    assert "[S1]" in repo.get(mid)["transcript"]


def test_off_means_channel_labels_only() -> None:
    repo, svc, mid = _seed({"mic": TIMED, "output": TIMED})
    svc._settle_transcript(mid)
    t = repo.get(mid)["transcript"]
    assert "[you]" in t and "[them]" in t and "[S1]" not in t


def test_speech_shaped_empty_detail_gains_no_speakers() -> None:
    repo, svc, mid = _seed({"output": {}})
    _run(svc, mid)
    assert repo.segment(mid, "output", 0)["speaker"] == ""
    svc._settle_transcript(mid)
    assert "[S1]" not in repo.get(mid)["transcript"]


def test_diarize_prefers_local_whisper_over_speech() -> None:
    real = (stt.speech_ready, stt.whisper_cli_path, stt.local_model_path)
    stt.speech_ready = lambda: True  # type: ignore[assignment]
    stt.whisper_cli_path = lambda: "/x/whisper"  # type: ignore[assignment]
    stt.local_model_path = lambda d, c: "/x/m.bin"  # type: ignore[assignment]
    try:
        assert stt.resolve_backend({"sttBackend": "auto"}, Path("/tmp")) == "speech"
        assert stt.resolve_backend({"sttBackend": "auto", "diarize": True}, Path("/tmp")) == "local"
    finally:
        stt.speech_ready, stt.whisper_cli_path, stt.local_model_path = real  # type: ignore[assignment]
