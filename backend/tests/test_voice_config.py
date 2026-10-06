"""Voice config: GET/PUT /voice/config, the legacy `meetings` carry-over, and dictationCleanup in /stt/transcribe.

Run: backend/.venv/bin/python backend/tests/test_voice_config.py
"""
from __future__ import annotations

import io
import os
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="voicecfg-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import assist, stt  # noqa: E402
from personal_os.app import AUTH_TOKEN, app, db  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})


def _clear() -> None:
    with db.tx() as c:
        c.execute("DELETE FROM settings WHERE key IN ('voice', 'meetings')")


@pytest.fixture(autouse=True)
def fresh() -> Any:
    _clear()
    yield
    _clear()


def test_defaults_when_nothing_is_stored() -> None:
    assert client.get("/voice/config").json() == stt.DEFAULT_CONFIG
    assert set(stt.DEFAULT_CONFIG) == {"sttBackend", "sttModel", "whisperModelPath", "whisperVadModelPath",
                                       "hallucinationFilter", "dictationCleanup"}


def test_put_is_a_partial_patch_and_persists() -> None:
    r = client.put("/voice/config", json={"sttBackend": "local", "dictationCleanup": True})
    assert r.status_code == 200
    assert r.json() == {**stt.DEFAULT_CONFIG, "sttBackend": "local", "dictationCleanup": True}
    r = client.put("/voice/config", json={"sttModel": "  my-whisper  "})
    assert r.json()["sttModel"] == "my-whisper" and r.json()["sttBackend"] == "local"
    assert client.get("/voice/config").json() == r.json()


def test_put_coerces_and_ignores_what_it_cannot_use() -> None:
    r = client.put("/voice/config", json={"sttBackend": "carrier-pigeon", "sttModel": "   ", "bogus": 1})
    assert r.json() == stt.DEFAULT_CONFIG
    assert "voice" in client.get("/settings").json()  # readable through /settings...
    client.put("/settings", json={"voice": {"sttBackend": "off"}})  # ...but only writable through its own route
    assert client.get("/voice/config").json()["sttBackend"] == "auto"


def test_a_legacy_meetings_row_seeds_the_defaults() -> None:
    db.set_settings({"meetings": {"enabled": True, "sttBackend": "whistle", "sttModel": "w-1", "whisperModelPath": "/m.bin",
                                  "hallucinationFilter": False, "dictationCleanup": True, "diarize": True, "template": "general"}})
    got = client.get("/voice/config").json()
    assert got == {**stt.DEFAULT_CONFIG, "sttBackend": "whistle", "sttModel": "w-1", "whisperModelPath": "/m.bin",
                   "hallucinationFilter": False, "dictationCleanup": True}
    db.set_settings({"voice": {"sttBackend": "proxy"}})  # once a voice row exists the old one is ignored
    assert client.get("/voice/config").json()["sttBackend"] == "proxy"
    assert client.get("/voice/config").json()["sttModel"] == "whisper-1"


def test_a_corrupt_row_falls_back_to_defaults() -> None:
    db.set_settings({"voice": ["not", "a", "dict"]})
    assert client.get("/voice/config").json() == stt.DEFAULT_CONFIG


def _wav() -> bytes:
    data = b"\x00\x00" * 1600
    return (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16) + b"data" + struct.pack("<I", len(data)) + data)


def test_dictation_cleanup_runs_only_when_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stt, "resolve_backend", lambda cfg, data_dir: "proxy")
    monkeypatch.setattr(stt, "transcribe", lambda path, **kw: {"text": "um hello there", "detail": {}, "backend": "proxy",
                                                             "error": "", "ms": 1})
    calls: list[str] = []

    async def tidy(_settings: Any, text: str, timeout: float = 3.0) -> str:
        calls.append(text)
        return "Hello there."

    monkeypatch.setattr(assist, "clean_dictation", tidy)

    def post() -> Any:
        return client.post("/stt/transcribe", files={"audio": ("c.wav", io.BytesIO(_wav()), "audio/wav")}, data={"prompt": ""})

    assert post().json()["text"] == "um hello there" and calls == []
    client.put("/voice/config", json={"dictationCleanup": True})
    assert post().json()["text"] == "Hello there." and calls == ["um hello there"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
