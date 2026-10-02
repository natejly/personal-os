"""The desk chain rule, the single nudge, the desk manual and the continue message.

Pure decisions, no LLM and no portal: `_chain_kind` reads a desk row and a Run, so both are built by hand.
Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_desk_chain.py   (or via pytest, through conftest)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="deskchain-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.app import _chain_kind, _desk_message, _should_chain  # noqa: E402
from personal_os.cowork import (DESK_CONTINUE, DESK_HINT, DESK_NUDGE, DESK_PLAN_HINT, DESK_RESUME, NOTES_CAP,  # noqa: E402
                                continue_message, desk_manual, read_notes)
from personal_os.runs import Run  # noqa: E402
from personal_os.working import PLAN_FOOTER, plan_block  # noqa: E402

passed = 0
BASE: dict[str, Any] = {"status": "working", "turn": 0, "cost": 0.0, "plan_id": None, "budget": None}


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def run_of(partial: str | None, *, steps: int = 0, tools: int = 0, content: str = "go") -> Run:
    r = Run("unused", input={"content": content})
    r.partial, r.steps_consumed, r.tool_ok = partial, steps, tools
    return r


def test_every_budget_stop_chains_on_progress() -> None:
    for p in ("rounds", "tokens", "time", "cost"):
        check(_chain_kind(BASE, run_of(p, tools=1)) == "continue", f"{p} stop with a clean tool call chains, no plan needed")
        check(_chain_kind(BASE, run_of(p, steps=1)) == "continue", f"{p} stop with a consumed step chains")
        check(_chain_kind(BASE, run_of(p)) is None, f"{p} stop with no progress does not chain")
    check(_chain_kind(BASE, run_of("loop", tools=3)) is None, "a stuck loop never chains")
    check(_chain_kind(BASE, run_of("blocked", tools=3)) is None, "a card the user must answer never chains")
    check(_should_chain(BASE, run_of("time", tools=1)) is True, "_should_chain is the boolean of the same decision")


def test_guards() -> None:
    r = run_of("time", tools=1)
    check(_chain_kind({**BASE, "status": "review"}, r) is None, "a desk that is not working never chains")
    check(_chain_kind({**BASE, "turn": 99}, r) is None, "turn cap")
    check(_chain_kind({**BASE, "cost": 99.0}, r) is None, "cost cap")
    check(_chain_kind({**BASE, "cost": 0.001, "budget": {"maxCost": 0.0001}}, r) is None, "a desk's own budget is stricter")
    check(_chain_kind(BASE, r, error="boom") is None, "an errored turn does not chain")
    r.stop.set()
    check(_chain_kind(BASE, r) is None, "a stopped run does not chain")


def test_ask_desk_without_a_plan_is_working() -> None:
    check(_chain_kind({**BASE, "status": "planning", "autonomy": "ask"}, run_of("time", tools=1)) == "continue",
          "claim_run leaves a plan-less ask desk in `planning`; it still chains")
    check(_chain_kind({**BASE, "status": "planning", "autonomy": "plan"}, run_of("time", tools=1)) is None,
          "a plan-autonomy desk still drafting its plan does not")


def test_single_nudge() -> None:
    check(_chain_kind(BASE, run_of(None)) == "nudge", "a reply that just ended gets one nudge")
    check(_chain_kind(BASE, run_of(None, content=continue_message("nudge", "my notes"))) is None,
          "a nudged turn that also just ends settles - never two nudges in a row, even with notes appended")
    check(_chain_kind({**BASE, "status": "blocked"}, run_of(None)) is None, "desk_ask/desk_done already moved the desk: no nudge")
    check(_chain_kind({**BASE, "turn": 99}, run_of(None)) is None, "caps hold for a nudge too")
    check(_chain_kind(BASE, run_of(None), error="x") is None, "an error is not nudged")
    check(_chain_kind(BASE, run_of("rounds", tools=1, content=DESK_NUDGE)) == "continue", "a nudged turn can still continue")


FULL = {"desk_list_files", "desk_read_file", "desk_write_file", "fs_edit", "fs_glob", "fs_grep", "shell_run", "run_python",
        "sandbox_exec", "web_search", "fetch_url", "browser_open", "desk_fetch_file", "view_image", "render_preview",
        "convert_document", "doc_guide", "agent_spawn", "todo_write", "desk_deliver", "desk_done", "desk_ask"}


def test_manual() -> None:
    full = desk_manual(FULL, {"shell_network": "allowlist", "sandbox_mount": True})
    check(len(full) < 2400, f"manual is {len(full)} chars")
    for needle in ("outputs/", "`doc_guide`", "/workspace/desk", "package registries", "background=true",
                   "`desk_fetch_file`", "`browser_open`", "`view_image`", "`desk_done`"):
        check(needle in full, f"full manual mentions {needle}")
    check(full.index("`desk_list_files`") < full.index("`shell_run`") < full.index("`run_python`") < full.index("`web_search`")
          < full.index("`view_image`") < full.index("`agent_spawn`") < full.index("Finish"), "lines keep the briefed order")
    check("network is open" in desk_manual({"shell_run"}, {"shell_network": "open"}), "open network sentence")
    check("no network" in desk_manual({"shell_run"}, {"shell_network": "off"}), "off network sentence")
    check("/workspace/desk" not in desk_manual(FULL, {"sandbox_mount": False}), "no mount, no mount line")
    small = desk_manual({"desk_read_file", "run_python"}, {})
    check("`run_python`" in small and "`shell_run`" not in small and "`browser_open`" not in small and "`doc_guide`" not in small,
          "only offered tools appear")
    check(desk_manual(set(), {}) == "", "nothing offered, no manual")
    check("desk_fetch_file" not in desk_manual({"web_search", "fetch_url"}, {}), "a tool that is not offered is not named")


def test_continue_message(tmp_path: Path) -> None:
    check(continue_message("continue") == DESK_CONTINUE and continue_message("resume") == DESK_RESUME
          and continue_message("nudge") == DESK_NUDGE, "no notes: the bare instruction")
    m = continue_message("continue", "- done: step 1")
    check(m.startswith(DESK_CONTINUE) and "- done: step 1" in m and "end of notes" in m, "notes are appended and delimited")
    (tmp_path / "work").mkdir()
    check(read_notes(tmp_path) == "", "no PROGRESS.md, no notes")
    (tmp_path / "work" / "PROGRESS.md").write_text("old\n" * 5000 + "NEWEST")
    n = read_notes(tmp_path)
    check(len(n) < NOTES_CAP + 60 and n.endswith("NEWEST"), "notes are capped and tail-biased")
    check("PROGRESS.md" in DESK_HINT, "DESK_HINT tells the desk to keep notes")
    check(isinstance(_desk_message("nope-desk", "continue"), str), "app helper builds a message")


def test_plan_hint_and_footer() -> None:
    check("desk_done" in DESK_PLAN_HINT and "does not need a step" in DESK_PLAN_HINT and "exact arguments" in DESK_PLAN_HINT,
          "plan hint explains binding and what needs no step")
    steps = [{"text": "a", "status": "done"}]
    check("desk_done" in plan_block(steps, desk=True) and "desk_done" not in plan_block(steps), "desk footer says deliver and desk_done")
    check(PLAN_FOOTER in plan_block(steps), "chat footer unchanged")


def main() -> None:
    for t in (test_every_budget_stop_chains_on_progress, test_guards, test_ask_desk_without_a_plan_is_working, test_single_nudge, test_manual, test_plan_hint_and_footer):
        t()
    test_continue_message(Path(tempfile.mkdtemp()))
    print(f"inner totals: {passed} passed")


def test_all() -> None:
    main()


if __name__ == "__main__":
    main()
