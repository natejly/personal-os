"""CORS: only sandboxed widget iframes (opaque "null" origin) may read a data-source fetch; nothing else.

Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_cors.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="corstest-"))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app  # noqa: E402

client = TestClient(app)
AUTH = {"X-Personal-OS-Token": AUTH_TOKEN}


class CorsTests(unittest.TestCase):
    def test_null_origin_not_allowed_on_ordinary_routes(self) -> None:
        r = client.get("/todos", headers={**AUTH, "Origin": "null"})
        self.assertNotIn("access-control-allow-origin", r.headers)
        r = client.options("/todos", headers={"Origin": "null", "Access-Control-Request-Method": "GET"})
        self.assertNotEqual(r.headers.get("access-control-allow-origin"), "null")

    def test_null_origin_allowed_for_widget_source_fetch_only(self) -> None:
        r = client.get("/sources/nope/fetch", headers={"Origin": "null"})
        self.assertEqual(r.headers.get("access-control-allow-origin"), "null")
        r = client.post("/sources/nope/fetch", headers={"Origin": "null"})
        self.assertNotIn("access-control-allow-origin", r.headers)

    def test_app_origins_still_work(self) -> None:
        r = client.get("/todos", headers={**AUTH, "Origin": "http://localhost:5173"})
        self.assertEqual(r.headers.get("access-control-allow-origin"), "http://localhost:5173")


if __name__ == "__main__":
    unittest.main()
