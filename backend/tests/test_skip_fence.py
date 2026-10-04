"""Skip-permissions lifts a plain ask only: forced, ask-rule, external, schedules, outside-folder and shell cards stay.

Reuses the scripted-model harness of test_permrules_loop. Run: python backend/tests/test_skip_fence.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_permrules_loop as T  # noqa: E402
from personal_os import llm, permrules  # noqa: E402
from personal_os.runs import Run  # noqa: E402
from personal_os.tools import ToolSpec, _obj  # noqa: E402

appmod, check = T.appmod, T.check
CALLS: list[str] = []
CTX: dict[str, Any] = {}


async def _fn(ctx: dict[str, Any], x: str = "") -> Any:
    CALLS.append(x)
    CTX.update(ctx)
    return {"ok": True}


for n, d in (("t_write", "writes"), ("t_ext", "external"), ("t_fs", "writes"), ("t_probe", "safe")):
    appmod.toolbox.specs[n] = ToolSpec(n, n, _obj({"x": {"type": "string"}}, []), _fn, "misc", d)
    appmod.toolbox.specs[n].default = "ask"
_fs = appmod.toolbox.fs_needs_ask
appmod.toolbox.fs_needs_ask = lambda name, args, ctx: name == "t_fs" or _fs(name, args, ctx)


def call(i: int, name: str, x: str = "a") -> dict[str, Any]:
    return {"id": f"c{i}", "name": name, "arguments": json.dumps({"x": x})}


def run(calls: list[list[dict[str, Any]]], rules: dict | None = None, tainted: bool = False, mode: str = "ask",
        job: bool = False, **kw: Any) -> list[tuple[str, Any]]:
    cid = T.setup(rules, skipPermissions=True, **kw)
    st: dict[str, Any] = {"tools": {n: mode for n in ("t_write", "t_ext", "t_fs", "t_probe", "shell_run")}}
    if tainted:
        st["tainted"] = True
    appmod.convos.update(cid, {"settings": st})
    CALLS.clear()
    return T.drive(cid, calls, ["deny"] * 5, run=Run(cid, None, kind="job") if job else None)


def test_plain_writes_ask_is_lifted() -> None:
    ev = run([[call(0, "t_write")], []])
    check(not T.cards(ev) and CALLS == ["a"], "unforced writes ask runs")


def test_deny_rule_still_refuses() -> None:
    ev = run([[T.sh(0, "git push x")], []], {"deny": ["Bash(git push *)"]}, mode="on")
    check(not T.RAN and not T.cards(ev), "deny refused, tool not called")


def test_ask_rule_stays_ask() -> None:
    ev = run([[T.sh(0, "make x")], []], {"ask": ["Bash(make *)"]}, mode="on")
    check(len(T.cards(ev)) == 1 and not T.RAN, "ask rule leaves ask")


def test_tainted_external_stays_forced_ask() -> None:
    ev = run([[call(0, "t_ext")], []], tainted=True, mode="on")
    c = T.cards(ev)
    check(len(c) == 1 and c[0]["forced"] and not CALLS, "tainted external asks, forced")
    ev = run([[call(0, "t_ext")], []])
    check(len(T.cards(ev)) == 1 and not CALLS, "external asks even untainted")


def test_fs_ask_stays() -> None:
    ev = run([[call(0, "t_fs")], []])
    check(len(T.cards(ev)) == 1 and not CALLS, "outside-folder write asks")


def test_doom_loop_still_asks() -> None:
    ev = run([[call(0, "t_write")], [call(1, "t_write")], [call(2, "t_write")], []], mode="on")
    cs = T.cards(ev)
    check(len(CALLS) == 2 and len(cs) == 1 and cs[0]["forced"], "third identical call raises a forced doom-loop card")


def test_predicate() -> None:
    lift = permrules.lift_permission_ask
    check(lift("t_write", "ask", skip=True, danger="writes") == "on", "plain ask lifts")
    check(lift("propose_plan", "ask", skip=True, danger="safe") == "ask", "plan stays")
    check(lift("desk_ask", "ask", skip=True, danger="safe") == "ask", "desk_ask stays")
    check(lift("t_write", "ask", skip=True, forced=True, danger="writes") == "ask", "forced stays")
    check(lift("t_write", "ask", skip=True, fenced=True, danger="writes") == "ask", "fenced stays")
    check(lift("x", "ask", skip=True, danger="schedules") == "ask", "schedules stays")
    check(lift("shell_run", "ask", skip=True, danger="executes") == "ask", "uncleared shell stays")
    check(lift("t_write", "off", skip=True) == "off" and lift("t_write", "ask", skip=False) == "ask", "off and unset")


def test_bridge_fenced_call_not_approved() -> None:
    cid = T.setup(None, skipPermissions=True)
    appmod.convos.update(cid, {"settings": {"tools": {"t_probe": "on"}}})
    T.ROUNDS[:] = [[call(0, "t_probe")], []]

    async def go() -> dict[str, Any]:
        prev = llm.stream_chat
        llm.stream_chat = T._scripted
        try:
            async for _ in appmod._chat_stream(cid, appmod.ChatIn(content="go"), asyncio.Event(), run=None):
                pass
        finally:
            llm.stream_chat = prev
        ap = CTX["bridge_approve"]
        return {"plain": await ap("t_write", {"x": "a"}, False), "forced": await ap("t_write", {"x": "a"}, True),
                "ext": await ap("t_ext", {"x": "a"}, False), "fs": await ap("t_fs", {"x": "a"}, False),
                "shell": await ap("shell_run", {"command": "ls"}, False)}

    r = asyncio.run(go())
    check(r["plain"] is True, "plain writes bridge call approved")
    check(not (r["forced"] or r["ext"] or r["fs"] or r["shell"]), "fenced bridge calls not approved")


def test_job_run_clears_skip() -> None:
    """A job's ctx carries no flag, so its bridge approver does not skip-approve a plain call either."""
    cid = T.setup(None, skipPermissions=True)
    appmod.convos.update(cid, {"settings": {"tools": {"t_probe": "on"}}})
    CTX.clear()
    T.drive(cid, [[call(0, "t_probe")], []], run=Run(cid, None, kind="job"))
    check(CTX["skip_permissions"] is False, "job run ctx never carries the flag")
    check(asyncio.run(CTX["bridge_approve"]("t_write", {"x": "a"}, False)) is False, "job bridge call not skip-approved")


