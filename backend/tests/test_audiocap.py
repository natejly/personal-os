"""Audio plumbing: the device cache, device resolution, wav validation and the segment argv.

Nothing here needs a granted microphone permission. The tests that need a real ffmpeg say so and
skip with a printed note instead of failing, because the point of audiocap is that every function
degrades on a machine that is missing a binary.

Runs under pytest, or directly: python backend/tests/test_audiocap.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import audiocap  # noqa: E402

MIC = {"index": "0", "name": "MacBook Pro Microphone"}
LOOP = {"index": "1", "name": "BlackHole 2ch"}
# The same two inputs after an interface was plugged in: avfoundation renumbered them.
MIC_MOVED = {"index": "1", "name": MIC["name"]}
LOOP_MOVED = {"index": "0", "name": LOOP["name"]}


class devices_are:
    """Pin the avfoundation device list and count how often it is actually probed.

    `_list_devices` shells out to ffmpeg, so the live list is whatever hardware happens to be
    plugged into this machine; the cache's whole job is to not ask twice, which is only
    observable by counting.
    """

    def __init__(self, *devices: dict[str, str]):
        self.devices = [dict(d) for d in devices]
        self.probes = 0

    def __enter__(self) -> devices_are:
        self.real = audiocap._list_devices
        self.cache = audiocap._dev_cache
        audiocap._dev_cache = None
        audiocap._list_devices = self._probe  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: object) -> None:
        audiocap._list_devices = self.real  # type: ignore[assignment]
        audiocap._dev_cache = self.cache

    def _probe(self) -> list[dict[str, str]]:
        self.probes += 1
        return [dict(d) for d in self.devices]


class no_binary:
    """Hide ffmpeg, ffprobe or both, the way a machine without brew install ffmpeg looks."""

    def __init__(self, ffmpeg: bool = False, ffprobe: bool = False):
        self.hide = {"ffmpeg_path": ffmpeg, "ffprobe_path": ffprobe}

    def __enter__(self) -> None:
        self.real = {k: getattr(audiocap, k) for k in self.hide}
        for name, hidden in self.hide.items():
            if hidden:
                setattr(audiocap, name, lambda: "")

    def __exit__(self, *exc: object) -> None:
        for name, fn in self.real.items():
            setattr(audiocap, name, fn)


def _wav(tmp: Path, name: str, size: int) -> Path:
    """A file that is the right size and the wrong content - exactly what a killed ffmpeg leaves."""
    p = tmp / name
    p.write_bytes(b"\0" * size)
    return p


# ---------------------------------------------------------------- devices


def test_looks_like_loopback() -> None:
    assert audiocap.looks_like_loopback("BlackHole 2ch") is True
    assert audiocap.looks_like_loopback("Loopback Audio") is True
    assert audiocap.looks_like_loopback("Existential Audio BlackHole") is True
    assert audiocap.looks_like_loopback("Aggregate Device") is True
    assert audiocap.looks_like_loopback("MacBook Pro Microphone") is False
    assert audiocap.looks_like_loopback("") is False


def test_audio_devices_caches_within_ttl() -> None:
    with devices_are(MIC, LOOP) as d:
        first = audiocap.audio_devices()
        assert [x["name"] for x in first] == [MIC["name"], LOOP["name"]]
        assert audiocap.audio_devices() is first   # same object, not just equal
        assert d.probes == 1
        assert audiocap.audio_devices(ttl=0) is not first
        assert d.probes == 2


def test_resolve_device_prefers_name_over_stale_index() -> None:
    with devices_are(LOOP_MOVED, MIC_MOVED):
        # The mic was index 0 when the user picked it; plugging in an interface moved it to 1.
        index, note = audiocap.resolve_device("0", MIC["name"], ttl=0)
        assert index == "1"
        assert "moved" in note
        index, note = audiocap.resolve_device("1", MIC["name"], ttl=0)
        assert (index, note) == ("1", "")
        # No such name, but the index is still a real input: use it and say so.
        index, note = audiocap.resolve_device("0", "Shure MV7", ttl=0)
        assert index == "0"
        assert "Shure MV7" in note
        # Neither resolves - refuse rather than record whatever happens to be on index 0.
        assert audiocap.resolve_device("9", "Shure MV7", ttl=0) == (
            "", "audio input Shure MV7 is no longer present")
    with devices_are():
        assert audiocap.resolve_device("0", MIC["name"], ttl=0) == (
            "", "no audio inputs visible")


# ---------------------------------------------------------------- wav validation


def test_validate_wav_rejects_junk() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        assert audiocap.validate_wav(tmp / "missing.wav") == (False, "empty")
        assert audiocap.validate_wav(_wav(tmp, "tiny.wav", 10)) == (False, "empty")
        # Big enough to pass any size threshold, still not audio: this is why we probe.
        ok, note = audiocap.validate_wav(_wav(tmp, "zeros.wav", 4096))
        if audiocap.ffprobe_path():
            assert ok is False, note
            assert note and len(note) <= 160
        else:
            print("  note  no ffprobe on PATH; size-only fallback asserted instead")
            assert (ok, note) == (True, "unverified")


def test_validate_wav_without_ffprobe_degrades() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        junk = _wav(tmp, "zeros.wav", 4096)
        with no_binary(ffprobe=True):
            assert audiocap.validate_wav(junk) == (True, "unverified")
            assert audiocap.validate_wav(_wav(tmp, "tiny.wav", 10)) == (False, "empty")


def test_validate_wav_repairs_a_bad_header() -> None:
    """A segment ffmpeg can still read but never finished writing sizes for.

    ffprobe happily reads a wav truncated mid-body, so this case cannot be produced by cutting a
    real file short - the probe is stubbed to fail once, which is what the repair branch exists
    for, and `repair=False` must take the same file straight to a rejection.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if not audiocap.ffmpeg_path() or not audiocap.ffprobe_path():
            print("  note  no ffmpeg/ffprobe on PATH; skipping the remux repair")
            return
        path = tmp / "seg.wav"
        assert audiocap.silence_wav(path, 0.4) is True
        real, calls = audiocap._probe_duration, []
        real_wave = audiocap._wav_is_pcm16

        def once_broken(p: Path) -> tuple[float, str]:
            calls.append(p)
            if len(calls) == 1:
                return 0.0, "Invalid data found when processing input"
            return real(p)

        audiocap._probe_duration = once_broken  # type: ignore[assignment]
        audiocap._wav_is_pcm16 = lambda p: False  # type: ignore[assignment]
        try:
            assert audiocap.validate_wav(path) == (True, "remuxed")
            assert path.stat().st_size >= audiocap.MIN_WAV_BYTES   # replaced in place
            assert not list(tmp.glob(".*.remux.wav"))              # no scratch file left behind
            calls.clear()
            assert audiocap.validate_wav(path, repair=False) == (
                False, "Invalid data found when processing input")
        finally:
            audiocap._probe_duration = real  # type: ignore[assignment]
            audiocap._wav_is_pcm16 = real_wave  # type: ignore[assignment]


