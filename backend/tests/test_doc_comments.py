"""Doc comments: migration 10, the thread/reply routes, resolve/reopen, deletion with the doc, per-doc typography,
and the doc_comments / doc_comment_reply tools. Run: python backend/tests/test_doc_comments.py"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="doccomments-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import migrations  # noqa: E402
from personal_os.app import AUTH_TOKEN, app, db, docs, toolbox, trash  # noqa: E402
from personal_os.docs import clean_typography  # noqa: E402
from personal_os.tools import PROMPT_WRITES  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]} (wanted {expect})"
    return r.json() if r.content else None


def call(name: str, args: dict[str, Any]) -> Any:
    return asyncio.new_event_loop().run_until_complete(toolbox.call(name, args, {"project_id": None}))


# migration 10 ran and left the table and column behind
assert migrations.latest() >= 10
with db.tx() as c:
    assert migrations.current(c) == migrations.latest()
    assert c.execute("SELECT 1 FROM sqlite_master WHERE name='doc_comments'").fetchone()
    assert "typography" in {r["name"] for r in c.execute("PRAGMA table_info(docs)").fetchall()}

d = j("POST", "/docs", {"title": "Plan", "content": "The quick brown fox jumps over the lazy dog."})
did = d["id"]
assert d["typography"] is None
assert j("GET", f"/docs/{did}/comments") == []

# a thread with its anchor
t = j("POST", f"/docs/{did}/comments", {"body": "Too quick?", "quote": "quick brown fox", "prefix": "The ", "suffix": " jumps over", "offset_hint": 4})
assert t["parent_id"] is None and t["author"] == "user" and t["quote"] == "quick brown fox" and t["resolved"] == 0
j("POST", f"/docs/{did}/comments", {"body": "   ", "quote": "x"}, expect=422)

# a reply joins the thread, carries no anchor; a reply to a reply still lands on the root
r = j("POST", f"/docs/comments/{t['id']}/replies", {"body": "Keep it."})
assert r["parent_id"] == t["id"] and r["quote"] == ""
r2 = j("POST", f"/docs/comments/{r['id']}/replies", {"body": "Agreed."})
assert r2["parent_id"] == t["id"]
j("POST", f"/docs/comments/{t['id']}/replies", {"body": ""}, expect=422)
j("POST", "/docs/comments/nope/replies", {"body": "x"}, expect=404)

rows = j("GET", f"/docs/{did}/comments")
assert [x["id"] for x in rows] == [t["id"], r["id"], r2["id"]]

# edit own; resolve from a reply's id resolves the thread; hidden with include_resolved=false; reopen
assert j("PATCH", f"/docs/comments/{t['id']}", {"body": "Too quick, really?"})["body"] == "Too quick, really?"
j("PATCH", f"/docs/comments/{r['id']}", {"resolved": True})
assert docs.comment(t["id"])["resolved"] == 1
assert j("GET", f"/docs/{did}/comments?include_resolved=false") == []
j("PATCH", f"/docs/comments/{t['id']}", {"resolved": False})
assert len(j("GET", f"/docs/{did}/comments?include_resolved=false")) == 3

# tools: read as threads, reply as the agent; the agent's words cannot be edited through the route
assert "doc_comment_reply" in PROMPT_WRITES and toolbox.specs["doc_comment_reply"].danger == "writes"
assert toolbox.specs["doc_comments"].danger == "safe"
view = call("doc_comments", {"doc": "Plan"})
assert view["threads"][0]["thread_id"] == t["id"] and len(view["threads"][0]["messages"]) == 3
out = call("doc_comment_reply", {"thread_id": t["id"], "body": "I shortened it."})
assert out["status"] == "posted"
agent_row = docs.comment(out["comment_id"])
assert agent_row["author"] == "agent" and agent_row["parent_id"] == t["id"]
j("PATCH", f"/docs/comments/{agent_row['id']}", {"body": "no"}, expect=403)
assert call("doc_comment_reply", {"thread_id": "nope", "body": "x"})["error"]

# deleting a reply leaves the thread; deleting the thread takes its replies
j("DELETE", f"/docs/comments/{r2['id']}")
assert len(j("GET", f"/docs/{did}/comments")) == 3
j("DELETE", f"/docs/comments/{t['id']}")
assert j("GET", f"/docs/{did}/comments") == []
j("DELETE", f"/docs/comments/{t['id']}", expect=404)

# per-doc typography: validated, cleared by {}, and the global default is validated the same way
assert clean_typography({"font": "book", "size": 18, "measure": 64, "junk": 1}) == {"font": "book", "size": 18, "measure": 64}
assert clean_typography({"font": "comic", "size": 400}) is None
assert j("PATCH", f"/docs/{did}", {"typography": {"font": "serif", "size": 17}})["typography"] == {"font": "serif", "size": 17}
assert [x for x in j("GET", "/docs") if x["id"] == did][0]["typography"] == {"font": "serif", "size": 17}
assert j("PATCH", f"/docs/{did}", {"typography": {}})["typography"] is None
assert j("PUT", "/settings", {"docTypography": {"font": "sans", "measure": 999}})["docTypography"] == {"font": "sans"}

# comments go with their doc when the trash purges it
t2 = j("POST", f"/docs/{did}/comments", {"body": "last words", "quote": "lazy dog"})
j("DELETE", f"/docs/{did}")
j("GET", f"/docs/{did}/comments", expect=404)
assert trash.purge("doc", did)
with db.tx() as c:
    assert not c.execute("SELECT 1 FROM docs WHERE id=?", (did,)).fetchone()
    assert c.execute("SELECT COUNT(*) FROM doc_comments WHERE doc_id=?", (did,)).fetchone()[0] == 0
print("test_doc_comments: ok", t2["id"])
