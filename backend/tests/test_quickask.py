"""The quick-ask hotkey setting: it has a default and round-trips through PUT /settings (string only).

Run: zsh tests/e2e/run-pytest.sh backend/tests/test_quickask.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="asktest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod, llm  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})


def test_default_is_alt_space_and_distinct_from_the_other_hotkeys() -> None:
    d = llm.DEFAULT_SETTINGS
    assert d["quickAskShortcut"] == "Alt+Space"
    assert len({d["gatherShortcut"], d["quickCaptureShortcut"], d["quickAskShortcut"]}) == 3
    assert client.get("/settings").json()["quickAskShortcut"] == "Alt+Space"


def test_shortcut_round_trips_and_rejects_a_non_string() -> None:
    assert client.put("/settings", json={"quickAskShortcut": "Control+Alt+K"}).status_code == 200
    assert client.get("/settings").json()["quickAskShortcut"] == "Control+Alt+K"
    assert client.put("/settings", json={"quickAskShortcut": 5}).status_code >= 400
    client.put("/settings", json={"quickAskShortcut": "Alt+Space"})
