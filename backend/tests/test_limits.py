"""limits.py: the derived window, worker slots and context shares, and old stored values for knobs that became automatic.

Run: python backend/tests/test_limits.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="limits-"))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import limits, llm  # noqa: E402
from personal_os.app import AUTH_TOKEN, app  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
GB = 2**30


def slots_for(cpu: int | None, gb: float | None) -> int:
    limits.worker_slots.cache_clear()
    sysconf = (lambda name: (int(gb * GB) // 4096) if name == "SC_PHYS_PAGES" else 4096) if gb is not None else mock.Mock(side_effect=OSError)
    try:
        with mock.patch("os.cpu_count", return_value=cpu), mock.patch("os.sysconf", sysconf):
            return limits.worker_slots()
    finally:
        limits.worker_slots.cache_clear()


class ContextWindowTests(unittest.TestCase):
    def test_order_stored_then_known_then_fallback(self) -> None:
        self.assertEqual(limits.context_window(50_000, 200_000), 50_000)
        self.assertEqual(limits.context_window(0, 200_000), 200_000)
        self.assertEqual(limits.context_window(500_000, 200_000), 200_000)  # an override only lowers the proxy's figure
        self.assertEqual(limits.context_window(None, None), limits.CONTEXT_WINDOW_FALLBACK)
        self.assertEqual(limits.context_window("junk", -5), limits.CONTEXT_WINDOW_FALLBACK)

    def test_learned_limit_caps_and_floor_holds(self) -> None:
        self.assertEqual(limits.context_window(50_000, None, 20_000), 20_000)
        self.assertEqual(limits.context_window(0, 32_000, 64_000), 32_000)
        self.assertEqual(limits.context_window(0, None, 100), 4096)


class SlotsTests(unittest.TestCase):
    def test_worker_slots_bounds(self) -> None:
        self.assertEqual(slots_for(2, 16), 2)
        self.assertEqual(slots_for(8, 16), 4)
        self.assertEqual(slots_for(64, 256), 8)
        self.assertEqual(slots_for(None, None), 2)
        self.assertGreaterEqual(slots_for(16, None), 2)  # sysconf failing assumes 8 GB

    def test_a_stored_value_overrides(self) -> None:
        self.assertEqual(limits.slots({"parallelReads": 3}, "parallelReads"), 3)
        self.assertEqual(limits.slots({"parallelReads": 0}, "parallelReads"), limits.worker_slots())
        self.assertEqual(limits.slots({}, "deskMaxLive"), limits.worker_slots())


class ContextSharesTests(unittest.TestCase):
    def test_shares_scale_with_the_window_up_to_the_fallback(self) -> None:
        small, mid, big = (limits.context_shares(w) for w in (8_000, limits.CONTEXT_WINDOW_FALLBACK, 1_000_000))
        for k in limits.CONTEXT_SHARES:
            self.assertLess(small[k], mid[k], k)
            self.assertEqual(mid[k], big[k], k)  # a 1M window gets the 128K block sizes
        self.assertEqual(mid["memories"], int(limits.CONTEXT_WINDOW_FALLBACK * limits.CONTEXT_SHARES["memories"]))

    def test_no_budget_constants_remain(self) -> None:
        for name in ("RUN_TOKENS", "RUN_SECONDS", "MAX_ROUNDS_HARD", "JOB_MAX_ROUNDS", "JOB_RUN_TOKENS", "JOB_RUN_SECONDS",
                     "JOB_HARD_SECONDS", "SUBAGENT_MAX_ROUNDS", "DESK_MAX_TURNS", "CODING_SESSION_TIMEOUT_MINUTES",
                     "SKILLS_INLINE_BUDGET", "CONTEXT_BUDGET", "max_rounds"):
            self.assertFalse(hasattr(limits, name), name)
        self.assertGreater(limits.JOB_IDLE_SECONDS, 0)


class StoredValueTests(unittest.TestCase):
    def test_derived_keys_default_to_automatic(self) -> None:
        for k in limits.AUTOMATIC:
            self.assertEqual(llm.DEFAULT_SETTINGS[k], 0, k)

    def test_put_accepts_every_old_value_and_zero(self) -> None:
        for k, v in (("contextWindow", 128000), ("subagentMaxConcurrent", 4), ("deskMaxLive", 4),
                     ("parallelReads", 4), ("stuckDetection", False)):
            self.assertEqual(client.put("/settings", json={k: v}).status_code, 200, k)
        for k in limits.AUTOMATIC:
            client.put("/settings", json={k: 0})
        client.put("/settings", json={"stuckDetection": True})

    def test_every_default_has_a_range_or_is_not_numeric_limit(self) -> None:
        for k in limits.RANGES:
            self.assertIn(k, llm.DEFAULT_SETTINGS, k)


if __name__ == "__main__":
    unittest.main()
