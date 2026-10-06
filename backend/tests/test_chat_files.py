"""Which chat each file belongs to (chat_files.py). Offline: a temp HOME and a temp data dir.

Run: python backend/tests/test_chat_files.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HOME = Path(tempfile.mkdtemp(prefix="cfhome-")).resolve()
os.environ["HOME"] = str(HOME)
DATA = Path(tempfile.mkdtemp(prefix="cfdb-")).resolve()
os.environ["PERSONAL_OS_DATA_DIR"] = str(DATA)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import mac  # noqa: E402
from personal_os.chat_files import ChatFiles, router  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.docs import Docs  # noqa: E402
from personal_os.filesnap import FileSnapshots  # noqa: E402
from personal_os.repos import Conversations, Documents, Projects  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402
from personal_os.workspace import Workspace  # noqa: E402

(HOME / "Desktop").mkdir()
(HOME / "Library").mkdir()
DB = Database(DATA)
CONVOS, PROJECTS, DOCUMENTS, DOCS = Conversations(DB), Projects(DB), Documents(DB), Docs(DB)
CHATS = Workspace(DATA, sub="chats")
CF = ChatFiles(DB, CHATS.root)
CHATS.on_save = CF.record_output
FS = FileSnapshots(DB, DATA / "snapshots", lambda: {})
TB = Toolbox(None, None, None, lambda: {}, docs=DOCS)  # type: ignore[arg-type]
TB.chat_files = CF
CL = TestClient(FastAPI())
CL.app.include_router(router(CF))  # type: ignore[attr-defined]
passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def chat(project: str | None = None) -> str:
    return CONVOS.create(project, "t", "m")["id"]


def rows(cid: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
    with DB.tx() as c:
        q, a = "SELECT * FROM chat_files WHERE 1=1", []
        if cid:
            q, a = q + " AND conversation_id=?", [cid]
        if kind:
            q, a = q + " AND kind=?", a + [kind]
        return [dict(r) for r in c.execute(q + " ORDER BY created_at", a).fetchall()]


def call(name: str, args: dict[str, Any], ctx: dict[str, Any]) -> Any:
    return asyncio.run(TB.call(name, args, ctx))


def snap(op: str, path: Path, cid: str, to: Path | None = None, fail: bool = False) -> None:
    """What the local-file tools do: capture, write, finalize (or discard when the write fails)."""
    ctx = {"conversation_id": cid, "message_id": "m1"}
    s = FS.capture(op, str(path), ctx, to=str(to) if to else None)
    if fail:
        FS.discard(s["snapshot_id"])
        return
    if op == "move":
        path.rename(to)  # type: ignore[arg-type]
    else:
        mac.write_local(str(path), "x", op)
    FS.finalize(s["snapshot_id"], str(to or path))


def test_upload_attachments() -> None:
    cid = chat()
    CONVOS.add_message(cid, "user", "hi", attachments=[{"id": "d1", "name": "a.pdf"}, {"id": "d2"}, "junk"])
    CONVOS.add_message(cid, "user", "again", attachments=[{"id": "d1", "name": "a.pdf"}])
    r = rows(cid, "upload")
    check({x["ref"] for x in r} == {"d1", "d2"}, "one row per attached file, junk elements ignored")
    d1 = next(x for x in r if x["ref"] == "d1")
    check(d1["action"] == "attached" and d1["message_id"] and d1["name"] == "a.pdf", "upload row carries action and message id")
    check(next(x for x in r if x["ref"] == "d2")["name"] == "d2", "no name falls back to the id")
    with DB.tx() as c:
        c.execute("UPDATE messages SET attachments='not json' WHERE conversation_id=?", (cid,))
    check(len(rows(cid, "upload")) == 2, "invalid JSON never raises")


def test_notes_and_revisions() -> None:
    cid = chat()
    ctx = {"conversation_id": cid, "message_id": "m9", "project_id": None}
    made = call("doc_create", {"title": "Plan", "content": "alpha\n"}, ctx)
    n = rows(cid, "note")
    check(len(n) == 1 and n[0]["ref"] == made["doc_id"] and n[0]["action"] == "created" and n[0]["message_id"] == "m9", "doc_create -> note/created")
    ed = call("doc_edit", {"doc": made["doc_id"], "edits": [{"find": "alpha", "replace": "beta"}]}, {**ctx, "settings": {"docEditMode": "apply"}})
    check(ed["status"] == "applied" and len(rows(cid, "note")) == 1, "one row per file per chat: the first action wins")
    cid2 = chat()
    ctx2 = {"conversation_id": cid2, "message_id": "m2", "project_id": None}
    d = call("doc_edit", {"doc": made["doc_id"], "edits": [{"find": "beta", "replace": "gamma"}]}, {**ctx2, "settings": {"docEditMode": "apply"}})
    check([x["action"] for x in rows(cid2, "note")] == ["edited"], f"doc_edit apply -> note/edited in the editing chat: {d}")
    # a review-mode proposal accepted from the card: the chat is found through the message's tool events
    cid3 = chat()
    held = call("doc_edit", {"doc": made["doc_id"], "edits": [{"find": "gamma", "replace": "delta"}]}, {**ctx2, "conversation_id": cid3})
    check(held["status"] == "pending_review" and not rows(cid3, "note"), "a pending proposal records nothing yet")
    with DB.tx() as c:
        c.execute("INSERT INTO messages(id,conversation_id,role,content,tool_events,created_at) VALUES('mm',?,'assistant','x',?,?)",
                  (cid3, '[{"result": {"revision_id": "%s"}}]' % held["revision_id"], time.time()))
    CF.record_accepted_revision(held["revision_id"], DOCS.accept(held["revision_id"]))  # type: ignore[arg-type]
    r = rows(cid3, "note")
    check(len(r) == 1 and r[0]["action"] == "edited" and r[0]["message_id"] == "mm", "accepted card -> note/edited")
    user_rev = DOCS.propose(made["doc_id"], "z", "", tool=None)
    CF.record_accepted_revision(user_rev["id"], {"id": made["doc_id"], "title": "Plan"})  # type: ignore[index]
    check(len(rows(cid3, "note")) == 1, "only doc_edit revisions are recorded")
    CF.record_accepted_revision("nope", {"id": "x", "title": "x"})
    CF.record(None, "note", "x", "x", "created")
    check(True, "unknown revision / no conversation are silent no-ops")


def test_outputs() -> None:
    cid = chat()
    e = CHATS.save_bytes(cid, "outputs/report.csv", b"a,b\n")
    src = Path(tempfile.mkdtemp(prefix="cfkeep-"))
    (src / "plot.png").write_bytes(b"png")
    CHATS.keep_files(cid, src)
    r = rows(cid, "output")
    check(sorted(x["name"] for x in r) == ["plot.png", "report.csv"] and all(x["action"] == "saved" for x in r), "save_bytes and keep_files -> output rows")
    got = {x["name"]: x for x in CL.get(f"/conversations/{cid}/files").json()["files"]}
    check(got["report.csv"]["rel"] == e["path"] == "outputs/report.csv" and not got["report.csv"]["missing"], "rel is relative to the chat folder")
    (CHATS.desk_root(cid) / "outputs" / "report.csv").unlink()
    got = {x["name"]: x for x in CL.get(f"/conversations/{cid}/files").json()["files"]}
    check(got["report.csv"]["missing"] is True and got["plot.png"]["missing"] is False, "a deleted output is flagged missing")
    CHATS.on_save = lambda *_: 1 / 0  # type: ignore[assignment,return-value]
    try:
        CHATS.save_bytes(cid, "outputs/safe.txt", b"x")
        check(True, "a failing recorder never breaks the write")
    finally:
        CHATS.on_save = CF.record_output


def test_local_writes() -> None:
    cid = chat()
    d = HOME / "Desktop"
    snap("create", d / "new.md", cid)
    (d / "old.md").write_text("old")
    snap("overwrite", d / "old.md", cid)
    (d / "mv.md").write_text("m")
    snap("move", d / "mv.md", cid, to=d / "moved.md")
    (d / "bad.md").write_text("b")
    snap("overwrite", d / "bad.md", cid, fail=True)
    by = {x["name"]: x for x in rows(cid, "local")}
    check(set(by) == {"new.md", "old.md", "moved.md"}, f"create / overwrite / move record, a failed write does not: {set(by)}")
    check(by["new.md"]["action"] == "created" and by["old.md"]["action"] == "edited" and by["moved.md"]["ref"] == str(d / "moved.md"),
          "create is 'created', the rest 'edited', a move records the destination")
    FS.restore(next(s["snapshot_id"] for s in FS.list(cid) if s["path"].endswith("old.md")), force=True)
    check(len(rows(cid, "local")) == 3, "a restore is not recorded")
    (d / "new.md").unlink()
    got = {x["name"]: x for x in CL.get(f"/conversations/{cid}/files").json()["files"]}
    check(got["new.md"]["missing"] and not got["old.md"]["missing"], "a deleted local file is flagged missing")
    with DB.tx() as c:
        c.execute("INSERT INTO chat_files VALUES('x1',?,'local',?,'secret','edited',NULL,?)", (cid, str(HOME / ".ssh" / "secret"), time.time()))
        c.execute("INSERT INTO chat_files VALUES('x2',?,'local',?,'hid','edited',NULL,?)", (cid, str(HOME / "Desktop" / ".env"), time.time()))
    names = {x["name"] for x in CL.get(f"/conversations/{cid}/files").json()["files"]}
    check("secret" not in names and "hid" not in names, "a credential store is dropped from the list")


def test_coding_sessions() -> None:
    cid = chat()
    now = time.time()
    with DB.tx() as c:
        for i, conv in enumerate((cid, None, "coding:abc")):
            c.execute("INSERT INTO coding_sessions(id,agent,repo_path,worktree,conversation_id,name,prompt,status,created_at,updated_at) "
                      "VALUES(?,'claude','/r',?,?,?,'p','done',?,?)", (f"cs{cid}{i}", f"/wt/{cid}/{i}", conv, f"job{i}", now, now))
    r = rows(None, "coding")
    mine = [x for x in r if x["conversation_id"] == cid]
    check(len(mine) == 1 and mine[0]["name"] == "job0" and mine[0]["ref"] == f"/wt/{cid}/0" and mine[0]["action"] == "created", "coding session -> coding row")
    check(not [x for x in r if x["conversation_id"] in ("coding:abc", None)], "internal coding chats and unattached sessions are skipped")


def test_backfill_idempotent() -> None:
    cid = chat()
    CONVOS.add_message(cid, "user", "hi", attachments=[{"id": "bf", "name": "b.txt"}])
    CHATS.save_bytes(cid, "outputs/bf/out.txt", b"x")
    (CHATS.desk_root(cid) / "outputs" / ".hidden").write_text("h")
    (CHATS.desk_root(cid) / "outputs" / "link").symlink_to(HOME / "Desktop")
    (CHATS.root / "ghost-chat" / "outputs").mkdir(parents=True)
    (CHATS.root / "ghost-chat" / "outputs" / "g.txt").write_text("g")
    snap("create", HOME / "Desktop" / "bf.md", cid)
    with DB.tx() as c:
        c.execute("INSERT INTO coding_sessions(id,agent,repo_path,worktree,conversation_id,name,prompt,status,created_at,updated_at) "
                  "VALUES('csbf','claude','/r','/wt/bf',?,'bf','p','done',1,1)", (cid,))
    want = rows(cid)
    check(len(want) == 4, f"four sources recorded live: {len(want)}")
    with DB.tx() as c:
        c.execute("DELETE FROM chat_files")
    first = CF.backfill()
    got = rows()
    check(first == len(got) and {(x["kind"], x["ref"]) for x in got if x["conversation_id"] == cid} == {(x["kind"], x["ref"]) for x in want},
          "backfill rebuilds what the triggers recorded (dotfiles, symlinks and unknown chats skipped)")
    check(CF.backfill() == 0 and [x["id"] for x in rows()] == [x["id"] for x in got], "a second run inserts nothing and keeps every id")


def _filled() -> tuple[str, str, list[str]]:
    """Two chats (one in a project), five uploads in the personal one, a trashed doc and a deleted upload."""
    pid = PROJECTS.create("P")["id"]
    cid, pc = chat(), chat(pid)
    ids = []
    for i in range(5):
        d = DOCUMENTS.create(None, f"f{i}.txt", "text/plain", 1, "", "t")
        ids.append(d["id"])
        CONVOS.add_message(cid, "user", "x", attachments=[{"id": d["id"], "name": f"stale{i}"}])
    CONVOS.add_message(pc, "user", "x", attachments=[{"id": ids[0], "name": "p.txt"}])
    return cid, pc, ids


def test_routes_paging_scope_and_counts() -> None:
    cid, pc, ids = _filled()
    with DB.tx() as c:  # distinct, known times so keyset paging is deterministic
        for i, d in enumerate(ids):
            c.execute("UPDATE chat_files SET created_at=? WHERE conversation_id=? AND ref=?", (1000.0 + i, cid, d))
    got = CL.get(f"/conversations/{cid}/files").json()["files"]
    check([f["name"] for f in got] == [f"f{i}.txt" for i in (4, 3, 2, 1, 0)], "newest first, name is the document's current name")
    check(all(f["conversation_title"] == "t" and f["rel"] is None and f["missing"] is False for f in got), "row shape")
    seen, cur = [], None
    for _ in range(100):
        r = CL.get("/chat-files", params={"scope": "personal", "limit": 2, **({"cursor": cur} if cur else {})}).json()
        seen += [f["id"] for f in r["files"] if f["conversation_id"] == cid]
        cur = r["next_cursor"]
        if not cur:
            break
    check(not cur and len(seen) == len(set(seen)) and len(seen) == 5, "cursor paging visits every row once and ends with None")
    check(CL.get("/chat-files?scope=bogus").status_code == 400 and CL.get("/chat-files?cursor=zzz").status_code == 400, "bad scope / cursor -> 400")
    check(CL.get("/conversations/nope/files").status_code == 404, "unknown conversation -> 404")
    allc = {f["conversation_id"] for f in CL.get("/chat-files?limit=200").json()["files"]}
    pers = {f["conversation_id"] for f in CL.get("/chat-files?scope=personal&limit=200").json()["files"]}
    check(pc in allc and pc not in pers and cid in pers, "scope=personal excludes a project chat")
    counts = CL.get("/chat-files/counts").json()["counts"]
    check(counts[cid] == 5 and counts[pc] == 1 and pc not in CL.get("/chat-files/counts?scope=personal").json()["counts"], "counts route")
    with DB.tx() as c:
        c.execute("UPDATE documents SET deleted_at=1 WHERE id=?", (ids[0],))
        c.execute("DELETE FROM documents WHERE id=?", (ids[1],))
    check(len(CL.get(f"/conversations/{cid}/files").json()["files"]) == 3 and CL.get("/chat-files/counts").json()["counts"][cid] == 3,
          "trashed and deleted uploads are skipped, in the list and the counts alike")
    made = call("doc_create", {"title": "Gone soon"}, {"conversation_id": cid, "project_id": None})
    check(len(CL.get(f"/conversations/{cid}/files").json()["files"]) == 4, "a note shows")
    with DB.tx() as c:
        c.execute("UPDATE docs SET deleted_at=1 WHERE id=?", (made["doc_id"],))
    check(len(CL.get(f"/conversations/{cid}/files").json()["files"]) == 3, "a trashed note is skipped")
    with DB.tx() as c:
        c.execute("UPDATE conversations SET deleted_at=1 WHERE id=?", (cid,))
    check(CL.get(f"/conversations/{cid}/files").status_code == 404 and cid not in CL.get("/chat-files/counts").json()["counts"],
          "a trashed chat hides its files")


def _walk(**params: Any) -> list[dict[str, Any]]:
    out, cur = [], None
    for _ in range(100):
        r = CL.get("/chat-files", params={**params, **({"cursor": cur} if cur else {})}).json()
        out += r["files"]
        cur = r["next_cursor"]
        if not cur:
            return out
    raise AssertionError("cursor never ended")


def test_project_scope() -> None:
    P, Q = PROJECTS.create("PS-P")["id"], PROJECTS.create("PS-Q")["id"]
    p1, p2, q1, pers = chat(P), chat(P), chat(Q), chat()
    ctx = {"message_id": "m", "settings": {"docEditMode": "apply"}}
    shared = call("doc_create", {"title": "Shared", "content": "a\n"}, {**ctx, "conversation_id": p1, "project_id": P})["doc_id"]
    call("doc_edit", {"doc": shared, "edits": [{"find": "a", "replace": "b"}]}, {**ctx, "conversation_id": p2, "project_id": P})
    direct = DOCS.create("Direct", "x", P)["id"]
    trashed = DOCS.create("Trashed", "x", P)["id"]
    up_both = DOCUMENTS.create(P, "both.txt", "text/plain", 1, "", "t")["id"]
    up_direct = DOCUMENTS.create(P, "direct.txt", "text/plain", 1, "", "t")["id"]
    up_trash = DOCUMENTS.create(P, "trash.txt", "text/plain", 1, "", "t")["id"]
    CONVOS.add_message(p1, "user", "x", attachments=[{"id": up_both, "name": "both.txt"}])
    CONVOS.add_message(p2, "user", "x", attachments=[{"id": up_both, "name": "both.txt"}])
    CHATS.save_bytes(p1, "outputs/p.csv", b"a")
    CHATS.save_bytes(q1, "outputs/q.csv", b"a")
    snap("create", HOME / "Desktop" / "q-only.md", q1)
    CHATS.save_bytes(pers, "outputs/personal.csv", b"a")
    with DB.tx() as c:  # known times: p1 note 100, p2 edit 200, direct note created 300 then edited 400
        c.execute("UPDATE chat_files SET created_at=100 WHERE conversation_id=? AND ref=?", (p1, shared))
        c.execute("UPDATE chat_files SET created_at=200 WHERE conversation_id=? AND ref=?", (p2, shared))
        c.execute("UPDATE docs SET created_at=100, updated_at=100 WHERE id=?", (shared,))
        c.execute("UPDATE docs SET created_at=300, updated_at=300 WHERE id=?", (direct,))
        c.execute("UPDATE docs SET deleted_at=1 WHERE id=?", (trashed,))
        c.execute("UPDATE documents SET created_at=500 WHERE id=?", (up_direct,))
        c.execute("UPDATE documents SET created_at=50, deleted_at=1 WHERE id=?", (up_trash,))
        c.execute("UPDATE documents SET created_at=60 WHERE id=?", (up_both,))
        c.execute("UPDATE chat_files SET created_at=700 WHERE conversation_id=? AND ref=?", (p1, up_both))
        c.execute("UPDATE chat_files SET created_at=650 WHERE conversation_id=? AND ref=?", (p2, up_both))
        c.execute("UPDATE chat_files SET created_at=10 WHERE kind='output' AND conversation_id=?", (p1,))
    files = _walk(scope="project", project_id=P, limit=200)
    by = {f["ref"]: f for f in files}
    check(len(files) == len(by) == 5 and {f["kind"] for f in files} == {"note", "upload", "output"} and {shared, direct, up_both, up_direct} < set(by),
          f"project P is the deduped union, no trashed items, nothing from Q or personal: {[f['name'] for f in files]}")
    s = by[shared]
    check(s["chat_count"] == 2 and s["conversation_id"] == p2 and s["action"] == "edited" and s["created_at"] == 200 and s["kind"] == "note"
          and s["project_id"] == P and s["id"] == f"note:{shared}", "a note touched by two chats is one item with the newest chat")
    d = by[direct]
    check(d["conversation_id"] is None and d["chat_count"] == 0 and d["action"] == "created" and d["created_at"] == 300, "direct note: no chat, created")
    with DB.tx() as c:
        c.execute("UPDATE docs SET updated_at=400 WHERE id=?", (direct,))
        c.execute("UPDATE docs SET updated_at=450 WHERE id=?", (shared,))
    again = {f["ref"]: f for f in CL.get("/chat-files", params={"scope": "project", "project_id": P, "limit": 200}).json()["files"]}
    check(again[direct]["action"] == "edited" and again[shared]["created_at"] == 450 and again[shared]["conversation_id"] == p2 and again[shared]["chat_count"] == 2,
          "a direct edit is the newest activity but the newest chat id is kept")
    u = by[up_direct]
    check(u["conversation_id"] is None and u["chat_count"] == 0 and u["action"] == "uploaded" and u["kind"] == "upload" and u["pinned"] is False, "direct upload")
    b = by[up_both]
    check(b["chat_count"] == 2 and b["conversation_id"] == p1 and b["action"] == "attached" and b["created_at"] == 700, "attached in a P chat and owned by P: one item")
    DOCUMENTS.set_pinned(up_both, True)
    check(next(f for f in _walk(scope="project", project_id=P) if f["ref"] == up_both)["pinned"] is True, "pinned follows documents.pinned")
    out = next(f for f in files if f["kind"] == "output")
    check(out["rel"] == "outputs/p.csv" and out["conversation_id"] == p1 and not out["missing"], "output rel uses its chat")
    q = _walk(scope="project", project_id=Q)
    check(sorted(f["kind"] for f in q) == ["local", "output"] and all(f["conversation_id"] == q1 for f in q), "Q has only its own chat's files")
    order = [(f["created_at"], f["id"]) for f in _walk(scope="project", project_id=P, limit=200)]
    check(order == sorted(order, reverse=True), "newest first, id breaks ties")
    for lim in (1, 2, 3):
        pg = _walk(scope="project", project_id=P, limit=lim)
        check([f["id"] for f in pg] == [f["id"] for f in _walk(scope="project", project_id=P, limit=200)] and len({f["id"] for f in pg}) == 5,
              f"limit={lim} walks the merged set with no dupes or gaps")
    n = CL.get("/chat-files/counts").json()
    check(n["projects"][P] == 5 and n["projects"][Q] == 2 and pers not in n["projects"], f"project counts match the lists: {n['projects']}")
    check(isinstance(n["counts"], dict) and n["counts"][p1] >= 1 and n["counts"][pers] == 1, "per-chat counts unchanged")
    check(p1 not in CL.get("/chat-files/counts?scope=personal").json()["counts"], "per-chat counts still honour scope")
    pf_rows = _walk(scope="personal", limit=200)
    mine = [f for f in pf_rows if f["conversation_id"] == pers]
    check(len(mine) == 1 and mine[0]["kind"] == "output" and mine[0]["pinned"] is False and mine[0]["chat_count"] == 1 and not any(f["conversation_id"] in (p1, p2, q1) for f in pf_rows),
          "personal scope is unchanged")
    check(CL.get("/chat-files?scope=project").status_code == 400 and CL.get("/chat-files?scope=nope").status_code == 400, "project scope needs project_id")
    check(CL.get("/chat-files", params={"scope": "project", "project_id": "none"}).json() == {"files": [], "next_cursor": None}, "unknown project is empty")
    check(CL.get("/chat-files?scope=project&project_id=x&cursor=zzz").status_code == 400, "bad cursor -> 400")
    with DB.tx() as c:
        c.execute("UPDATE conversations SET deleted_at=1 WHERE id IN (?,?)", (p1, p2))
    after = {f["ref"]: f for f in _walk(scope="project", project_id=P, limit=200)}
    check(set(after) == {shared, direct, up_both, up_direct} and after[shared]["chat_count"] == 0 and after[up_both]["conversation_id"] is None and after[up_both]["chat_count"] == 0,
          "trashed chats drop out; directly owned items stay")


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
