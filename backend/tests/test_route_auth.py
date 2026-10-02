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
    for path in ("/notes", "/memories", "/settings", "/conversations", "/todos", "/docs", "/sources/abc/fetch"):
        r = anon.get(path)
        assert r.status_code == 401, path
    assert client.get("/notes", headers={"X-Personal-OS-Token": "nope"}).status_code == 401


def test_widget_render_requires_the_token():
    made = client.post("/dashboards", json={"name": "Secret board"})
    assert made.status_code == 200, made.text
    wid = client.post(f"/dashboards/{made.json()['id']}/widgets", json={
        "title": "Secret", "kind": "markdown", "code": "SECRET-WIDGET-BODY",
    })
    assert wid.status_code == 200, wid.text
    path = f"/widgets/{wid.json()['id']}/render"
    hidden = anon.get(path)
    assert hidden.status_code == 401
    assert "SECRET-WIDGET-BODY" not in hidden.text
    shown = client.get(path)
    assert shown.status_code == 200
    assert "SECRET-WIDGET-BODY" in shown.text


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
