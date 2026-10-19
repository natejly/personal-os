"""The `mascot` setting (which creature draws the faces): defaults to the Grain mascot and round-trips through /settings.

Run: cd backend && .venv/bin/python -m pytest -q tests/test_mascot_setting.py
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GRAIN_SECRETS_BACKEND", "file")
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="mascot-"))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})


def test_mascot_defaults_to_grain_and_round_trips() -> None:
    assert client.get("/settings").json()["mascot"] == "grain"
    for choice in ("blobs", "off", "grain"):
        assert client.put("/settings", json={"mascot": choice}).json()["mascot"] == choice
        assert client.get("/settings").json()["mascot"] == choice
