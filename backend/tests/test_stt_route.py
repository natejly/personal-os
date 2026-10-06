"""POST /stt/transcribe: the composer's mic clip, with stt.transcribe stubbed (no audio stack).

Run: backend/.venv/bin/python backend/tests/test_stt_route.py
"""
from __future__ import annotations

import io
import os
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="sttroute-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as app_mod, stt  # noqa: E402
from personal_os.app import AUTH_TOKEN, app, db  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})


def wav(samples: int = 1600) -> bytes:
    data = b"\x00\x00" * samples
    return (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16) + b"data" + struct.pack("<I", len(data)) + data)


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    box: dict[str, Any] = {"backend": "proxy", "reply": {"text": "hello there", "detail": {}, "error": "", "ms": 5},
                           "paths": [], "cfg": {"sttBackend": "proxy"}}

    def fake(path: Path, **kw: Any) -> dict[str, Any]:
        box["paths"].append(path)
        box["prompt"] = kw.get("prompt")
        assert path.read_bytes()[:4] == b"RIFF"
        if isinstance(box["reply"], Exception):
            raise box["reply"]
        return {**box["reply"], "backend": box["backend"]}

    monkeypatch.setattr(stt, "transcribe", fake)
    monkeypatch.setattr(stt, "resolve_backend", lambda cfg, data_dir: box["backend"])
    monkeypatch.setattr(stt, "config_for", lambda stored: {**stt.DEFAULT_CONFIG, **box["cfg"]})
    return box


def post(body: bytes, prompt: str = "") -> Any:
    return client.post("/stt/transcribe", files={"audio": ("clip.wav", io.BytesIO(body), "audio/wav")},
                       data={"prompt": prompt})


def test_valid_wav_returns_text_and_deletes_the_temp_file(stub: dict[str, Any]) -> None:
    r = post(wav(), prompt="names")
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "hello there", "backend": "proxy", "error": "", "ms": 5}
    assert stub["prompt"] == "names"
    assert stub["paths"] and not stub["paths"][0].exists()


def test_hallucination_filter_is_applied(stub: dict[str, Any]) -> None:
    stub["reply"] = {"text": "go go go go go", "detail": {}, "error": "", "ms": 1}
    assert post(wav()).json()["text"] == "go"


def test_oversize_is_413(stub: dict[str, Any]) -> None:
    r = post(wav(app_mod.STT_CLIP_MAX_BYTES // 2 + 10))
    assert r.status_code == 413
    assert not stub["paths"]


def test_not_a_wav_is_400(stub: dict[str, Any]) -> None:
    assert post(b"not audio at all").status_code == 400


def test_off_is_409_with_the_fix(stub: dict[str, Any]) -> None:
    stub["backend"] = "off"
    r = post(wav())
    assert r.status_code == 409
    assert stt.OFF_FIX in r.json()["detail"]
    assert not stub["paths"]


def test_unauthorized_speech_is_409_with_the_fix(stub: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    stub["backend"] = "speech"
    monkeypatch.setattr(stt, "speech_ready", lambda: False)
    r = post(wav())
    assert r.status_code == 409
    assert r.json()["detail"]
    assert not stub["paths"]


def test_temp_file_deleted_when_transcribe_raises(stub: dict[str, Any]) -> None:
    stub["reply"] = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        post(wav())
    assert stub["paths"] and not stub["paths"][0].exists()
    assert not list((db.data_dir / "tmp").glob("dictate-*.wav"))


def test_backend_error_comes_back_as_data(stub: dict[str, Any]) -> None:
    stub["reply"] = {"text": "", "detail": {}, "error": "proxy 404", "ms": 2}
    assert post(wav()).json()["error"] == "proxy 404"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
