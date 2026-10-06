"""Host-list settings are bare hostnames on PUT /settings, and fetch_url ignores junk stored before that check.

Run: python backend/tests/test_settings_lists.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="settingslists-"))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import tools  # noqa: E402
from personal_os.app import AUTH_TOKEN, HOST_LIST_SETTINGS, app  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
KEYS = ("fetchAllowlist", "shellAllowedDomains", "browserAllowlist")


class HostListTests(unittest.TestCase):
    def tearDown(self) -> None:
        client.put("/settings", json={k: [] for k in KEYS})

    def test_every_host_list_is_checked(self) -> None:
        self.assertEqual(set(KEYS), HOST_LIST_SETTINGS)

    def test_rejects_anything_but_a_hostname(self) -> None:
        for k in KEYS:
            for bad in ("https://x.com/a", "x.com/a", "*", "*.x.com", "com", "10.0.0.1", "x.com:443", "me@x.com", 5):
                r = client.put("/settings", json={k: ["ok.com", bad]})
                self.assertEqual(r.status_code, 422, (k, bad))
            self.assertEqual(client.get("/settings").json()[k], [], k)

    def test_stores_the_normalized_host(self) -> None:
        for k in KEYS:
            r = client.put("/settings", json={k: ["X.com", " .Docs.Example.org ", "x.com"]})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()[k], ["x.com", "docs.example.org"])

    def test_unattended_approvals_round_trips_and_rejects_junk(self) -> None:
        self.assertEqual(client.put("/settings", json={"unattendedApprovals": "ask"}).json()["unattendedApprovals"], "ask")
        self.assertEqual(client.put("/settings", json={"unattendedApprovals": "always"}).status_code, 422)
        self.assertEqual(client.put("/settings", json={"unattendedApprovals": "deny"}).json()["unattendedApprovals"], "deny")

    def test_snapshot_switch_round_trips_and_availability_is_reported_not_stored(self) -> None:
        r = client.put("/settings", json={"snapshotsEnabled": False, "snapshotsAvailable": False})
        self.assertIs(r.json()["snapshotsEnabled"], False)
        self.assertIsInstance(r.json()["snapshotsAvailable"], bool)
        client.put("/settings", json={"snapshotsEnabled": True})


class FetchAllowlistTests(unittest.TestCase):
    def test_a_previously_stored_junk_entry_does_not_widen_the_list(self) -> None:
        got = tools._allowed_hosts({"fetchAllowlist": ["com", "*", "https://x.com/a", ".Docs.example.org", "ok.com"]})
        self.assertEqual(got, {"docs.example.org", "ok.com"})

    def test_hint_names_the_settings_control(self) -> None:
        self.assertIn("Allowed hosts", tools.TAINTED_HINT)


if __name__ == "__main__":
    unittest.main()
