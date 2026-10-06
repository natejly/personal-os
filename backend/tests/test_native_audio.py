"""Native capture: the sine generator, which needs no microphone and no binary."""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import audiocap, native_audio  # noqa: E402


def test_sine_capture_produces_16k_mono_pcm() -> None:
    cap = native_audio.Capture("sine", freq=440)
    cap.start()
    try:
        pcm = cap.read_seconds(0.4, threading.Event())
    finally:
        cap.stop()
    assert cap.error == "", cap.error
    # 0.4s at 16k s16le is 12800 bytes; allow a little underrun from scheduling.
    assert len(pcm) >= 8000, len(pcm)
    assert len(pcm) % 2 == 0


def test_native_sine_input_round_trips_through_channel_spec() -> None:
    cap = native_audio.Capture.from_spec(audiocap.native_sine_input(880))
    assert cap.kind == "sine" and cap.freq == 880
    cap.start()
    halt = threading.Event()
    t0 = time.time()
    pcm = cap.read_seconds(0.25, halt)
    cap.stop()
    assert time.time() - t0 < 2.0
    assert len(pcm) >= 4000
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "sine.wav"
        assert audiocap.write_pcm16_wav(path, pcm)
        assert audiocap.validate_wav(path) == (True, "")


def test_can_capture_sine_everywhere() -> None:
    assert native_audio.can_capture("sine") is True


def test_a_missing_mic_uid_is_an_error_not_the_default_input() -> None:
    class _Node:
        pass

    class _Engine:
        def alloc(self): return self
        def init(self): return self
        def inputNode(self): return _Node()
        def prepare(self): pass

    real = (native_audio._av_engine_cls, native_audio._device_id_for_uid)
    native_audio._av_engine_cls = lambda: _Engine()  # type: ignore[assignment]
    native_audio._device_id_for_uid = lambda uid: 0  # type: ignore[assignment]
    try:
        native_audio.Capture("mic", uid="UnpluggedUSB_UID")._start_engine("UnpluggedUSB_UID")
        raise AssertionError("an unplugged mic fell back to the default input")
    except RuntimeError as e:
        assert "not present" in str(e), e
    finally:
        native_audio._av_engine_cls, native_audio._device_id_for_uid = real


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

