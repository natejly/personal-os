"""The next turn sees the previous turn's tool results (the for_model text), within the unsummarized tail.

Run: backend/.venv/bin/python backend/tests/test_tool_results_next_turn.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="trnt-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod  # noqa: E402
from personal_os import compaction  # noqa: E402

passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


appmod.db.set_settings({"autoLearn": False, "baseUrl": ""})
convos, compactor = appmod.convos, appmod.compactor
CFG = {"contextWindow": 10000, "compactAt": 0.7, "compactKeepRecent": 8, "autoCompact": True}


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def add_turn(cid: str, i: int, size: int = 50) -> None:
    convos.add_message(cid, "user", f"q{i} " + "x" * size)
    am = convos.add_message(cid, "assistant", "")
    ev = {"id": f"{am['id']}:c{i}", "call_id": f"c{i}", "name": "web_search", "arguments": {"q": i},
          "result_preview": "p", "images": [{"name": "pic.png", "data": "BASE64BYTES"}],
          "for_model": json.dumps({"hit": f"RESULT{i}"})}
    convos.finish_message(am["id"], f"a{i}", None, None, [ev])


# (1) under threshold: results ride along in order, as assistant tool_calls + tool messages
c1 = convos.create(None, "t", "m")["id"]
add_turn(c1, 1)
h, info = run(compaction.prepare_history(compactor, convos, CFG, "m", c1, 100))
roles = [m["role"] for m in h]
check(roles == ["user", "assistant", "tool", "assistant"], f"shape {roles}")
check(h[1]["tool_calls"][0]["id"] == "c1" and h[1]["tool_calls"][0]["function"]["name"] == "web_search", "call replayed")
check(h[2]["tool_call_id"] == "c1" and h[2]["content"] == json.dumps({"hit": "RESULT1"}), "text equals for_model")
check("BASE64BYTES" not in json.dumps(h), "no image bytes")


# (2) over threshold: old results only in the summary, tail results remain
async def stub(cfg: Any, model: str, messages: list[Any], kind: str = "learn") -> str:
    return "SUMMARY"


c2 = convos.create(None, "t", "m")["id"]
for i in range(20):
    add_turn(c2, i, 2000)
h, info = run(compaction.prepare_history(compactor, convos, CFG, "m", c2, 100, complete=stub))
blob = json.dumps(h)
check(info["compacted"] and any(compaction.SUMMARY_PREFIX in str(m.get("content")) for m in h), "summary placeholder present")
check("RESULT0" not in blob and 'RESULT1"' not in blob, "aged results absent")
check("RESULT19" in blob, "tail results remain")

print(f"{passed} passed")
