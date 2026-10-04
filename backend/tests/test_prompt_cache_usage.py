"""Prompt-cache friendly layout and cached/reasoning token accounting.

Run: backend/.venv/bin/python backend/tests/test_prompt_cache_usage.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="pcutest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod  # noqa: E402
from personal_os import llm  # noqa: E402
from personal_os.context import build_context, layout_messages  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.usage import Pricing, Usage  # noqa: E402

passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


# ---- 1. layout
hist = [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"}, {"role": "user", "content": "u2"}]
m = layout_messages("STABLE", ["v1", "v2", "v3"], hist)
check([x["role"] for x in m] == ["system", "user", "assistant", "system", "user"], "layout order")
check(m[0]["content"] == "STABLE" and m[4]["content"] == "u2", "stable first, newest user last")
check("v1" in m[3]["content"] and "v3" in m[3]["content"], "volatile in its own message")
m2 = layout_messages("STABLE", ["other"], hist)
check(m[0]["content"].encode() == m2[0]["content"].encode(), "stable prefix byte-identical")
legacy = layout_messages("STABLE", ["v1"], hist, cache_layout=False)
check(len(legacy) == 4 and legacy[0]["content"] == "STABLE\n\nv1", "legacy single system message")
check(len(layout_messages("S", [], hist)) == 4, "no volatile -> no extra message")

# ---- 2. build_context pieces
appmod.db.set_settings({"autoLearn": False, "baseUrl": ""})


def ctx(q: str):
    return build_context(
        memories=appmod.memories, graph=appmod.graph, documents=appmod.documents, project=None, project_id=None,
        query=q, settings=appmod.settings(), conv_settings={}, global_system_prompt="You are Grain.", skills=appmod.skills,
        page={"label": f"Todos {q}"},
    )


s1, u1 = ctx("tea")
s2, u2 = ctx("coffee")
check(u1["stable_system"] == u2["stable_system"] and u1["stable_system"].startswith("You are Grain."), "stable part query-independent")
check(len(u1["volatile_blocks"]) >= 1 and u1["volatile_blocks"] != u2["volatile_blocks"], "volatile differs per query/page")
check(u1["system_prompt"] == "\n\n".join([u1["stable_system"], *u1["volatile_blocks"]]) == s1, "system_prompt = stable + volatile")

# ---- 3. usage parsing
o = llm.parse_usage({"prompt_tokens": 1000, "completion_tokens": 400, "prompt_tokens_details": {"cached_tokens": 800},
                     "completion_tokens_details": {"reasoning_tokens": 300}})
check(o["cached_tokens"] == 800 and o["reasoning_tokens"] == 300 and o["cache_write_tokens"] == 0 and o["prompt_tokens"] == 1000, "openai shape")
a = llm.parse_usage({"prompt_tokens": 10, "completion_tokens": 2, "cache_read_input_tokens": 7, "cache_creation_input_tokens": 3})
check(a["cached_tokens"] == 7 and a["cache_write_tokens"] == 3 and a["reasoning_tokens"] == 0, "anthropic shape")
z = llm.parse_usage({"prompt_tokens": 5, "completion_tokens": 1})
check(z["cached_tokens"] == 0 and z["cache_write_tokens"] == 0 and z["reasoning_tokens"] == 0, "absent -> zeros")
check(llm.parse_usage(None)["cached_tokens"] == 0, "None -> zeros")
seen: list[dict[str, Any]] = []
llm.on_usage(seen.append)
llm._emit_usage("m", "chat", o, 5, 0, 0)
check(seen[-1]["cached_tokens"] == 800 and seen[-1]["reasoning_tokens"] == 300, "emit carries buckets")
llm._emit_usage("m", "chat", None, 5, 40, 8)
check(seen[-1]["cached_tokens"] == 0 and seen[-1]["estimated"], "estimated rows keep zeros")
check(llm.DEFAULT_SETTINGS["cacheLayout"] is True, "cacheLayout default on")

# ---- 4. pricing
p = Pricing()
p._proxy = {"m": {"input": 1.0, "output": 4.0, "cache_read": 0.1}, "n": {"input": 1.0, "output": 4.0}}
c = p.cost({}, "m", 1000, 100, cached_tokens=800)
check(abs(c - (200 * 1.0 + 800 * 0.1 + 100 * 4.0) / 1e6) < 1e-12, "cache-read price")
check(abs(p.cost({}, "n", 1000, 100, cached_tokens=800) - (1000 + 400) / 1e6) < 1e-12, "fallback to input price")
check(p.cost({}, "m", 100, 0, cached_tokens=500) >= 0, "cached > prompt not negative")
ov = p.cost({"modelPrices": {"x": {"input": 2, "output": 2, "cache_read": 0.5, "cache_write": 3}}}, "x", 100, 0, 40, 10)
check(abs(ov - (50 * 2 + 40 * 0.5 + 10 * 3) / 1e6) < 1e-12, "override cache prices + write")

# ---- 5. migration + report
d = Path(tempfile.mkdtemp(prefix="pcumig-"))
con = sqlite3.connect(d / "personal-os.db")
con.executescript("""CREATE TABLE usage_log (id TEXT PRIMARY KEY, created_at REAL NOT NULL, model TEXT NOT NULL, kind TEXT NOT NULL,
  conversation_id TEXT, project_id TEXT, prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0,
  duration_ms INTEGER NOT NULL DEFAULT 0, cost REAL, estimated INTEGER NOT NULL DEFAULT 0);""")
con.execute("INSERT INTO usage_log VALUES('old',?,'m','chat',NULL,NULL,100,10,5,0.1,0)", (time.time(),))
con.commit()
con.close()
db = Database(d)
with db.tx() as cx:
    cols = {r["name"] for r in cx.execute("PRAGMA table_info(usage_log)")}
    old = dict(cx.execute("SELECT * FROM usage_log WHERE id='old'").fetchone())
check({"cached_tokens", "cache_write_tokens", "reasoning_tokens"} <= cols, "columns migrated")
check(old["cached_tokens"] == 0 and old["prompt_tokens"] == 100, "old rows keep data, zeros in new columns")
u = Usage(db)
u.record(model="m", kind="chat", prompt_tokens=1000, completion_tokens=200, duration_ms=1, cost=None, estimated=False,
         conversation_id=None, project_id=None, cached_tokens=500, reasoning_tokens=100)
rep = u.report(1)
check(rep["totals"]["cached_tokens"] == 500 and rep["totals"]["reasoning_tokens"] == 100, "report sums")
check(abs(rep["totals"]["cache_hit_rate"] - 500 / 1100) < 1e-3, "cache_hit_rate")
check(abs(rep["totals"]["reasoning_share"] - 100 / 210) < 1e-3, "reasoning_share")
check(u.reprice(p, {}) == 2, "reprice runs with new columns")

print(f"test_prompt_cache_usage: {passed} checks passed")
