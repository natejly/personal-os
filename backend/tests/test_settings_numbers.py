"""Numeric settings are validated on PUT /settings, and Budget survives junk already stored.

Run: python backend/tests/test_settings_numbers.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="settingsnum-"))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import llm  # noqa: E402
from personal_os.app import AUTH_TOKEN, Budget, _caps, app  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})


class PutSettingsTests(unittest.TestCase):
    def tearDown(self) -> None:
        client.put("/settings", json={"maxToolRounds": llm.DEFAULT_SETTINGS["maxToolRounds"], "maxRunCost": llm.DEFAULT_SETTINGS["maxRunCost"]})

    def test_rejects_values_that_would_mean_unlimited_or_crash(self) -> None:
        # 0 or a negative was "unlimited" to Budget; a string made int() raise on every reply.
        for bad in (0, -3, "abc", None, True, 1e9):
            r = client.put("/settings", json={"maxToolRounds": bad})
            self.assertEqual(r.status_code, 422, bad)
        self.assertEqual(client.get("/settings").json()["maxToolRounds"], llm.DEFAULT_SETTINGS["maxToolRounds"])

    def test_accepts_and_normalises_a_valid_number(self) -> None:
        r = client.put("/settings", json={"maxToolRounds": 7.6, "maxRunCost": 0.25})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["maxToolRounds"], 7)
        self.assertEqual(r.json()["maxRunCost"], 0.25)

    def test_a_rejected_patch_writes_nothing(self) -> None:
        r = client.put("/settings", json={"maxRunCost": 0.3, "maxToolRounds": "x"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(client.get("/settings").json()["maxRunCost"], llm.DEFAULT_SETTINGS["maxRunCost"])


class ContextSettingsTests(unittest.TestCase):
    def tearDown(self) -> None:
        client.put("/settings", json={k: llm.DEFAULT_SETTINGS[k] for k in ("contextWindow", "compactAt", "microAt", "compactKeepRecent", "microKeep")})

    def test_context_settings_are_clamped_to_their_ranges(self) -> None:
        for key, bad in (("contextWindow", 10), ("contextWindow", 5_000_000), ("compactAt", 0), ("compactAt", 1.5),
                         ("microAt", 0.01), ("compactKeepRecent", 1), ("microKeep", -1), ("compactKeepRecent", "x")):
            self.assertEqual(client.put("/settings", json={key: bad}).status_code, 422, (key, bad))
        r = client.put("/settings", json={"contextWindow": 32000, "compactAt": 0.6, "microKeep": 0})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["contextWindow"], 32000)


class BudgetTests(unittest.TestCase):
    def test_junk_stored_before_validation_falls_back_to_defaults(self) -> None:
        b = Budget({"maxToolRounds": "abc", "maxRunTokens": None, "maxRunSeconds": -5, "maxRunCost": "nan"})
        self.assertEqual(b.max_rounds, llm.DEFAULT_SETTINGS["maxToolRounds"])
        self.assertEqual(b.max_tokens, llm.DEFAULT_SETTINGS["maxRunTokens"])
        self.assertEqual(b.max_seconds, llm.DEFAULT_SETTINGS["maxRunSeconds"])
        self.assertEqual(b.max_cost, llm.DEFAULT_SETTINGS["maxRunCost"])

    def test_job_caps_survive_junk(self) -> None:
        self.assertEqual(_caps({"maxToolRounds": "abc"}, {"maxToolRounds": 8})["maxToolRounds"], 8)
        self.assertEqual(_caps({"maxToolRounds": 3}, {"maxToolRounds": 8})["maxToolRounds"], 3)


if __name__ == "__main__":
    unittest.main()
