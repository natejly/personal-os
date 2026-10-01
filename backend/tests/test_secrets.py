"""Secret storage: file backend, plaintext migration, GET /settings redaction, update semantics.

Run: python backend/tests/test_secrets.py
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GRAIN_SECRETS_BACKEND"] = "file"
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="secrets-"))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.mcp_servers import McpServers  # noqa: E402
from personal_os.secrets import SecretStore  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
KEY = "sk-live-SECRET-123"


def mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


class FileBackendTests(unittest.TestCase):
    def test_roundtrip_delete_and_perms(self) -> None:
        d = Path(tempfile.mkdtemp())
        s = SecretStore(d, backend="file")
        self.assertIsNone(s.get("a"))
        s.set("a", "one\nüñí'\"")
        self.assertEqual(SecretStore(d, backend="file").get("a"), "one\nüñí'\"")  # survives a fresh process
        self.assertEqual(mode(d / ".secrets.json"), 0o600)
        s.delete("a")
        self.assertIsNone(SecretStore(d, backend="file").get("a"))

    def test_empty_value_deletes(self) -> None:
        d = Path(tempfile.mkdtemp())
        s = SecretStore(d, backend="file")
        s.set("a", "x")
        s.set("a", "")
        self.assertIsNone(s.get("a"))


class MigrationTests(unittest.TestCase):
    def test_plaintext_moves_out_of_sqlite_and_is_idempotent(self) -> None:
        d = Path(tempfile.mkdtemp())
        db = Database(d)
        with db.tx() as c:
            for k, v in {"apiKey": KEY, "googleClientSecret": "GOCSPX-1",
                         "googleToken": {"token": "at", "refresh_token": "rt", "client_secret": "cs", "email": "a@b.c"}}.items():
                c.execute("INSERT INTO settings(key, value) VALUES(?, ?)", (k, json.dumps(v)))
        for _ in range(2):  # second open must change nothing
            db = Database(d)
            with db.tx() as c:
                raw = json.dumps([tuple(r) for r in c.execute("SELECT key, value FROM settings")])
            for needle in (KEY, "GOCSPX-1", '\\"rt\\"', '\\"cs\\"'):
                self.assertNotIn(needle, raw)
            s = db.get_settings()
            self.assertEqual(s["apiKey"], KEY)
            self.assertEqual(s["googleClientSecret"], "GOCSPX-1")
            self.assertEqual(s["googleToken"], {"token": "at", "refresh_token": "rt", "client_secret": "cs", "email": "a@b.c"})

    def test_mcp_secrets_migrate(self) -> None:
        d = Path(tempfile.mkdtemp())
        db = Database(d)
        mcp = McpServers(db)
        srv = mcp.create_server("gh", secrets={"TOKEN": "ghp_abc"})
        self.assertEqual(srv["secret_keys"], ["TOKEN"])
        with db.tx() as c:
            col = c.execute("SELECT secrets FROM mcp_servers").fetchone()[0]
        self.assertNotIn("ghp_abc", col)
        self.assertEqual(mcp.launch_env(srv["id"])["TOKEN"], "ghp_abc")
        # legacy row with the values in the column
        with db.tx() as c:
            c.execute("UPDATE mcp_servers SET secrets=?", (json.dumps({"TOKEN": "ghp_abc", "B": "two"}),))
        db.secrets.delete(f"mcp:{srv['id']}")
        db2 = Database(d)
        mcp = McpServers(db2)
        self.assertEqual(mcp.launch_env(srv["id"]), {"TOKEN": "ghp_abc", "B": "two"})
        with db2.tx() as c:
            self.assertNotIn("two", c.execute("SELECT secrets FROM mcp_servers").fetchone()[0])
        mcp.update_server(srv["id"], {"clear_secrets": ["B"], "secrets": {"TOKEN": ""}})
        self.assertEqual(mcp.launch_env(srv["id"]), {"TOKEN": "ghp_abc"})
        mcp.delete_server(srv["id"])
        self.assertIsNone(db2.secrets.get(f"mcp:{srv['id']}"))


class PermissionTests(unittest.TestCase):
    def test_data_dir_and_db_are_owner_only(self) -> None:
        d = Path(tempfile.mkdtemp())
        Database(d)
        self.assertEqual(mode(d), 0o700)
        self.assertEqual(mode(d / "personal-os.db"), 0o600)


class SettingsRouteTests(unittest.TestCase):
    def tearDown(self) -> None:
        client.put("/settings", json={"apiKey": None, "braveApiKey": None})

    def test_get_never_returns_secret_values(self) -> None:
        client.put("/settings", json={"apiKey": KEY, "braveApiKey": "BSA-xyz"})
        body = client.get("/settings")
        self.assertNotIn(KEY, body.text)
        self.assertNotIn("BSA-xyz", body.text)
        j = body.json()
        self.assertTrue(j["apiKeySet"])
        self.assertTrue(j["braveApiKeySet"])
        self.assertFalse(j["googleClientSecretSet"])
        self.assertEqual(j["apiKey"], "")

    def test_update_semantics(self) -> None:
        from personal_os.app import settings
        client.put("/settings", json={"apiKey": KEY})
        self.assertEqual(settings()["apiKey"], KEY)
        r = client.put("/settings", json={"apiKey": "", "theme": "light"})  # blank = unchanged
        self.assertEqual(settings()["apiKey"], KEY)
        self.assertTrue(r.json()["apiKeySet"])
        client.put("/settings", json={"theme": "dark"})  # absent = unchanged
        self.assertEqual(settings()["apiKey"], KEY)
        client.put("/settings", json={"apiKey": "sk-new"})  # replace
        self.assertEqual(settings()["apiKey"], "sk-new")
        r = client.put("/settings", json={"apiKey": None})  # null clears
        self.assertFalse(r.json()["apiKeySet"])
        self.assertEqual(settings()["apiKey"], "")
        self.assertEqual(client.put("/settings", json={"apiKey": 5}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
