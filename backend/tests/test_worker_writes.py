"""A background worker writes where the chat works: it keeps the chat's desk (so the desk's file tools and the plain file tools
reach the workspace), answers cards like the main agent (any non-deny decision runs the call), leaves undo snapshots, and two
workers never write one path at once.

Harness (scripted model, desks, workers) comes from test_autonomous_workers.py.
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402

import test_autonomous_workers as T  # noqa: E402
from personal_os import fsx, snapshots  # noqa: E402
from test_autonomous_workers import FRONT, GATES, WORKER, _app, _fresh, appmod, call, client, delegate, make_desk, mgr, seat, settle, wait  # noqa: E402,F401


def desk_file(did: str, rel: str) -> Path:
    return Path(appmod.workspace.desk_root(did)) / rel


def tool_results(brief: str) -> list[dict[str, Any]]:
    """What the worker's tools answered, read off the messages the model saw in its last round."""
    last = [s for s in seat("worker") if s["marker"] == brief][-1]["messages"]
    return [json.loads(m["content"]) if str(m["content"]).startswith("{") else {"raw": m["content"]} for m in last if m["role"] == "tool"]


def test_a_worker_of_a_desk_chat_is_offered_the_writing_tools_its_parent_has() -> None:
    FRONT[:] = [delegate("Plain job"), {"text": "Started."}]
    cid, did = make_desk("ask")
    settle(cid)
    front, worker = set(seat("front")[0]["tools"]), set(seat("worker")[0]["tools"])
    assert {"desk_write_file", "write_local_file"} <= worker
    for name in ("write_local_file", "fs_edit", "shell_run", "desk_write_file", "desk_trash_file"):
        assert (name in front) == (name in worker), name  # exactly the parent's modes
    assert not {"desk_done", "desk_deliver", "desk_ask", "desk_start"} & worker


def test_a_workers_file_write_in_the_chats_folder_runs_without_a_card_under_allow_all(tmp_path: Path) -> None:
    appmod.db.set_settings({"permissionMode": "allow_all"})
    cid = appmod.convos.create(None, "Autonomous", "test-model")["id"]
    r = client.post("/cowork/desks", json={"conversation_id": cid, "autonomy": "ask", "brief": "write", "start": False})
    assert r.status_code == 200, r.text
    did = r.json()["desk"]["id"]
    # fs_mkdir reaches the desk workspace only through the worker's desk_id; write_local_file writes anywhere outside Grain's own folder
    WORKER["Write the note"] = [{"text": "", "calls": [call("w1", "write_local_file", {"path": str(tmp_path / "note.md"), "content": "hello"}),
                                                       call("w2", "desk_write_file", {"path": "work/other.md", "content": "again"}),
                                                       call("w3", "fs_mkdir", {"path": "work/sub/dir"})]},
                                {"text": "wrote both"}]
    FRONT[:] = [delegate("Write the note"), {"text": "Started."}]
    assert client.post(f"/cowork/desks/{did}/message", json={"content": "please do the thing"}).status_code == 200
    settle(cid)
    assert not any(r.get("error") for r in tool_results("Write the note")), tool_results("Write the note")
    assert (tmp_path / "note.md").read_text() == "hello" and desk_file(did, "work/other.md").read_text() == "again"
    assert desk_file(did, "work/sub/dir").is_dir()
    assert not T.store.approvals("pending"), "no card was raised"
    if snapshots.available():  # the worker's own run id keys its folder snapshot, closed when it ends, so Changes and Undo cover it
        rows = appmod.snaps.rows(mgr.list(cid)[0]["id"])
        assert rows and rows[0]["before_tree"] and rows[0]["after_tree"]
        assert any(f["path"].endswith("other.md") for f in rows[0]["files"])


def plain_chat() -> str:
    return appmod.convos.create(None, "Workers write", "test-model")["id"]


def say(cid: str) -> None:
    assert client.post(f"/conversations/{cid}/chat", json={"content": "please do the thing"}).status_code == 200


def card_for(tool: str) -> dict[str, Any]:
    card = wait(lambda: next(iter(T.store.approvals("pending")), None), "the worker's card")
    assert card["tool"] == tool
    return card