def test_bridge_card_unattended_refused_and_wait_off_the_clock() -> None:
    """A job's bridge card is refused on the spot; a chat's wait is off the budget; bridged calls are journaled."""
    cid = T.setup(None, unattendedApprovals="deny", skipPermissions=False)
    appmod.convos.update(cid, {"settings": {"tools": {"t_probe": "on"}}})
    T.drive(cid, [[call(0, "t_probe")], []], run=Run(cid, appmod.run_store, kind="job"))
    job_ap = CTX["bridge_approve"]
    check(asyncio.run(asyncio.wait_for(job_ap("t_write", {"x": "a"}, False), 5)) is False, "job bridge card refused, not parked")
    check([a["decided_by"] for a in appmod.run_store.approvals(status=None, run_id=CTX["run_id"])] == ["unattended"],
          "refusal recorded as unattended")

    run = Run(cid, appmod.run_store)
    T.drive(cid, [[call(0, "t_probe")], []], run=run)
    ap, budget, seen = CTX["bridge_approve"], CTX["budget"], []

    async def go() -> bool:
        before = budget.paused
        task = asyncio.create_task(ap("t_write", {"x": "a"}, False))
        while not (uid := next((u for u in appmod._approvals if ":bridge" in u), None)):
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.3)
        seen.append(run.status)
        await appmod.approve_tool_call(uid, appmod.ApprovalIn(decision="allow"))
        ok = await task
        seen.append(budget.paused - before)
        return ok
    check(asyncio.run(go()) is True and seen[0] == "awaiting_approval" and seen[1] >= 0.3 and run.status == "running",
          "bridge wait marks the run waiting and is credited back to the budget")
    CALLS.clear()
    asyncio.run(CTX["bridge_call"]("t_write", {"x": "j"}, CTX))
    check(CALLS == ["j"] and any(r["tool"] == "t_write" for r in appmod.run_store.executed(run.run_id)),
          "bridged write goes through the journal")


def test_plan_stays_ask_in_chat() -> None:
    plan = {"title": "t", "steps": [{"tool": "t_write", "arguments": {"x": "a"}}]}
    ev = run([[{"id": "p0", "name": "propose_plan", "arguments": json.dumps(plan)}], []])
    c = T.cards(ev)
    check(len(c) == 1 and c[0]["name"] == "propose_plan" and not CALLS, "propose_plan still raises a card under skip")


def main() -> int:
    for k, v in sorted(globals().items()):
        if k.startswith("test_") and callable(v):
            v()
            print("ok  ", k)
    print(T.passed, "assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
