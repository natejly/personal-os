"""The agent-loop tunables as settings: defaults, ranges, validation, the stuck thresholds, the plan loop's bounds.

Run: python -m pytest backend/tests/test_loop_settings.py
(The loops' behaviour under each setting is exercised in test_agent_loop.py.)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="loopsettings-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import limits, llm, stuck, working  # noqa: E402
from personal_os.runtime.plan_loop import PlanExecuteVerify  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})

DEFAULTS = {"loopMaxRounds": 0, "loopModelCalls": 60, "loopRepeatLimit": 5, "loopToolErrorLimit": 3, "loopDoomLimit": 3,
            "finalRoundSeconds": 90, "toolResultInlineChars": 12000, "planLoopMaxSteps": 8, "planLoopVerifyPasses": 1}
RANGES = {"loopMaxRounds": (0, 200), "loopModelCalls": (5, 500), "loopRepeatLimit": (2, 20), "loopToolErrorLimit": (1, 10),
          "loopDoomLimit": (1, 10), "finalRoundSeconds": (10, 600), "toolResultInlineChars": (2000, 60000),
          "planLoopMaxSteps": (2, 20), "planLoopVerifyPasses": (0, 3)}


def test_defaults_equal_todays_constants() -> None:
    for k, v in DEFAULTS.items():
        assert llm.DEFAULT_SETTINGS[k] == v and limits.LOOP_DEFAULTS[k] == v, k
    assert llm.DEFAULT_SETTINGS["stuckDetection"] == "standard"
    assert (limits.REPEAT_LIMIT, limits.TOOL_ERROR_LIMIT, limits.LOOP_MODEL_CALLS, limits.FINAL_ROUND_SECONDS) == (5, 3, 60, 90.0)
    assert working.INLINE_CHARS == 12000


def test_ranges_match_the_contract_and_clamp_on_read() -> None:
    for k, (lo, hi) in RANGES.items():
        assert limits.RANGES[k] == (lo, hi)
        assert limits.loop_setting({k: lo - 1000}, k) == lo and limits.loop_setting({k: hi + 1000}, k) == hi
        assert limits.loop_setting({k: (lo + hi) // 2}, k) == (lo + hi) // 2
        for junk in (None, "x", True, float("nan"), float("inf")):
            assert limits.loop_setting({k: junk}, k) == DEFAULTS[k], (k, junk)
        assert limits.loop_setting({}, k) == DEFAULTS[k]


def test_put_settings_validates_the_range_and_the_enum() -> None:
    for k, (lo, hi) in RANGES.items():
        assert client.put("/settings", json={k: hi + 1}).status_code == 422, k
        assert client.put("/settings", json={k: lo}).status_code == 200, k
    assert client.put("/settings", json={"stuckDetection": "strict"}).status_code == 200
    assert client.put("/settings", json={"stuckDetection": "paranoid"}).status_code == 422
    assert client.get("/settings").json()["stuckDetection"] == "strict"
    client.put("/settings", json={**DEFAULTS, "stuckDetection": "standard"})


def test_stuck_thresholds_per_mode() -> None:
    std = stuck.limits_for("standard")
    assert std == stuck.Limits() and stuck.limits_for(None) == std and stuck.limits_for("bogus") == std
    assert stuck.limits_for("off") is None and stuck.detector_for("off").enabled is False
    rel, strict = stuck.limits_for("relaxed"), stuck.limits_for("strict")
    for f in ("same_result", "error_cycle", "alternations", "error_storm", "not_run"):
        assert getattr(rel, f) == getattr(std, f) * 2
        assert getattr(strict, f) == max(2, getattr(std, f) - 1)
    assert strict.error_cycle == 2


def test_an_off_detector_never_flags_but_still_counts_repeats() -> None:
    d = stuck.detector_for("off")
    for _ in range(10):
        d.observe("t", {"a": 1}, {"ok": 1})
    assert d.check() is None and d.repeat_count("t", {"a": 1}) == 10
    s = stuck.detector_for("standard")
    for _ in range(4):
        s.observe("t", {"a": 1}, {"ok": 1})
    assert s.check() is not None


def test_inline_chars_falls_back_to_the_module_default_without_settings() -> None:
    blob = {"t": "q" * 5000}
    cid = appmod.convos.create(None, "x", "m")["id"]
    whole, rid = appmod.tool_results.render(cid, None, "t", blob)
    assert rid is None
    small, rid2 = appmod.tool_results.render(cid, None, "t", blob, inline_chars=2000)
    assert rid2 and "result_id" in small


def test_plan_loop_bounds_follow_the_settings() -> None:
    assert dict(PlanExecuteVerify.turn_bounds({})) == dict(PlanExecuteVerify.bounds)
    assert PlanExecuteVerify.turn_bounds({"planLoopVerifyPasses": 0})["verify"] == 1
    assert PlanExecuteVerify.turn_bounds({"planLoopVerifyPasses": 3})["verify"] == 4
    assert PlanExecuteVerify.turn_bounds({"planLoopVerifyPasses": 99})["verify"] == 4
    assert appmod._loop_bounds(PlanExecuteVerify(), {"planLoopVerifyPasses": 2})["verify"] == 3
    fixed = type("L", (), {"bounds": {"x": 1}})()
    assert appmod._loop_bounds(fixed, {}) == {"x": 1}
