"""History compaction and in-run microcompaction.

Run: backend/.venv/bin/python backend/tests/test_compaction.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="comptest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import compaction, llm  # noqa: E402

passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


appmod.db.set_settings({"autoLearn": False, "baseUrl": ""})
convos, compactor = appmod.convos, appmod.compactor
CFG = {"contextWindow": 10000, "compactAt": 0.7, "compactKeepRecent": 8, "autoCompact": True}

calls: list[list[dict[str, str]]] = []


async def stub(cfg: Any, model: str, messages: list[dict[str, str]], kind: str = "learn") -> str:
    calls.append(messages)
    return f"SUMMARY-{len(calls)}"


async def boom(cfg: Any, model: str, messages: list[dict[str, str]], kind: str = "learn") -> str:
    raise RuntimeError("model down")


def make_conv(n: int, size: int = 2000) -> str:
    c = convos.create(None, "t", "m")
    for i in range(n):
        convos.add_message(c["id"], "user" if i % 2 == 0 else "assistant", f"msg{i} " + "x" * size)
    return c["id"]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# (a) below threshold: untouched, no model call
small = make_conv(6, 50)
h, info = run(compaction.prepare_history(compactor, convos, CFG, "m", small, 100, complete=stub))
check(h == convos.history(small) and not info["compacted"] and not calls, "below threshold is a no-op")

# (b) over threshold
big = make_conv(40)
full = convos.history(big)
h, info = run(compaction.prepare_history(compactor, convos, CFG, "m", big, 100, complete=stub))
check(info["compacted"] and len(calls) == 1, "compacted with one model call")
check(h[0] == full[0] and h[1]["content"].startswith(compaction.SUMMARY_PREFIX + "SUMMARY-1"), "first user + summary lead")
check(h[2:] == full[-8:] and h[2]["role"] == "user", "recent tail kept verbatim, opens on user")
row = compactor.get(big)
check(row and row["tokens_after"] < row["tokens_before"] and row["summarized_messages"] == 32, "row written, smaller")
check(len(convos.history(big)) == 40, "transcript untouched")

# (c) nothing new: no second call
h2, info2 = run(compaction.prepare_history(compactor, convos, CFG, "m", big, 100, complete=stub))
check(len(calls) == 1 and h2 == h and not info2["compacted"], "second call does not re-summarize")

# (d) more messages: rolling
for i in range(40, 70):
    convos.add_message(big, "user" if i % 2 == 0 else "assistant", f"msg{i} " + "x" * 2000)
h3, info3 = run(compaction.prepare_history(compactor, convos, CFG, "m", big, 100, complete=stub))
check(info3["compacted"] and len(calls) == 2, "re-compacts after growth")
check("SUMMARY-1" in calls[1][-1]["content"], "prompt carries the previous summary")
check(compactor.get(big)["summary"] == "SUMMARY-2" and h3[1]["content"].endswith("SUMMARY-2"), "summary replaced")

# (e) summarizer failure
bad = make_conv(40)
hb, infob = run(compaction.prepare_history(compactor, convos, CFG, "m", bad, 100, complete=boom))
check(hb == convos.history(bad) and not infob["compacted"] and compactor.get(bad) is None, "failure falls back to full history")

# (f) microcompact
big_inline = json.dumps({"rows": ["y" * 900]})
tiny = json.dumps({"ok": True})
handle = json.dumps({"result_id": "tr_abc", "tool": "gmail_search", "total_chars": 99999, "preview": "p" * 600})


def tmsg(i: int, content: str) -> list[dict[str, Any]]:
    return [{"role": "assistant", "content": None, "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": f"tool{i}", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": f"c{i}", "content": content}]


msgs: list[dict[str, Any]] = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}]
contents = [handle, big_inline, tiny, handle, big_inline, tiny, handle, big_inline]
for i, c in enumerate(contents):
    msgs += tmsg(i, c)
msgs.append({"role": "system", "content": "## Your current plan\n" + "p" * 500})
before = [dict(m) for m in msgs]
check(compaction.microcompact(msgs, 3, 10000, 0.9) == (0, 0), "below threshold no-op")
n, saved = compaction.microcompact(msgs, 3, 100, 0.5)
tools = [m for m in msgs if m["role"] == "tool"]
cleared = [json.loads(m["content"]).get("cleared") for m in tools]
# 8 results, the last is in flight; of the 7 eligible keep the newest 3 (idx 4,5,6); clear idx 0..3 above 400 chars
check(cleared[:4] == [True, True, None, True] and all(c is None for c in cleared[4:]), "only old, big results cleared")
check(n == 3 and saved > 0, "count and savings reported")
stub0 = json.loads(tools[0]["content"])
check(stub0["result_id"] == "tr_abc" and stub0["tool"] == "gmail_search", "handle result_id preserved")
stub1 = json.loads(tools[1]["content"])
check(stub1["result_id"] is None and stub1["tool"] == "tool1" and stub1["chars"] == len(big_inline), "inline stub names the tool")
check([m.get("tool_call_id") for m in msgs] == [m.get("tool_call_id") for m in before], "tool_call_id pairing untouched")
check(msgs[-1] == before[-1] and msgs[-2]["content"] == big_inline, "plan and in-flight round untouched")
snap = json.dumps(msgs)
check(compaction.microcompact(msgs, 3, 100, 0.5) == (0, 0) and json.dumps(msgs) == snap, "idempotent")

# (g) cascade
convos.delete(big)
with appmod.db.tx() as c:
    left = c.execute("SELECT COUNT(*) AS n FROM conv_summaries WHERE conversation_id=?", (big,)).fetchone()["n"]
check(left == 0, "summary cascades with the conversation")

# (h) routes
client = TestClient(appmod.app)
H = {"X-Personal-OS-Token": appmod.AUTH_TOKEN}
appmod.db.set_settings({"contextWindow": 10000, "compactKeepRecent": 8})
routed = make_conv(30, 300)
orig = llm.complete
llm.complete = stub  # type: ignore[assignment]
try:
    r = client.post(f"/conversations/{routed}/compact", headers=H, json={"focus": "the budget"})
finally:
    llm.complete = orig  # type: ignore[assignment]
check(r.status_code == 200 and r.json()["compacted"] is True, "compact route compacts")
check("the budget" in calls[-1][-1]["content"], "focus reaches the summarizer")
m = client.get(f"/conversations/{routed}/context-meter", headers=H).json()
check(m["window"] == 10000 and m["summary"] and m["summary"]["summary"].startswith("SUMMARY"), "meter reports the summary")
d = client.delete(f"/conversations/{routed}/summary", headers=H)
check(d.json()["removed"] is True and client.get(f"/conversations/{routed}/context-meter", headers=H).json()["summary"] is None, "discard restores replay")
check(client.post("/conversations/nope/compact", headers=H, json={}).status_code == 404, "unknown conversation 404")

print(f"test_compaction: {passed} checks passed")
