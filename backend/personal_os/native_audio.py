"""macOS capture without ffmpeg: microphone, system output, and a test tone.

Meetings and the activity monitor used to shell out to ffmpeg's avfoundation device
and, for the far side of a call, a BlackHole loopback driver. Both are still the
fallback when this module cannot start. The native path is:

  mic      AVAudioEngine's input tap (Microphone permission)
  output   Core Audio process tap, macOS 14.2+ (no loopback device)
  sine     generated here, so tests do not need a binary or a grant

Nothing here raises into a capture thread except `Capture.start`, which the
recorder turns into a channel error. Listing devices and probing availability
never prompt.
"""
from __future__ import annotations

import array
import ctypes
import logging
import math
import sys
import threading
import time
from typing import Any

log = logging.getLogger("personal_os.native_audio")

IS_MAC = sys.platform == "darwin"

RATE = 16000
BYTES_PER_SAMPLE = 2
BYTES_PER_SECOND = RATE * BYTES_PER_SAMPLE
# Cap the unread ring so a stuck reader cannot grow without bound (~30s of 16k mono).
SINK_CAP_BYTES = BYTES_PER_SECOND * 30

# AudioUnit property used to point AVAudioEngine's input at a specific device (the
# aggregate that hosts a process tap, or a named microphone). 2000 is
# kAudioOutputUnitProperty_CurrentDevice; scope 0 is global.
_AU_CURRENT_DEVICE = 2000
_AU_SCOPE_GLOBAL = 0


def mic_available() -> bool:
    """Can we start an AVAudioEngine input tap on this Mac?"""
    return bool(_av_engine_cls())


def system_available() -> bool:
    """Does this Mac expose CATapDescription? Creating a tap still happens at start()."""
    return bool(_tap_description_cls())


def can_capture(kind: str) -> bool:
    if kind == "sine":
        return True
    if kind == "output":
        return system_available()
    return mic_available()


def list_inputs() -> list[dict[str, str]]:
    """Microphones AVFoundation can see, as [{index, name}] with the uniqueID as index.

    Unique IDs survive unplug/replug, which ffmpeg's avfoundation index does not.
    Empty on a non-Mac, or when the framework is missing. Does not ask for permission.
    """
    if not IS_MAC:
        return []
    try:
        import AVFoundation  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return []
    devices: list[Any] = []
    with _swallow():
        # macOS 14+: AVCaptureDeviceTypeMicrophone. Older: devicesWithMediaType_.
        session_cls = getattr(AVFoundation, "AVCaptureDeviceDiscoverySession", None)
        mic_type = getattr(AVFoundation, "AVCaptureDeviceTypeMicrophone", None) or getattr(
            AVFoundation, "AVCaptureDeviceTypeBuiltInMicrophone", None)
        media = getattr(AVFoundation, "AVMediaTypeAudio", "soun")
        if session_cls is not None and mic_type is not None:
            types = [mic_type]
            extra = getattr(AVFoundation, "AVCaptureDeviceTypeExternalUnknown", None)
            if extra is not None:
                types.append(extra)
            session = session_cls.discoverySessionWithDeviceTypes_mediaType_position_(
                types, media, 0)
            devices = list(session.devices() or [])
    if not devices:
        with _swallow():
            devices = list(AVFoundation.AVCaptureDevice.devicesWithMediaType_("soun") or [])
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for dev in devices:
        try:
            uid = str(dev.uniqueID() or "").strip()
            name = str(dev.localizedName() or uid or "Microphone").strip()
        except Exception:  # noqa: BLE001
            continue
        if not uid or uid in seen:
            continue
        seen.add(uid)
        out.append({"index": uid, "name": name})
    return out


def record_seconds(kind: str, seconds: float, uid: str = "", freq: int = 440) -> bytes:
    """Blocking helper for AudioCollector: start, read one chunk, stop. 16k mono s16le."""
    cap = Capture(kind, uid=uid, freq=freq)
    cap.start()
    try:
        halt = threading.Event()
        return cap.read_seconds(seconds, halt)
    finally:
        cap.stop()


