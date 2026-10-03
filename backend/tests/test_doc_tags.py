"""#tags in docs: extraction, the doc_tags rebuild on save, tag rows in list, #tag search. Run: python backend/tests/test_doc_tags.py"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="doctagstest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app, docs  # noqa: E402
from personal_os.docs import extract_tags  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


check(extract_tags("# Heading\n`#x` see #todo/now and #Todo/now") == ["todo/now"], "heading, code, nesting, case")
check(extract_tags("```\n#f\n```\n$$\n#m\n$$\na#b #café #1 #after") == ["café", "after"], "fences, math, unicode, mid-word")

a = docs.create("A", "plan #work/now and #idea")
b = docs.create("B", "nothing here #idea")


def found(q: str) -> list[str]:
    return sorted(d["title"] for d in client.get("/docs", params={"q": q}).json())


check(found("#idea") == ["A", "B"], "tag search")
check(found("#work") == ["A"], "parent tag matches nested")
check(found("#wor") == [], "no prefix match inside a segment")
row = next(d for d in client.get("/docs").json() if d["id"] == a["id"])
check(row["tags"] == ["idea", "work/now"], "list rows carry tags")

docs.save(a["id"], content="plan only")
check(found("#idea") == ["B"] and found("#work") == [], "editing a tag away removes its row")

docs.delete(b["id"])
with docs.db.tx() as c:
    check(c.execute("SELECT COUNT(*) n FROM doc_tags WHERE doc_id=?", (b["id"],)).fetchone()["n"] == 0, "hard delete cascades")
print(f"test_doc_tags: {passed} checks passed")
