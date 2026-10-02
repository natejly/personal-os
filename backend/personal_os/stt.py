"""Speech to text, swappable: on-device Speech, the LLM proxy, whisper.cpp, or nothing.

This module exists because nothing currently serves speech to text. litellm.yaml's model_list
carries ten chat models and one embedding model and no /v1/audio/transcriptions route, while
activity.py defaults `audio.model` to `whisper-1` and a capability probe that only checks the
model string reports ok on every machine while every call fails. `selftest` is the fix: it
writes a real wav and does a real round trip, which is exactly the thing a capability probe
cannot do.

`auto` prefers Apple's on-device Speech framework when it is authorized, then whisper.cpp if
the binary and a model are both present, then the proxy. ffmpeg is not required to write the
self-test wav.
"""
from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from . import audiocap, llm

log = logging.getLogger("personal_os.stt")

BACKENDS = ("auto", "speech", "proxy", "local", "off")

# whisper.cpp renamed its binary twice: `main` in the original tree, `whisper-cpp` in the brew
# formula, `whisper-cli` since the examples were reorganised. Try the newest name first.
CLI_NAMES = ("whisper-cli", "whisper-cpp", "main")

# The prompt carries the tail of the previous segment so a word straddling a segment boundary is
# not mangled into two half-words. Whisper's conditioning window is 224 tokens and anything past
# it is dropped silently, so cap the string here rather than trusting every caller to.
MAX_PROMPT_CHARS = 896

# What to do about it, in both directions, because "off" is a configuration and not a failure.
OFF_FIX = ("Set meetings.sttBackend to 'speech' for on-device dictation, 'local' after "
           "brew install whisper-cpp and downloading a model, or 'proxy' after adding a "
           "/v1/audio/transcriptions route to litellm.yaml.")

MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin"


def whisper_cli_path() -> str:
    for name in CLI_NAMES:
        hit = shutil.which(name)
        if hit:
            return hit
    return ""


def local_model_path(data_dir: Path, cfg: dict[str, Any]) -> str:
    """The ggml weights the local backend would load, or "" if there are none."""
    configured = str(cfg.get("whisperModelPath") or "").strip()
    if configured and Path(configured).is_file():
        return configured
    try:
        found = sorted(p for p in (data_dir / "models").glob("*.bin") if p.is_file())
    except Exception:  # noqa: BLE001 - a missing or unreadable models dir is just "no model"
        return ""
    return str(found[0]) if found else ""


def speech_available() -> bool:
    """Speech framework imports and this locale has an on-device model. Never prompts."""
    rec = _speech_recognizer()
    if rec is None:
        return False
    with contextlib.suppress(Exception):
        return bool(rec.supportsOnDeviceRecognition())
    return False


def speech_authorized() -> bool:
    """Stored Speech Recognition grant. 3 is authorized; never prompts."""
    try:
        from Speech import SFSpeechRecognizer  # type: ignore[import-not-found]
        return int(SFSpeechRecognizer.authorizationStatus()) == 3
    except Exception:  # noqa: BLE001
        return False


def speech_ready() -> bool:
    return speech_available() and speech_authorized()


def resolve_backend(cfg: dict[str, Any], data_dir: Path) -> str:
    """Which backend a transcription would actually use. Never "auto": that is a request, not an answer."""
    want = str(cfg.get("sttBackend") or "auto").strip().lower()
    if want in ("speech", "proxy", "local", "off"):
        return want
    if speech_ready():
        return "speech"
    if whisper_cli_path() and local_model_path(data_dir, cfg):
        return "local"
    return "proxy"


