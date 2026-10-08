"""The permissions store (permissions.py) and migration 5, which folds the legacy top-level permission keys into it.

Run: python backend/tests/test_permissions_store.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import migrations, permissions  # noqa: E402
from personal_os.db import Database  # noqa: E402

LEGACY = {
    "tools": {"web_search": "off", "calendar_delete": "on"},
    "alwaysAsk": ["gmail_send"],
    "skipPermissions": True,
    "permissionRules": {"allow": [], "ask": [], "deny": ["web_search"]},
    "fetchAllowlist": ["example.com"],
    "docEditMode": "apply",
}


def _rows(path: Path) -> dict[str, object]:
    with sqlite3.connect(path) as c:
        return {k: json.loads(v) for k, v in c.execute("SELECT key, value FROM settings")}


def _legacy_db() -> Path:
    """A database as a build before migration 5 left it: permission keys as top-level settings rows, user_version 4."""
    d = Path(tempfile.mkdtemp(prefix="permstore-legacy-"))
    db = Database(d)
    with sqlite3.connect(db.path) as c:
        c.execute("DELETE FROM settings")
        c.executemany("INSERT INTO settings(key, value) VALUES(?, ?)",
                      [(k, json.dumps(v)) for k, v in {**LEGACY, "theme": "light"}.items()])
        c.execute("PRAGMA user_version = 4")
    return d


# The app module opens its database at import: point it at a legacy one so the migration runs for real.
LEGACY_DIR = _legacy_db()
os.environ["PERSONAL_OS_DATA_DIR"] = str(LEGACY_DIR)

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as app_mod, permrules, tools  # noqa: E402

client = TestClient(app_mod.app, headers={"X-Personal-OS-Token": app_mod.AUTH_TOKEN})


class FreshDatabase(unittest.TestCase):
    def test_fresh_database_gets_an_empty_versioned_store_and_the_defaults(self) -> None:
        db = Database(tempfile.mkdtemp(prefix="permstore-fresh-"))
        rows = _rows(db.path)
        self.assertEqual(rows[permissions.KEY], {"version": permissions.VERSION, "permissionMode": "auto"})
        self.assertFalse(permissions.KEYS & set(rows))
        self.assertEqual(permissions.load(db.get_settings()), permissions.DEFAULTS)


class Migration(unittest.TestCase):
    def test_legacy_keys_are_folded_and_removed(self) -> None:
        with sqlite3.connect(app_mod.db.path) as c:
            self.assertEqual(migrations.current(c), migrations.latest())
        rows = _rows(app_mod.db.path)
        self.assertFalse(set(LEGACY) & set(rows), "legacy top-level rows survived the migration")
        self.assertEqual(rows["theme"], "light")  # not a permission: untouched
        stored = rows[permissions.KEY]
        self.assertEqual(stored["version"], permissions.VERSION)
        for k, v in LEGACY.items():
            self.assertEqual(stored[k], v, k)

    def test_migration_is_a_fold_not_a_reset(self) -> None:
        """Run on its own over a store that already holds a value, a stray legacy row is folded over it."""
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        c.execute("INSERT INTO settings VALUES ('permissions', ?)", (json.dumps({"version": 1, "docEditMode": "apply"}),))
        c.execute("INSERT INTO settings VALUES ('shellNetwork', 'true')")
        permissions.migrate(c)
        got = dict(c.execute("SELECT key, value FROM settings").fetchall())
        self.assertEqual(set(got), {"permissions"})
        self.assertEqual(json.loads(got["permissions"]), {"version": permissions.VERSION, "docEditMode": "apply", "shellNetwork": True})


class GatesReadTheSameValues(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = app_mod.settings()

    def test_flattened_and_nested_views_agree(self) -> None:
        for k in LEGACY:
            self.assertEqual(self.cfg[k], LEGACY[k], k)
            self.assertEqual(self.cfg[permissions.KEY][k], LEGACY[k], k)
            self.assertEqual(permissions.get(self.cfg, k), LEGACY[k], k)

    def test_tool_mode(self) -> None:
        modes = app_mod.toolbox.effective(permissions.get(self.cfg, "tools"), None, None)
        self.assertEqual(modes["web_search"], "off")

    def test_always_ask_cap(self) -> None:
        # calendar_delete left Always ask, so the stored 'on' stands; gmail_send is still capped at ask.
        self.assertIn("gmail_send", app_mod.toolbox.always_ask())
        self.assertNotIn("calendar_delete", app_mod.toolbox.always_ask())
        modes = app_mod.toolbox.effective({**permissions.get(self.cfg, "tools"), "gmail_send": "on"}, None, None)
        self.assertEqual(modes["calendar_delete"], "on")
        self.assertEqual(modes["gmail_send"], "ask")

    def test_skip_permissions(self) -> None:
        # skipPermissions is legacy: only the global permissionMode skips cards.
        self.assertFalse(permrules.skip_permissions_on({}, self.cfg))
        self.assertTrue(permrules.skip_permissions_on({}, {**self.cfg, "permissionMode": "allow_all"}))

    def test_permission_rule(self) -> None:
        rules = permrules.load_rules(permissions.get(self.cfg, "permissionRules"))
        res = permrules.resolve("web_search", {"query": "x"}, "on", False, rules=rules)
        self.assertIsNotNone(res.refusal)

    def test_fetch_allowlist(self) -> None:
        self.assertEqual(tools._allowed_hosts(self.cfg), {"example.com"})


class Writes(unittest.TestCase):
    def tearDown(self) -> None:
        client.put("/settings", json={"docEditMode": "apply", "unattendedApprovals": "deny", "trustExternalContent": False})

    def test_legacy_put_lands_in_the_store_and_reads_back_flat(self) -> None:
        r = client.put("/settings", json={"docEditMode": "apply"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["docEditMode"], "apply")
        self.assertEqual(r.json()[permissions.KEY]["docEditMode"], "apply")
        rows = _rows(app_mod.db.path)
        self.assertNotIn("docEditMode", rows)
        self.assertEqual(rows[permissions.KEY]["docEditMode"], "apply")

    def test_nested_put_and_validation(self) -> None:
        r = client.put("/settings", json={permissions.KEY: {"unattendedApprovals": "ask"}})
        self.assertEqual(r.json()["unattendedApprovals"], "ask")
        self.assertEqual(client.put("/settings", json={permissions.KEY: {"docEditMode": "sometimes"}}).status_code, 422)
        self.assertEqual(client.put("/settings", json={"tools": "x"}).status_code, 422)
        # A top-level key wins over the nested copy a client read and sent back unchanged.
        r = client.put("/settings", json={"docEditMode": "apply", permissions.KEY: {"docEditMode": "review"}})
        self.assertEqual(r.json()["docEditMode"], "apply")

    def test_a_stray_legacy_row_is_newest_and_the_next_save_folds_it(self) -> None:
        app_mod.db.set_settings({"unattendedApprovals": "ask"})  # an old code path writing top-level
        self.assertEqual(app_mod.settings()["unattendedApprovals"], "ask")
        permissions.save(app_mod.db, {"docEditMode": "apply"})
        rows = _rows(app_mod.db.path)
        self.assertNotIn("unattendedApprovals", rows)
        self.assertEqual(rows[permissions.KEY]["unattendedApprovals"], "ask")

    def test_update_merges_into_the_current_value(self) -> None:
        permissions.update(app_mod.db, lambda cur: {"fetchAllowlist": [*cur["fetchAllowlist"], "docs.example.org"]})
        self.assertEqual(app_mod.settings()["fetchAllowlist"], ["example.com", "docs.example.org"])
        permissions.save(app_mod.db, {"fetchAllowlist": ["example.com"]})

    def test_trust_external_content_defaults_off_and_round_trips(self) -> None:
        self.assertFalse(permissions.DEFAULTS["trustExternalContent"])
        r = client.put("/settings", json={"trustExternalContent": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["trustExternalContent"])
        self.assertTrue(r.json()[permissions.KEY]["trustExternalContent"])
        self.assertEqual(_rows(app_mod.db.path)[permissions.KEY]["trustExternalContent"], True)
        self.assertFalse(client.put("/settings", json={"trustExternalContent": "yes"}).status_code == 200)
        r = client.put("/settings", json={"trustExternalContent": False})
        self.assertFalse(r.json()["trustExternalContent"])


if __name__ == "__main__":
    unittest.main()
