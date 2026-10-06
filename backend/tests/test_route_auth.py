"""Data routes require the app token. /health stays open and returns nothing else."""
from __future__ import annotations

import os
import sys
import tempfile

os.environ["PERSONAL_OS_DATA_DIR"] = tempfile.mkdtemp(prefix="authtest-")
os.environ["PERSONAL_OS_AUTH_TOKEN"] = "test-token"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
anon = TestClient(app)


def test_health_is_open_and_leaks_nothing():
    r = anon.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_data_routes_reject_a_missing_token():
    for path in ("/notes", "/memories", "/settings", "/conversations", "/todos", "/docs", "/recap"):
        r = anon.get(path)
        assert r.status_code == 401, path
    assert client.get("/notes", headers={"X-Personal-OS-Token": "nope"}).status_code == 401


def test_google_callback_stays_open_without_completing_sign_in():
    r = anon.get("/integrations/google/callback")
    assert r.status_code == 200
    assert "Connected" not in r.text


def _run() -> int:
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    print(f"\n{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(_run())