class Capture:
    """Long-lived PCM source. ChannelCapture holds one for the whole meeting.

    `kind` is mic | output | sine. `start` raises; `read_seconds` does not.
    """

    def __init__(self, kind: str, uid: str = "", freq: int = 440):
        self.kind = kind if kind in ("mic", "output", "sine") else "mic"
        self.uid = uid or ""
        self.freq = int(freq) or 440
        self.error = ""
        self._stop = threading.Event()
        self._ready = threading.Event()
        self.sink = _PcmSink()
        # Called with each raw AVAudioPCMBuffer from the tap (live preview). Read per buffer, so one can join mid-capture.
        self.tap_hooks: list[Any] = []
        self._thread: threading.Thread | None = None
        self._engine: Any = None
        self._tap_block: Any = None
        self._tap_id = 0
        self._agg_id = 0
        self._tap_desc: Any = None
        self._input_node: Any = None

    @classmethod
    def from_spec(cls, spec: list[str]) -> Capture:
        """`['native', kind, ...]` as produced by audiocap.native_*_input."""
        kind = spec[1] if len(spec) > 1 else "mic"
        if kind == "sine":
            freq = 440
            with _swallow():
                freq = int(spec[2]) if len(spec) > 2 else 440
            return cls("sine", freq=freq)
        if kind == "output":
            return cls("output")
        uid = spec[2] if len(spec) > 2 else ""
        return cls("mic", uid=uid)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._ready.clear()
        self.error = ""
        self._thread = threading.Thread(target=self._io, name=f"native-{self.kind}", daemon=True)
        self._thread.start()
        if not self._ready.wait(12):
            self.stop()
            raise RuntimeError(self.error or "native capture did not start")
        if self.error:
            err = self.error
            self.stop()
            raise RuntimeError(err)

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=4)
        self._thread = None
        self._teardown_engine()
        self._teardown_tap()

    def read_seconds(self, seconds: float, halt: threading.Event) -> bytes:
        """Block until `seconds` of audio, halt, or a short overrun timeout."""
        want = max(0, int(float(seconds) * BYTES_PER_SECOND))
        if want == 0:
            return b""
        got = bytearray()
        deadline = time.time() + max(0.2, float(seconds) + 1.0)
        while len(got) < want:
            if halt.is_set() or self._stop.is_set():
                break
            chunk = self.sink.take(want - len(got))
            if chunk:
                got.extend(chunk)
                continue
            # A tap-callback failure lands on the sink, not on `self.error`; surface it so the
            # caller's error check (and restart path) sees a dead tap instead of silent reads.
            if self.sink.error and not self.error:
                self.error = self.sink.error
            if self.error:
                break
            if time.time() >= deadline:
                break
            halt.wait(0.05)
        leftover = self.sink.take(want - len(got))
        if leftover:
            got.extend(leftover)
        return bytes(got)

    def drain(self) -> bytes:
        return self.sink.take(self.sink.available())

    # ------------------------------------------------------------ io thread

    def _io(self) -> None:
        try:
            if self.kind == "sine":
                self._run_sine()
            elif self.kind == "output":
                self._run_system()
            else:
                self._run_mic()
        except Exception as e:  # noqa: BLE001 - the reader sees this on the error string
            self.error = f"{type(e).__name__}: {e}"[:200]
            log.warning("native_audio: %s failed: %s", self.kind, self.error)
            self._ready.set()
        finally:
            self._teardown_engine()
            self._teardown_tap()

    def _run_sine(self) -> None:
        self._ready.set()
        phase = 0.0
        # 100 ms of samples at a time, paced to wall clock so a 1s segment takes ~1s.
        n = RATE // 10
        omega = 2.0 * math.pi * self.freq / RATE
        while not self._stop.is_set():
            t0 = time.time()
            buf = array.array("h")
            for _ in range(n):
                buf.append(int(max(-1.0, min(1.0, math.sin(phase))) * 32767))
                phase += omega
                if phase > 2.0 * math.pi:
                    phase -= 2.0 * math.pi
            self.sink.push(buf.tobytes())
            delay = (n / RATE) - (time.time() - t0)
            if delay > 0:
                self._stop.wait(delay)

    def _run_mic(self) -> None:
        self._start_engine(self.uid)
        self._ready.set()
        self._pump_runloop()

    def _run_system(self) -> None:
        if not system_available():
            raise RuntimeError("this macOS has no Core Audio process tap (needs 14.2+)")
        device_id = self._create_system_tap()
        self._start_engine("", device_id=device_id)
        self._ready.set()
        self._pump_runloop()

    def _pump_runloop(self) -> None:
        NSRunLoop, NSDate, mode = _runloop()
        while not self._stop.is_set():
            if NSRunLoop is None:
                self._stop.wait(0.05)
                continue
            NSRunLoop.currentRunLoop().runMode_beforeDate_(
                mode, NSDate.dateWithTimeIntervalSinceNow_(0.1))

    # ------------------------------------------------------------ AVAudioEngine

    def _start_engine(self, uid: str, device_id: int = 0) -> None:
        cls = _av_engine_cls()
        if cls is None:
            raise RuntimeError("AVAudioEngine is unavailable (install pyobjc-framework-AVFoundation)")
        engine = cls.alloc().init()
        node = engine.inputNode()
        engine.prepare()
        if device_id or uid:
            resolved = device_id or _device_id_for_uid(uid)
            if not resolved or not _set_input_device(node, resolved):
                if device_id:
                    raise RuntimeError("could not attach the system-audio tap to AVAudioEngine")
                # A chosen mic that is gone (unplugged, stale uid) is an error, never the default
                # input: that would record a different room under the chosen device's name.
                raise RuntimeError(f"input {uid} is not present")
        hw = node.outputFormatForBus_(0)
        rate = float(hw.sampleRate() or 0.0)
        channels = int(hw.channelCount() or 0)
        if rate <= 0 or channels <= 0:
            raise RuntimeError("no input format; grant Microphone permission to the app and retry")
        sink = self.sink

        def tap(buffer: Any, _when: Any) -> None:
            try:
                pcm = _buffer_to_s16_mono(buffer)
                if pcm:
                    sink.push(pcm)
            except Exception as e:  # noqa: BLE001 - realtime callback
                sink.error = f"{type(e).__name__}: {e}"[:160]
            for hook in list(self.tap_hooks):
                try:
                    hook(buffer)
                except Exception:  # noqa: BLE001 - a preview consumer must not break capture
                    pass

        self._tap_block = tap
        node.installTapOnBus_bufferSize_format_block_(0, 4096, hw, tap)
        ok, err = engine.startAndReturnError_(None)
        if not ok:
            raise RuntimeError(str(err) if err else "AVAudioEngine failed to start")
        self._engine = engine
        self._input_node = node

    def _teardown_engine(self) -> None:
        engine, node = self._engine, self._input_node
        self._engine = None
        self._input_node = None
        self._tap_block = None
        if node is not None:
            with _swallow():
                node.removeTapOnBus_(0)
        if engine is not None:
            with _swallow():
                engine.stop()

    # ------------------------------------------------------------ Core Audio tap

    def _create_system_tap(self) -> int:
        """Process tap + private aggregate. Returns the aggregate AudioObjectID."""
        desc_cls = _tap_description_cls()
        if desc_cls is None:
            raise RuntimeError("CATapDescription is missing")
        ca = _coreaudio()
        # Exclude our own PID so the tap does not hear Grain's own playback (none today,
        # but a later TTS would otherwise loop).
        try:
            desc = desc_cls.alloc().initStereoGlobalTapButExcludeProcesses_([])
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"could not describe a system tap: {e}") from e
        self._tap_desc = desc
        tap_id = ctypes.c_uint32(0)
        status = int(ca.AudioHardwareCreateProcessTap(objc_id(desc), ctypes.byref(tap_id)))
        if status != 0 or tap_id.value == 0:
            raise RuntimeError(f"AudioHardwareCreateProcessTap failed ({status}); "
                               "grant System Audio Recording in Privacy & Security")
        self._tap_id = int(tap_id.value)
        tap_uid = ""
        with _swallow():
            tap_uid = str(desc.UUID().UUIDString())
        if not tap_uid:
            raise RuntimeError("process tap has no UUID")
        agg = _create_aggregate(ca, tap_uid)
        self._agg_id = agg
        return agg

    def _teardown_tap(self) -> None:
        ca = None
        with _swallow():
            ca = _coreaudio()
        agg, tap = self._agg_id, self._tap_id
        self._agg_id = 0
        self._tap_id = 0
        self._tap_desc = None
        if ca is None:
            return
        if agg:
            with _swallow():
                ca.AudioHardwareDestroyAggregateDevice(ctypes.c_uint32(agg))
        if tap:
            with _swallow():
                ca.AudioHardwareDestroyProcessTap(ctypes.c_uint32(tap))


