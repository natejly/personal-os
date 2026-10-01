"""Files hierarchy: folders scoped per project, and docs moving between them.

A folder path is unique inside a scope, not globally — Personal and every project each own a tree, and
a doc's `project_id` is the only thing that says which tree it is in. Run:
python backend/tests/test_doc_scopes.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="docscopestest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app, docs  # noqa: E402
from personal_os.docs import Docs, scope_key  # noqa: E402

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


def keys(tree: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(f["scope"], f["path"]) for f in tree]


def placed(doc_id: str) -> tuple[str, str]:
    d = docs.get(doc_id)
    return (scope_key(d["project_id"]), d["folder"])


# ---- scope normalisation ----
check(scope_key(None) == "", "a doc with no project is in the personal tree")
check(scope_key(" p1 ") == "p1", "a scope is trimmed")

alpha = j("POST", "/projects", {"name": "Alpha"})["id"]
beta = j("POST", "/projects", {"name": "Beta"})["id"]

# ---- the same folder name in two projects is two folders ----
j("POST", "/docs/folders", {"path": "Research", "scope": alpha})
tree = j("POST", "/docs/folders", {"path": "Research", "scope": beta})
check(keys(tree).count((alpha, "Research")) == 1 and keys(tree).count((beta, "Research")) == 1,
      f"each project owns its own Research: {keys(tree)}")
j("POST", "/docs/folders", {"path": "Research"})
check(("", "Research") in keys(j("GET", "/docs/folders")), "and so does Personal")

# ---- a doc is filed by project and folder together ----
a_doc = j("POST", "/docs", {"title": "Alpha notes", "project_id": alpha, "folder": "Research"})["id"]
mine = j("POST", "/docs", {"title": "Mine"})["id"]
check(placed(a_doc) == (alpha, "Research"), f"created straight into a project's folder: {placed(a_doc)}")
check(placed(mine) == ("", ""), "and a plain doc lands loose in Personal")

tree = j("GET", "/docs/folders")
a_research = [f for f in tree if f["scope"] == alpha and f["path"] == "Research"][0]
b_research = [f for f in tree if f["scope"] == beta and f["path"] == "Research"][0]
check(a_research["docs"] == 1 and b_research["docs"] == 0, "counts do not leak between projects")

# ---- one patch carries a whole drag: new project, new folder ----
j("PATCH", f"/docs/{mine}", {"scope": beta, "folder": "Research"})
check(placed(mine) == (beta, "Research"), f"a doc moves project and folder at once: {placed(mine)}")
j("PATCH", f"/docs/{mine}", {"scope": "", "folder": ""})
check(placed(mine) == ("", ""), "and back out to loose Personal — `scope` can say personal out loud")
j("PATCH", f"/docs/{mine}", {"scope": beta, "folder": "Research"})

# ---- renaming a folder touches only its own tree ----
j("PATCH", "/docs/folders", {"path": "Research", "new_path": "Studies", "scope": alpha})
check(placed(a_doc) == (alpha, "Studies"), "the renamed project's doc followed")
check(placed(mine) == (beta, "Research"), "the other project's identically named folder did not move")
check(("", "Research") in keys(j("GET", "/docs/folders")), "nor did Personal's")

# A name already taken in this tree is refused; the same name in another tree is not its business.
j("POST", "/docs/folders", {"path": "Studies", "scope": beta})
j("PATCH", "/docs/folders", {"path": "Research", "new_path": "Studies", "scope": beta}, expect=400)
j("PATCH", "/docs/folders", {"path": "Research", "new_path": "Studies"})
check(("", "Studies") in keys(j("GET", "/docs/folders")), "Personal may take a name Alpha already uses")

# ---- deleting a folder promotes only its own tree's docs ----
nested = j("POST", "/docs", {"title": "Deep", "project_id": beta, "folder": "Research/2026"})["id"]
j("DELETE", f"/docs/folders?path=Research&scope={beta}")
check(placed(nested) == (beta, "") and placed(mine) == (beta, ""), "Beta's docs moved up to its own root")
check(placed(a_doc) == (alpha, "Studies"), "Alpha was untouched")
check((beta, "Research") not in keys(j("GET", "/docs/folders")), "and the folder row is gone")

# ---- deleting a project keeps the writing and drops the folders ----
j("DELETE", f"/projects/{alpha}")
tree = j("GET", "/docs/folders")
check(not [f for f in tree if f["scope"] == alpha], f"no folder rows left for a dead project: {keys(tree)}")
check(docs.get(a_doc) is not None, "its docs survive")
check(placed(a_doc) == ("", "Studies"), "demoted to personal, still remembering where it was filed")

# ---- an unscoped install's folders become personal ones ----
tmp = Path(tempfile.mkdtemp(prefix="docmigrate-")) / "old.db"
con = sqlite3.connect(tmp)
con.executescript(
    "CREATE TABLE doc_folders (path TEXT PRIMARY KEY, created_at REAL NOT NULL);"
    "INSERT INTO doc_folders VALUES('Work', 1.0), ('Work/Research', 1.0);"
)
con.commit()
con.close()


class _OneOff:
    """Just enough of Database for the migration: a cursor with row access by name."""

    def __init__(self, path: Path) -> None:
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row

    def tx(self) -> Any:
        from contextlib import contextmanager

        @contextmanager
        def _tx() -> Any:
            cur = self.con.cursor()
            try:
                yield cur
                self.con.commit()
            except Exception:
                self.con.rollback()
                raise

        return _tx()


migrated = Docs(_OneOff(tmp))  # type: ignore[arg-type]
check(keys(migrated.folders()) == [("", "Work"), ("", "Work/Research")],
      f"an old tree carries over whole, as personal: {keys(migrated.folders())}")

# ---- leave the tables as we found them ----
for d in j("GET", "/docs"):
    j("DELETE", f"/docs/{d['id']}")
for f in j("GET", "/docs/folders"):
    if "/" not in f["path"]:
        j("DELETE", f"/docs/folders?path={f['path']}&scope={f['scope']}")
j("DELETE", f"/projects/{beta}")
check(j("GET", "/docs") == [] and j("GET", "/docs/folders") == [], "cleans up after itself")

print(f"test_doc_scopes: {passed} checks passed")
