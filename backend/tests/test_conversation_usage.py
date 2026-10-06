"""Per-chat usage totals: GET /conversations/{id}/usage, the idx_usage_conv index, /usage unchanged by the hoisted
bucket helpers, and auto-learn rows attributed to the chat that caused them. Offline.

Run: backend/.venv/bin/python backend/tests/test_conversation_usage.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="convusage-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import learn, llm  # noqa: E402

passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def rec(conv: str | None, kind: str, pt: int, ct: int, cost: float | None, est: bool = False) -> None:
    appmod.usage.record(model="m", kind=kind, prompt_tokens=pt, completion_tokens=ct, duration_ms=10, cost=cost, estimated=est,
                        conversation_id=conv, project_id=None, cached_tokens=pt // 2)


def test_conversation_usage() -> None:
    a = appmod.convos.create(None, "a", "m")["id"]
    b = appmod.convos.create(None, "b", "m")["id"]
    empty = appmod.convos.create(None, "c", "m")["id"]
    rec(a, "chat", 100, 20, 0.5)
    rec(a, "chat", 200, 30, None, est=True)
    rec(a, "learn", 50, 5, 0.1)
    rec(b, "chat", 999, 999, 9.0)
    rec(None, "chat", 777, 77, 7.0)

    u = appmod.usage.conversation(a)
    t = u["totals"]
    check(t["calls"] == 3 and t["prompt_tokens"] == 350 and t["completion_tokens"] == 55 and t["tokens"] == 405, f"totals {t}")
    check(t["unpriced"] == 1 and abs(t["cost"] - 0.6) < 1e-9, "one unpriced row, priced rows summed")
    check(t["cached_tokens"] == 50 + 100 + 25, "cached tokens summed")
    check({k["kind"]: k["calls"] for k in u["by_kind"]} == {"chat": 2, "learn": 1}, f"by_kind {u['by_kind']}")
    check(u["estimated"] == 1 and u["since"] is not None, "estimated count and first-row time")
    z = appmod.usage.conversation(empty)
    check(z["totals"]["calls"] == 0 and z["since"] is None and z["by_kind"] == [], "an empty chat is a zeroed bucket")

    client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
    r = client.get(f"/conversations/{a}/usage")
    check(r.status_code == 200 and r.json()["totals"]["calls"] == 3, "route returns the totals")
    check(client.get(f"/conversations/{empty}/usage").json()["totals"]["calls"] == 0, "route for an empty chat")
    check(client.get("/conversations/nope/usage").status_code == 404, "unknown chat is 404")

    rep = appmod.usage.report(30)
    check(rep["totals"]["calls"] == 5 and rep["totals"]["unpriced"] == 1 and {k["kind"] for k in rep["by_kind"]} == {"chat", "learn"},
          "/usage report unchanged in shape")
    check(abs(rep["totals"]["cost"] - 16.6) < 1e-6, "/usage report cost unchanged")

    with appmod.db.tx() as c:
        idx = {row[1] for row in c.execute("PRAGMA index_list(usage_log)").fetchall()}
    check("idx_usage_conv" in idx, "per-conversation index exists")


def test_learn_rows_attributed() -> None:
    conv = appmod.convos.create(None, "learn", "m")["id"]

    async def fake_learn(**kw: Any) -> dict[str, Any]:
        llm._emit_usage("m", "learn", {"prompt_tokens": 3, "completion_tokens": 1}, 5, 0, 0)
        return {"memories": [], "nodes": [], "edges": [], "superseded": [], "invalidated": [], "ended": []}

    worker = learn.LearnWorker.__new__(learn.LearnWorker)
    worker._alive = None
    worker._memories = worker._graph = None
    worker.index = None
    worker._publish = lambda *a, **k: None
    worker._set_trace = lambda *a, **k: None

    async def no_consolidate(*a: Any, **k: Any) -> None:
        return None

    worker._maybe_consolidate = no_consolidate  # type: ignore[method-assign]
    job = learn.LearnJob(conversation_id=conv, message_id="m1", project_id=None, user_text="u", assistant_text="a",
                         model="m", settings={}, spans=[])
    real = learn.learn_from_exchange
    learn.learn_from_exchange = fake_learn  # type: ignore[assignment]
    try:
        asyncio.run(worker._run(job))
    finally:
        learn.learn_from_exchange = real  # type: ignore[assignment]
    lu = appmod.usage.conversation(conv)
    check(lu["totals"]["calls"] == 1 and lu["by_kind"][0]["kind"] == "learn", f"learn row attributed to the chat: {lu}")


if __name__ == "__main__":
    test_conversation_usage()
    test_learn_rows_attributed()
    print(f"test_conversation_usage: {passed} checks ok")
