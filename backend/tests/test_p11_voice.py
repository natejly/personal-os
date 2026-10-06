"""Read-aloud and voice-chat settings: defaults exist and round-trip through PUT /settings.

Run: zsh tests/e2e/run-pytest.sh backend/tests/test_p11_voice.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="voicetest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod, llm  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})


def test_defaults() -> None:
    d = llm.DEFAULT_SETTINGS
    assert d["ttsVoice"] == "" and d["ttsRate"] == 1.0 and d["voiceLoopMaxTurns"] == 20


def test_settings_round_trip() -> None:
    r = client.put("/settings", json={"ttsVoice": "com.apple.voice.x", "ttsRate": 1.3, "voiceLoopMaxTurns": 5})
    assert r.status_code == 200, r.text
    got = client.get("/settings").json()
    assert (got["ttsVoice"], got["ttsRate"], got["voiceLoopMaxTurns"]) == ("com.apple.voice.x", 1.3, 5)
