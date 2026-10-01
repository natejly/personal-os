"""The silence gate and the hallucination filter, offline and stdlib-only.

Runs under pytest, or directly: python backend/tests/test_meeting_vad.py
"""
from __future__ import annotations

import json
import math
import random
import struct
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import meeting_vad, stt  # noqa: E402

RATE = 16000


def _write(path: Path, samples: list[int], width: int = 2) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(RATE)
        if width == 2:
            w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
        else:
            w.writeframes(bytes((s >> 8) + 128 & 255 for s in samples))
    return path


def _dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="vad-"))


def _noise(n: int, amp: int, seed: int = 1) -> list[int]:
    rnd = random.Random(seed)
    return [rnd.randint(-amp, amp) for _ in range(n)]


def _tone(n: int, amp: int = 8000, start: int = 0) -> list[int]:
    return [int(amp * math.sin(2 * math.pi * 440 * (start + i) / RATE)) for i in range(n)]


def test_silence_has_no_speech() -> None:
    v = meeting_vad.analyze(_write(_dir() / "s.wav", [0] * RATE * 10))
    assert v["ok"] and v["speech_ratio"] < 0.03 and v["spans"] == []


def test_a_tone_burst_in_near_silence_is_one_span() -> None:
    quiet = _noise(RATE * 3, 4)
    samples = quiet + _tone(RATE * 3) + _noise(RATE * 4, 4, seed=2)
    v = meeting_vad.analyze(_write(_dir() / "t.wav", samples))
    assert v["ok"]
    assert abs(v["speech_ratio"] - 0.3) < 0.05, v["speech_ratio"]
    assert len(v["spans"]) == 1
    a, b = v["spans"][0]
    assert abs(a - 3) < 0.1 and abs(b - 6) < 0.1, v["spans"]


def test_steady_low_noise_is_not_speech() -> None:
    v = meeting_vad.analyze(_write(_dir() / "n.wav", _noise(RATE * 10, 30)))
    assert v["ok"] and v["speech_ratio"] < 0.03, v


def test_unreadable_audio_is_unknown_not_silent() -> None:
    d = _dir()
    assert meeting_vad.analyze(_write(d / "8.wav", [0, 1000] * 4000, width=1))["ok"] is False
    bad = d / "bad.wav"
    bad.write_bytes(b"not a wav at all")
    assert meeting_vad.analyze(bad)["ok"] is False
    assert meeting_vad.analyze(d / "missing.wav")["ok"] is False


def _seg(text: str, **kw: float) -> dict:
    return {"text": text, **kw}


def test_filter_drops_the_joint_no_speech_rule() -> None:
    detail = {"segments": [_seg(" ghost", no_speech_prob=0.9, avg_logprob=-1.5),
                           _seg(" real words", no_speech_prob=0.9, avg_logprob=-0.2)]}
    text, out, dropped = stt.filter_hallucinations("ghost real words", detail, 0.8)
    assert text == "real words" and dropped == ["ghost"] and out["filtered"] == ["ghost"]
    assert len(out["segments"]) == 1 and len(detail["segments"]) == 2, "input must not be mutated"


def test_filter_drops_high_compression_ratio() -> None:
    detail = {"segments": [_seg("la la la", compression_ratio=3.0), _seg("ok", compression_ratio=1.2)]}
    text, _out, dropped = stt.filter_hallucinations("la la la ok", detail, None)
    assert text == "ok" and dropped == ["la la la"]


def test_thank_you_only_goes_when_the_segment_was_quiet() -> None:
    detail = {"segments": [_seg("Thank you.")]}
    assert stt.filter_hallucinations("Thank you.", detail, 0.05)[0] == ""
    assert stt.filter_hallucinations("Thank you.", detail, 0.6)[0] == "Thank you."
    assert stt.filter_hallucinations("Thank you.", detail, None)[0] == "Thank you."
    # plain-text reply, no segments
    assert stt.filter_hallucinations("Thanks for watching!", {}, 0.05) == ("", {"filtered": ["Thanks for watching!"]}, ["Thanks for watching!"])
    assert stt.filter_hallucinations("Thanks for watching!", {}, 0.5)[0] == "Thanks for watching!"


def test_repetition_loops_collapse() -> None:
    assert stt.filter_hallucinations("a a a a a a", {}, None)[0] == "a"
    assert stt.filter_hallucinations("go go go", {}, None)[0] == "go go go"
    assert stt.filter_hallucinations("see you see you see you see you", {}, None)[0] == "see you"


def test_empty_detail_and_empty_text() -> None:
    assert stt.filter_hallucinations("", None, 0.0) == ("", {}, [])
    assert stt.filter_hallucinations("fine", {"segments": []}, 0.0)[0] == "fine"


def test_local_whisper_returns_segment_offsets_and_cleans_up() -> None:
    d = _dir()
    wav = _write(d / "seg.wav", [0] * 1600)
    (d / "models").mkdir()
    (d / "models" / "ggml-base.en.bin").write_bytes(b"x")
    seen: dict = {}

    class _R:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(argv, **kw):
        seen["argv"] = argv
        base = Path(argv[argv.index("-of") + 1])
        Path(str(base) + ".json").write_text(json.dumps({"transcription": [
            {"offsets": {"from": 0, "to": 1500}, "text": " hello"},
            {"offsets": {"from": 1500, "to": 3000}, "text": " world"}]}))
        Path(str(base) + ".txt").write_text("hello world")
        return _R()

    real_run, real_cli = stt.subprocess.run, stt.whisper_cli_path
    stt.subprocess.run, stt.whisper_cli_path = fake_run, lambda: "/bin/whisper-cli"
    try:
        text, detail, err = stt._local(wav, d, {})
        assert err == "" and text == "hello world"
        assert detail["segments"][1] == {"start": 1.5, "end": 3.0, "text": "world"}
        assert "--vad" not in seen["argv"]
        assert not list(d.glob("seg.wav.*")), "side-effect files were left behind"
        (d / "models" / "ggml-silero-v6.2.0.bin").write_bytes(b"v")
        stt._local(wav, d, {})
        assert "--vad" in seen["argv"] and seen["argv"][seen["argv"].index("-vm") + 1].endswith("ggml-silero-v6.2.0.bin")
    finally:
        stt.subprocess.run, stt.whisper_cli_path = real_run, real_cli


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