# ---------------------------------------------------------------- pcm


class _PcmSink:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buf = bytearray()
        self.error = ""

    def push(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            self._buf.extend(data)
            extra = len(self._buf) - SINK_CAP_BYTES
            if extra > 0:
                del self._buf[:extra]

    def take(self, n: int) -> bytes:
        if n <= 0:
            return b""
        with self._lock:
            out = bytes(self._buf[:n])
            del self._buf[:n]
            return out

    def available(self) -> int:
        with self._lock:
            return len(self._buf)


def _buffer_to_s16_mono(buffer: Any) -> bytes:
    """One AVAudioPCMBuffer → 16 kHz mono s16le. Empty on a silent or unreadable buffer."""
    nframes = int(buffer.frameLength() or 0)
    if nframes <= 0:
        return b""
    fmt = buffer.format()
    nch = max(1, int(fmt.channelCount() or 1))
    src_rate = float(fmt.sampleRate() or RATE)
    interleaved = True
    with _swallow():
        interleaved = bool(fmt.isInterleaved())
    common = 1
    with _swallow():
        common = int(fmt.commonFormat())
    # 1 = float32, 3 = int16 (AVAudioCommonFormat).
    samples: list[float]
    if common == 3:
        samples = _int16_channels(buffer, nframes, nch, interleaved)
    else:
        samples = _float_channels(buffer, nframes, nch, interleaved)
    if not samples:
        return b""
    if abs(src_rate - RATE) > 0.5:
        samples = _resample(samples, src_rate, float(RATE))
    out = array.array("h")
    for s in samples:
        if s > 1.0:
            s = 1.0
        elif s < -1.0:
            s = -1.0
        out.append(int(s * 32767.0))
    return out.tobytes()


def _float_channels(buffer: Any, nframes: int, nch: int, interleaved: bool) -> list[float]:
    ptrs = buffer.floatChannelData()
    if interleaved:
        raw = _ptr_bytes(ptrs[0] if ptrs is not None else None, nframes * nch * 4)
        floats = array.array("f")
        floats.frombytes(raw[: len(raw) - (len(raw) % 4)])
        if nch == 1:
            return list(floats)
        return [_mix(floats, i, nch) for i in range(nframes) if (i + 1) * nch <= len(floats)]
    chans: list[array.array] = []
    for c in range(nch):
        raw = _ptr_bytes(ptrs[c] if ptrs is not None else None, nframes * 4)
        a = array.array("f")
        a.frombytes(raw[: len(raw) - (len(raw) % 4)])
        chans.append(a)
    if not chans:
        return []
    n = min(nframes, min(len(ch) for ch in chans))
    return [sum(ch[i] for ch in chans) / float(nch) for i in range(n)]


def _int16_channels(buffer: Any, nframes: int, nch: int, interleaved: bool) -> list[float]:
    ptrs = None
    with _swallow():
        ptrs = buffer.int16ChannelData()
    if ptrs is None:
        return []
    scale = 1.0 / 32768.0
    if interleaved:
        raw = _ptr_bytes(ptrs[0], nframes * nch * 2)
        ints = array.array("h")
        ints.frombytes(raw[: len(raw) - (len(raw) % 2)])
        if nch == 1:
            return [v * scale for v in ints]
        return [_mix(ints, i, nch) * scale for i in range(nframes) if (i + 1) * nch <= len(ints)]
    chans: list[array.array] = []
    for c in range(nch):
        raw = _ptr_bytes(ptrs[c], nframes * 2)
        a = array.array("h")
        a.frombytes(raw[: len(raw) - (len(raw) % 2)])
        chans.append(a)
    if not chans:
        return []
    n = min(nframes, min(len(ch) for ch in chans))
    return [sum(ch[i] for ch in chans) / float(nch) * scale for i in range(n)]


def _mix(buf: array.array, i: int, nch: int) -> float:
    acc = 0.0
    base = i * nch
    for c in range(nch):
        acc += float(buf[base + c])
    return acc / float(nch)


def _resample(samples: list[float], src: float, dst: float) -> list[float]:
    if not samples or src <= 0:
        return samples
    ratio = src / dst
    n_out = max(1, int(round(len(samples) / ratio)))
    out: list[float] = []
    last = len(samples) - 1
    for i in range(n_out):
        x = i * ratio
        j = int(x)
        if j >= last:
            out.append(samples[last])
            continue
        f = x - j
        out.append(samples[j] + (samples[j + 1] - samples[j]) * f)
    return out


def _ptr_bytes(ptr: Any, nbytes: int) -> bytes:
    if ptr is None or nbytes <= 0:
        return b""
    if isinstance(ptr, (bytes, bytearray, memoryview)):
        return bytes(ptr[:nbytes])
    addr = 0
    with _swallow():
        addr = int(ptr)
    if not addr:
        with _swallow():
            addr = int(ctypes.cast(ptr, ctypes.c_void_p).value or 0)
    if not addr:
        return b""
    return ctypes.string_at(addr, nbytes)


# ---------------------------------------------------------------- Core Audio / AVFoundation probes


def _av_engine_cls() -> Any:
    if not IS_MAC:
        return None
    try:
        from AVFoundation import AVAudioEngine  # type: ignore[import-not-found]
        return AVAudioEngine
    except Exception:  # noqa: BLE001
        return None


def _tap_description_cls() -> Any:
    if not IS_MAC:
        return None
    try:
        import objc  # type: ignore[import-not-found]
        return objc.lookUpClass("CATapDescription")
    except Exception:  # noqa: BLE001
        return None


def _runloop() -> tuple[Any, Any, Any]:
    try:
        from Foundation import NSDate, NSDefaultRunLoopMode, NSRunLoop  # type: ignore[import-not-found]
        return NSRunLoop, NSDate, NSDefaultRunLoopMode
    except Exception:  # noqa: BLE001
        return None, None, None


_ca_lib: Any = None


def _coreaudio() -> Any:
    global _ca_lib
    if _ca_lib is False:
        raise RuntimeError("CoreAudio.framework is unavailable")
    if _ca_lib is not None:
        return _ca_lib
    if not IS_MAC:
        _ca_lib = False
        raise RuntimeError("CoreAudio.framework is unavailable")
    lib = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
    lib.AudioHardwareCreateProcessTap.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    lib.AudioHardwareCreateProcessTap.restype = ctypes.c_int32
    lib.AudioHardwareDestroyProcessTap.argtypes = [ctypes.c_uint32]
    lib.AudioHardwareDestroyProcessTap.restype = ctypes.c_int32
    lib.AudioHardwareCreateAggregateDevice.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    lib.AudioHardwareCreateAggregateDevice.restype = ctypes.c_int32
    lib.AudioHardwareDestroyAggregateDevice.argtypes = [ctypes.c_uint32]
    lib.AudioHardwareDestroyAggregateDevice.restype = ctypes.c_int32
    _ca_lib = lib
    return lib


def _create_aggregate(ca: Any, tap_uid: str) -> int:
    from Foundation import NSDictionary, NSUUID  # type: ignore[import-not-found]

    agg_uid = str(NSUUID.UUID().UUIDString())
    payload = {
        "name": "GrainSystemAudio",
        "uid": agg_uid,
        "private": True,
        "tapAutoStart": True,
        "tapList": [{"uid": tap_uid}],
    }
    nsd = NSDictionary.dictionaryWithDictionary_(payload)
    agg_id = ctypes.c_uint32(0)
    status = int(ca.AudioHardwareCreateAggregateDevice(objc_id(nsd), ctypes.byref(agg_id)))
    if status != 0 or agg_id.value == 0:
        raise RuntimeError(f"AudioHardwareCreateAggregateDevice failed ({status})")
    return int(agg_id.value)


def _set_input_device(node: Any, device_id: int) -> bool:
    """Point the engine's input HAL unit at `device_id`. False if the property is missing."""
    au = None
    with _swallow():
        au = node.audioUnit()
    if au is None:
        with _swallow():
            wrapper = node.AUAudioUnit()
            au = wrapper.audioUnit() if wrapper is not None else None
    if au is None:
        return False
    try:
        at = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox")
        at.AudioUnitSetProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.c_uint32,
        ]
        at.AudioUnitSetProperty.restype = ctypes.c_int32
        value = ctypes.c_uint32(int(device_id))
        ptr = au
        with _swallow():
            ptr = int(au)
        status = int(at.AudioUnitSetProperty(
            ctypes.c_void_p(int(ptr)), _AU_CURRENT_DEVICE, _AU_SCOPE_GLOBAL, 0,
            ctypes.byref(value), ctypes.sizeof(value),
        ))
        return status == 0
    except Exception as e:  # noqa: BLE001
        log.debug("native_audio: could not set input device %s: %s", device_id, e)
        return False


