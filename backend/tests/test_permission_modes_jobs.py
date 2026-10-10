"""Auto mode in an unattended job run, and allow_all's doom-loop card. Reuses the scripted-model harness of
test_permrules_external_loop (a fake external `gmail_draft`). Run: python backend/tests/test_permission_modes_jobs.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_permrules_external_loop as T  # noqa: E402
from personal_os import llm, permrules  # noqa: E402
from personal_os.runs import Run  # noqa: E402
from personal_os.tools import ToolSpec, _obj  # noqa: E402

appmod, check = T.appmod, T.check
# gmail_send is never routed like other external tools (its card is the user's email), so these generic-routing tests
# use gmail_draft as their stand-in for an external tool.
appmod.toolbox.specs["gmail_draft"] = ToolSpec("gmail_draft", "fake draft", _obj({"to": {"type": "string"}}, ["to"]), T._fake_shell, "google", "external")


def sh(i: int, to: str) -> dict[str, Any]:
    return {**T.sh(i, to), "name": "gmail_draft"}


def job_reply(answer: dict[str, Any], to: str) -> tuple[Run, list[Any], list[str]]:
    reviews: list[str] = []

    async def complete(settings: Any, model: str, messages: list[dict[str, Any]], kind: str = "other", **_kw: Any) -> str:
        reviews.append(kind)
        return json.dumps(answer)

    cid = T.setup(None, mode=None, permissionMode="auto", alwaysAsk=[])
    # "scheduled": unattended but not proposal-only, so the call reaches the reviewer (a "job" run files every external
    # call as a proposal before any review).
    run = Run(cid, appmod.run_store, kind="scheduled")
    prev = llm.complete
    llm.complete = complete
    try:
        ev = T.drive(cid, [[sh(0, to)], []], run=run)
    finally:
        llm.complete = prev
    return run, ev, reviews


def test_job_auto_ask_becomes_a_proposal_and_is_not_executed() -> None:
    run, ev, reviews = job_reply({"verdict": "ask", "confidence": "low", "reason": "unsure"}, "p@x.com")
    check(reviews.count("review") == 1, f"the reviewer looked at it once: {reviews}")
    check(not T.RAN and not T.cards(ev), "nothing ran and no card was parked")
    mine = [p for p in appmod.proposals.list("pending", limit=500) if p["run_id"] == run.run_id]
    check(len(mine) == 1 and mine[0]["tool"] == "gmail_draft", "the call was filed as a proposal")


def test_job_auto_deny_is_refused_and_logged() -> None:
    run, ev, reviews = job_reply({"verdict": "deny", "confidence": "high", "reason": "not requested"}, "d@x.com")
    check(not T.RAN and not T.cards(ev), "nothing ran")
    check(any("refused by the safety reviewer: not requested" in m for m in T.tool_messages()), "the reason reached the model")
    check(not [p for p in appmod.proposals.list("pending", limit=500) if p["run_id"] == run.run_id], "no proposal was filed")
    rows = [r for r in appmod.approval_log.history(appmod.db, tool="gmail_draft", limit=200)["items"]
            if r["run_id"] == run.run_id and r["decision"] == "deny" and r["scope"] == "auto-review"]
    check(rows, "an approval_log row records the reviewer's deny")


def test_allow_all_doom_loop_does_not_ask() -> None:
    cid = T.setup(None, mode=None, permissionMode="allow_all", alwaysAsk=[])
    appmod.convos.update(cid, {"settings": {"tools": {"gmail_draft": "on"}}})
    ev = T.drive(cid, [[sh(i, "same@x.com")] for i in range(permrules.DOOM_LIMIT)] + [[]], ["deny"])
    check(not T.cards(ev), "allow-all raises no card for the repeated call")
    check(len(T.RAN) == permrules.DOOM_LIMIT, "every call ran")


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print(T.passed, "checks passed")
