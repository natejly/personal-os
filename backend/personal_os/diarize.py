"""Who spoke when: an optional diarization seam with a graceful no-op fallback.

The default path has no model and no new dependency. `resolve_backend` answers 'none' unless the
user installed sherpa-onnx (pip install sherpa-onnx, ONNX only, no torch) and downloaded a
segmentation and an embedding model, in which case 'sherpa' clusters the WHOLE retained file once
(per-segment clustering would hand every 20 s clip its own S1/S2). Whatever the backend returns is
just a list of turns; `assign_speakers` is the WhisperX rule (each utterance goes to the speaker
whose turns overlap it most) and is pure, so it is tested without audio.

Nothing here raises into the caller: a missing backend, an unreadable model or a failed concat all
come back as "no turns", and the transcript keeps its channel labels.
"""
from __future__ import annotations

import array
import contextlib
import logging
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any, Protocol

from . import audiocap

log = logging.getLogger("personal_os.diarize")

Turn = tuple[float, float, str]  # (start seconds, end seconds, speaker id)

BACKENDS = ("auto", "none", "sherpa")
NEAREST_TURN_SECONDS = 1.0

SEGMENTATION_HINT = ("download sherpa-onnx-pyannote-segmentation-3-0 from "
                     "https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-segmentation-models")
EMBEDDING_HINT = ("download an embedding model such as 3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx "
                  "from https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models")


class Backend(Protocol):
    def diarize(self, wav: Path, cfg: dict[str, Any]) -> list[Turn]: ...


class NullBackend:
    """No diarizer installed: no turns, so every caller falls back to channel labels."""

    def diarize(self, wav: Path, cfg: dict[str, Any]) -> list[Turn]:
        return []


def _model(cfg: dict[str, Any], key: str, data_dir: Path | None) -> str:
    raw = str(cfg.get(key) or "").strip()
    if not raw:
        return ""
    p = Path(raw).expanduser()
    if not p.is_absolute() and data_dir is not None:
        p = data_dir / "models" / raw
    return str(p) if p.is_file() else ""


def _sherpa_importable() -> bool:
    try:
        import sherpa_onnx  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 - not installed is the normal case
        return False


class SherpaBackend:
    """sherpa-onnx offline speaker diarization (pyannote segmentation + embedding + clustering)."""

    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir

    def diarize(self, wav: Path, cfg: dict[str, Any]) -> list[Turn]:
        try:
            import sherpa_onnx  # type: ignore[import-not-found]
        except Exception:  # noqa: BLE001
            return []
        seg, emb = _model(cfg, "diarizeSegmentationModel", self.data_dir), _model(cfg, "diarizeEmbeddingModel", self.data_dir)
        if not seg or not emb:
            return []
        try:
            config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
                segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                    pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=seg)),
                embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=emb),
                clustering=sherpa_onnx.FastClusteringConfig(
                    num_clusters=int(cfg.get("diarizeSpeakers") or 0) or -1,
                    threshold=float(cfg.get("diarizeThreshold") or 0.5)),
                min_duration_on=0.3, min_duration_off=0.5)
            sd = sherpa_onnx.OfflineSpeakerDiarization(config)
            samples = _read_float_wav(wav)
            result = sd.process(samples).sort_by_start_time()
            return [(float(r.start), float(r.end), f"spk{int(r.speaker)}") for r in result]
        except Exception as e:  # noqa: BLE001 - a broken model is "no diarization", not an error banner
            log.warning("diarize: sherpa failed: %s: %s", type(e).__name__, e)
            return []


def _read_float_wav(path: Path) -> Any:
    import numpy as np  # sherpa-onnx depends on numpy, so it exists whenever this runs

    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
    a = array.array("h")
    a.frombytes(raw[: len(raw) - len(raw) % 2])
    return np.asarray(a, dtype="float32") / 32768.0


def resolve_backend(cfg: dict[str, Any], data_dir: Path | None = None) -> str:
    """'none' or 'sherpa'. Never 'auto': that is a request, not an answer."""
    want = str(cfg.get("diarizeBackend") or "auto").strip().lower()
    if want == "none":
        return "none"
    if want in ("auto", "sherpa") and _sherpa_importable() \
            and _model(cfg, "diarizeSegmentationModel", data_dir) and _model(cfg, "diarizeEmbeddingModel", data_dir):
        return "sherpa"
    return "none"


