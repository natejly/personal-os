"""uiZoom: a whole-window percent, 80-160, rejected outside that range."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="zoomtest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})


def test_uizoom_setting_validated():
    assert client.get("/settings").json()["uiZoom"] == 110
    assert client.put("/settings", json={"uiZoom": 125}).status_code == 200
    assert client.get("/settings").json()["uiZoom"] == 125
    for bad in (79, 161, "big", True):
        assert client.put("/settings", json={"uiZoom": bad}).status_code == 422
    assert client.get("/settings").json()["uiZoom"] == 125
    client.put("/settings", json={"uiZoom": 110})
