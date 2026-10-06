"""Speech to text, swappable: on-device Speech, Whistle, the LLM proxy, whisper.cpp, or nothing.

This module exists because nothing currently serves speech to text. litellm.yaml's model_list
carries chat models and one embedding model and no /v1/audio/transcriptions route, so a capability
probe that only checks the model string would report ok on every machine while every call fails. `selftest` is the fix: it
writes a real wav and does a real round trip, which is exactly the thing a capability probe
cannot do.

`auto` prefers Apple's on-device Speech framework when it is authorized, then Whistle (the
cactus-needle package, in-process) if it is installed, then whisper.cpp if the binary and a model
are both present, then the proxy. ffmpeg is not required to write the self-test wav.
"""
from __future__ import annotations

import array
import contextlib
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Any

import httpx

from . import audiocap, llm, providers

log = logging.getLogger("personal_os.stt")

# cactus-needle (Whistle) posts anonymous usage counts and prints a first-run notice unless told
# not to. Decide that here, before anything in this process can import it.
os.environ.setdefault("NEEDLE_TELEMETRY", "0")

BACKENDS = ("auto", "speech", "whistle", "proxy", "local", "off")

# Settings key `voice`: how composer dictation is transcribed. Read through config_for, which also seeds
# these fields from a `meetings` row left behind by an older build when no `voice` row exists yet.
DEFAULT_CONFIG: dict[str, Any] = {
    "sttBackend": "auto",         # auto | speech | whistle | proxy | local | off
    "sttModel": "whisper-1",      # the model name the proxy backend asks /v1/audio/transcriptions for
    "whisperModelPath": "",       # ggml weights for local; "" = the first *.bin under <data>/models
    "whisperVadModelPath": "",    # optional Silero ggml for whisper.cpp --vad
    "hallucinationFilter": True,  # drop known silence phrases and repeated-word runs
    "dictationCleanup": False,    # model pass over each clip (assist.clean_dictation)
}


def clean_config(raw: dict[str, Any]) -> dict[str, Any]:
    """DEFAULT_CONFIG overlaid with the valid fields of `raw`; anything unknown or mistyped falls back."""
    out = dict(DEFAULT_CONFIG)
    for key, default in DEFAULT_CONFIG.items():
        v = raw.get(key, default)
        if isinstance(default, bool):
            out[key] = v if isinstance(v, bool) else default
        elif isinstance(v, str):
            out[key] = v.strip()[:500]
    if out["sttBackend"] not in BACKENDS:
        out["sttBackend"] = "auto"
    out["sttModel"] = out["sttModel"] or DEFAULT_CONFIG["sttModel"]
    return out


def config_for(stored: dict[str, Any]) -> dict[str, Any]:
    """The voice config from the full settings dict (db.get_settings())."""
    row = stored.get("voice")
    if not isinstance(row, dict):
        row = stored.get("meetings")  # an older build kept these fields in its meetings config
    return clean_config(row if isinstance(row, dict) else {})

# Whistle takes one pass of at most 30 s of 16 kHz mono; longer wavs are fed in windows.
WHISTLE_MAX_SECONDS = 30
WHISTLE_RATE = 16000
# Where cactus-needle keeps the 17 MB weights it downloads on first use.
WHISTLE_WEIGHTS = Path.home() / ".cache" / "cactus-needle" / "whistle"
WHISTLE_FIX = "cd backend && uv pip install -e '.[whistle]', then restart the app."
# One model per process, and it is not thread-safe.
_whistle_lock = threading.Lock()

# whisper.cpp renamed its binary twice: `main` in the original tree, `whisper-cpp` in the brew
# formula, `whisper-cli` since the examples were reorganised. Try the newest name first.
CLI_NAMES = ("whisper-cli", "whisper-cpp", "main")

# The prompt carries the tail of the previous clip so a word straddling a clip boundary is
# not mangled into two half-words. Whisper's conditioning window is 224 tokens and anything past
# it is dropped silently, so cap the string here rather than trusting every caller to.
MAX_PROMPT_CHARS = 896

# What to do about it, in both directions, because "off" is a configuration and not a failure.
OFF_FIX = ("Set the transcription backend (Settings, Voice input) to 'speech' for on-device dictation, 'whistle' after "
           "installing cactus-needle, 'local' after brew install whisper-cpp and downloading a "
           "model, or 'proxy' after adding a /v1/audio/transcriptions route to litellm.yaml.")

# The Speech Recognition grant is its own switch in Privacy & Security; this id is the pane deep link
# and the Grant button in the permissions panel.
SPEECH_PERMISSION = "speech_recognition"

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


