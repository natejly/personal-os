"""unattendedApprovals defaults to deny: an unattended run whose call would still ask is refused and recorded.

Reuses the scripted-model harness of test_permrules_loop. Run: backend/.venv/bin/python -m pytest backend/tests/test_unattended_deny_default.py
"""
from __future__ import annotations

from test_permrules_loop import RAN, Run, appmod, cards, drive, llm, setup, sh, tool_messages


def _unattended(cid: str, kind: str = "job") -> Run:
    return Run(cid, appmod.run_store, kind=kind)


def _fresh_cfg(**kw):  # type: ignore[no-untyped-def]
    cid = setup(**{"skipPermissions": False, **kw})
    appmod.db.set_settings({"unattendedApprovals": llm.DEFAULT_SETTINGS["unattendedApprovals"]})
    return cid


def test_default_is_deny() -> None:
    assert llm.DEFAULT_SETTINGS["unattendedApprovals"] == "deny"


def test_ask_is_denied_and_recorded_even_with_skip_on() -> None:
    for kind in ("job", "scheduled"):
        cid = _fresh_cfg(skipPermissions=True)
        run = _unattended(cid, kind)
        ev = drive(cid, [[sh(0, "make deploy")], []], run=run)
        assert not cards(ev) and not RAN
        assert any("unattendedApprovals" in m for m in tool_messages())
        rows = appmod.run_store.approvals(status="denied", run_id=run.run_id)
        assert len(rows) == 1 and rows[0]["decided_by"] == "unattended"


def test_conversation_skip_is_also_ignored() -> None:
    cid = _fresh_cfg()
    appmod.convos.update(cid, {"settings": {"tools": {"shell_run": "ask"}, "skipPermissions": True}})
    drive(cid, [[sh(0, "make deploy")], []], run=_unattended(cid))
    assert not RAN


def test_allow_rule_still_runs() -> None:
    cid = _fresh_cfg(permissionRules={"allow": ["Bash(make deploy)"], "ask": [], "deny": []})
    run = _unattended(cid, "scheduled")
    drive(cid, [[sh(0, "make deploy")], []], run=run)
    assert RAN == ["make deploy"]
    assert not appmod.run_store.approvals(status="denied", run_id=run.run_id)


def test_mode_on_still_runs() -> None:
    cid = _fresh_cfg()
    appmod.convos.update(cid, {"settings": {"tools": {"shell_run": "on"}}})
    drive(cid, [[sh(0, "make deploy")], []], run=_unattended(cid, "scheduled"))
    assert RAN == ["make deploy"]


def test_allow_plus_deny_does_not_run() -> None:
    cid = _fresh_cfg(permissionRules={"allow": ["Bash(make *)"], "ask": [], "deny": ["Bash(make deploy)"]})
    drive(cid, [[sh(0, "make deploy")], []], run=_unattended(cid, "scheduled"))
    assert not RAN


def test_interactive_chat_still_waits() -> None:
    cid = _fresh_cfg()
    ev = drive(cid, [[sh(0, "make deploy")], []], ["deny"])
    assert len(cards(ev)) == 1 and not RAN
