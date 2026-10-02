"""Permission rules inside the chat loop (app._chat_stream), driven with a scripted model and a fake `shell_run`.

The real shell tool is built elsewhere; the rules only look at its name and its `command`, so a stand-in that
records what it was asked to run is enough. Run: python backend/tests/test_permrules_loop.py
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="permloop-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import llm  # noqa: E402
from personal_os import app as appmod  # noqa: E402
from personal_os import permrules  # noqa: E402
from personal_os.runs import Run  # noqa: E402
from personal_os.tools import ToolSpec, _obj  # noqa: E402

passed = 0
RAN: list[str] = []
SEEN: list[list[dict[str, Any]]] = []
ROUNDS: list[list[dict[str, Any]]] = []


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


async def _fake_shell(ctx: dict[str, Any], command: str) -> Any:
    RAN.append(command)
    return {"ok": True, "ran": command}


appmod.toolbox.specs["shell_run"] = ToolSpec(
    "shell_run", "fake shell", _obj({"command": {"type": "string"}}, ["command"]), _fake_shell, "files", "writes")


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: Any = None, kind: str = "chat",
                    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: Any = None) -> Any:
    SEEN.append([dict(m) for m in messages])
    calls = ROUNDS.pop(0) if ROUNDS else []
    yield {"type": "delta", "text": "ok" if not calls else ""}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": calls, "usage": None}


_missing = set(inspect.signature(llm.stream_chat).parameters) - set(inspect.signature(_scripted).parameters)
assert not _missing, f"_scripted is missing {sorted(_missing)}"


def sh(i: int, command: str) -> dict[str, Any]:
    return {"id": f"c{i}", "name": "shell_run", "arguments": json.dumps({"command": command})}


def setup(rules: dict[str, list[str]] | None = None, mode: str = "ask", **settings: Any) -> str:
    appmod.db.set_settings({"autoLearn": False, "baseUrl": "", "stuckDetection": True, "workspaceRoots": [],
                            "unattendedApprovals": "ask", "permissionRules": {"allow": [], "ask": [], "deny": [], **(rules or {})}, **settings})
    cid = appmod.convos.create(None, "t", "m")["id"]
    appmod.convos.update(cid, {"settings": {"tools": {"shell_run": mode}}})
    RAN.clear()
    SEEN.clear()
    permrules.SESSION.clear()
    return cid


def drive(cid: str, calls_per_round: list[list[dict[str, Any]]], answers: list[Any] | None = None, run: Run | None = None) -> list[tuple[str, Any]]:
    """Run one reply. `answers` is consumed one per card: a decision string, or (decision, note)."""
    ROUNDS[:] = calls_per_round
    queue = list(answers or [])

    async def answerer(uid: str) -> None:
        while uid not in appmod._approvals:
            await asyncio.sleep(0.01)
        a = queue.pop(0) if queue else "allow"
        dec, note = a if isinstance(a, tuple) else (a, None)
        await appmod.approve_tool_call(uid, appmod.ApprovalIn(decision=dec, note=note))

    async def go() -> list[tuple[str, Any]]:
        out, tasks = [], []
        async for ev in appmod._chat_stream(cid, appmod.ChatIn(content="go"), asyncio.Event(), run=run):
            out.append(ev)
            if ev[0] == "tool_call" and ev[1].get("needs_approval"):
                tasks.append(asyncio.create_task(answerer(ev[1]["id"])))
        await asyncio.gather(*tasks)
        return out

    prev = llm.stream_chat
    llm.stream_chat = _scripted
    try:
        return asyncio.run(go())
    finally:
        llm.stream_chat = prev


def cards(events: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    return [d for e, d in events if e == "tool_call" and d.get("needs_approval")]


def results(events: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    return [d for e, d in events if e == "tool_result"]


def tool_messages() -> list[str]:
    return [m["content"] for r in SEEN[-1:] for m in r if m["role"] == "tool"]


def test_allow_rule_lets_the_command_through_without_a_card() -> None:
    cid = setup({"allow": ["Bash(npm run test *)"]})
    ev = drive(cid, [[sh(0, "npm run test -- -k foo")]])
    check(not cards(ev) and RAN == ["npm run test -- -k foo"], "an allowed command ran with no card")


def test_compound_with_an_unallowed_part_still_asks() -> None:
    cid = setup({"allow": ["Bash(git *)"]})
    ev = drive(cid, [[sh(0, "git status && rm -rf x")]], ["deny"])
    check(len(cards(ev)) == 1 and not RAN, "git status && rm -rf x asked even with git allowed, and the denial stopped it")
    cid = setup({"allow": ["Bash(git *)"]})
    ev = drive(cid, [[sh(0, "git status && git log")]])
    check(not cards(ev) and RAN, "two allowed subcommands run")


def test_a_plain_mode_on_tool_is_untouched_by_empty_rules() -> None:
    cid = setup(None, mode="on")
    check(not cards(drive(cid, [[sh(0, "anything at all")]])) and RAN, "no rules, mode on: runs")


def test_deny_rule_refuses_before_the_tool_runs() -> None:
    cid = setup({"allow": ["Bash(*)"], "deny": ["Bash(git push *)"]}, mode="on")
    ev = drive(cid, [[sh(0, "git push origin main")]])
    check(not RAN and not cards(ev), "refused without a card")
    check("Bash(git push *)" in (results(ev)[0]["error"] or ""), "the model is told which rule refused it")


def test_hardline_refuses_even_with_everything_allowed() -> None:
    cid = setup({"allow": ["Bash(*)", "Bash(rm *)"]}, mode="on")
    ev = drive(cid, [[sh(0, "rm -rf ~")]])
    check(not RAN and "never allowed" in (results(ev)[0]["error"] or ""), "hardline refused")


def test_card_carries_the_suggested_rule() -> None:
    cid = setup()
    ev = drive(cid, [[sh(0, "git commit -m hi")]], ["allow"])
    perm = cards(ev)[0].get("permission")
    check(perm and perm["suggestions"] == ["Bash(git commit *)"] and perm["session"], "the card offers the arity rule and the session grant")


def test_session_grant_is_scoped_to_the_chat_and_the_command() -> None:
    cid = setup()
    ev = drive(cid, [[sh(0, "make deploy")], [sh(1, "make deploy")], [sh(2, "make clean")]], ["always_session", "allow"])
    check(len(cards(ev)) == 2 and RAN == ["make deploy", "make deploy", "make clean"], "second identical call skipped its card; another command asked")
    other = setup()
    permrules.SESSION.add(cid, ["Bash(make deploy)"])
    ev = drive(other, [[sh(0, "make deploy")]], ["allow"])
    check(len(cards(ev)) == 1, "another chat is not covered")


def test_always_session_on_a_forced_card_is_one_shot() -> None:
    cid = setup(None, mode="on")
    ev = drive(cid, [[sh(0, "x1")], [sh(1, "x1")], [sh(2, "x1")], [sh(3, "x1")]], ["always_session", "allow"])
    forced = [c for c in cards(ev) if c["forced"]]
    check(forced and forced[0]["permission"]["session"] is False, "a forced card offers no session grant")
    check(RAN.count("x1") == 4, "the always_session answer on it was one-shot, so every call still ran once approved")


def test_doom_loop_card_cannot_be_lifted() -> None:
    cid = setup({"allow": ["Bash(echo *)", "doom_loop(shell_run)"]}, mode="on")
    ev = drive(cid, [[sh(0, "echo hi")], [sh(1, "echo hi")], [sh(2, "echo hi")]], ["allow"])
    c = cards(ev)
    check(len(c) == 1 and c[0]["forced"] and c[0]["id"].endswith(":c2"), "the third identical call asked, and only that one")
    check(c[0]["permission"] is None or c[0]["permission"]["kind"] == "doom_loop", "the card names the doom loop")


def test_deny_with_a_note_returns_it_and_the_turn_continues() -> None:
    cid = setup()
    ev = drive(cid, [[sh(0, "make deploy")], []], [("deny", "use make staging instead")])
    check(not RAN, "denied call did not run")
    check(any("use make staging instead" in m for m in tool_messages()), "the note reached the model as the tool result")
    check(ev[-1][0] == "done", "the reply carried on")
    cid = setup()
    drive(cid, [[sh(0, "make deploy")], []], ["deny"])
    check(any("just declined" in m for m in tool_messages()), "a plain deny behaves as before")


def test_three_refusals_in_a_row_add_a_hard_stop_note() -> None:
    cid = setup({"deny": ["Bash(git *)"]}, mode="on")
    ev = drive(cid, [[sh(0, "git a")], [sh(1, "git b")], [sh(2, "git c")], [sh(3, "git d")]])
    notes = [r for r in results(ev)]
    msgs = [m for r in SEEN[-1:] for m in r if m["role"] == "tool"]
    check("permission_note" not in msgs[2]["content"] and "permission_note" in msgs[3]["content"], "the fourth result carries the note, not the third")
    check(len(notes) == 4, "all four refused")


def test_unattended_runs_refuse_instead_of_asking() -> None:
    cid = setup(None, mode="ask", unattendedApprovals="deny")
    run = Run(cid, None, kind="job")
    ev = drive(cid, [[sh(0, "make deploy")], []], run=run)
    check(not cards(ev) and not RAN, "no card was raised and nothing ran")
    check(any("unattendedApprovals" in m for m in tool_messages()), "the reason reached the model")
    cid = setup(None, mode="ask", unattendedApprovals="ask")
    check(len(cards(drive(cid, [[sh(0, "make deploy")]], ["allow", "allow"], run=Run(cid, None, kind="job")))) == 1,
          "with the default setting a job run still asks")


def test_saving_always_allow_rules_validates_and_appends() -> None:
    setup()
    row = {"tool": "shell_run", "args": {"command": "git commit -m x && npm run build"}, "desk_id": None}
    saved = appmod._save_allow_rules(row, None)
    check(saved == ["Bash(git commit *)", "Bash(npm run build)"], f"suggestions saved: {saved}")
    cur = appmod.settings()["permissionRules"]["allow"]
    check(all(r in cur for r in saved), "appended to permissionRules.allow")
    try:
        appmod._save_allow_rules(row, ["Bash(*)"])
        check(False, "a blanket rule must be refused")
    except appmod.HTTPException as e:
        check(e.status_code == 400, "blanket rule is a 400")
    edited = appmod._save_allow_rules(row, ["Bash(git commit -m *)"])
    check(edited == ["Bash(git commit -m *)"], "an edited rule is what gets saved")


def test_settings_validation_and_evaluate_route() -> None:
    setup()
    try:
        appmod.put_settings({"permissionRules": {"allow": ["(("], "ask": [], "deny": []}})
        check(False, "malformed rule rejected")
    except appmod.HTTPException as e:
        check(e.status_code == 422, "422 on a malformed rule")
    appmod.put_settings({"permissionRules": {"allow": ["Bash(git *)", "Bash(git *)"], "ask": [], "deny": ["Bash(rm *)"]}})
    check(appmod.settings()["permissionRules"]["allow"] == ["Bash(git *)"], "stored deduplicated")
    r = appmod.evaluate_permission(appmod.PermissionEvalIn(command="git status && rm -rf x"))
    check(r["action"] == "deny" and r["rule"] == "Bash(rm *)", "the test box reports the deciding rule")
    check(appmod.evaluate_permission(appmod.PermissionEvalIn(command="git log"))["action"] == "allow", "allowed")
    check(appmod.evaluate_permission(appmod.PermissionEvalIn(rule="Bash(x)"))["ok"] is True, "rule validation")
    check(appmod.evaluate_permission(appmod.PermissionEvalIn(rule="((("))["ok"] is False, "rule validation rejects")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{passed} assertions passed, {failed} test(s) failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