def test_a_workers_card_answered_always_rule_runs_the_call(tmp_path: Path) -> None:
    target = tmp_path / "card.md"
    WORKER["Write the card note"] = [{"text": "", "calls": [call("w1", "write_local_file", {"path": str(target), "content": "carded"})]},
                                     {"text": "done"}]
    FRONT[:] = [delegate("Write the card note"), {"text": "Started."}]
    cid = appmod.convos.create(None, "Workers write", "test-model")["id"]
    appmod.convos.update(cid, {"settings": {"tools": {"write_local_file": "ask"}}})
    say(cid)
    card = card_for("write_local_file")
    r = client.post(f"/approvals/{card['call_id']}", json={"decision": "always_rule", "rules": [f"write_local_file({tmp_path}/*)"]})
    assert r.status_code == 200, r.text
    settle(cid)
    assert target.read_text() == "carded"
    appmod.db.set_settings({"permissionRules": {"allow": [], "ask": [], "deny": []}})


def test_a_workers_card_answered_with_edited_arguments_runs_the_edit(tmp_path: Path) -> None:
    target = tmp_path / "e.md"
    WORKER["Write the edited note"] = [{"text": "", "calls": [call("w1", "write_local_file", {"path": str(target), "content": "draft"})]},
                                       {"text": "done"}]
    FRONT[:] = [delegate("Write the edited note"), {"text": "Started."}]
    cid = appmod.convos.create(None, "Workers write", "test-model")["id"]
    appmod.convos.update(cid, {"settings": {"tools": {"write_local_file": "ask"}}})
    say(cid)
    card = card_for("write_local_file")
    r = client.post(f"/approvals/{card['call_id']}", json={"decision": "allow", "arguments": {"path": str(target), "content": "final"}})
    assert r.status_code == 200, r.text
    settle(cid)
    assert target.read_text() == "final"


def test_a_worker_denied_a_card_does_not_write(tmp_path: Path) -> None:
    target = tmp_path / "no.md"
    WORKER["Write the denied note"] = [{"text": "", "calls": [call("w1", "write_local_file", {"path": str(target), "content": "x"})]},
                                       {"text": "done"}]
    FRONT[:] = [delegate("Write the denied note"), {"text": "Started."}]
    cid = appmod.convos.create(None, "Workers write", "test-model")["id"]
    appmod.convos.update(cid, {"settings": {"tools": {"write_local_file": "ask"}}})
    say(cid)
    card = card_for("write_local_file")
    assert client.post(f"/approvals/{card['call_id']}", json={"decision": "deny"}).status_code == 200
    settle(cid)
    assert not target.exists()


# ---------------- write claims ----------------
def test_two_children_cannot_write_one_path_until_the_first_ends() -> None:
    claims = fsx.WriteClaims()
    assert claims.claim("a", ["/x/f", "/x/g"]) is None
    assert claims.claim("a", ["/x/f"]) is None, "the owner may write again"
    assert claims.claim("b", ["/x/h", "/x/f"]) == "/x/f", "a second child is refused"
    assert claims.held_by_other(["/x/h"], "b") is None, "and its refused call took nothing"
    assert claims.held_by_other(["/x/g"]) == "/x/g", "the reply is held off too"
    claims.release("a")
    assert claims.claim("b", ["/x/f"]) is None


def test_the_second_worker_is_told_to_wait_for_the_first(tmp_path: Path) -> None:
    target = tmp_path / "shared.md"
    WORKER["Hold the file"] = [{"text": "", "calls": [call("w1", "write_local_file", {"path": str(target), "content": "one"})]},
                               {"text": "held", "delay": 3.0}]  # still alive, so still holding its claim, while the second one tries
    WORKER["Write after it"] = [{"text": "", "calls": [call("w1", "write_local_file", {"path": str(target), "content": "two", "mode": "overwrite"})]},
                                {"text": "second done"}]
    appmod.db.set_settings({"permissionMode": "allow_all"})
    FRONT[:] = [{"text": "", "calls": [call("d1", "delegate", {"goal": "Hold the file", "context": "c", "done_criteria": "d", "report_format": "r"})]},
                {"text": "ok"}]
    cid = plain_chat()
    say(cid)
    wait(target.exists, "the first worker's write")
    FRONT[:] = [{"text": "", "calls": [call("d2", "delegate", {"goal": "Write after it", "context": "c", "done_criteria": "d", "report_format": "r"})]},
                {"text": "ok"}]
    wait(lambda: not appmod.bus.live(cid), "the first reply to end")
    say(cid)
    wait(lambda: len([s for s in seat("worker") if s["marker"] == "Write after it"]) >= 2, "the second worker's answer")
    assert "being edited by another worker" in json.dumps(tool_results("Write after it")[0])
    assert target.read_text() == "one"
    settle(cid)
    assert not fsx.CLAIMS.held_by_other([str(target.resolve())]), "claims are released when the workers end"
