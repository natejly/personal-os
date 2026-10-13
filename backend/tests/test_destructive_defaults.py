"""Destructive actions by permission mode, and permissions_update (the assistant's way to change them, always a card).

Manual refuses each destructive category by default (settings.destructiveDefaults: block | ask | allow); Auto and Allow
everything only ever card a destructive shell command. Reuses the stand-in tools and the scripted chat loop of
test_allow_all_floor. Run: PERSONAL_OS_TEST_CHILD=1 python -m pytest -q backend/tests/test_destructive_defaults.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_allow_all_floor as F  # noqa: E402
from personal_os import permissions, permrules  # noqa: E402

T, appmod = F.T, F.appmod
ASK = {k: "ask" for k in permissions.DESTRUCTIVE_KINDS}
# One tool per category: shell, files, items, send
CATEGORY_CALLS = {"shell": F.sh("rm -rf ~/x"), "files": F.tool("trash_local_file"), "items": F.tool("calendar_delete"),
                  "send": F.tool("gmail_send")}


def told() -> str:
    return json.dumps(T.SEEN[-1], default=str)


@pytest.mark.parametrize("kind", sorted(CATEGORY_CALLS))
def test_manual_refuses_each_category_by_default_and_says_not_to_work_around_it(kind: str) -> None:
    assert permissions.DEFAULTS["destructiveDefaults"] == {k: "block" for k in permissions.DESTRUCTIVE_KINDS}
    assert not F.go("manual", CATEGORY_CALLS[kind]) and not T.RAN and not F.CALLS, kind
    assert "blocked by the user's settings" in told() and "work around" in told(), kind


@pytest.mark.parametrize("kind", sorted(CATEGORY_CALLS))
def test_manual_ask_is_a_forced_card_and_a_no_runs_nothing(kind: str) -> None:
    c = F.go("manual", CATEGORY_CALLS[kind], destructiveDefaults=ASK)
    assert len(c) == 1 and c[0]["forced"] and not T.RAN and not F.CALLS, kind


def test_manual_allow_leaves_a_call_to_the_usual_manual_gate() -> None:
    allow = {**ASK, "shell": "allow"}
    assert not F.go("manual", F.sh("rm -rf ~/x"), shell_mode="on", destructiveDefaults=allow) and T.RAN == ["rm -rf ~/x"], "a tool set to on runs"
    assert len(F.go("manual", F.sh("rm -rf ~/x"), destructiveDefaults=allow)) == 1, "a tool on its default ask still asks"
    assert len(F.go("manual", F.tool("gmail_send"), destructiveDefaults={**ASK, "send": "allow"})) == 1, "mail stays an email card"


def test_manual_still_cards_what_is_not_destructive() -> None:
    assert len(F.go("manual", F.sh("make all"))) == 1 and not T.RAN
    assert len(F.go("manual", F.sh("make all", unsandboxed=True))) == 1 and not T.RAN


def test_manual_blocks_overwriting_a_file_but_not_creating_one() -> None:
    tb = appmod.toolbox
    assert tb.floor_kind("write_local_file", {"mode": "overwrite"}, {}) == "files"
    assert tb.floor_kind("write_local_file", {"mode": "create"}, {}) is None
    assert tb.floor_kind("write_local_file", {}, {}) is None and tb.floor_kind("todo_delete", {}, {}) == "items"


def test_a_connector_is_in_a_category_only_when_it_says_it_is_destructive() -> None:
    tb = appmod.toolbox
    appmod.db.set_settings({"destructiveDefaults": dict(permissions.DEFAULTS["destructiveDefaults"])})
    ctx = {"permission_mode": "manual"}
    assert tb.floor_action("mcp__x", {}, ctx) is None
    assert tb.floor_action("mcp__x", {}, ctx, kind="items") == "block"
    assert tb.floor_action("mcp__x", {}, {"permission_mode": "auto"}, kind="items") is None


@pytest.mark.parametrize("pmode", ["auto", "allow_all"])
def test_other_modes_ignore_the_category_settings_except_for_shell(pmode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_review(*a: Any, **k: Any) -> Any:
        raise AssertionError("the reviewer must not see a destructive shell command")
    monkeypatch.setattr(appmod.autoreview, "review", no_review)
    block = {k: "block" for k in permissions.DESTRUCTIVE_KINDS}
    assert len(F.go(pmode, F.sh("rm -rf ~/x"), destructiveDefaults={**block, "shell": "allow"})) == 1 and not T.RAN, "shell still cards"
    if pmode == "allow_all":
        for kind in ("files", "items", "send"):
            assert not F.go(pmode, CATEGORY_CALLS[kind], destructiveDefaults=block) and F.CALLS == ["a"], kind


def test_auto_sends_mail_through_its_own_card_never_the_reviewer(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_review(*a: Any, **k: Any) -> Any:
        raise AssertionError("the reviewer must not lift the email card")
    monkeypatch.setattr(appmod.autoreview, "review", no_review)
    c = F.go("auto", F.tool("gmail_send"))
    assert len(c) == 1 and not F.CALLS


def test_destructive_defaults_are_validated_on_save() -> None:
    assert permissions.validate("destructiveDefaults", {"send": "ask"}) == {**permissions.DEFAULTS["destructiveDefaults"], "send": "ask"}
    for bad in ({"mail": "ask"}, {"send": "maybe"}, {"send": None}):
        with pytest.raises(ValueError):
            permissions.validate("destructiveDefaults", bad)
    with pytest.raises(ValueError):
        permissions.validate("destructiveDefaults", "block")
    with pytest.raises(HTTPException) as e:
        appmod.put_settings({"destructiveDefaults": {"files": "never"}})
    assert e.value.status_code == 422
    assert appmod.put_settings({"destructiveDefaults": {"files": "ask"}})["destructiveDefaults"]["files"] == "ask"
    appmod.put_settings({"destructiveDefaults": permissions.DEFAULTS["destructiveDefaults"]})


# ---- permissions_update

def pu(**args: Any) -> dict[str, Any]:
    return {"id": "c0", "name": "permissions_update", "arguments": json.dumps(args)}


def drive_pu(pmode: str, args: dict[str, Any], answers: list[str], rules: dict | None = None) -> list[Any]:
    cid = T.setup(rules, permissionMode=pmode, destructiveDefaults=dict(permissions.DEFAULTS["destructiveDefaults"]))
    return T.drive(cid, [[pu(**args)], []], answers)


@pytest.mark.parametrize("pmode", ["manual", "auto", "allow_all"])
def test_permissions_update_is_a_forced_card_in_every_mode_and_never_reviewed(pmode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_review(*a: Any, **k: Any) -> Any:
        raise AssertionError("the reviewer must not see a settings change")
    monkeypatch.setattr(appmod.autoreview, "review", no_review)
    ev = drive_pu(pmode, {"kind": "destructive", "target": "send", "value": "allow"}, ["deny"])
    cards = T.cards(ev)
    assert len(cards) == 1 and cards[0]["forced"], pmode
    assert cards[0]["permission"]["change"] == ["Sending email and messages (Manual mode): Block -> Allow"], "the card says what changes"
    assert appmod.settings()["destructiveDefaults"]["send"] == "block", "a denial changes nothing"


def test_approving_applies_each_kind_of_change() -> None:
    cases = [({"kind": "mode", "value": "auto"}, lambda s: s["permissionMode"] == "auto"),
             ({"kind": "destructive", "target": "files", "value": "ask"}, lambda s: s["destructiveDefaults"]["files"] == "ask"),
             ({"kind": "tool", "target": "calendar_update", "value": "on"}, lambda s: s["tools"]["calendar_update"] == "on"),
             ({"kind": "rule", "target": "allow", "value": "Bash(git status *)"}, lambda s: "Bash(git status *)" in s["permissionRules"]["allow"]),
             ({"kind": "rule", "target": "allow", "value": "Bash(git status *)", "remove": True},
              lambda s: "Bash(git status *)" not in s["permissionRules"]["allow"])]
    for args, ok in cases:
        ev = drive_pu("manual", args, ["allow"])
        assert len(T.cards(ev)) == 1, args
        assert ok(appmod.settings()), args
    appmod.put_settings({"permissionMode": "manual", "tools": {}, "destructiveDefaults": permissions.DEFAULTS["destructiveDefaults"]})


def test_an_ask_locked_tool_set_to_on_tops_out_at_ask_and_a_bad_request_is_an_error() -> None:
    drive_pu("manual", {"kind": "tool", "target": "calendar_delete", "value": "on"}, ["allow"])
    assert appmod.settings()["tools"]["calendar_delete"] == "ask"
    ev = drive_pu("manual", {"kind": "mode", "value": "yolo"}, ["allow"])
    assert "permissionMode" in appmod.settings() and appmod.settings()["permissionMode"] == "manual"
    assert any(k == "tool_result" and d.get("error") for k, d in ev)
    appmod.put_settings({"tools": {}})


def test_no_rule_grant_or_tool_mode_lifts_the_settings_card() -> None:
    args = {"kind": "mode", "value": "allow_all"}
    assert len(T.cards(drive_pu("manual", args, ["deny"], {"allow": ["permissions_update", "permissions_update(*)"]}))) == 1
    cid = T.setup(None, permissionMode="allow_all")
    permrules.SESSION.add(cid, ["permissions_update"])
    appmod.convos.update(cid, {"settings": {"tools": {"permissions_update": "on"}}})
    assert len(T.cards(T.drive(cid, [[pu(**args)], []], ["deny"]))) == 1
    assert appmod.settings()["permissionMode"] == "allow_all", "the card was denied, the mode was set by setup"


def test_it_cannot_lift_the_destructive_shell_card_or_a_hardline_refusal() -> None:
    # Allow everything with an allow rule for the command: the card stays (the rule cannot reach the floor).
    assert len(F.go("allow_all", F.sh("rm -rf ~/x"), {"allow": ["Bash(rm *)"]})) == 1 and not T.RAN
    # Hardline commands are refused whatever the rules say.
    hard = next(iter(c for c in ("rm -rf /", "rm -rf ~") if permrules.hardline(c)), None)
    if hard:
        assert not F.go("allow_all", F.sh(hard), {"allow": ["Bash(*)"]}) and not T.RAN


def test_a_background_run_never_applies_it() -> None:
    from personal_os.runs import Run
    cid = T.setup(None, permissionMode="allow_all")
    run = Run(cid, appmod.run_store, kind="job")
    T.ROUNDS[:] = [[pu(kind="mode", value="manual")], []]
    import asyncio

    async def go() -> None:
        async for _ in appmod._chat_stream(cid, appmod.ChatIn(content="go"), asyncio.Event(), run=run):
            pass
    prev = appmod.llm.stream_chat
    appmod.llm.stream_chat = T._scripted
    try:
        asyncio.run(go())
    finally:
        appmod.llm.stream_chat = prev
    assert appmod.settings()["permissionMode"] == "allow_all"


def test_a_worker_is_never_offered_it() -> None:
    from personal_os.subagents import CHILD_BLOCK
    assert "permissions_update" in CHILD_BLOCK
