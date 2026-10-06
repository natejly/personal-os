"""Whole-folder snapshots and undo (snapshots.py). Offline: temp roots and a temp data dir.

Run: python backend/tests/test_snapshots.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import snapshots as sn  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.runs import RunStore  # noqa: E402
from personal_os.snapshots import Snapshots  # noqa: E402

passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def sh(root: Path, script: str) -> None:
    subprocess.run(["sh", "-c", script], cwd=root, check=True, capture_output=True)


def git_out(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True).stdout


DB = Database(tempfile.mkdtemp(prefix="snapdb-"))
store = RunStore(DB)
ROOT = Path(tempfile.mkdtemp(prefix="snaproot-")).resolve()
DESKS = Path(tempfile.mkdtemp(prefix="snapdesk-")).resolve()
SETTINGS: dict[str, Any] = {"workspaceRoots": [str(ROOT)]}
S = Snapshots(DB, DB.data_dir / "snapshots", lambda: SETTINGS, lambda d: DESKS / d)

check(sn.available(), "git is present")

# The user's own repo in the root, to prove we never touch it.
sh(ROOT, "git init -q . && echo hello > keep.txt && git add keep.txt && git -c user.name=u -c user.email=u@x commit -qm init")
own_head = git_out(ROOT, "rev-parse", "HEAD")
own_index = (ROOT / ".git" / "index").read_bytes()

(ROOT / "draft.md").write_text("draft v1\n")
(ROOT / "sub").mkdir()
(ROOT / "sub" / "a.txt").write_text("a\n")
(ROOT / "node_modules").mkdir()
(ROOT / "node_modules" / "x.js").write_text("x")
(ROOT / "big.bin").write_bytes(b"0" * (sn.MAX_FILE_BYTES + 10))
(ROOT / ".gitignore").write_text("ignored.log\n")
(ROOT / "ignored.log").write_text("noise")

run_id = "run-1"
store.create(run_id, None)

# roots and call classification
check(S.roots() == [ROOT], "granted root listed")
(DESKS / "d1").mkdir()
check(S.roots("d1") == [ROOT, DESKS / "d1"], "desk workspace is a root")
check(sn.read_only_shell("ls -la | grep foo") and sn.read_only_shell("git status"), "read-only shell recognised")
check(not sn.read_only_shell("rm draft.md && python build.py") and not sn.read_only_shell("echo hi > f")
      and not sn.read_only_shell("find . -delete") and not sn.read_only_shell("git commit -m x"), "mutating shell recognised")
check(not any(sn.read_only_shell(c) for c in ("sort -o notes.txt notes.txt", "uniq a.txt b.txt", "tree -o out.txt",
                                                "git diff --output=patch.diff")), "commands that write by flag are mutating")
check(S.wants("shell_run", {"command": "rm draft.md && python build.py"}, None), "mutating shell is snapshotted")
check(not S.wants("shell_run", {"command": "ls"}, None), "read-only shell is not")
check(S.wants("write_local_file", {"path": str(ROOT / "new.txt")}, None), "write inside a root is snapshotted")
check(not S.wants("write_local_file", {"path": "/tmp/elsewhere.txt"}, None), "write outside every root is not")
check(not S.wants("web_search", {}, None), "other tools are not")

S.before(run_id, S.roots_for_call("shell_run", {"command": "rm draft.md"}, None))
S.before(run_id, [ROOT])  # once per run per root
rows = S.rows(run_id)
check(len(rows) == 1 and rows[0]["before_tree"], "one before-snapshot")
check(any("big.bin" in x for x in rows[0]["skipped"]), "oversize file reported as skipped")

# the "run": delete, modify, create (via a subprocess, like shell_run would)
sh(ROOT, "rm draft.md && echo 'a changed' > sub/a.txt && mkdir -p out/deep && echo built > out/deep/build.txt")
S.finish(run_id)
row = S.rows(run_id)[0]
by = {f["path"]: f["status"] for f in row["files"]}
check(by == {"draft.md": "D", "sub/a.txt": "M", "out/deep/build.txt": "A"}, f"ledger lists the three changes: {by}")

summ = S.summary(run_id)
check(summ["count"] == 3 and summ["state"] == "applied", "summary counts")

res = S.undo(run_id)
check(sorted(res["reverted"]) == sorted(by) and not res["edited_since"], "undo reverted all three")
check((ROOT / "draft.md").read_text() == "draft v1\n", "deleted file is back")
check((ROOT / "sub" / "a.txt").read_text() == "a\n", "modified file restored")
check(not (ROOT / "out").exists(), "created file and its emptied directories removed")
check((ROOT / "node_modules" / "x.js").exists() and (ROOT / "big.bin").exists(), "skipped files untouched")
check(S.summary(run_id)["state"] == "undone", "state is undone")
try:
    S.undo(run_id)
    check(False, "double undo refused")
except sn.SnapshotError:
    check(True, "double undo refused")

res = S.redo(run_id)
check(sorted(res["reverted"]) == sorted(by), "redo reapplied all three")
check(not (ROOT / "draft.md").exists() and (ROOT / "sub" / "a.txt").read_text() == "a changed\n"
      and (ROOT / "out" / "deep" / "build.txt").read_text() == "built\n", "redo state matches the run's result")

# a later edit: undo reverts the rest and reports that file as edited since
(ROOT / "sub" / "a.txt").write_text("user edited afterwards\n")
res = S.undo(run_id)
check(res["edited_since"] == ["sub/a.txt"], "later edit is reported, not clobbered")
check((ROOT / "sub" / "a.txt").read_text() == "user edited afterwards\n", "later edit kept")
check((ROOT / "draft.md").exists() and not (ROOT / "out").exists(), "untouched paths still reverted")

# the user's own repo is untouched
check(git_out(ROOT, "rev-parse", "HEAD") == own_head, "own HEAD unchanged")
check((ROOT / ".git" / "index").read_bytes() == own_index, "own index unchanged")
check(not list((ROOT / ".git").glob("index-*")) and not (ROOT / ".git" / "refs" / "grain").exists(), "nothing of ours in the user's .git")

# a run that changed nothing has an empty ledger
store.create("run-2", None)
S.before("run-2", [ROOT])
S.finish("run-2")
check(S.summary("run-2")["count"] == 0, "no change, no footer")

# retention: only the newest KEEP_PER_ROOT snapshots stay
for i in range(sn.KEEP_PER_ROOT + 3):
    (ROOT / "tick.txt").write_text(str(i))
    S.track(ROOT)
check(len(S._refs(sn._h16(ROOT))) == sn.KEEP_PER_ROOT, "snapshots per root are capped")
check(S.prune() >= 0, "prune runs")

# too many files: reported, not snapshotted
BIG = Path(tempfile.mkdtemp(prefix="snapbig-")).resolve()
(BIG / "a").write_text("x")
old = sn.MAX_FILES
sn.MAX_FILES = 0
t = S.track(BIG)
sn.MAX_FILES = old
check(t["tree"] is None and t["skipped"], "file-count cap skips and reports")

# routes
app = FastAPI()
app.include_router(sn.router(S, lambda rid: store.get(rid) is not None))
c = TestClient(app)
(ROOT / "r.txt").write_text("1")
store.create("run-3", None)
S.before("run-3", [ROOT])
(ROOT / "r.txt").write_text("2")
S.finish("run-3")
check(c.get("/runs/run-3/changes").json()["count"] == 1, "GET changes")
check(c.get("/runs/nope/changes").status_code == 404, "unknown run 404")
store.update("run-3", message_id="msg-3")
mc = c.get("/messages/msg-3/changes").json()
check(mc["run_id"] == "run-3" and mc["count"] == 1, "changes found from the message")
check(c.get("/messages/other/changes").json()["count"] == 0, "message with no run has none")
check(c.post("/runs/run-3/undo").json()["reverted"] == ["r.txt"] and (ROOT / "r.txt").read_text() == "1", "POST undo")
check(c.post("/runs/run-3/undo").status_code == 409, "second undo is a 409")
check(c.post("/runs/run-3/redo").json()["reverted"] == ["r.txt"] and (ROOT / "r.txt").read_text() == "2", "POST redo")

# after `done` the changes are readable at once, even while the run is still closing (auto-learn tail)
(ROOT / "d.txt").write_text("1")
store.create("run-4", None)
store.update("run-4", message_id="msg-4")
S.before("run-4", [ROOT])
(ROOT / "d.txt").write_text("2")
check(c.get("/messages/msg-4/changes").json()["count"] == 0, "before done the run is still open")
store.append("run-4", 1, "done", {"id": "msg-4", "error": None, "segment": True})
check(c.get("/messages/msg-4/changes").json()["count"] == 0, "a segment's done does not settle the run")
store.append("run-4", 2, "done", {"id": "msg-4", "error": None, "segment": False, "context_used": {"x": "segment"}})
mc = c.get("/messages/msg-4/changes").json()
check(mc.get("run_id") == "run-4" and mc["count"] == 1, "after done the changes show without waiting for the run to close")
check(c.post("/runs/run-4/undo").json()["reverted"] == ["d.txt"] and (ROOT / "d.txt").read_text() == "1", "undo right after done")

# a run whose snapshot trees were pruned: undo refuses and leaves the state alone
(ROOT / "e.txt").write_text("1")
store.create("run-5", None)
S.before("run-5", [ROOT])
(ROOT / "e.txt").write_text("2")
S.finish("run-5")
with DB.tx() as cx:
    cx.execute("UPDATE run_snapshots SET before_tree=? WHERE run_id=?", ("0" * 40, "run-5"))
r5 = c.post("/runs/run-5/undo")
check(r5.status_code == 409 and "expired" in r5.json()["detail"], "expired snapshot: undo is a 409")
check(S.summary("run-5")["state"] == "applied" and (ROOT / "e.txt").read_text() == "2", "expired snapshot: nothing changed")

# without git the feature reports itself unavailable
real = sn.shutil.which
sn.shutil.which = lambda _n: None  # type: ignore[assignment]
check(not S.enabled() and not S.wants("shell_run", {"command": "rm x"}, None), "no git: nothing is snapshotted")
check(c.post("/runs/run-3/undo").status_code == 503, "no git: routes say unavailable")
sn.shutil.which = real  # type: ignore[assignment]

print(f"ok: {passed} checks")