def transcribe(path: Path, *, settings: dict[str, Any], cfg: dict[str, Any], data_dir: Path,
               prompt: str = "") -> dict[str, Any]:
    """One wav in, one result dict out: {text, detail, backend, error, ms}. Never raises.

    `error` and `backend` are both always set: a backend that returned nothing useful still
    reports which one tried, so the segment row can say so and `retranscribe` can replay it once
    the user has fixed their route.
    """
    t0 = time.time()
    backend = resolve_backend(cfg, data_dir)
    text, detail, error = "", {}, ""
    try:
        if backend == "speech":
            text, detail, error = _speech(path)
        elif backend == "proxy":
            text, detail, error = _proxy(path, settings, str(cfg.get("sttModel") or "whisper-1"), prompt)
        elif backend == "local":
            text, detail, error = _local(path, data_dir, cfg)
        else:
            error = f"transcription is off. {OFF_FIX}"
    except Exception as e:  # noqa: BLE001 - this runs on a worker thread; a failure is data
        log.warning("transcribe failed (%s): %s: %s", backend, type(e).__name__, e)
        error = f"{type(e).__name__}: {e}"
    return {"text": text, "detail": detail, "backend": backend, "error": error,
            "ms": int((time.time() - t0) * 1000)}


def selftest(*, settings: dict[str, Any], cfg: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    """Record 0.4s of silence and transcribe it, to prove the route answers at all.

    Silence legitimately transcribes to "", so empty text is a PASS - the only failure is an
    error. That is the whole point: capabilities() can tell you a model name is set, it cannot
    tell you anything is listening on the other end, and today nothing is.
    """
    out: dict[str, Any] = {"ok": False, "backend": resolve_backend(cfg, data_dir), "record_ms": 0,
                           "transcribe_ms": 0, "text": "", "error": ""}
    fd, name = tempfile.mkstemp(prefix="stt-selftest-", suffix=".wav")
    os.close(fd)
    path = Path(name)
    try:
        t0 = time.time()
        if not audiocap.silence_wav(path, 0.4):
            out["error"] = "could not write a test wav"
            return out
        out["record_ms"] = int((time.time() - t0) * 1000)
        res = transcribe(path, settings=settings, cfg=cfg, data_dir=data_dir)
        out["backend"] = res["backend"]
        out["transcribe_ms"] = res["ms"]
        out["text"] = res["text"]
        out["error"] = res["error"]
        out["ok"] = not res["error"]
        return out
    finally:
        with contextlib.suppress(Exception):
            path.unlink(missing_ok=True)


def capabilities(cfg: dict[str, Any], data_dir: Path) -> list[dict[str, Any]]:
    """The two transcription rows, in the checklist shape activity.capabilities() uses.

    Read-only and offline: probing must never make a network call and never ask the OS for a
    permission. Anything not ok carries a copy-pasteable fix, which is the invariant the
    capability tests assert (test_activity.py:340-349).
    """
    backend = resolve_backend(cfg, data_dir)
    model = str(cfg.get("sttModel") or "").strip()
    cli = whisper_cli_path()
    weights = local_model_path(data_dir, cfg)
    local_ok = bool(cli and weights)
    speech_ok = speech_available()
    authorized = speech_authorized()
    if backend == "off":
        ready = False
        detail = "Transcription is turned off; a meeting keeps your notes and produces no transcript."
    elif backend == "speech":
        ready = speech_ok and authorized
        if not speech_ok:
            detail = "Set to Speech, but the on-device recognizer is missing for this locale."
        elif not authorized:
            detail = "Set to Speech; macOS has not granted Speech Recognition to this app yet."
        else:
            detail = "On-device Speech Recognition; audio never leaves the machine."
    elif backend == "local":
        ready = local_ok
        detail = (f"whisper.cpp at {cli} with {Path(weights).name}; audio never leaves the machine."
                  if local_ok else "Set to local, but the binary or the model is missing.")
    else:
        ready = bool(model)
        detail = (f"Audio is POSTed to /v1/audio/transcriptions on your configured base URL as {model}. "
                  "A default litellm.yaml has no model behind that path and errors, so only the "
                  "self-test proves this works." if model else "No transcription model set.")
    speech_fix = ""
    if not speech_ok:
        speech_fix = ("Install pyobjc-framework-Speech (`cd backend && uv pip install -e '.[activity]'`) "
                      "and use a locale with an on-device speech model.")
    elif not authorized:
        speech_fix = ("Grant Speech Recognition in System Settings → Privacy & Security, or set the "
                      "backend to Speech and press Test transcription — macOS asks once.")
    return [
        {
            "id": "stt", "label": "Transcription", "ok": ready, "detail": detail,
            "fix": "" if ready else (
                OFF_FIX if backend == "off" else
                speech_fix if backend == "speech" else
                "brew install whisper-cpp and download a model (see the next row)."
                if backend == "local" else
                "add a speech-to-text route to litellm.yaml (nothing answers "
                "/v1/audio/transcriptions today), point sttBackend at speech, or install whisper.cpp"),
        },
        {
            "id": "stt_speech", "label": "On-device transcription (Speech)", "ok": speech_ok and authorized,
            "detail": ("On-device Speech Recognition is authorized." if speech_ok and authorized else
                       "Speech framework is present; macOS has not granted Speech Recognition yet."
                       if speech_ok else
                       "Apple Speech is unavailable, so auto falls through to whisper.cpp or the proxy."),
            "fix": "" if (speech_ok and authorized) else speech_fix,
        },
        {
            "id": "stt_local", "label": "On-device transcription (whisper.cpp)", "ok": local_ok,
            "detail": (f"{cli} will load {weights}." if local_ok else
                       f"Binary found at {cli}, but no *.bin model to load." if cli else
                       "whisper-cli not on PATH. Optional; Speech covers the same job on a recent Mac."),
            "fix": "" if local_ok else (
                f"brew install whisper-cpp && mkdir -p {data_dir / 'models'} && "
                f"curl -L -o {data_dir / 'models' / 'ggml-base.en.bin'} {MODEL_URL}"),
        },
    ]


# ---------------------------------------------------------------- backends


def _speech_recognizer() -> Any:
    try:
        from Foundation import NSLocale  # type: ignore[import-not-found]
        from Speech import SFSpeechRecognizer  # type: ignore[import-not-found]
        rec = SFSpeechRecognizer.alloc().initWithLocale_(NSLocale.currentLocale())
        return rec or SFSpeechRecognizer.alloc().init()
    except Exception:  # noqa: BLE001
        return None


def _speech(path: Path) -> tuple[str, dict[str, Any], str]:
    """On-device SFSpeechRecognizer. Prompts only when status is not-determined."""
    try:
        from Foundation import NSDate, NSDefaultRunLoopMode, NSRunLoop, NSURL  # type: ignore[import-not-found]
        from Speech import (  # type: ignore[import-not-found]
            SFSpeechRecognizer,
            SFSpeechURLRecognitionRequest,
        )
    except Exception as e:  # noqa: BLE001
        return "", {}, f"Speech framework unavailable: {e}"

    status = int(SFSpeechRecognizer.authorizationStatus())
    if status == 0:  # not determined — this is the one prompt, and only from transcribe()
        box: dict[str, int] = {}
        done_auth = threading.Event()

        def _auth(st: int) -> None:
            box["st"] = int(st)
            done_auth.set()

        SFSpeechRecognizer.requestAuthorization_(_auth)
        t0 = time.time()
        while not done_auth.is_set() and time.time() - t0 < 30:
            NSRunLoop.currentRunLoop().runMode_beforeDate_(
                NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.1))
        status = int(box.get("st", 0))
    if status != 3:
        return "", {}, ("Speech Recognition is not granted. Enable it in System Settings → "
                        "Privacy & Security → Speech Recognition.")

    rec = _speech_recognizer()
    if rec is None:
        return "", {}, "SFSpeechRecognizer could not be created for this locale"
    with contextlib.suppress(Exception):
        rec.setDefaultTaskHint_(1)  # dictation
    url = NSURL.fileURLWithPath_(str(path.resolve()))
    req = SFSpeechURLRecognitionRequest.alloc().initWithURL_(url)
    req.setRequiresOnDeviceRecognition_(True)
    req.setShouldReportPartialResults_(False)

    box_res: dict[str, str] = {"text": "", "error": ""}
    done = threading.Event()

    def handler(result: Any, error: Any) -> None:
        if error is not None:
            box_res["error"] = str(error)
            done.set()
            return
        if result is not None:
            with contextlib.suppress(Exception):
                box_res["text"] = str(result.bestTranscription().formattedString() or "")
            if bool(result.isFinal()):
                done.set()

    rec.recognitionTaskWithRequest_resultHandler_(req, handler)
    t0 = time.time()
    while not done.is_set() and time.time() - t0 < 60:
        NSRunLoop.currentRunLoop().runMode_beforeDate_(
            NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.05))
    err = box_res["error"]
    if err and _is_no_speech(err):
        return "", {}, ""
    if not done.is_set() and not err and not box_res["text"]:
        return "", {}, "Speech recognition timed out"
    return box_res["text"].strip(), {}, err