def whistle_installed() -> bool:
    """cactus-needle is importable. Never imports it: that would load a 17 MB model into a probe."""
    try:
        return importlib.util.find_spec("needle") is not None
    except Exception:  # noqa: BLE001 - a broken sys.path entry is "not installed"
        return False


def whistle_cached() -> bool:
    """The weights are already on disk, so the first transcribe needs no network."""
    try:
        return any(WHISTLE_WEIGHTS.rglob("*.cact"))
    except OSError:
        return False


def speech_available() -> bool:
    """Speech framework imports and this locale has an on-device model. Never prompts."""
    rec = _speech_recognizer()
    if rec is None:
        return False
    with contextlib.suppress(Exception):
        return bool(rec.supportsOnDeviceRecognition())
    return False


def speech_auth_status() -> int:
    """Stored Speech Recognition grant: 0 not asked, 1 denied, 2 restricted, 3 authorized; -1 when the
    framework is missing. Never prompts."""
    try:
        from Speech import SFSpeechRecognizer  # type: ignore[import-not-found]
        return int(SFSpeechRecognizer.authorizationStatus())
    except Exception:  # noqa: BLE001
        return -1


def speech_authorized() -> bool:
    return speech_auth_status() == 3


def speech_ready() -> bool:
    return speech_available() and speech_authorized()


def resolve_backend(cfg: dict[str, Any], data_dir: Path) -> str:
    """Which backend a transcription would actually use. Never "auto": that is a request, not an answer."""
    want = str(cfg.get("sttBackend") or "auto").strip().lower()
    if want in ("speech", "whistle", "proxy", "local", "off"):
        return want
    whistle = whistle_installed()
    local = bool(whisper_cli_path() and local_model_path(data_dir, cfg))
    if speech_ready():
        return "speech"
    if whistle:
        return "whistle"
    return "local" if local else "proxy"


