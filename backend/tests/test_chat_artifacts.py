"""Files → Artifacts (chat_files.py): every chat's outputs indexed by path, desk workspaces included, trashed chats
kept until purge, missing files left out, the backfill migration. Offline: a temp HOME and a temp data dir.

Run: python backend/tests/test_chat_artifacts.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

HOME = Path(tempfile.mkdtemp(prefix="cahome-")).resolve()
os.environ["HOME"] = str(HOME)
DATA = Path(tempfile.mkdtemp(prefix="cadb-")).resolve()
os.environ["PERSONAL_OS_DATA_DIR"] = str(DATA)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import migrations  # noqa: E402
from personal_os.chat_files import ChatFiles, router  # noqa: E402
from personal_os.cowork import Desks  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.docs import Docs  # noqa: E402
from personal_os.repos import Conversations, Documents  # noqa: E402
from personal_os.todos import Todos  # noqa: E402
from personal_os.trash import Trash  # noqa: E402
from personal_os.workspace import Workspace  # noqa: E402

(HOME / "Library").mkdir()
DB = Database(DATA)
CONVOS, DOCUMENTS, DOCS = Conversations(DB), Documents(DB), Docs(DB)
CHATS = Workspace(DATA, sub="chats")
DESKWS = Workspace(DATA)
DESKS = Desks(DB, DESKWS)
CF = ChatFiles(DB, CHATS.root)
CHATS.on_save = CF.record_output
DESKWS.on_save = CF.record_desk_output
TRASH = Trash(DB, Todos(DB), DOCS)
CL = TestClient(FastAPI())
CL.app.include_router(router(CF))  # type: ignore[attr-defined]
passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def chat(title: str = "t") -> str:
    return CONVOS.create(None, title, "m")["id"]


def artifacts() -> list[dict[str, Any]]:
    CF._reconciled = 0.0  # the throttle is for the UI, not the test
    r = CL.get("/chat-files/artifacts")
    assert r.status_code == 200, r.text
    return r.json()["files"]


def test_chat_outbox_and_desk_workspace_are_indexed() -> None:
    cid = chat("Report chat")
    CHATS.save_bytes(cid, "outputs/report.csv", b"a,b\n1,2\n")
    did = DESKS.create(conversation_id=cid, brief="b", title="Desk")["id"]
    DESKWS.write(did, "work/notes.md", "# hi\n")                      # desk_write_file: recorded by on_save
    DESKWS.save_bytes(did, "outputs/chart.png", b"\x89PNG")           # a desk export
    (DESKWS.desk_root(did) / "work" / "made_by_shell.txt").write_text("x")  # a shell run writes straight to disk
    (DESKWS.desk_root(did) / ".baseline").mkdir(exist_ok=True)
    (DESKWS.desk_root(did) / ".baseline" / "hidden").write_text("x")  # the workspace's own: never an artifact
    files = artifacts()
    names = [f["name"] for f in files]
    check(names == ["made_by_shell.txt", "chart.png", "notes.md", "report.csv"] or set(names) == {"made_by_shell.txt", "chart.png", "notes.md", "report.csv"},
          "chat outbox, desk writes, desk exports and shell-written files are all listed, nothing from .baseline")
    check(all(f["conversation_id"] == cid and f["conversation_title"] == "Report chat" and f["kind"] == "output" for f in files), "every row names its chat")
    check(all(not f["chat_deleted"] and not f["missing"] and f["size"] > 0 for f in files), "live chat, files present, sizes set")
    rel = {f["name"]: f["rel"] for f in files}
    check(rel["report.csv"] == "outputs/report.csv" and rel["notes.md"] == "work/notes.md" and rel["chart.png"] == "outputs/chart.png", "rel is inside the outbox or the desk workspace")
    check(all(files[i]["created_at"] >= files[i + 1]["created_at"] for i in range(len(files) - 1)), "newest first")


def test_raw_route_serves_bytes_safely() -> None:
    cid = chat()
    CHATS.save_bytes(cid, "outputs/page.html", b"<script>1</script>")
    CHATS.save_bytes(cid, "outputs/data.csv", b"x,y\n")
    by = {f["name"]: f for f in artifacts() if f["conversation_id"] == cid}
    r = CL.get(f"/chat-files/{by['page.html']['id']}/raw")
    check(r.status_code == 200 and r.content == b"<script>1</script>", "raw bytes come back")
    check(r.headers["content-type"].startswith("text/plain") and r.headers["x-content-type-options"] == "nosniff", "agent-written HTML is served as text, never as a page")
    check(CL.get(f"/chat-files/{by['data.csv']['id']}/raw").status_code == 200, "csv")
    check(CL.get("/chat-files/nope/raw").status_code == 404, "unknown id")
    Path(by["data.csv"]["ref"]).unlink()
    check(CL.get(f"/chat-files/{by['data.csv']['id']}/raw").status_code == 404, "a file that is gone is a 404, not a traversal")
    with DB.tx() as c:  # a row whose path escaped the roots (a corrupt or forged row) is never served
        c.execute("UPDATE chat_files SET ref=? WHERE id=?", (str(DB.path), by["page.html"]["id"]))
    check(CL.get(f"/chat-files/{by['page.html']['id']}/raw").status_code == 404, "paths outside chats/ and cowork/ are refused")


def test_missing_files_are_left_out_but_stay_in_the_chat_list() -> None:
    cid = chat()
    CHATS.save_bytes(cid, "outputs/keep.txt", b"k")
    CHATS.save_bytes(cid, "outputs/gone.txt", b"g")
    (CHATS.desk_root(cid) / "outputs" / "gone.txt").unlink()
    mine = [f["name"] for f in artifacts() if f["conversation_id"] == cid]
    check(mine == ["keep.txt"], "a row whose file is gone is not an artifact")
    per_chat = {f["name"]: f["missing"] for f in CL.get(f"/conversations/{cid}/files").json()["files"]}
    check(per_chat == {"keep.txt": False, "gone.txt": True}, "the chat's own list still shows it as missing")


def test_trashed_chat_keeps_artifacts_until_purge() -> None:
    cid = chat("Doomed")
    CHATS.save_bytes(cid, "outputs/last.pdf", b"%PDF")
    path = Path(CHATS.desk_root(cid) / "outputs" / "last.pdf")
    check(TRASH.trash("conversation", cid), "trashed")
    mine = [f for f in artifacts() if f["conversation_id"] == cid]
    check(len(mine) == 1 and mine[0]["chat_deleted"] and mine[0]["conversation_title"] == "Doomed", "a trashed chat's file stays, labelled")
    check(CL.get(f"/chat-files/{mine[0]['id']}/raw").status_code == 200, "and still opens")
    check(TRASH.purge("conversation", cid), "purged")
    check(not path.exists(), "purge removes the bytes")
    check(not [f for f in artifacts() if f["conversation_id"] == cid], "and the artifact")
    with DB.tx() as c:
        n = c.execute("SELECT COUNT(*) FROM chat_files WHERE conversation_id=?", (cid,)).fetchone()[0]
    check(n == 0, "purge drops the index rows too: no orphans")


def test_migration_backfills_existing_outputs() -> None:
    cid = chat("Old chat")
    out = CHATS.desk_root(cid) / "outputs" / "deep"
    out.mkdir(parents=True)
    (out / "old.json").write_text("{}")  # written before any hook existed
    did = DESKS.create(conversation_id=cid, brief="b")["id"]
    (DESKWS.desk_root(did) / "work" / "plan.md").write_text("p")
    with DB.tx() as c:
        c.execute("DELETE FROM chat_files WHERE conversation_id=?", (cid,))
        c.execute("PRAGMA user_version = 26")
    c = DB.connect()  # migrations.run owns its transactions
    try:
        applied = migrations.run(c)
        check(applied == [27] and migrations.MIGRATIONS[-1][1] == "chat_artifacts_backfill", "step 27 is the backfill")
        c.execute("PRAGMA user_version = 26")
        check(migrations.run(c) == [27], "re-running the step is harmless")
    finally:
        c.close()
    mine = {f["name"]: f["rel"] for f in artifacts() if f["conversation_id"] == cid}
    check(mine == {"old.json": "outputs/deep/old.json", "plan.md": "work/plan.md"}, "the outbox and the desk workspace are indexed by the migration")
    with DB.tx() as c:
        n = c.execute("SELECT COUNT(*) FROM chat_files WHERE conversation_id=?", (cid,)).fetchone()[0]
    check(n == 2, "idempotent: one row per file")


def test_paging() -> None:
    cid = chat()
    for i in range(5):
        CHATS.save_bytes(cid, f"outputs/p{i}.txt", b"x")
    CF._reconciled = 0.0
    first = CL.get("/chat-files/artifacts?limit=2").json()
    check(len(first["files"]) == 2 and first["next_cursor"], "a page")
    second = CL.get(f"/chat-files/artifacts?limit=2&cursor={first['next_cursor']}").json()
    check(len(second["files"]) == 2 and not {f["id"] for f in first["files"]} & {f["id"] for f in second["files"]}, "the next page is new rows")
    check(CL.get("/chat-files/artifacts?cursor=bad").status_code == 400, "bad cursor")


if __name__ == "__main__":
    failed = 0
    for t in [v for k, v in sorted(globals().items(), key=lambda kv: getattr(kv[1], "__code__", None) and kv[1].__code__.co_firstlineno or 0)
              if k.startswith("test_") and callable(v)]:
        try:
            t()
            print(f"ok   {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{passed} assertions passed, {failed} test(s) failed")
    sys.exit(1 if failed else 0)
