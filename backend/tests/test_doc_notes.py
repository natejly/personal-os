"""Daily note + backlinks. Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_doc_notes.py"""
from __future__ import annotations

import datetime
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="docnotes-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app, docs  # noqa: E402

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


# ---- daily note ----
r = j("POST", "/docs/daily", {"date": "2026-10-02"})
check(r["created"] is True and r["doc"]["title"] == "2026-10-02", "first call creates the dated doc")
check(r["doc"]["folder"] == "Daily" and r["doc"]["project_id"] is None, "it lives in personal/Daily")
check(r["doc"]["content"] == "# Friday, October 2, 2026\n\n", f"body heading: {r['doc']['content']!r}")
check(any(f["path"] == "Daily" and f["scope"] == "" for f in j("GET", "/docs/folders")), "the Daily folder row exists")
again = j("POST", "/docs/daily", {"date": "2026-10-02"})
check(again["created"] is False and again["doc"]["id"] == r["doc"]["id"], "second call returns the same doc")
j("POST", "/docs/daily", {"date": "2026-13-45"}, expect=400)
j("POST", "/docs/daily", {"date": "tomorrow"}, expect=400)
j("POST", "/docs/daily", {"date": "2026-1-2"}, expect=400)
today = j("POST", "/docs/daily", {})
check(today["doc"]["title"] == datetime.date.today().isoformat(), "a missing date is today")
j("POST", "/docs/daily", {"date": None})

# a trashed daily note does not count
j("DELETE", f"/docs/{r['doc']['id']}")
fresh = j("POST", "/docs/daily", {"date": "2026-10-02"})
check(fresh["created"] is True and fresh["doc"]["id"] != r["doc"]["id"], "a trashed note is replaced by a fresh one")

# ---- backlinks ----
target = j("POST", "/docs", {"title": "Project Atlas", "content": "hub\n"})
tid = target["id"]
a = j("POST", "/docs", {"title": "A", "content": "intro\nsee [[Project Atlas]] for more\nend"})
b = j("POST", "/docs", {"title": "B", "content": "x [[ project atlas |the hub]] y"})
c = j("POST", "/docs", {"title": "C", "content": "```\n[[Project Atlas]]\n```\nnothing else [[Other]]"})
d = j("POST", "/docs", {"title": "D", "content": "no links here"})
j("PUT", f"/docs/{tid}", {"content": "I am [[Project Atlas]]"})
e = j("POST", "/docs", {"title": "E", "content": "z" * 300 + " [[Project Atlas]]"})
f = j("POST", "/docs", {"title": "F", "content": "[[Project Atlas]]"})
j("DELETE", f"/docs/{f['id']}")

bl = j("GET", f"/docs/{tid}/backlinks")
ids = {x["id"] for x in bl}
check(ids == {a["id"], b["id"], e["id"]}, f"linkers only: {[x['title'] for x in bl]}")
by = {x["id"]: x for x in bl}
check(by[a["id"]]["snippet"] == "see [[Project Atlas]] for more", "snippet is the linking line")
check(by[b["id"]]["snippet"] == "x [[ project atlas |the hub]] y", "alias, case and padding all match")
check(len(by[e["id"]]["snippet"]) == 160, "snippet clipped to 160")
check({"id", "title", "folder", "project_id", "snippet", "updated_at"} <= set(bl[0]), "row shape")
check(c["id"] not in ids and d["id"] not in ids and tid not in ids and f["id"] not in ids,
      "fenced, unlinked, itself and trashed docs are excluded")

blank = j("POST", "/docs", {"title": "Atlas2", "content": "[[]] [[ ]]"})
docs.update_meta(blank["id"], {"title": ""})
check(j("GET", f"/docs/{blank['id']}/backlinks") == [], "an empty-title doc has no backlinks")
j("GET", "/docs/nope/backlinks", expect=404)
j("DELETE", f"/docs/{tid}")
j("GET", f"/docs/{tid}/backlinks", expect=404)

# ---- quick capture append ----
r1 = j("POST", "/docs/daily/append", {"text": "first  thought", "date": "2031-05-06"})
r2 = j("POST", "/docs/daily/append", {"text": "second", "date": "2031-05-06"})
body = r2["doc"]["content"]
lines = [x for x in body.splitlines() if x.startswith("- ")]
check(len(lines) == 2 and lines[0].endswith(" first thought") and lines[1].endswith(" second"), "two appends land in order")
check(body.startswith("# "), "append created the daily note with its heading")
revs = [x for x in docs.revisions(r1["doc"]["id"]) if x["summary"] == "Quick capture"]
check(len(revs) == 2, "each capture is its own revision")
j("POST", "/docs/daily/append", {"text": "   "}, expect=400)
j("POST", "/docs/daily/append", {"text": "x", "date": "nope"}, expect=400)
docs.delete(r1["doc"]["id"])

# ---- auth behaves like sibling routes ----
bare = TestClient(app)
want = bare.get("/docs/pending").status_code
check(want in (401, 403), "sibling route needs the token")
check(bare.post("/docs/daily", json={}).status_code == want, "daily needs the token")
check(bare.get("/docs/x/backlinks").status_code == want, "backlinks needs the token")

for x in (a, b, c, d, e, blank):
    docs.delete(x["id"])
print(f"test_doc_notes: {passed} checks passed")