def test_silence_wav_round_trip() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "nested" / "silence.wav"
        assert audiocap.silence_wav(path, 0.4) is True
        assert path.stat().st_size >= audiocap.MIN_WAV_BYTES
        assert audiocap.validate_wav(path) == (True, "")
        assert audiocap.dir_bytes(path.parent) == path.stat().st_size


def test_silence_wav_without_ffmpeg_still_writes() -> None:
    with tempfile.TemporaryDirectory() as td:
        with no_binary(ffmpeg=True):
            path = Path(td) / "silence.wav"
            assert audiocap.silence_wav(path) is True
            assert audiocap.validate_wav(path) == (True, "")


def test_dir_bytes_ignores_everything_but_wavs() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _wav(tmp, "mic-00000.wav", 100)
        _wav(tmp, "mic-00001.wav", 200)
        (tmp / "notes.md").write_bytes(b"\0" * 999)
        assert audiocap.dir_bytes(tmp) == 300
        assert audiocap.dir_bytes(tmp / "nope") == 0


# ---------------------------------------------------------------- argv


def test_segment_argv_shape() -> None:
    argv = audiocap.segment_argv("ffmpeg", audiocap.synthetic_input(), "/tmp/x-%05d.wav", 2, 7)
    assert argv[0] == "ffmpeg"
    assert argv[-1] == "/tmp/x-%05d.wav"
    assert argv[argv.index("-t") + 1] == "7"
    assert argv[argv.index("-segment_time") + 1] == "2"
    assert argv[argv.index("-ar") + 1] == "16000"
    assert argv[argv.index("-ac") + 1] == "1"
    assert argv[argv.index("-f", argv.index("-t")) + 1] == "segment"
    assert argv[argv.index("-reset_timestamps") + 1] == "1"
    # The input fragment comes before the output options, and -t caps the whole run.
    assert argv.index("-i") < argv.index("-t") < argv.index("-segment_time")
    # List argv, never a shell: a device name with a space must not need quoting.
    assert all(not set(a) & set(";|&$`<>\n") for a in argv), argv


def test_input_specs() -> None:
    assert audiocap.device_input("0") == ["-f", "avfoundation", "-i", ":0"]
    assert audiocap.synthetic_input(440) == [
        "-re", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000"]
    assert audiocap.native_mic_input("BuiltIn") == ["native", "mic", "BuiltIn"]
    assert audiocap.native_output_input() == ["native", "output"]
    assert audiocap.native_sine_input(440) == ["native", "sine", "440"]
    assert audiocap.is_native_input(audiocap.native_sine_input())
    assert not audiocap.is_native_input(audiocap.device_input("0"))


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
