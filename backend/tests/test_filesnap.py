"""Pre-image snapshots and undo for local file writes (filesnap.py). Offline: a temp HOME and a temp data dir.

Run: python backend/tests/test_filesnap.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HOME = Path(tempfile.mkdtemp(prefix="snaphome-")).resolve()
os.environ["HOME"] = str(HOME)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import mac  # noqa: E402
from personal_os import filesnap as fsmod  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.filesnap import Conflict, FileSnapshots  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402

(HOME / "Desktop").mkdir()
(HOME / "Library").mkdir()
DB = Database(tempfile.mkdtemp(prefix="snapdb-"))
SETTINGS: dict[str, Any] = {}
FS = FileSnapshots(DB, DB.data_dir / "snapshots", lambda: SETTINGS)
CTX = {"conversation_id": "c1", "message_id": "m1"}
passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def _fake_trash(path: str) -> dict[str, Any]:
    p = mac.allowed_path(path)
    t = HOME / "FakeTrash"
    t.mkdir(exist_ok=True)
    dst = t / f"{time.time_ns()}-{p.name}"
    p.rename(dst)
    return {"path": str(p), "trashed_to": str(dst)}


mac.trash_local = _fake_trash  # type: ignore[assignment]


def write(path: Path, content: str, mode: str) -> str | None:
    """What the tool does: capture, write, finalize."""
    snap = FS.capture(mode, str(path), CTX)
    mac.write_local(str(path), content, mode)
    sid = snap and snap.get("snapshot_id")
    if sid:
        FS.finalize(sid, str(path))
    return sid


def test_overwrite_and_undo_of_undo() -> None:
    f = HOME / "Desktop" / "x.md"
    f.write_text("original")
    sid = write(f, "agent version", "overwrite")
    check(sid and f.read_text() == "agent version", "overwritten, snapshot taken")
    r = FS.restore(sid)
    check(f.read_text() == "original", "restore brings the old bytes back")
    check(r["undo_snapshot_id"], "the restore has its own snapshot")
    FS.restore(r["undo_snapshot_id"])
    check(f.read_text() == "agent version", "undo of the undo returns the agent's version")
    try:
        FS.restore(sid)
        check(False, "an already restored snapshot cannot be restored twice")
    except fsmod.Unavailable:
        check(True, "already restored refuses")


def test_append() -> None:
    f = HOME / "Desktop" / "log.md"
    f.write_text("a\n")
    sid = write(f, "b\n", "append")
    check(f.read_text() == "a\nb\n", "appended")
    FS.restore(sid)
    check(f.read_text() == "a\n", "append undone")


def test_create_then_restore_trashes() -> None:
    f = HOME / "Desktop" / "new.md"
    sid = write(f, "fresh", "create")
    check(sid and f.exists(), "created with a snapshot")
    r = FS.restore(sid)
    check(not f.exists(), "undo of a create trashes the file")
    FS.restore(r["undo_snapshot_id"])
    check(f.read_text() == "fresh", "and that is undoable too")


def test_move() -> None:
    a, b = HOME / "Desktop" / "a.txt", HOME / "Desktop" / "b.txt"
    a.write_text("m")
    snap = FS.capture("move", str(a), CTX, str(b))
    mac.move_local(str(a), str(b))
    FS.finalize(snap["snapshot_id"], str(b))
    r = FS.restore(snap["snapshot_id"])
    check(a.exists() and not b.exists(), "moved back")
    FS.restore(r["undo_snapshot_id"])
    check(b.exists() and not a.exists(), "undoing the undo moves it forward again")
    snap = FS.capture("move", str(b), CTX, str(a))
    mac.move_local(str(b), str(a))
    a_occupant = b
    a_occupant.write_text("someone else")
    try:
        FS.restore(snap["snapshot_id"])
        check(False, "occupied source refuses")
    except Conflict:
        check(True, "restore refuses when the original path is occupied")


def test_conflict_and_force() -> None:
    f = HOME / "Desktop" / "c.md"
    f.write_text("one")
    sid = write(f, "two", "overwrite")
    f.write_text("user edit")
    try:
        FS.restore(sid)
        check(False, "conflict expected")
    except Conflict:
        check(f.read_text() == "user edit", "a changed file is never silently clobbered")
    FS.restore(sid, force=True)
    check(f.read_text() == "one", "force restores anyway")


def test_too_large_still_writes() -> None:
    f = HOME / "Desktop" / "big.txt"
    f.write_text("x" * 100)
    SETTINGS["fileSnapshotMaxBytes"] = 10
    try:
        snap = FS.capture("overwrite", str(f), CTX)
    finally:
        SETTINGS.pop("fileSnapshotMaxBytes")
    check(snap["snapshot_id"] is None and "larger" in snap["reason"], "no snapshot over the limit, with a reason")
    mac.write_local(str(f), "new", "overwrite")
    check(f.read_text() == "new", "the write is never blocked")


def test_prune() -> None:
    f = HOME / "Desktop" / "p.md"
    f.write_text("p" * 1000)
    old = write(f, "q", "overwrite")
    f.write_text("p" * 1000)
    newer = write(f, "r", "overwrite")
    blob = DB.data_dir / "snapshots" / old
    check(blob.exists(), "blob exists")
    with DB.tx() as c:
        c.execute("UPDATE file_snapshots SET created_at=? WHERE snapshot_id=?", (time.time() - 40 * 86400, old))
    FS.prune()
    check(not blob.exists(), "aged-out blob removed")
    check(FS._row(old)["status"] == "expired", "row marked expired")
    check(FS._row(newer)["status"] == "live", "recent row kept")
    SETTINGS["fileSnapshotBudgetMB"] = 0.0001  # 100 bytes
    try:
        FS.prune()
    finally:
        SETTINGS.pop("fileSnapshotBudgetMB")
    check(FS._row(newer)["status"] == "expired", "over-budget blobs expire oldest first")


def test_paths_rejected() -> None:
    for bad in ("/etc/passwd", "~/Library/x.txt", "~/.ssh/k"):
        try:
            FS.capture("overwrite", bad, CTX)
            check(False, f"{bad} should be rejected")
        except mac.LocalPathError:
            check(True, f"{bad} rejected")


def test_failed_write_leaves_no_row() -> None:
    tb = Toolbox(None, None, None, lambda: {}, filesnap=FS)  # type: ignore[arg-type]
    f = HOME / "Desktop" / "fail.md"
    f.write_text("keep")
    before = len(FS.list())
    out = asyncio.run(tb.call("write_local_file", {"path": str(f), "content": "x", "mode": "bogus"}, dict(CTX)))
    check(out.get("error"), "bad mode is an error")
    check(len(FS.list()) == before, "no dangling live row after a failed write")
    ctx = dict(CTX)
    asyncio.run(tb.call("read_local_file", {"path": str(f)}, ctx))  # an overwrite needs the file read first
    out = asyncio.run(tb.call("write_local_file", {"path": str(f), "content": "x", "mode": "overwrite"}, ctx))
    check(out.get("undo", {}).get("snapshot_id"), "a good write returns undo.snapshot_id")
    out2 = asyncio.run(tb.call("move_local_file", {"path": str(f), "to": str(HOME / "Desktop" / "moved.md")}, dict(CTX)))
    check(out2.get("undo", {}).get("snapshot_id"), "move returns undo too")
    # nothing in the tool surface can restore or delete snapshots
    check(not [n for n in tb.specs if "snapshot" in n or "restore" in n or "undo" in n], "no model tool restores snapshots")


def test_toolbox_without_filesnap_unchanged() -> None:
    tb = Toolbox(None, None, None, lambda: {})  # type: ignore[arg-type]
    f = HOME / "Desktop" / "plain.md"
    out = asyncio.run(tb.call("write_local_file", {"path": str(f), "content": "hi"}, {}))
    check(out.get("created") is True and "undo" not in out, "no filesnap: same result shape, no undo key")


def test_setting_off() -> None:
    SETTINGS["fileSnapshots"] = False
    try:
        f = HOME / "Desktop" / "off.md"
        f.write_text("a")
        check(FS.capture("overwrite", str(f), CTX) is None, "disabled -> no capture")
    finally:
        SETTINGS.pop("fileSnapshots")


def test_routes() -> None:
    app = FastAPI()
    app.include_router(fsmod.router(FS))
    cl = TestClient(app)
    check(cl.post("/file-snapshots/nope/restore", json={}).status_code == 404, "unknown -> 404")
    f = HOME / "Desktop" / "r.md"
    f.write_text("orig")
    sid = write(f, "new", "overwrite")
    f.write_text("edited")
    r = cl.post(f"/file-snapshots/{sid}/restore", json={})
    check(r.status_code == 409 and r.json()["detail"]["conflict"], "conflict -> 409")
    r = cl.post(f"/file-snapshots/{sid}/restore", json={"force": True})
    check(r.status_code == 200 and r.json()["ok"] and f.read_text() == "orig", "force -> 200")
    check(cl.post(f"/file-snapshots/{sid}/restore", json={}).status_code == 409, "already restored -> 409")
    check(any(x["snapshot_id"] == sid for x in cl.get("/file-snapshots?conversation_id=c1").json()), "list by conversation")
    check(all("before_path" not in x for x in cl.get("/file-snapshots").json()), "blob paths are not exposed")


if __name__ == "__main__":
    failed = 0
    for t in [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]:
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
