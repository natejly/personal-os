"""One view of every standing grant, revoking them, and the decided-approvals history.

Reuses the scripted chat loop from test_permrules_loop. Run: python backend/tests/test_permission_grants.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import HTTPException  # noqa: E402

from test_permrules_loop import appmod, cards, drive, permrules, setup, sh  # noqa: E402


def _status(fn: Any, *a: Any, **kw: Any) -> int:
    try:
        r = fn(*a, **kw)
        if asyncio.iscoroutine(r):
            asyncio.run(r)
    except HTTPException as e:
        return e.status_code
    return 200


def _mcp_tool(server: str, name: str) -> str:
    sid = appmod.mcp_store.create_server(server, command="x")["id"]
    appmod.mcp_store.sync_tools(sid, [{"name": name, "description": name, "parameters": {"type": "object", "properties": {}}}])
    return [t["slug"] for t in appmod.mcp_store.tools() if server in t["slug"] and t["slug"].endswith(name)][0]


def test_session_grant_is_listed_and_revoking_it_asks_again() -> None:
    cid = setup()
    ev = drive(cid, [[sh(0, "make deploy")], [sh(1, "make deploy")]], ["always_session"])
    assert len(cards(ev)) == 1, "the grant covered the second call"
    row = [s for s in appmod.permission_grants()["session"] if s["conversation_id"] == cid]
    assert row and row[0]["keys"] == ["Bash(make deploy)"] and row[0]["title"] == "t"
    assert appmod.revoke_session_grant(cid)["keys"] == []
    assert not [s for s in appmod.permission_grants()["session"] if s["conversation_id"] == cid]
    ev = drive(cid, [[sh(0, "make deploy")]], ["allow"])
    assert len(cards(ev)) == 1, "after revoking, the identical call asks again"
    assert _status(appmod.revoke_session_grant, cid) == 404


def test_revoking_one_key_leaves_the_others() -> None:
    cid = setup()
    permrules.SESSION.add(cid, ["Bash(a)", "Bash(b)"])
    assert appmod.revoke_session_grant(cid, key="Bash(a)")["keys"] == ["Bash(b)"]
    assert permrules.SESSION.covers(cid, ["Bash(b)"]) and not permrules.SESSION.covers(cid, ["Bash(a)"])
    assert _status(appmod.revoke_session_grant, cid, key="Bash(a)") == 404


def test_chat_project_global_and_mcp_grants_are_listed() -> None:
    cid = setup()  # leaves the chat's shell_run override at ask
    pid = appmod.projects.create("P")["id"]
    appmod.projects.update(pid, {"tools": {"web_search": "on"}})
    slug = _mcp_tool("grantsrv", "ping")
    appmod.mcp_store.set_grant(slug, "on", "chat", cid)
    appmod.db.set_settings({"tools": {"gmail_send": "ask"}})
    g = appmod.permission_grants()
    assert {"conversation_id": cid, "title": "t", "tool": "shell_run", "mode": "ask"} in g["chat_overrides"]
    assert {"project_id": pid, "title": "P", "tool": "web_search", "mode": "on"} in g["project_overrides"]
    assert g["global"].get("gmail_send") == "ask"
    assert any(m["tool_slug"] == slug and m["scope"] == "chat" and m["scope_id"] == cid for m in g["mcp"])
    assert set(g["rules"]) == {"allow", "ask", "deny"}


def test_mcp_chat_grant_revoke_needs_its_scope_id() -> None:
    cid = setup()
    slug = _mcp_tool("revsrv", "send")
    appmod.mcp_store.set_grant(slug, "on", "chat", cid)
    assert _status(appmod.mcp_clear_grant, slug, "chat") == 400, "a chat revoke without scope_id is refused"
    assert _status(appmod.mcp_clear_grant, slug, "chat", "other-chat") == 404, "a revoke that matched nothing says so"
    assert appmod.mcp_store.effective_mode(slug, None, cid)["mode"] == "on", "the grant still stands"
    eff = appmod.mcp_clear_grant(slug, "chat", cid)
    assert eff["source"] == "default" and eff["mode"] != "on", "the chat falls back to the default"
    assert not appmod.mcp_store.grants(slug)


def test_decided_history_is_newest_first_and_excludes_pending() -> None:
    cid = setup()
    rs = appmod.run_store
    for c in ("first", "second", "later"):
        rs.open_approval(f"{cid}:{c}", None, "shell_run", {"command": c}, conversation_id=cid)
    rs.decide(f"{cid}:first", "deny", note="no")
    rs.decide(f"{cid}:second", "allow")  # "later" stays pending
    rows = asyncio.run(appmod.list_approvals(status="decided", order="desc"))
    mine = [r for r in rows if r["conversation_id"] == cid]
    assert [r["args"]["command"] for r in mine] == ["second", "first"], [r["args"] for r in mine]
    assert [r["status"] for r in mine] == ["approved", "denied"]
    assert mine[0]["conversation_title"] == "t" and mine[1]["note"] == "no"
    assert _status(appmod.list_approvals, order="sideways") == 400


if __name__ == "__main__":
    for k, v in sorted(dict(globals()).items()):
        if k.startswith("test_") and callable(v):
            v()
            print("ok  ", k)
