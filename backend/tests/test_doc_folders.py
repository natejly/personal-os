"""Nested doc folders: paths, the tree, renames and deletes. Run: python backend/tests/test_doc_folders.py"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="docfolderstest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app, docs  # noqa: E402
from personal_os.docs import ancestors, folder_path  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]} (wanted {expect})"
    return r.json()


def folder_of(doc_id: str) -> str:
    return docs.get(doc_id)["folder"]


def paths(tree: list[dict[str, Any]]) -> list[str]:
    return [f["path"] for f in tree]


# ---- path normalisation ----
check(folder_path("  Work / Research  ") == "Work/Research", "segments are trimmed")
check(folder_path("//a///b//") == "a/b", "empty segments collapse")
check(folder_path("..") == "", "a path of dots is no path at all")
check(folder_path(None) == "", "no folder is the root")
check(folder_path("a\\b") == "a/b", "a backslash files the same as a slash")
check(len(folder_path("/".join(str(i) for i in range(20))).split("/")) <= 8, "depth is capped")
check(ancestors("Work/Research/2026") == ["Work", "Work/Research", "Work/Research/2026"],
      "a path implies each folder above it")

# ---- an empty folder survives, and nesting fills in the middle ----
j("POST", "/docs/folders", {"path": "Work"})
tree = j("POST", "/docs/folders", {"path": "Work/Research/2026"})
check(paths(tree) == ["Work", "Work/Research", "Work/Research/2026"], f"implied ancestors exist: {paths(tree)}")
check(j("GET", "/docs/folders") == tree, "and they persist")
check([f for f in tree if f["path"] == "Work/Research"][0]["parent"] == "Work", "each knows its parent")
j("POST", "/docs/folders", {"path": "  "}, expect=400)

# ---- docs file into them, and the counts are shallow and deep ----
top = j("POST", "/docs", {"title": "Charter", "folder": "Work"})["id"]
deep = j("POST", "/docs", {"title": "Notes 2026", "content": "x", "folder": "Work/Research/2026"})["id"]
loose = j("POST", "/docs", {"title": "Shopping"})["id"]
tree = j("GET", "/docs/folders")
work = [f for f in tree if f["path"] == "Work"][0]
check(work["docs"] == 1 and work["docs_deep"] == 2, f"deep counts include the subtree: {work}")
check(folder_of(loose) == "", "a doc with no folder stays at the root")
check(folder_of(j("POST", "/docs", {"title": "Sloppy", "folder": " Work // Research "})["id"]) == "Work/Research",
      "a folder passed loosely is normalised on the way in")

# ---- moving a doc between folders is a meta patch ----
j("PATCH", f"/docs/{loose}", {"folder": "Work/Research"})
check(folder_of(loose) == "Work/Research", "a doc moves by patching its folder")

# ---- rename carries the subtree and the docs with it ----
tree = j("PATCH", "/docs/folders", {"path": "Work", "new_path": "Archive/Work"})
check("Archive/Work/Research/2026" in paths(tree), f"the subtree follows: {paths(tree)}")
check(folder_of(deep) == "Archive/Work/Research/2026", "and so do the docs filed deepest")
check(folder_of(top) == "Archive/Work", "and the ones filed at the top")
j("PATCH", "/docs/folders", {"path": "Archive", "new_path": "Archive/Work/Inner"}, expect=400)
check(folder_of(deep) == "Archive/Work/Research/2026", "a move into itself is refused, changing nothing")
j("PATCH", "/docs/folders", {"path": "Archive/Work", "new_path": "Archive"}, expect=400)

# ---- delete promotes the docs rather than dropping them ----
tree = j("DELETE", "/docs/folders?path=Archive/Work/Research")
check("Archive/Work/Research" not in paths(tree) and "Archive/Work/Research/2026" not in paths(tree),
      f"the folder and its children are gone: {paths(tree)}")
check(folder_of(deep) == "Archive/Work", "its docs moved up to the parent, not into the void")
check(docs.get(deep) is not None, "and still exist")

j("POST", "/docs/folders", {"path": "Scratch"})
scratch = j("POST", "/docs", {"title": "Throwaway", "folder": "Scratch"})["id"]
j("DELETE", "/docs/folders?path=Scratch&delete_docs=true")
check(docs.get(scratch) is None, "delete_docs=true takes the docs too, when that is what was asked")

# ---- the rest of the docs surface is untouched ----
check(len(j("GET", "/docs")) >= 3, "listing docs still works")
j("GET", "/docs/pending")
j("GET", f"/docs/{top}")

# ---- leave the tables as we found them ----
# Under pytest every one of these script-style test files shares the first data dir that was set, and
# test_docs.py starts by asserting there are no docs. Cleaning up is part of the test.
for d in j("GET", "/docs"):
    j("DELETE", f"/docs/{d['id']}")
for f in j("GET", "/docs/folders"):
    if "/" not in f["path"]:
        j("DELETE", f"/docs/folders?path={f['path']}")
check(j("GET", "/docs") == [] and j("GET", "/docs/folders") == [], "cleans up after itself")

print(f"test_doc_folders: {passed} checks passed")
