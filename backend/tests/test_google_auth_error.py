"""A failed Google sign-in is reported on /integrations/google/status, so the app's poll can stop and say why.

Run: python -m pytest backend/tests/test_google_auth_error.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="gauth-"))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as app_mod  # noqa: E402
from personal_os.app import AUTH_TOKEN, app  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})


def test_denied_callback_is_reported_then_cleared_by_a_new_sign_in() -> None:
    assert client.get("/integrations/google/status").json()["last_error"] is None
    client.get("/integrations/google/callback", params={"error": "access_denied"})
    assert "access_denied" in client.get("/integrations/google/status").json()["last_error"]
    client.post("/integrations/google/auth/start")  # may 400 without an OAuth client; it still clears the error
    assert client.get("/integrations/google/status").json()["last_error"] is None
    app_mod._google_auth_error = None
