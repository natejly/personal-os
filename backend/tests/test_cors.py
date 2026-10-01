"""CORS: the packaged renderer (file://) and sandboxed widget iframes both send Origin: null, so it must be allowed.

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
    def test_null_origin_allowed_for_packaged_renderer(self) -> None:
        r = client.get("/todos", headers={**AUTH, "Origin": "null"})
        self.assertEqual(r.headers.get("access-control-allow-origin"), "null")
        r = client.options("/todos", headers={"Origin": "null", "Access-Control-Request-Method": "GET",
                                              "Access-Control-Request-Headers": "x-personal-os-token"})
        self.assertEqual(r.headers.get("access-control-allow-origin"), "null")

    def test_null_origin_still_needs_the_token(self) -> None:
        self.assertEqual(client.get("/todos", headers={"Origin": "null"}).status_code, 401)

    def test_foreign_origin_refused(self) -> None:
        r = client.get("/todos", headers={**AUTH, "Origin": "https://evil.example"})
        self.assertNotIn("access-control-allow-origin", r.headers)

    def test_dev_origin_works(self) -> None:
        r = client.get("/todos", headers={**AUTH, "Origin": "http://localhost:5173"})
        self.assertEqual(r.headers.get("access-control-allow-origin"), "http://localhost:5173")


if __name__ == "__main__":
    unittest.main()
