"""Meetings and Activity ship on: the defaults, and migration 6 for databases that predate them.

Run: python -m unittest backend/tests/test_meetings_activity_defaults.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import activity, llm, meetings, migrations  # noqa: E402


def _run(rows: dict[str, object]) -> dict[str, object]:
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    c.executemany("INSERT INTO settings VALUES (?, ?)", [(k, json.dumps(v)) for k, v in rows.items()])
    migrations._meetings_activity_defaults(c)
    return {k: json.loads(v) for k, v in c.execute("SELECT key, value FROM settings")}


class DefaultsTests(unittest.TestCase):
    def test_both_ship_on_and_recording_stays_gated(self) -> None:
        self.assertTrue(activity.DEFAULT_CONFIG["enabled"])
        self.assertTrue(meetings.DEFAULT_CONFIG["enabled"])
        self.assertEqual(meetings.DEFAULT_CONFIG["consentedAt"], 0.0)  # nothing records before the notice
        self.assertFalse(meetings.DEFAULT_CONFIG["autoRecord"])
        self.assertEqual(activity.DEFAULT_CONFIG["retentionHours"], 48)
        self.assertFalse(activity.DEFAULT_CONFIG["signals"]["micAudio"])
        self.assertFalse(activity.DEFAULT_CONFIG["signals"]["text"])
        self.assertEqual(llm.DEFAULT_SETTINGS["hiddenViews"], [])
        self.assertTrue(llm.DEFAULT_SETTINGS["digest"]["enabled"])


class MigrationTests(unittest.TestCase):
    def test_bare_stub_is_flipped_on(self) -> None:
        out = _run({"activity": {"enabled": False}, "meetings": {"enabled": False}})
        self.assertEqual(out, {"activity": {"enabled": True}, "meetings": {"enabled": True}})

    def test_missing_rows_stay_missing(self) -> None:
        self.assertEqual(_run({}), {})

    def test_a_full_row_left_off_stays_off(self) -> None:
        # What Stop writes: the whole config with enabled false. The user was there and turned it off.
        stopped = {**activity.DEFAULT_CONFIG, "enabled": False}
        consented = {**meetings.DEFAULT_CONFIG, "enabled": False, "consentedAt": 1700000000.0}
        out = _run({"activity": stopped, "meetings": consented})
        self.assertFalse(out["activity"]["enabled"])
        self.assertFalse(out["meetings"]["enabled"])

    def test_on_rows_are_untouched(self) -> None:
        on = {**meetings.DEFAULT_CONFIG, "enabled": True, "micDevice": "1"}
        self.assertEqual(_run({"meetings": on})["meetings"], on)


if __name__ == "__main__":
    unittest.main()