def _device_id_for_uid(uid: str) -> int:
    """Core Audio object ID for an AVFoundation uniqueID. 0 if not found (the caller refuses it)."""
    if not uid or not IS_MAC:
        return 0
    try:
        ca = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
        cf = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        ca.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32
        ca.AudioObjectGetPropertyData.restype = ctypes.c_int32
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFRelease.argtypes = [ctypes.c_void_p]

        class _Addr(ctypes.Structure):
            _fields_ = [("sel", ctypes.c_uint32), ("scope", ctypes.c_uint32),
                        ("elem", ctypes.c_uint32)]

        def fourcc(code: str) -> int:
            return int.from_bytes(code.encode("ascii"), "big")

        # kAudioHardwarePropertyDevices / kAudioObjectPropertyScopeGlobal on the system object.
        addr = _Addr(fourcc("dev#"), fourcc("glob"), 0)
        size = ctypes.c_uint32(0)
        if int(ca.AudioObjectGetPropertyDataSize(1, ctypes.byref(addr), 0, None,
                                                 ctypes.byref(size))) != 0:
            return 0
        count = max(0, size.value // 4)
        ids = (ctypes.c_uint32 * count)()
        if int(ca.AudioObjectGetPropertyData(1, ctypes.byref(addr), 0, None,
                                             ctypes.byref(size), ids)) != 0:
            return 0
        uid_addr = _Addr(fourcc("uid "), fourcc("glob"), 0)
        buf = ctypes.create_string_buffer(256)
        for i in range(count):
            cfstr = ctypes.c_void_p()
            usize = ctypes.c_uint32(ctypes.sizeof(ctypes.c_void_p))
            if int(ca.AudioObjectGetPropertyData(int(ids[i]), ctypes.byref(uid_addr), 0, None,
                                                 ctypes.byref(usize), ctypes.byref(cfstr))) != 0:
                continue
            if not cfstr.value:
                continue
            try:
                ok = bool(cf.CFStringGetCString(cfstr, buf, 256, 0x08000100))  # kCFStringEncodingUTF8
            finally:
                cf.CFRelease(cfstr)
            if ok and buf.value.decode("utf-8", "replace") == uid:
                return int(ids[i])
    except Exception:  # noqa: BLE001
        return 0
    return 0


def objc_id(obj: Any) -> ctypes.c_void_p:
    import objc  # type: ignore[import-not-found]
    return ctypes.c_void_p(objc.pyobjc_id(obj))


class _swallow:
    def __enter__(self) -> _swallow:
        return self

    def __exit__(self, *exc: object) -> bool:
        return True
