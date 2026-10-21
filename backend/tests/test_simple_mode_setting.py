"""The `simpleMode` setting (hides advanced options and chat internals): off by default and round-trips through /settings.

Run: cd backend && .venv/bin/python -m pytest -q tests/test_simple_mode_setting.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GRAIN_SECRETS_BACKEND", "file")
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="simple-mode-"))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})


def test_simple_mode_defaults_off_and_round_trips() -> None:
    assert client.get("/settings").json()["simpleMode"] is False
    for choice in (True, False):
        assert client.put("/settings", json={"simpleMode": choice}).json()["simpleMode"] is choice
        assert client.get("/settings").json()["simpleMode"] is choice
