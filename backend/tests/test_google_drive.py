"""Drive query building and file/content shaping (no network; the discovery service is stubbed).

Run: python backend/tests/test_google_drive.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.google import Google, _drive_query  # noqa: E402


class _Req:
    def __init__(self, result: Any):
        self._result = result

    def execute(self) -> Any:
        return self._result


class _Files:
    """Stands in for drive.files(); records the kwargs of every call."""

    def __init__(self, results: dict[str, Any]):
        self.results, self.calls = results, {}

    def list(self, **kw: Any) -> _Req:
        self.calls["list"] = kw
        return _Req(self.results.get("list", {"files": []}))

    def get(self, **kw: Any) -> _Req:
        self.calls["get"] = kw
        return _Req(self.results.get("get", {}))

    def export(self, **kw: Any) -> _Req:
        self.calls["export"] = kw
        return _Req(self.results.get("export", b""))

    def get_media(self, **kw: Any) -> _Req:
        self.calls["get_media"] = kw
        return _Req(self.results.get("get_media", b""))


def _google_with(files: _Files) -> Google:
    g = Google(dict, lambda _s: None)

    class _Svc:
        def files(self) -> _Files:
            return files

    g._svc = lambda name, version: _Svc()  # type: ignore[method-assign]
    return g


class DriveQueryTests(unittest.TestCase):
    def test_empty_query_lists_untrashed(self) -> None:
        self.assertEqual(_drive_query(""), "trashed = false")
        self.assertEqual(_drive_query("   "), "trashed = false")

    def test_query_searches_name_and_content(self) -> None:
        q = _drive_query("quarterly report")
        self.assertIn("name contains 'quarterly report'", q)
        self.assertIn("fullText contains 'quarterly report'", q)
        self.assertTrue(q.startswith("trashed = false and "))

    def test_quotes_and_backslashes_are_escaped(self) -> None:
        self.assertIn(r"name contains 'O\'Brien'", _drive_query("O'Brien"))
        self.assertIn(r"contains 'a\\b'", _drive_query("a\\b"))


class DriveFilesTests(unittest.TestCase):
    def test_recent_listing_orders_by_modified(self) -> None:
        files = _Files({"list": {"files": []}})
        _google_with(files).drive_files("")
        self.assertEqual(files.calls["list"]["orderBy"], "modifiedTime desc")

    def test_search_omits_order_by(self) -> None:
        # Drive rejects orderBy combined with fullText queries.
        files = _Files({"list": {"files": []}})
        _google_with(files).drive_files("notes")
        self.assertNotIn("orderBy", files.calls["list"])

    def test_rows_are_shaped(self) -> None:
        files = _Files({"list": {"files": [{
            "id": "f1", "name": "Plan.gdoc", "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2026-09-29T10:00:00Z", "webViewLink": "https://docs.google.com/x",
            "size": "123", "owners": [{"displayName": "Nate", "me": True}],
        }]}})
        rows = _google_with(files).drive_files("")
        self.assertEqual(rows, [{
            "id": "f1", "name": "Plan.gdoc", "mime_type": "application/vnd.google-apps.document",
            "modified": "2026-09-29T10:00:00Z", "link": "https://docs.google.com/x",
            "size": 123, "owner": "me",
        }])


class DriveReadTests(unittest.TestCase):
    def test_google_doc_is_exported_as_text(self) -> None:
        files = _Files({
            "get": {"id": "f1", "name": "Plan", "mimeType": "application/vnd.google-apps.document", "webViewLink": "l"},
            "export": "hello".encode(),
        })
        out = _google_with(files).drive_read("f1")
        self.assertEqual(out["content"], "hello")
        self.assertEqual(files.calls["export"]["mimeType"], "text/plain")
        self.assertFalse(out["truncated"])

    def test_plain_text_is_downloaded_and_truncated(self) -> None:
        files = _Files({
            "get": {"id": "f2", "name": "notes.txt", "mimeType": "text/plain", "webViewLink": "l"},
            "get_media": b"abcdef",
        })
        out = _google_with(files).drive_read("f2", max_chars=3)
        self.assertEqual(out["content"], "abc")
        self.assertTrue(out["truncated"])

    def test_binary_returns_link_only(self) -> None:
        files = _Files({"get": {"id": "f3", "name": "pic.png", "mimeType": "image/png", "webViewLink": "l"}})
        out = _google_with(files).drive_read("f3")
        self.assertIsNone(out["content"])
        self.assertIn("note", out)
        self.assertNotIn("get_media", files.calls)


if __name__ == "__main__":
    unittest.main()
