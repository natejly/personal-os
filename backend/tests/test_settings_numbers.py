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

from personal_os import limits, llm  # noqa: E402
from personal_os.app import AUTH_TOKEN, Budget, _caps, app  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})


class PutSettingsTests(unittest.TestCase):
    def tearDown(self) -> None:
        client.put("/settings", json={"maxToolRounds": llm.DEFAULT_SETTINGS["maxToolRounds"]})

    def test_rejects_values_that_would_mean_unlimited_or_crash(self) -> None:
        # A negative, a string or a bool must never reach Budget (0 is the "automatic" value, accepted below).
        for bad in (-3, "abc", None, True, 1e9):
            r = client.put("/settings", json={"maxToolRounds": bad})
            self.assertEqual(r.status_code, 422, bad)
        self.assertEqual(client.get("/settings").json()["maxToolRounds"], llm.DEFAULT_SETTINGS["maxToolRounds"])

    def test_accepts_and_normalises_a_valid_number(self) -> None:
        r = client.put("/settings", json={"maxToolRounds": 7.6})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["maxToolRounds"], 7)

    def test_zero_means_automatic_for_the_derived_keys(self) -> None:
        for key in limits.AUTOMATIC:
            r = client.put("/settings", json={key: 0})
            self.assertEqual(r.status_code, 200, key)
            self.assertEqual(r.json()[key], 0, key)
        self.assertEqual(client.put("/settings", json={"llmRetries": 0}).json()["llmRetries"], 0)
        self.assertEqual(client.put("/settings", json={"compactKeepRecent": 0}).status_code, 422, "0 is only automatic for the derived keys")

    def test_a_rejected_patch_writes_nothing(self) -> None:
        r = client.put("/settings", json={"maxToolRounds": "x"})
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


class BudgetTests(unittest.TestCase):
    def test_junk_stored_before_validation_falls_back_to_defaults(self) -> None:
        b = Budget({"maxToolRounds": "abc", "maxRunTokens": None, "maxRunSeconds": -5})
        self.assertEqual(b.max_rounds, limits.MAX_ROUNDS_HARD)
        self.assertEqual(b.max_tokens, llm.DEFAULT_SETTINGS["maxRunTokens"])
        self.assertEqual(b.max_seconds, llm.DEFAULT_SETTINGS["maxRunSeconds"])

    def test_stored_round_cap_below_the_hard_one_is_honoured(self) -> None:
        self.assertEqual(Budget({"maxToolRounds": 7}).max_rounds, 7)
        self.assertEqual(Budget({"maxToolRounds": 0}).max_rounds, limits.MAX_ROUNDS_HARD)
        self.assertEqual(Budget(_caps({"maxToolRounds": 0}, {"maxToolRounds": 8})).max_rounds, 8, "a job keeps its 8 rounds")

    def test_job_caps_survive_junk(self) -> None:
        self.assertEqual(_caps({"maxToolRounds": "abc"}, {"maxToolRounds": 8})["maxToolRounds"], 8)
        self.assertEqual(_caps({"maxToolRounds": 3}, {"maxToolRounds": 8})["maxToolRounds"], 3)


if __name__ == "__main__":
    unittest.main()
