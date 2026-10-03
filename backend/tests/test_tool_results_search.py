"""Conversation-scoped keyword search over stored tool results.

Run: PERSONAL_OS_DATA_DIR=/tmp/trs python backend/tests/test_tool_results_search.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="trstest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod  # noqa: E402
from personal_os import retention  # noqa: E402
from personal_os.working import INLINE_CHARS, _dumps  # noqa: E402

TR, db = appmod.tool_results, appmod.db
passed = 0


def check(cond, label):
    global passed
    assert cond, label
    passed += 1


def conv() -> str:
    return appmod.convos.create(None, "Test chat", "test-model")["id"]


def call(name, args, ctx):
    return asyncio.run(appmod.toolbox.call(name, args, ctx))


a, b = conv(), conv()
filler = "lorem ipsum dolor sit amet " * 200
text = filler + "the kiln ships Friday morning " + filler[:4000]
check(len(text) > 9000, "fixture is big")
rid = TR.store(a, None, "web_search", text, {})["id"]
other = TR.store(b, None, "web_search", "kiln ships Friday elsewhere", {})["id"]

# one window with the phrase; its offset reads back the phrase
ctx_a = {"project_id": None, "conversation_id": a}
out = call("search_tool_results", {"query": "kiln ships Friday"}, ctx_a)
check(len(out["matches"]) == 1 and "kiln ships Friday" in out["matches"][0]["text"], "one window with the phrase")
m = out["matches"][0]
back = call("read_tool_result", {"result_id": rid, "offset": m["offset"]}, ctx_a)
check("kiln ships Friday" in back["text"], "offset reads back the phrase")
check(len(m["text"]) <= 600, "window <= 600")

# other conversation never returned
check(all(x["result_id"] != other for x in out["matches"]), "other chat's row not returned")
check(TR.search(b, "kiln")["matches"][0]["result_id"] == other, "its own chat still finds it")

# untrusted hit taints the run, same flag and source as read_tool_result
c = conv()
TR.store(c, None, "fetch_url", "x" * 5000 + " zebra crossing " + "y" * 100, {"untrusted": True})
ctx = {"project_id": None, "conversation_id": c, "tainted": False}
res = call("search_tool_results", {"query": "zebra"}, ctx)
check(res["matches"], "hit found")
check(ctx.get("tainted") is True and "read_tool_result" in ctx["taint_sources"], "untrusted hit taints the run")
check(ctx_a.get("tainted") is not True, "trusted hit does not taint")

# many matches stay inline-sized
d = conv()
for _ in range(10):
    TR.store(d, None, "web_search", '{"a": "quote kiln", ' * 400, {})
big = call("search_tool_results", {"query": "kiln", "limit": 10}, {"project_id": None, "conversation_id": d})
check(len(_dumps(big)) <= INLINE_CHARS, f"serialized {len(_dumps(big))} <= {INLINE_CHARS}")
check(len(TR.search(d, "kiln")["matches"]) == 3, "default limit 3")

# retention removes blob and index row
with db.tx() as cx:
    cx.execute("UPDATE tool_results SET created_at=? WHERE id=?", (time.time() - 90 * 86400, rid))
retention.sweep(db, {})
check(TR.search(a, "kiln")["matches"] == [], "gone after retention")
with db.tx() as cx:
    n = cx.execute("SELECT count(*) FROM tool_results_fts WHERE result_id=?", (rid,)).fetchone()[0]
check(n == 0, "no orphan fts row")
print(f"ok {passed} checks")