def transcribe(path: Path, *, settings: dict[str, Any], cfg: dict[str, Any], data_dir: Path,
               prompt: str = "", vocab: str = "") -> dict[str, Any]:
    """One wav in, one result dict out: {text, detail, backend, error, ms}. Never raises.

    `prompt` is the vocabulary plus the previous clip's tail, for the backends that condition on
    text; `vocab` is the comma-separated vocabulary alone, for Whistle's keyword biasing (feeding
    it the previous tail would bias it toward repeating the last clip).

    `error` and `backend` are both always set: a backend that returned nothing useful still
    reports which one tried.
    """
    t0 = time.time()
    backend = resolve_backend(cfg, data_dir)
    text, detail, error = "", {}, ""
    try:
        if backend == "speech":
            text, detail, error = _speech(path)
        elif backend == "whistle":
            text, detail, error = _whistle(path, vocab)
        elif backend == "proxy":
            text, detail, error = _proxy(path, settings, str(cfg.get("sttModel") or "whisper-1"), prompt)
        elif backend == "local":
            text, detail, error = _local(path, data_dir, cfg, prompt)
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
    """The transcription rows: one per backend plus the active one, as {id, label, ok, detail, fix}.

    Read-only and offline: probing must never make a network call and never ask the OS for a
    permission. Anything not ok carries a copy-pasteable fix.
    """
    backend = resolve_backend(cfg, data_dir)
    model = str(cfg.get("sttModel") or "").strip()
    cli = whisper_cli_path()
    weights = local_model_path(data_dir, cfg)
    local_ok = bool(cli and weights)
    speech_ok = speech_available()
    authorized = speech_authorized()
    whistle_ok = whistle_installed()
    whistle_detail = ("Whistle (cactus-needle) runs in this process; audio never leaves the machine. "
                      + ("The weights are cached." if whistle_cached()
                         else "The 17 MB weights download from Hugging Face on first use."))
    if backend == "off":
        ready = False
        detail = "Transcription is turned off; dictation returns nothing."
    elif backend == "speech":
        ready = speech_ok and authorized
        if not speech_ok:
            detail = "Set to Speech, but the on-device recognizer is missing for this locale."
        elif not authorized:
            detail = "Set to Speech; macOS has not granted Speech Recognition to this app yet."
        else:
            detail = "On-device Speech Recognition; audio never leaves the machine."
    elif backend == "whistle":
        ready = whistle_ok
        detail = whistle_detail if whistle_ok else "Set to Whistle, but cactus-needle is not installed."
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
        speech_fix = ("Install pyobjc-framework-Speech (`cd backend && uv pip install -e '.[mac]'`) "
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
                WHISTLE_FIX if backend == "whistle" else
                "brew install whisper-cpp and download a model (see the next row)."
                if backend == "local" else
                "add a speech-to-text route to litellm.yaml (nothing answers "
                "/v1/audio/transcriptions today), point sttBackend at speech, install cactus-needle "
                "for Whistle, or install whisper.cpp"),
        },
        {
            "id": "stt_speech", "label": "On-device transcription (Speech)", "ok": speech_ok and authorized,
            "detail": ("On-device Speech Recognition is authorized." if speech_ok and authorized else
                       "Speech framework is present; macOS has not granted Speech Recognition yet."
                       if speech_ok else
                       "Apple Speech is unavailable, so auto falls through to whisper.cpp or the proxy."),
            "fix": "" if (speech_ok and authorized) else speech_fix,
            # macOS can be asked (SFSpeechRecognizer prompts once); after a refusal only the pane works.
            "permission": SPEECH_PERMISSION, "requestable": speech_ok and speech_auth_status() == 0,
        },
        {
            "id": "stt_whistle", "label": "On-device transcription (Whistle)", "ok": whistle_ok,
            "detail": whistle_detail if whistle_ok else
            "cactus-needle not installed. Optional; a 17 MB seven-language model that runs on the CPU.",
            "fix": "" if whistle_ok else WHISTLE_FIX,
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
    """POST the wav to /audio/transcriptions as verbose_json with an optional `prompt`; the failure comes
    back in the return value."""
    base = str(settings.get("baseUrl") or "http://localhost:4000")
    headers = {"Authorization": f"Bearer {settings['apiKey']}"} if settings.get("apiKey") else {}
    data = {"model": model, "response_format": "verbose_json"}
    if prompt:
        data["prompt"] = prompt[-MAX_PROMPT_CHARS:]
    with httpx.Client(timeout=120) as client, path.open("rb") as fh:
        r = client.post(providers.endpoint(base, "/audio/transcriptions"), headers=headers,
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


def _whistle(path: Path, vocab: str = "") -> tuple[str, dict[str, Any], str]:
    """Whistle in-process via cactus-needle: text plus one timed segment per 30 s window.

    The model takes at most 30 s of 16 kHz mono per pass. A longer wav is fed in 30 s windows with the word times shifted back onto the wav's clock.
    """
    try:
        import needle  # type: ignore[import-not-found]
    except ImportError:
        return "", {}, f"cactus-needle is not installed ({WHISTLE_FIX})"
    with wave.open(str(path), "rb") as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        if (rate, channels, width) != (WHISTLE_RATE, 1, 2):
            return "", {}, f"Whistle needs 16 kHz mono 16-bit wav; this one is {rate} Hz, {channels} ch, {width * 8}-bit"
        pcm = w.readframes(w.getnframes())
    keywords = [k.strip() for k in vocab.split(",") if k.strip()] or None
    step = WHISTLE_RATE * WHISTLE_MAX_SECONDS * 2
    segments: list[dict[str, Any]] = []
    language = ""
    # ponytail: a word straddling a 30 s cut is split in two; cut on silence if long wavs show up
    with _whistle_lock:
        for off in range(0, len(pcm), step):
            ints = array.array("h")
            ints.frombytes(pcm[off:off + step])
            samples = array.array("f", (v / 32768.0 for v in ints))
            res = needle.transcribe(samples, keywords=keywords, word_timestamps=True)
            t0 = off / (WHISTLE_RATE * 2)
            text = str(res.get("text") or "").strip()
            language = language or str(res.get("language") or "")
            if not text:
                continue
            words = [wd for wd in (res.get("words") or []) if isinstance(wd, dict)]
            segments.append({
                "start": t0 + float(words[0].get("start", 0)) if words else t0,
                "end": t0 + float(words[-1].get("end", 0)) if words else t0 + len(ints) / WHISTLE_RATE,
                "text": text,
            })
    return " ".join(s["text"] for s in segments), {"segments": segments, "language": language}, ""


def _local(path: Path, data_dir: Path, cfg: dict[str, Any], prompt: str = "") -> tuple[str, dict[str, Any], str]:
    """whisper.cpp on this machine. -oj writes <wav>.json (segment offsets), -otxt the fallback text."""
    cli = whisper_cli_path()
    if not cli:
        return "", {}, "whisper-cli not found on PATH (brew install whisper-cpp)"
    model = local_model_path(data_dir, cfg)
    if not model:
        return "", {}, f"no whisper model found; download one into {data_dir / 'models'}"
    txt = path.with_suffix(path.suffix + ".txt")
    js = path.with_suffix(path.suffix + ".json")
    argv = [cli, "-m", model, "-f", str(path), "-of", str(path), "-oj", "-otxt", "-nt", "-l", "auto"]
    if prompt:
        argv += ["--prompt", prompt[-MAX_PROMPT_CHARS:]]
    vad = vad_model_path(data_dir, cfg)
    if vad:
        argv += ["--vad", "-vm", vad]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            return "", {}, _first_line(r.stderr) or f"{Path(cli).name} exited {r.returncode}"
        segments = _read_local_json(js)
        if segments:
            return " ".join(s["text"] for s in segments if s["text"]).strip(), {"segments": segments}, ""
        text = ""
        with contextlib.suppress(OSError):
            text = txt.read_text(errors="replace")
        return (text or r.stdout or "").strip(), {}, ""
    except subprocess.TimeoutExpired:
        return "", {}, "whisper.cpp timed out after 300s"
    finally:
        # Both files are side effects of -oj/-otxt, not something worth leaving beside the clip.
        for f in (txt, js):
            with contextlib.suppress(Exception):
                f.unlink(missing_ok=True)


def vad_model_path(data_dir: Path, cfg: dict[str, Any]) -> str:
    """A Silero ggml model for whisper.cpp's --vad, or "" (optional; the energy gate runs regardless)."""
    configured = str(cfg.get("whisperVadModelPath") or "").strip()
    if configured and Path(configured).is_file():
        return configured
    try:
        found = sorted(p for p in (data_dir / "models").glob("ggml-silero*.bin") if p.is_file())
    except Exception:  # noqa: BLE001
        return ""
    return str(found[0]) if found else ""


def _read_local_json(js: Path) -> list[dict[str, Any]]:
    """whisper.cpp -oj output as verbose_json-shaped segments; [] if absent or malformed."""
    try:
        doc = json.loads(js.read_text(errors="replace"))
        out = []
        for e in doc.get("transcription") or []:
            off = e.get("offsets") or {}
            out.append({"start": float(off.get("from", 0)) / 1000.0, "end": float(off.get("to", 0)) / 1000.0,
                        "text": str(e.get("text") or "").strip()})
        return out
    except Exception:  # noqa: BLE001 - fall back to the txt file
        return []


# Phrases Whisper emits on silence or noise. Only trusted when the energy VAD also says the
# segment is nearly silent, because a person can really say "thank you".
HALLUCINATIONS = frozenset({
    "thank you", "thanks", "thanks for watching", "thank you for watching", "you", "bye", "bye bye",
    "subtitles by the amara.org community", "please subscribe", "like and subscribe",
    "thank you very much", "see you next time", "see you in the next video",
})
LOW_SPEECH_RATIO = 0.15
REPEAT_RUN = 4


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s']", " ", text.lower()).split())


def _collapse_repeats(text: str) -> str:
    """A word or short phrase repeated >= REPEAT_RUN times in a row becomes one."""
    words = text.split()
    for n in (1, 2, 3):
        i, out = 0, []
        while i < len(words):
            unit = words[i:i + n]
            j = i + n
            while len(unit) == n and [_norm(w) for w in words[j:j + n]] == [_norm(w) for w in unit]:
                j += n
            if len(unit) == n and (j - i) // n >= REPEAT_RUN:
                out.extend(unit)
                i = j
            else:
                out.append(words[i])
                i += 1
        words = out
    return " ".join(words)


def filter_hallucinations(text: str, detail: dict[str, Any] | None, speech_ratio: float | None = None,
                          cfg: dict[str, Any] | None = None) -> tuple[str, dict[str, Any], list[str]]:
    """faster-whisper's decode-quality rules plus a known-phrase rule, applied after the fact.

    Returns (text, detail, dropped). Never raises and never mutates its inputs.
    """
    detail = dict(detail or {})
    dropped: list[str] = []
    quiet = speech_ratio is not None and speech_ratio < LOW_SPEECH_RATIO
    segments = detail.get("segments")
    if isinstance(segments, list) and segments:
        kept = []
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            seg_text = str(seg.get("text") or "")
            nsp, lp, cr = seg.get("no_speech_prob"), seg.get("avg_logprob"), seg.get("compression_ratio")
            bad = (isinstance(nsp, (int, float)) and isinstance(lp, (int, float)) and nsp > 0.6 and lp < -1.0) \
                or (isinstance(cr, (int, float)) and cr > 2.4) \
                or (quiet and _norm(seg_text) in HALLUCINATIONS)
            if bad:
                dropped.append(seg_text.strip())
            else:
                kept.append(seg)
        detail["segments"] = kept
        new_text = _collapse_repeats(" ".join(str(s.get("text") or "").strip() for s in kept).strip())
    else:
        new_text = _collapse_repeats(text.strip())
        if quiet and _norm(new_text) in HALLUCINATIONS:
            dropped.append(new_text)
            new_text = ""
    if dropped:
        detail["filtered"] = dropped
    return new_text, detail, dropped


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:160]
    return ""
