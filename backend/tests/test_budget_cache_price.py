"""Cached-token pricing in the run budget, the date line outside the stable prefix, the context meter's spend figure."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="budgetcache-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm  # noqa: E402

passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


PRICES = {"modelPrices": {"m": {"input": 10.0, "output": 0.0, "cache_read": 1.0}}}
appmod.db.set_settings({"autoLearn": False, "baseUrl": "", **PRICES})
cfg = appmod.settings()

# 1. discounted cost, and Budget.add equals it
full = appmod.pricing.cost(cfg, "m", 100_000, 0)
disc = appmod.pricing.cost(cfg, "m", 100_000, 0, 90_000, 0)
check(disc < full and abs(full - 1.0) < 1e-9 and abs(disc - 0.19) < 1e-9, "cached tokens discount the cost")
b = appmod.Budget(cfg)
b.add(100_000, 0, disc)
check(abs(b.cost - disc) < 1e-9, "Budget.add equals the discounted figure")
check(appmod.pricing.cost(cfg, "nope", 10, 10, 5, 0) is None, "unpriced model stays unpriced")

# 2. run: undiscounted 1.0 > 0.5 cap, discounted 0.19 < cap => no partial=cost; and date line placement
SEEN: list[list[dict[str, Any]]] = []


async def scripted(settings: Any, model: str, messages: list[dict[str, Any]], tools: Any = None, kind: str = "chat",
                   effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: Any = None) -> Any:
    SEEN.append([dict(m) for m in messages])
    u = {"prompt_tokens": 100_000, "completion_tokens": 0, "cached_tokens": 90_000}
    llm._emit_usage("m", "chat", u, 1, 0, 0)  # what the real stream does, so the listener writes the usage row
    yield {"type": "delta", "text": "ok"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": u}


def drive() -> list[tuple[str, Any]]:
    cid = appmod.convos.create(None, "t", "m")["id"]

    async def go() -> list[tuple[str, Any]]:
        return [ev async for ev in appmod._chat_stream(cid, appmod.ChatIn(content="go"), asyncio.Event())]

    prev = llm.stream_chat
    llm.stream_chat = scripted
    try:
        return asyncio.run(go())
    finally:
        llm.stream_chat = prev


ev1 = drive()
check(not any(isinstance(d, dict) and d.get("partial") == "cost" for _, d in ev1), "a run never stops on cost")
drive()
for msgs in SEEN:
    check(msgs[0]["role"] == "system" and "Today is" not in msgs[0]["content"], "stable prefix omits the date line")
    vol = [m for m in msgs if m["role"] == "system" and m["content"].startswith("## Context for this turn")]
    check(vol and "Today is" in vol[0]["content"], "date line is in the volatile section")
check(SEEN[0][0]["content"] == SEEN[1][0]["content"], "two builds share the same stable prefix")

# 3. meter: window follows summary + tail, spend still counts pre-summary rows; usage row carries the round
cid = appmod.convos.create(None, "t", "m")["id"]
for i in range(6):
    appmod.convos.add_message(cid, "user" if i % 2 == 0 else "assistant", f"msg{i} " + "x" * 4000)
appmod.usage.record(model="m", kind="chat", prompt_tokens=1000, completion_tokens=0, duration_ms=1, cost=0.25, estimated=False,
                    conversation_id=cid, project_id=None, round=3)
client = TestClient(appmod.app)
H = {"X-Personal-OS-Token": appmod.AUTH_TOKEN}
before = client.get(f"/conversations/{cid}/context-meter", headers=H).json()
rows = appmod.convos.history_rows(cid)
appmod.compactor._save(cid, rows[3], "short summary", 4, before["estimated_tokens"], 10)
after = client.get(f"/conversations/{cid}/context-meter", headers=H).json()
check(after["estimated_tokens"] < before["estimated_tokens"], "window figure follows summary plus tail")
check(abs(after["spend"]["cost"] - 0.25) < 1e-9 and after["spend"]["tokens"] == 1000, "spend still includes the pre-summary row")
with appmod.db.tx() as c:
    check(c.execute("SELECT round FROM usage_log WHERE conversation_id=? AND round=3", (cid,)).fetchone() is not None, "usage row stores the round")
    check(c.execute("SELECT 1 FROM usage_log WHERE round=1").fetchone() is not None, "chat loop stamps the round on usage rows")

print(f"test_budget_cache_price: {passed} checks passed")