def get_backend(cfg: dict[str, Any], data_dir: Path | None = None) -> Backend:
    return SherpaBackend(data_dir) if resolve_backend(cfg, data_dir) == "sherpa" else NullBackend()


def capabilities(cfg: dict[str, Any], data_dir: Path | None = None) -> dict[str, Any]:
    """A non-blocking checklist row, like stt_local: optional, so never in BLOCKING_CAPABILITIES."""
    missing: list[str] = []
    if not _sherpa_importable():
        missing.append("pip install sherpa-onnx")
    if not _model(cfg, "diarizeSegmentationModel", data_dir):
        missing.append(f"{SEGMENTATION_HINT}, then set diarizeSegmentationModel")
    if not _model(cfg, "diarizeEmbeddingModel", data_dir):
        missing.append(f"{EMBEDDING_HINT}, then set diarizeEmbeddingModel")
    ok = resolve_backend(cfg, data_dir) == "sherpa"
    return {
        "id": "diarize", "label": "Speaker separation (sherpa-onnx)", "ok": ok,
        "detail": ("sherpa-onnx will separate speakers on retained audio." if ok else
                   "Optional. Without it every remote participant is one [them]."),
        "fix": "" if ok else "; ".join(missing),
    }


# ---------------------------------------------------------------- pure assignment


def rename_turns(turns: list[Turn]) -> list[Turn]:
    """Speaker ids become S1, S2, ... in order of first appearance, turns sorted by start."""
    ids: dict[str, str] = {}
    out: list[Turn] = []
    for s, e, spk in sorted(turns, key=lambda t: (t[0], t[1])):
        out.append((s, e, ids.setdefault(spk, f"S{len(ids) + 1}")))
    return out


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def assign_speakers(utterances: list[dict[str, Any]], turns: list[Turn],
                    min_overlap: float = 0.0) -> list[dict[str, Any]]:
    """WhisperX's max-overlap rule. Returns copies with a 'speaker' ('' when nothing is close)."""
    out: list[dict[str, Any]] = []
    for u in utterances:
        d = dict(u)
        s, e = float(d.get("start") or 0.0), float(d.get("end") or 0.0)
        if not turns:
            out.append(d)
            continue
        totals: dict[str, float] = {}
        for ts, te, spk in turns:
            ov = _overlap(s, e, ts, te)
            if ov > min_overlap:
                totals[spk] = totals.get(spk, 0.0) + ov
        speaker = ""
        if totals:
            # max() keeps the first maximal key, i.e. the earliest turn: a stable tie-break.
            speaker = max(totals.items(), key=lambda kv: kv[1])[0]
        else:
            best, dist = "", NEAREST_TURN_SECONDS + 1e-9
            for ts, te, spk in turns:
                gap = ts - e if ts >= e else (s - te if te <= s else 0.0)
                if gap < dist:
                    best, dist = spk, gap
            speaker = best
        d["speaker"] = speaker
        out.append(d)
    return out


def merge_adjacent(utterances: list[dict[str, Any]], gap: float = 1.5) -> list[dict[str, Any]]:
    """Join consecutive same-speaker utterances separated by at most `gap` seconds."""
    out: list[dict[str, Any]] = []
    for u in utterances:
        last = out[-1] if out else None
        if last and last.get("speaker") == u.get("speaker") and float(u["start"]) - float(last["end"]) <= gap:
            last["end"] = max(float(last["end"]), float(u["end"]))
            last["text"] = f"{last['text']} {u['text']}".strip()
        else:
            out.append(dict(u))
    return out


# ---------------------------------------------------------------- audio


def concat_wavs(wavs: list[Path], out: Path) -> bool:
    """One 16 kHz mono wav from several, via ffmpeg's concat demuxer. False if it could not."""
    ff = audiocap.ffmpeg_path()
    if not ff or not wavs:
        return False
    listing = Path(tempfile.mkstemp(prefix="diarize-", suffix=".txt")[1])
    try:
        listing.write_text("".join("file '" + str(p).replace("'", "'\\''") + "'\n" for p in wavs))
        r = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                            "-i", str(listing), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-y", str(out)],
                           capture_output=True, text=True, timeout=600)
        return r.returncode == 0 and out.exists()
    except Exception as e:  # noqa: BLE001
        log.warning("diarize: concat failed: %s", e)
        return False
    finally:
        with contextlib.suppress(Exception):
            listing.unlink(missing_ok=True)
