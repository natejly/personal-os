"""The two plan-mode rules the cowork suite does not reach.

test_cowork.py covers plan mode where it is visible: a plan card, a desk that cards every change,
withholding writes while one is being drafted, and the digest bind across turns. Two rules are left,
and both are refusals rather than behaviour, so they are easier to assert directly than to drive:

* a `propose` desk may plan an external action and may never perform one;
* an approved plan stops standing in for the card the moment the reply reads something the plan did
  not predict, because the user approved the plan against what the card said it would touch.

Runs under pytest, or directly: python backend/tests/test_plan_mode.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="planmode-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.plans import (MUTATING, PLAN_SAFE_DANGER, PLAN_TOOL, plan_voided_by_taint,  # noqa: E402
                               taint_expected)

passed = 0


def check(cond: bool, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def plan(*, steps: list[dict[str, Any]], expected: list[str] | None = None) -> dict[str, Any]:
    return {"plan_id": "p1", "status": "approved", "expected_taint": expected or [],
            "steps": [{"tool": t, "status": s} for t, s in (x["pair"] for x in steps)]}


def step(tool: str, status: str) -> dict[str, Any]:
    return {"pair": (tool, status)}


# ---------------------------------------------------------------- the tiers


def test_the_tiers_do_not_overlap() -> None:
    """A tier is either safe enough to run while a plan is being drafted, or it is what a plan is
    about. If one were in both, planning would either leak a write or withhold a read."""
    check(not (set(MUTATING) & set(PLAN_SAFE_DANGER)),
          f"a tier cannot be both, got {sorted(set(MUTATING) & set(PLAN_SAFE_DANGER))}")
    check("plan" in PLAN_SAFE_DANGER,
          "propose_plan's own tier has to survive the filter, or planning could never propose a plan")
    check("external" in MUTATING and "writes" in MUTATING, "the two tiers a plan exists to authorise")


def test_an_unknown_tier_is_treated_as_consequential() -> None:
    """A tool whose danger nobody recognised must not slip through the planning filter."""
    check("made_up_tier" not in PLAN_SAFE_DANGER, "an unknown tier is never plan-safe")


# ---------------------------------------------------------------- taint_expected


def test_unexpected_taint_voids_a_web_fetch_as_well_as_mail() -> None:
    check(plan_voided_by_taint("network", True, False) is True, "an unplanned fetch asks again")
    check(plan_voided_by_taint("external", True, False) is True, "an unplanned send asks again")
    check(plan_voided_by_taint("schedules", True, False) is True, "an unplanned schedule asks again")
    check(plan_voided_by_taint("network", True, True) is False, "a fetch the plan predicted still stands")
    check(plan_voided_by_taint("network", False, False) is False, "a clean reply does not void the plan")
    check(plan_voided_by_taint("safe", True, False) is False, "a plain read is not what the plan was authorising")


def test_a_reply_that_read_nothing_untrusted_keeps_its_pre_approval() -> None:
    check(taint_expected(None, []) is True, "no taint, nothing to void - even with no plan at all")
    check(taint_expected(plan(steps=[]), []) is True, "and the same with one")


def test_taint_the_card_predicted_is_still_covered() -> None:
    p = plan(steps=[step("fetch_url", "approved")], expected=["fetch_url"])
    check(taint_expected(p, ["fetch_url"]) is True,
          "the card said it would read that, and the user approved it knowing so")


def test_a_step_that_actually_ran_counts_as_predicted() -> None:
    """The same promise, kept rather than declared: a step the plan contained and the run consumed."""
    p = plan(steps=[step("fetch_url", "consumed")])
    check(taint_expected(p, ["fetch_url"]) is True, "a consumed step is its own prediction")
    for done in ("done", "failed"):
        check(taint_expected(plan(steps=[step("fetch_url", done)]), ["fetch_url"]) is True,
              f"and still is once it has finished as {done}")


def test_taint_from_a_step_that_was_only_proposed_does_not_count() -> None:
    """Approved-but-unclaimed is not evidence the run went there: the digest never matched."""
    p = plan(steps=[step("fetch_url", "approved")])
    check(taint_expected(p, ["fetch_url"]) is False,
          "an unclaimed step predicts nothing; the fetch that tainted this reply was some other call")


def test_one_unpredicted_source_voids_the_whole_pre_approval() -> None:
    p = plan(steps=[step("fetch_url", "consumed")], expected=["fetch_url"])
    check(taint_expected(p, ["fetch_url", "web_search"]) is False,
          "one off-plan read is enough: the user approved the plan against what it said it would touch")


def test_taint_with_no_plan_at_all_is_never_expected() -> None:
    check(taint_expected(None, ["fetch_url"]) is False,
          "nothing predicted it, so nothing pre-approved it")


def test_empty_source_names_are_ignored_rather_than_treated_as_unpredicted() -> None:
    check(taint_expected(plan(steps=[]), ["", None]) is True,  # type: ignore[list-item]
          "a blank source is a bookkeeping artefact, not an unpredicted read")


# ---------------------------------------------------------------- the plan tool itself


def test_the_plan_tool_is_named_once() -> None:
    """app.py, tools.py and mcp_servers.py all key off this, so it is worth pinning."""
    check(PLAN_TOOL == "propose_plan", f"got {PLAN_TOOL!r}")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"ok   {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{passed} assertions passed, {failures} test(s) failed")
    sys.exit(1 if failures else 0)