def _is_no_speech(err: str) -> bool:
    n = err.lower()
    return "no speech" in n or "nospeech" in n or "kafspeech" in n


def _proxy(path: Path, settings: dict[str, Any], model: str,
           prompt: str) -> tuple[str, dict[str, Any], str]:
    """The body of AudioCollector._transcribe (activity.py:872-886), with the error returned.

    Two changes from the original: verbose_json plus a `prompt` carrying the tail of the previous
    segment (the original sends no cross-chunk context at all, so every boundary can split a
    word), and the failure comes back in the return value instead of being assigned to a
    collector attribute that only the status line ever shows.
    """
    base = str(settings.get("baseUrl") or "http://localhost:4000").rstrip("/")
    headers = {"Authorization": f"Bearer {settings['apiKey']}"} if settings.get("apiKey") else {}
    data = {"model": model, "response_format": "verbose_json"}
    if prompt:
        data["prompt"] = prompt[-MAX_PROMPT_CHARS:]
    with httpx.Client(timeout=120) as client, path.open("rb") as fh:
        r = client.post(f"{base}/v1/audio/transcriptions", headers=headers,
                        files={"file": (path.name, fh, "audio/wav")}, data=data)
    if r.status_code >= 400:
        return "", {}, f"transcription {r.status_code}: {r.text[:160]}"
    try:
        payload = r.json()
    except Exception:  # noqa: BLE001 - a proxy that answered text/plain still answered
        return r.text[:2000], {}, ""
    if not isinstance(payload, dict):
        return r.text[:2000], {}, ""
    # verbose_json reports the audio length, which is the only honest number for the usage log;
    # the wall clock would bill a slow proxy instead of the recording.
    seconds = payload.get("duration")
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds > 0:
        llm.audio_usage(model, float(seconds))
    return str(payload.get("text") or ""), {"segments": payload.get("segments") or []}, ""


def _local(path: Path, data_dir: Path, cfg: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    """whisper.cpp on this machine. -otxt writes <wav>.txt beside the wav; -nt drops timestamps."""
    cli = whisper_cli_path()
    if not cli:
        return "", {}, "whisper-cli not found on PATH (brew install whisper-cpp)"
    model = local_model_path(data_dir, cfg)
    if not model:
        return "", {}, f"no whisper model found; download one into {data_dir / 'models'}"
    txt = path.with_suffix(path.suffix + ".txt")
    try:
        r = subprocess.run([cli, "-m", model, "-f", str(path), "-otxt", "-nt", "-l", "auto"],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            return "", {}, _first_line(r.stderr) or f"{Path(cli).name} exited {r.returncode}"
        text = ""
        with contextlib.suppress(OSError):
            text = txt.read_text(errors="replace")
        return (text or r.stdout or "").strip(), {}, ""
    except subprocess.TimeoutExpired:
        return "", {}, "whisper.cpp timed out after 300s"
    finally:
        # The txt file is a side effect of -otxt, not something worth leaving in recordings/.
        with contextlib.suppress(Exception):
            txt.unlink(missing_ok=True)


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:160]
    return ""
