"""Desk workspaces: path containment first, then quotas, trash and diffs.

`Workspace.resolve_in` is the only thing standing between `desk_write_file` and the rest of the
disk, and an autonomous agent is the caller, so the traversal cases are written first and asserted
hardest: a `..` segment, an absolute path, a `~`, and — the one a string check misses — a symlink
the agent planted inside its own workspace that points out of it.

Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_workspace.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="wstest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.workspace import (  # noqa: E402
    BLOCKED_SUFFIXES, MAX_FILE_CHARS, MAX_FILES, MAX_TOTAL_BYTES, Workspace, WorkspaceError,
)

passed = 0
DATA_DIR = Path(os.environ["PERSONAL_OS_DATA_DIR"])
OUTSIDE = Path(tempfile.mkdtemp(prefix="wsoutside-"))


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def refuses(fn: Any, label: str, *, contains: str = "") -> WorkspaceError:
    """Assert the call raises WorkspaceError — never a bare OSError, never a silent success."""
    try:
        got = fn()
    except WorkspaceError as e:
        check(not contains or contains in str(e), f"{label}: message mentions {contains!r}, got {e}")
        return e
    raise AssertionError(f"{label}: expected WorkspaceError, got {got!r}")


def fresh(desk_id: str) -> Workspace:
    ws = Workspace(DATA_DIR)
    shutil.rmtree(ws.desk_root(desk_id), ignore_errors=True)
    ws.ensure(desk_id)
    return ws


# ---- layer 1: containment ----
def test_layout() -> None:
    ws = fresh("d-layout")
    root = ws.desk_root("d-layout")
    check(root == DATA_DIR / "cowork" / "d-layout", "desk root is <data_dir>/cowork/<desk_id>")
    for sub in ("outputs", "work", ".baseline", ".trash"):
        check((root / sub).is_dir(), f"ensure() creates {sub}/")
    check(ws.rel_root("d-layout") == "cowork/d-layout", "the stored workspace path is relative, not absolute")
    check(ws.ensure("d-layout") == root, "ensure() is idempotent and returns the root")


def test_resolve_in_refuses_traversal() -> None:
    ws = fresh("d-trav")
    for bad in ("../escape.txt", "work/../../escape.txt", "a/b/../../../escape.txt", ".."):
        refuses(lambda b=bad: ws.resolve_in("d-trav", b), f"'..' segment in {bad!r}", contains="..")
    refuses(lambda: ws.resolve_in("d-trav", "work/../outputs/ok.txt"),
            "a '..' segment is rejected even when it would have stayed inside")


def test_resolve_in_refuses_absolute() -> None:
    ws = fresh("d-abs")
    for bad in ("/etc/passwd", "/", str(OUTSIDE / "loot.txt"), "~/Documents/loot.txt", "~"):
        refuses(lambda b=bad: ws.resolve_in("d-abs", b), f"absolute or home path {bad!r}")
    refuses(lambda: ws.resolve_in("d-abs", "a\x00b"), "NUL byte in a path")
    refuses(lambda: ws.resolve_in("d-abs", "   "), "a blank path names nothing")


def test_resolve_in_refuses_symlink_escape() -> None:
    """The case a string check misses: every segment is clean, the resolved path is not."""
    ws = fresh("d-link")
    root = ws.desk_root("d-link")
    secret = OUTSIDE / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    (root / "work" / "door").symlink_to(OUTSIDE)            # a directory symlink out
    (root / "work" / "leak.txt").symlink_to(secret)         # a file symlink out

    refuses(lambda: ws.resolve_in("d-link", "work/door/secret.txt"), "read through a directory symlink")
    refuses(lambda: ws.resolve_in("d-link", "work/door/new.txt"), "write through a directory symlink (target absent)")
    refuses(lambda: ws.resolve_in("d-link", "work/leak.txt"), "read through a file symlink")
    refuses(lambda: ws.write("d-link", "work/door/owned.txt", "x", mode="overwrite"), "write() goes through resolve_in")
    refuses(lambda: ws.trash("d-link", "work/door/secret.txt"), "trash() goes through resolve_in")
    check(secret.read_text(encoding="utf-8") == "private", "nothing outside the workspace was touched")

    (root / "work" / "home").symlink_to(root / "outputs")   # a symlink that stays inside is fine
    p = ws.resolve_in("d-link", "work/home/fine.txt")
    check(p == (root / "outputs" / "fine.txt").resolve(), "an inside-pointing symlink resolves and is allowed")


def test_resolve_in_refuses_cross_desk() -> None:
    """Layer 2: a desk id is a path segment too, so it gets the same paranoia."""
    ws = fresh("d-self")
    fresh("d-other")
    for bad in ("../d-other", "/tmp", "a/b", "", ".", "..", "has space"):
        refuses(lambda b=bad: ws.desk_root(b), f"desk id {bad!r} is not a bare id")
    root = ws.desk_root("d-self")
    check(ws.resolve_in("d-self", "work/notes.md") == (root / "work" / "notes.md").resolve(),
          "a plain relative path resolves under the desk root")
    check(ws.resolve_in("d-self", "") == root.resolve(), "the empty path is the desk root itself")


# ---- writes, modes, quotas ----
def test_write_modes() -> None:
    ws = fresh("d-write")
    out = ws.write("d-write", "work/note.md", "one\n")
    check(out["created"] and out["bytes"] == 4, "create writes a new file")
    e = refuses(lambda: ws.write("d-write", "work/note.md", "two\n"), "create refuses to clobber",
                contains="already exists")
    check("overwrite" in str(e), "the clobber error names the mode that would work")
    check(ws.read("d-write", "work/note.md")["text"] == "one\n", "the refused write changed nothing")

    ws.write("d-write", "work/note.md", "two\n", mode="append")
    check(ws.read("d-write", "work/note.md")["text"] == "one\ntwo\n", "append appends")
    ws.write("d-write", "work/note.md", "three\n", mode="overwrite")
    check(ws.read("d-write", "work/note.md")["text"] == "three\n", "overwrite replaces")
    refuses(lambda: ws.write("d-write", "work/note.md", "x", mode="patch"), "an unknown mode is refused")
    refuses(lambda: ws.write("d-write", "work", "x", mode="overwrite"), "a directory is not a file")
    refuses(lambda: ws.write("d-write", ".baseline/note.md", "x"), "the bookkeeping dirs are not writable",
            contains="reserved")
    refuses(lambda: ws.write("d-write", ".trash/note.md", "x"), "the trash is not writable")


def test_write_refuses_blocked_suffixes() -> None:
    ws = fresh("d-suffix")
    for suffix in BLOCKED_SUFFIXES:
        refuses(lambda s=suffix: ws.write("d-suffix", f"outputs/payload{s}", "#!/bin/sh\n"),
                f"{suffix} is not writable")
    refuses(lambda: ws.write("d-suffix", "outputs/Payload.COMMAND", "x"), "the suffix check ignores case")
    refuses(lambda: ws.write("d-suffix", "outputs/Thing.app/run.sh", "x"), "a blocked suffix on a parent counts too")
    ws.write("d-suffix", "outputs/report.md", "safe\n")
    check(ws.sha("d-suffix", "outputs/report.md"), "an ordinary suffix still writes")


def test_quotas_report_usage() -> None:
    ws = fresh("d-quota")
    e = refuses(lambda: ws.write("d-quota", "work/big.txt", "x" * (MAX_FILE_CHARS + 1)),
                "a write over MAX_FILE_CHARS is refused")
    check(e.usage["files"] == 0 and e.usage["bytes"] == 0, "the quota error carries current usage")
    check(str(MAX_FILE_CHARS) in str(e), "the quota error names the limit it hit")

    ws.write("d-quota", "work/a.txt", "hello")
    use = ws.usage("d-quota")
    check(use == {"files": 1, "bytes": 5}, f"usage counts files and bytes, got {use}")

    few = Workspace(DATA_DIR, max_files=2)
    shutil.rmtree(few.desk_root("d-few"), ignore_errors=True)
    few.ensure("d-few")
    few.write("d-few", "work/a.txt", "a")
    few.write("d-few", "work/b.txt", "b")
    e = refuses(lambda: few.write("d-few", "work/c.txt", "c"), "MAX_FILES is enforced before the write")
    check(e.usage == {"files": 2, "bytes": 2}, f"the file-count error reports usage, got {e.usage}")
    check(not (few.desk_root("d-few") / "work" / "c.txt").exists(), "the refused file was never created")

    tight = Workspace(DATA_DIR, max_total_bytes=64)
    shutil.rmtree(tight.desk_root("d-tight"), ignore_errors=True)
    tight.ensure("d-tight")
    tight.write("d-tight", "work/b.txt", "b")
    e = refuses(lambda: tight.write("d-tight", "work/b.txt", "y" * 100, mode="overwrite"),
                "MAX_TOTAL_BYTES is enforced before the write")
    check(e.usage == {"files": 1, "bytes": 1}, f"the byte-quota error reports usage, got {e.usage}")
    check(tight.read("d-tight", "work/b.txt")["text"] == "b", "the refused overwrite changed nothing")
    check(MAX_FILES == 500 and MAX_TOTAL_BYTES == 200_000_000, "the shipped defaults are the spec's")


# ---- trash ----
def test_trash_never_unlinks() -> None:
    ws = fresh("d-trash")
    root = ws.desk_root("d-trash")
    ws.write("d-trash", "work/draft.md", "first\n")
    first = ws.trash("d-trash", "work/draft.md")
    check(not (root / "work" / "draft.md").exists(), "the file left its place")
    check((root / first["trashed_to"]).read_text(encoding="utf-8") == "first\n", "the bytes are in .trash/")
    check(first["trashed_to"].startswith(".trash/"), "trash lands under .trash/")

    ws.write("d-trash", "work/draft.md", "second\n")
    second = ws.trash("d-trash", "work/draft.md")
    check(second["trashed_to"] != first["trashed_to"], "a second trash of the same name does not overwrite the first")
    check(second["trashed_to"].endswith("draft 2.md"), f"Finder-style ' 2' suffixing, got {second['trashed_to']}")
    check((root / first["trashed_to"]).read_text(encoding="utf-8") == "first\n", "the first copy survived intact")
    check((root / second["trashed_to"]).read_text(encoding="utf-8") == "second\n", "the second copy is beside it")

    ws.write("d-trash", "work/draft.md", "third\n")
    third = ws.trash("d-trash", "work/draft.md")
    check(third["trashed_to"].endswith("draft 3.md"), "suffixing counts up")
    kept = sorted(p.name for p in (root / ".trash" / "work").iterdir())
    check(kept == ["draft 2.md", "draft 3.md", "draft.md"], f"three versions kept, none unlinked: {kept}")
    refuses(lambda: ws.trash("d-trash", "work/draft.md"), "trashing a missing file is an error, not a silent no-op")


def test_trash_refuses_reserved_dirs() -> None:
    """`.baseline/` is the review diff's only before-copy, so trash() has to refuse it the way
    write() does — otherwise one ordinary desk_trash_file call empties every diff in the desk."""
    ws = fresh("d-reserved")
    root = ws.desk_root("d-reserved")
    ws.write("d-reserved", "outputs/report.md", "v1\n")
    ws.write("d-reserved", "outputs/report.md", "v2\n", mode="overwrite")
    check((root / ".baseline" / "outputs" / "report.md").is_file(), "the overwrite snapshotted a baseline")
    refuses(lambda: ws.trash("d-reserved", ".baseline"), "the baseline directory cannot be trashed",
            contains="reserved")
    refuses(lambda: ws.trash("d-reserved", ".baseline/outputs/report.md"), "nor one file inside it",
            contains="reserved")
    check((root / ".baseline" / "outputs" / "report.md").read_text(encoding="utf-8") == "v1\n",
          "the before-copy is still on disk")
    check(ws.diff("d-reserved", "outputs/report.md")["has_baseline"], "...so the review still has a diff")
    e = refuses(lambda: ws.trash("d-reserved", ".trash"), "the trash itself is still refused")
    check("already in the trash" in str(e), f"with its own distinct message, got {e}")


def test_errors_never_leak_the_data_dir() -> None:
    """desk_write_file catches WorkspaceError only, so a raw OSError's text — which carries the
    absolute data-dir path — would be handed straight to the model."""
    ws = fresh("d-leak")
    ws.write("d-leak", "work/notes.md", "hi\n")
    e = refuses(lambda: ws.write("d-leak", "work/notes.md/deeper.md", "x"),
                "a parent that is a regular file is a WorkspaceError, not a bare OSError")
    check(str(DATA_DIR) not in str(e), f"the absolute data dir never reaches the model, got {e}")
    check("work/notes.md/deeper.md" in str(e), f"the message names the relative path, got {e}")
    check(ws.read("d-leak", "work/notes.md")["text"] == "hi\n", "the refused write changed nothing")


# ---- baseline, state, diff ----
def test_diff_against_baseline() -> None:
    ws = fresh("d-diff")
    root = ws.desk_root("d-diff")
    (root / "work" / "given.md").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")  # seeded, not agent-written

    check(ws.state("d-diff", "work/given.md") == "new", "a file with no baseline reads as new")
    ws.write("d-diff", "work/given.md", "alpha\nBETA\ngamma\n", mode="overwrite")
    check((root / ".baseline" / "work" / "given.md").read_text(encoding="utf-8") == "alpha\nbeta\ngamma\n",
          "the first write snapshots the file as it was")
    check(ws.state("d-diff", "work/given.md") == "modified", "it now differs from its baseline")

    d = ws.diff("d-diff", "work/given.md")
    text = d["diff"]
    check(text.startswith("--- a/work/given.md"), f"a real unified diff header, got {text[:40]!r}")
    check("+++ b/work/given.md" in text, "and the to-file header")
    check("@@" in text, "and a hunk header")
    check("-beta\n" in text and "+BETA\n" in text, f"the changed line both ways:\n{text}")
    check(" alpha\n" in text and " gamma\n" in text, "with context lines")
    check(d["added"] == 1 and d["removed"] == 1, f"one line each way, got {d['added']}/{d['removed']}")

    ws.write("d-diff", "work/given.md", "alpha\nbeta\ngamma\n", mode="overwrite")
    check(ws.state("d-diff", "work/given.md") == "unchanged", "written back to the baseline is unchanged")
    check(ws.diff("d-diff", "work/given.md")["diff"] == "", "an unchanged file has an empty diff")

    ws.write("d-diff", "outputs/made.md", "one\ntwo\n")
    check(not (root / ".baseline" / "outputs" / "made.md").exists(), "a file the desk created has no baseline")
    fresh_diff = ws.diff("d-diff", "outputs/made.md")
    check("+one\n" in fresh_diff["diff"] and "+two\n" in fresh_diff["diff"],
          f"a new file diffs as all additions:\n{fresh_diff['diff']}")
    check(fresh_diff["added"] == 2 and fresh_diff["removed"] == 0, "two lines added, none removed")
    check(ws.state("d-diff", "outputs/made.md") == "new", "and stays new however often it is rewritten")


def test_tree_and_read() -> None:
    ws = fresh("d-tree")
    root = ws.desk_root("d-tree")
    (root / "work" / "seed.txt").write_text("a\nb\n", encoding="utf-8")
    ws.write("d-tree", "work/seed.txt", "a\nc\n", mode="overwrite")
    ws.write("d-tree", "outputs/report.md", "line1\nline2\nline3\n")
    (root / "work" / "blob.bin").write_bytes(b"\x00\x01\x02")
    ws.trash("d-tree", "outputs/report.md")
    ws.write("d-tree", "outputs/report.md", "line1\nline2\nline3\n")

    by_path = {e["path"]: e for e in ws.tree("d-tree")}
    check(not any(p.startswith(".baseline") for p in by_path), "the tree hides .baseline/")
    check(not any(p.startswith(".trash") for p in by_path), "the tree hides .trash/")
    check(by_path["work/seed.txt"]["state"] == "modified", "tree reports state against the baseline")
    check(by_path["outputs/report.md"]["state"] == "new", "a desk-created file reads as new")
    check(by_path["work/seed.txt"]["bytes"] == 4 and by_path["work/seed.txt"]["is_text"], "size and text flag")
    check(by_path["work/blob.bin"]["is_text"] is False, "a NUL byte makes a file non-text")
    check(by_path["work"]["is_dir"] and by_path["work"]["bytes"] == 0, "directories are listed too")
    check(by_path["outputs/report.md"]["modified"] > 0, "entries carry an mtime")
    check([e["path"] for e in ws.tree("d-tree", "outputs")] == ["outputs/report.md"], "tree() can be scoped")

    r = ws.read("d-tree", "outputs/report.md", offset=0, length=7)
    check(r["text"] == "line1\n", f"the window snaps back to a line boundary, got {r['text']!r}")
    check(r["truncated"] and r["next_offset"] == 6, f"and reports where to continue, got {r.get('next_offset')}")
    rest = ws.read("d-tree", "outputs/report.md", offset=r["next_offset"], length=6000)
    check(rest["text"] == "line2\nline3\n" and not rest["truncated"], "paging reaches the end")
    check(rest["chars"] == 18, "the total is reported with every page")
    refuses(lambda: ws.read("d-tree", "work/blob.bin"), "a binary file is refused, not mojibake")
    refuses(lambda: ws.read("d-tree", "work/missing.txt"), "a missing file is an error")


def test_sha_and_purge() -> None:
    ws = fresh("d-purge")
    ws.write("d-purge", "outputs/a.md", "hello\n")
    sha = ws.sha("d-purge", "outputs/a.md")
    check(len(sha) == 64 and sha == "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03",
          f"sha256 of the bytes on disk, got {sha}")
    ws.write("d-purge", "outputs/a.md", "hello!\n", mode="overwrite")
    check(ws.sha("d-purge", "outputs/a.md") != sha, "the sha follows the bytes")

    root = ws.desk_root("d-purge")
    ws.purge("d-purge")
    check(not root.exists(), "purge removes the whole desk root")
    ws.purge("d-purge")
    check(not root.exists(), "purge of an absent workspace is a no-op, not a crash")
    check(ws.usage("d-purge") == {"files": 0, "bytes": 0}, "usage of an absent workspace is zero")
    check(ws.tree("d-purge") == [], "tree of an absent workspace is empty")


TESTS = [test_layout, test_resolve_in_refuses_traversal, test_resolve_in_refuses_absolute,
         test_resolve_in_refuses_symlink_escape, test_resolve_in_refuses_cross_desk,
         test_write_modes, test_write_refuses_blocked_suffixes, test_quotas_report_usage,
         test_trash_never_unlinks, test_trash_refuses_reserved_dirs,
         test_errors_never_leak_the_data_dir, test_diff_against_baseline, test_tree_and_read, test_sha_and_purge]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"ok   {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {t.__name__}: {e}")
    shutil.rmtree(OUTSIDE, ignore_errors=True)
    print(f"\n{passed} assertions passed, {failures} test(s) failed  (data dir {os.environ['PERSONAL_OS_DATA_DIR']})")
    sys.exit(1 if failures else 0)
