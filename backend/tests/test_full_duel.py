"""Full model duels: each side runs its whole turn with tools in a private overlay of the desk folder, reads run for real,
anything that would change the outside world is held, and a pick promotes the winner's files and runs its held actions.

Run: PERSONAL_OS_TEST_CHILD=1 pytest backend/tests/test_full_duel.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="fullduel-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import duel, llm  # noqa: E402
from personal_os.tools import ToolSpec, _obj  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
CALLS: list[str] = []  # the model of every stream_chat call, in order
SENT: list[dict[str, Any]] = []  # what the fake outside-world tool really did


def _tool(i: int, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"id": f"call_{i}", "name": name, "arguments": json.dumps(args)}


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
                    kind: str = "chat", effort: str = "default", tool_choice: str = "auto", fast: bool = False,
                    cancel: asyncio.Event | None = None) -> Any:
    """fd-a reads, writes outputs/a.txt and mails; fd-b writes outputs/b.txt and adds a todo. Both then answer."""
    CALLS.append(model)
    side = model[-1]
    if not any(m.get("role") == "tool" for m in messages):
        calls = {"a": [_tool(0, "desk_list_files", {}), _tool(1, "desk_write_file", {"path": "outputs/a.txt", "content": "from a"}),
                       _tool(2, "test_mail_send", {"to": "x@example.com"})],
                 "b": [_tool(0, "desk_write_file", {"path": "outputs/b.txt", "content": "from b"}),
                       _tool(1, "todo_add", {"title": "from-b"})]}[side]
        await asyncio.sleep(0.05)
        yield {"type": "delta", "text": f"{side.upper()} is on it"}
        yield {"type": "end", "finish_reason": "tool_calls", "tool_calls": calls, "usage": {"prompt_tokens": 5, "completion_tokens": 7}}
    else:
        yield {"type": "delta", "text": f"{side.upper()} finished"}
        yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}


async def _test_mail_send(ctx: dict[str, Any], to: str = "") -> Any:
    SENT.append({"to": to})
    return {"sent": True, "to": to}


@pytest.fixture(scope="module", autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    real = llm.stream_chat
    llm.stream_chat = _scripted
    appmod.toolbox.specs["test_mail_send"] = ToolSpec(
        "test_mail_send", "Send a test email.", _obj({"to": {"type": "string"}}, ["to"]), _test_mail_send, "utility", "external")
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": "", "devTools": True, "permissionMode": "allow_all"})
        yield
    appmod.toolbox.specs.pop("test_mail_send", None)
    llm.stream_chat = real


@pytest.fixture(autouse=True)
def _clean():  # type: ignore[no-untyped-def]
    CALLS.clear()
    SENT.clear()
    yield


def wait_until(pred: Callable[[], Any], label: str, timeout: float = 20.0) -> Any:
    end = time.time() + timeout
    while time.time() < end:
        if v := pred():
            return v
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {label}")


def ready() -> str:
    return wait_until(lambda: next((m for m, f in appmod._ab_waits.items() if not f.done()), None), "the duel to wait for a pick")


def start_desk_duel() -> tuple[str, str, Path]:
    """A desk on its own chat, a message with ab_model: returns (desk id, conversation id, the desk's real folder)."""
    cid = client.post("/conversations", json={}).json()["id"]
    appmod.convos.update(cid, {"model": "fd-a"})
    did = client.post("/cowork/desks", json={"conversation_id": cid, "brief": "go", "autonomy": "ask", "start": False}).json()["desk"]["id"]
    real = appmod.workspace.ensure(did)
    assert client.post(f"/cowork/desks/{did}/message", json={"content": "make a file", "ab_model": "fd-b"}).status_code == 200
    return did, cid, real


def overlay(mid: str, side: str, did: str) -> Path:
    return duel.ab_root(appmod.db.data_dir) / mid / side / "desk"


def todos(title: str) -> list[Any]:
    return [t for t in client.get("/todos").json() if title in json.dumps(t)]


def wait_done(cid: str) -> None:
    wait_until(lambda: not appmod.bus.answering(cid), "the run to finish")


def shadows() -> list[str]:
    with appmod.db.tx() as c:
        return [r["id"] for r in c.execute("SELECT id FROM conversations WHERE json_extract(settings,'$.ab_shadow') = 1")]


def test_each_side_works_in_its_own_overlay_and_the_real_folder_is_untouched() -> None:
    before = len(todos("from-b"))
    did, cid, real = start_desk_duel()
    mid = ready()
    assert CALLS.count("fd-a") == 2 and CALLS.count("fd-b") == 2  # both sides ran two rounds: tools, then the answer
    # each side wrote into its own copy of the desk folder, and read for real (desk_list_files saw the folder)
    assert (overlay(mid, "a", did) / "outputs" / "a.txt").read_text() == "from a"
    assert (overlay(mid, "b", did) / "outputs" / "b.txt").read_text() == "from b"
    assert not (overlay(mid, "a", did) / "outputs" / "b.txt").exists()
    assert not (real / "outputs" / "a.txt").exists() and not (real / "outputs" / "b.txt").exists()  # nothing real before the pick
    assert SENT == [] and len(todos("from-b")) == before  # held: the outside write and the todo
    got = client.get(f"/conversations/{cid}/ab/file", params={"message_id": mid, "side": "a", "path": "outputs/a.txt"}).json()
    assert got["text"] == "from a" and client.get(f"/conversations/{cid}/ab/file", params={"message_id": mid, "side": "b", "path": "outputs/a.txt"}).status_code == 404
    assert client.get(f"/conversations/{cid}/ab/file", params={"message_id": mid, "side": "a", "path": "../../../x"}).status_code == 404
    client.post(f"/conversations/{cid}/ab/pick", json={"message_id": mid, "winner": "tie"})
    wait_done(cid)


def test_pick_b_promotes_its_files_runs_its_held_action_and_keeps_its_transcript() -> None:
    before = len(todos("from-b"))
    did, cid, real = start_desk_duel()
    mid = ready()
    assert client.post(f"/conversations/{cid}/ab/pick", json={"message_id": mid, "winner": "b"}).status_code == 200
    wait_done(cid)
    assert (real / "outputs" / "b.txt").read_text() == "from b"  # the winner's file is real now
    assert not (real / "outputs" / "a.txt").exists() and SENT == []  # the loser's file and mail never happened
    assert not duel.ab_root(appmod.db.data_dir).joinpath(mid).exists() and shadows() == []  # temp folders and hidden chats are gone
    assert len(todos("from-b")) == before + 1  # B's held todo_add ran once, after the pick
    rows = [m for m in client.get(f"/conversations/{cid}").json()["messages"] if m["role"] == "assistant"]
    assert rows and rows[-1]["model"] == "fd-b" and "B is on it" in rows[-1]["content"] and "B finished" in rows[-1]["content"]
    names = [e["name"] for e in rows[-1]["tool_events"]]
    assert "desk_write_file" in names and names.count("todo_add") == 1  # the side's card plus the real run of the held call: only the real one is kept
    assert not [e for e in rows[-1]["tool_events"] if e["name"] == "test_mail_send"]


def test_pick_a_runs_the_held_mail_once() -> None:
    before = len(todos("from-b"))
    did, cid, real = start_desk_duel()
    mid = ready()
    client.post(f"/conversations/{cid}/ab/pick", json={"message_id": mid, "winner": "a"})
    wait_done(cid)
    assert SENT == [{"to": "x@example.com"}] and (real / "outputs" / "a.txt").exists() and not (real / "outputs" / "b.txt").exists()
    assert len(todos("from-b")) == before


def test_regenerate_resets_that_sides_overlay() -> None:
    did, cid, real = start_desk_duel()
    mid = ready()
    junk = overlay(mid, "a", did) / "work" / "junk.txt"
    junk.write_text("scratch")
    (overlay(mid, "b", did) / "work" / "keep.txt").write_text("kept")
    assert client.post(f"/conversations/{cid}/ab/regenerate", json={"message_id": mid, "side": "a"}).status_code == 200
    wait_until(lambda: CALLS.count("fd-a") == 4 and mid in appmod._ab_waits and not appmod._ab_waits[mid].done(), "the duel to wait again")
    assert not junk.exists() and (overlay(mid, "a", did) / "outputs" / "a.txt").read_text() == "from a"  # a fresh copy, re-run
    assert (overlay(mid, "b", did) / "work" / "keep.txt").exists() and CALLS.count("fd-b") == 2  # B was left alone
    client.post(f"/conversations/{cid}/ab/pick", json={"message_id": mid, "winner": "tie"})
    wait_done(cid)


def test_stop_cleans_up_both_sides() -> None:
    did, cid, real = start_desk_duel()
    mid = ready()
    assert overlay(mid, "a", did).exists() and len(shadows()) == 2
    client.post(f"/conversations/{cid}/stop")
    wait_done(cid)
    assert not duel.ab_root(appmod.db.data_dir).joinpath(mid).exists() and shadows() == []
    assert not (real / "outputs" / "a.txt").exists() and not (real / "outputs" / "b.txt").exists() and SENT == []


def test_gate_runs_reads_and_overlay_writes_and_holds_the_rest(tmp_path: Path) -> None:
    side = duel.Side("a", "m", tmp_path / "a")
    real = tmp_path / "desk"
    (real / "outputs").mkdir(parents=True)
    side.add_mount(real, real, "desk", None)
    side.seed()
    tb = appmod.toolbox
    ctx = {"desk_id": "d1", "settings": appmod.settings()}
    assert side.gate(tb, "search_memory", {"query": "x"}, ctx) is None  # a read
    assert side.gate(tb, "desk_write_file", {"path": "outputs/x"}, ctx) is None  # the workspace resolves through the overlay
    assert side.gate(tb, "fs_edit", {"path": str(real / "outputs" / "x.txt")}, ctx) is None  # the real desk path maps to the copy
    assert side.gate(tb, "write_local_file", {"path": "/tmp/elsewhere.txt", "content": ""}, ctx)["held"]  # outside the overlay
    assert side.gate(tb, "shell_run", {"command": "ls", "unsandboxed": True}, ctx)["held"]
    for name in ("todo_add", "doc_create", "save_memory", "gmail_send", "calendar_create", "agent_spawn", "delegate", "browser_open", "coding_session_start", "nope"):
        assert side.gate(tb, name, {}, ctx)["held"], name
    assert len(side.held) == 12  # every refusal was kept as a pending action


def test_shell_profile_confines_writes_when_asked() -> None:
    from personal_os.osplat.seatbelt import shell_profile
    open_ = shell_profile(["/tmp/x"])
    confined = shell_profile(["/tmp/x"], confine=True)
    assert "\n(allow file-write*)\n" in open_ and "\n(allow file-write*)\n" not in confined
    assert '(allow file-write* (subpath "' in confined and "/tmp/x" in confined.split("(allow file-write* (subpath")[1].split("\n")[0]


def test_chat_outbox_mount_promotes_into_the_real_chat_and_records_the_file(tmp_path: Path) -> None:
    shadow_dir, real_dir, seen = tmp_path / "chats" / "shadow", tmp_path / "chats" / "real", []
    real_dir.mkdir(parents=True)
    (real_dir / "old.txt").write_text("old")
    side = duel.Side("b", "m", tmp_path / "b")
    side.add_mount(shadow_dir, real_dir, "chat", seen.append)
    side.seed()
    assert side.remap(shadow_dir) == tmp_path / "b" / "chat" and (tmp_path / "b" / "chat" / "old.txt").read_text() == "old"  # seeded with the real files
    (tmp_path / "b" / "chat" / "new.txt").write_text("new")
    (tmp_path / "b" / "chat" / "old.txt").unlink()
    assert [f["path"] for f in side.files()] == ["new.txt"]
    side.promote()
    assert (real_dir / "new.txt").read_text() == "new" and not (real_dir / "old.txt").exists() and seen == [real_dir / "new.txt"]
    side.discard()
    assert not (tmp_path / "b").exists()
