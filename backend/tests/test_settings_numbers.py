"""Numeric settings are validated on PUT /settings; old budget keys are dropped, not stored.

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

from personal_os import limits, llm  # noqa: E402
from personal_os.app import AUTH_TOKEN, RunMeter, app  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})


class PutSettingsTests(unittest.TestCase):
    def tearDown(self) -> None:
        client.put("/settings", json={"llmRetries": llm.DEFAULT_SETTINGS["llmRetries"]})

    def test_rejects_values_that_would_mean_unlimited_or_crash(self) -> None:
        # A negative, a string or a bool must never be stored (0 is the "automatic" value, accepted below).
        for bad in (-3, "abc", None, True, 1e9):
            r = client.put("/settings", json={"llmRetries": bad})
            self.assertEqual(r.status_code, 422, bad)
        self.assertEqual(client.get("/settings").json()["llmRetries"], llm.DEFAULT_SETTINGS["llmRetries"])

    def test_accepts_and_normalises_a_valid_number(self) -> None:
        r = client.put("/settings", json={"llmRetries": 7.6})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["llmRetries"], 7)

    def test_zero_means_automatic_for_the_derived_keys(self) -> None:
        for key in limits.AUTOMATIC:
            r = client.put("/settings", json={key: 0})
            self.assertEqual(r.status_code, 200, key)
            self.assertEqual(r.json()[key], 0, key)
        self.assertEqual(client.put("/settings", json={"llmRetries": 0}).json()["llmRetries"], 0)
        self.assertEqual(client.put("/settings", json={"compactKeepRecent": 0}).status_code, 422, "0 is only automatic for the derived keys")

    def test_a_rejected_patch_writes_nothing(self) -> None:
        r = client.put("/settings", json={"llmRetries": "x"})
        self.assertEqual(r.status_code, 422)


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


class RetrievalSettingsTests(unittest.TestCase):
    KEYS = ("retrievalMinSimilarity", "retrievalPerDocCap", "retrievalCandidates", "fetchCacheSeconds", "retrievalMode")

    def tearDown(self) -> None:
        client.put("/settings", json={k: llm.DEFAULT_SETTINGS[k] for k in self.KEYS})

    def test_retrieval_settings_are_validated(self) -> None:
        for key, bad in (("retrievalMinSimilarity", 2), ("retrievalPerDocCap", 0), ("retrievalCandidates", 500),
                         ("fetchCacheSeconds", -1), ("retrievalMode", "keyword")):
            self.assertEqual(client.put("/settings", json={key: bad}).status_code, 422, (key, bad))
        r = client.put("/settings", json={"retrievalMinSimilarity": 0.4, "retrievalPerDocCap": 5, "retrievalCandidates": 30,
                                          "fetchCacheSeconds": 600, "retrievalMode": "bm25"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["retrievalMode"], r.json()["retrievalPerDocCap"]), ("bm25", 5))


class RemovedBudgetTests(unittest.TestCase):
    OLD = {"maxToolRounds": 25, "maxRunTokens": 1000, "maxRunSeconds": 5, "subagentMaxRounds": 3, "deskMaxTurns": 2,
           "codingSessionTimeoutMinutes": 9, "contextBudget": {"memories": 1}, "skillsInlineBudget": 10,
           "usageAlerts": {"dailyCost": 1, "monthlyCost": 1}}

    def test_old_budget_keys_are_accepted_and_not_stored(self) -> None:
        r = client.put("/settings", json={**self.OLD, "llmRetries": 2})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["llmRetries"], 2)
        for k in self.OLD:
            self.assertNotIn(k, r.json(), k)
            self.assertNotIn(k, client.get("/settings").json(), k)

    def test_no_default_or_range_names_a_budget(self) -> None:
        for k in self.OLD:
            self.assertNotIn(k, llm.DEFAULT_SETTINGS, k)
            self.assertNotIn(k, limits.RANGES, k)
        self.assertNotIn("maxToolRounds", limits.AUTOMATIC)

    def test_the_meter_counts_and_never_limits(self) -> None:
        m = RunMeter()
        m.add(100, 50, 0.5)
        m.add(10, 5, None)
        snap = m.snapshot()
        self.assertEqual((snap["tokens"], snap["cost"]), (165, 0.5))
        self.assertFalse([k for k in snap if k.startswith("max_")])


if __name__ == "__main__":
    unittest.main()
