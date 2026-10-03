"""Consecutive read-only calls in one parent round run together (app._chat_stream). Offline, scripted model, fake tools.
Run: python backend/tests/test_parallel_reads_parent.py
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="parreads-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import llm  # noqa: E402
from personal_os import app as appmod  # noqa: E402
from personal_os.runs import Run  # noqa: E402
from personal_os.tools import ToolSpec, _obj  # noqa: E402

passed = 0
LOG: list[str] = []
STARTED: dict[str, asyncio.Event] = {}
GO: asyncio.Event | None = None
STOP: asyncio.Event | None = None
ROUNDS: list[list[dict[str, Any]]] = []


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def _tool(name: str, danger: str, gate: bool = False, stops: bool = False) -> None:
    async def fn(ctx: dict[str, Any], k: str = "") -> Any:
        LOG.append(f"start:{name}:{k}")
        STARTED.setdefault(f"{name}:{k}", asyncio.Event()).set()
        if stops and STOP is not None:
            STOP.set()
        if gate and GO is not None:
            await asyncio.wait_for(GO.wait(), 5)
        LOG.append(f"end:{name}:{k}")
        return {"ok": True, "k": k}
    appmod.toolbox.specs[name] = ToolSpec(name, "fake", _obj({"k": {"type": "string"}}, []), fn, "files", danger)


_tool("rd_a", "safe", gate=True)
_tool("rd_b", "network", gate=True)
_tool("rd_stop", "safe", stops=True)
_tool("wr", "writes")
_tool("ext", "external")


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: Any = None, kind: str = "chat",
                    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: Any = None) -> Any:
    calls = ROUNDS.pop(0) if ROUNDS else []
    yield {"type": "delta", "text": "ok" if not calls else ""}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": calls, "usage": None}


assert not set(inspect.signature(llm.stream_chat).parameters) - set(inspect.signature(_scripted).parameters)


def c(i: int, name: str, k: str = "") -> dict[str, Any]:
    return {"id": f"c{i}", "name": name, "arguments": json.dumps({"k": k})}


def drive(calls: list[dict[str, Any]], release_after: str | None = None, stop_first: bool = False) -> tuple[list[tuple[str, Any]], int]:
    appmod.db.set_settings({"autoLearn": False, "baseUrl": "", "workspaceRoots": [], "permissionRules": {"allow": [], "ask": [], "deny": []}})
    cid = appmod.convos.create(None, "t", "m")["id"]
    appmod.convos.update(cid, {"settings": {"tools": {n: "on" for n in ("rd_a", "rd_b", "rd_stop", "wr", "ext")}}})
    LOG.clear()
    STARTED.clear()
    ROUNDS[:] = [calls]

    async def go() -> tuple[list[tuple[str, Any]], int]:
        global GO, STOP
        GO, STOP = asyncio.Event(), asyncio.Event()
        run = Run(cid, appmod.run_store)

        async def releaser() -> None:
            if release_after:
                for _ in range(500):
                    if release_after in STARTED:
                        break
                    await asyncio.sleep(0.01)
                await asyncio.sleep(0.05)
            GO.set()

        t = asyncio.create_task(releaser())
        out = [ev async for ev in appmod._chat_stream(cid, appmod.ChatIn(content="go"), STOP if stop_first else asyncio.Event(), run=run)]
        await t
        row = appmod.run_store._one("SELECT count(*) AS n FROM executed_calls WHERE run_id=?", (run.run_id,))
        return out, row["n"]

    prev = llm.stream_chat
    llm.stream_chat = _scripted
    try:
        return asyncio.run(go())
    finally:
        llm.stream_chat = prev


def test_two_reads_start_before_either_finishes() -> None:
    ev, _ = drive([c(0, "rd_a", "1"), c(1, "rd_b", "2")], release_after="rd_b:2")
    check(LOG.index("start:rd_b:2") < LOG.index("end:rd_a:1"), "second read started before the first finished")
    kinds = [(e, d["name"]) for e, d in ev if e in ("tool_call", "tool_result")]
    check(kinds == [("tool_call", "rd_a"), ("tool_result", "rd_a"), ("tool_call", "rd_b"), ("tool_result", "rd_b")], "event order per call is unchanged")


def test_identical_calls_run_once_and_both_get_results() -> None:
    ev, _ = drive([c(0, "rd_a", "same"), c(1, "rd_a", "same")], release_after="rd_a:same")
    check(LOG.count("start:rd_a:same") == 1, "function invoked once")
    res = [d for e, d in ev if e == "tool_result"]
    check(len(res) == 2 and all(not d["error"] for d in res), "both results delivered")


def test_write_between_reads_waits_and_is_the_only_journaled_call() -> None:
    ev, n = drive([c(0, "rd_a", "1"), c(1, "wr", "w"), c(2, "rd_b", "2")], release_after="rd_a:1")
    check(LOG.index("start:wr:w") > LOG.index("end:rd_a:1"), "the write waited for the read before it")
    check(LOG.index("start:rd_b:2") > LOG.index("end:wr:w"), "the read after the write waited for it")
    check(n == 1, "only the write hit the journal")


def test_external_stays_out_of_the_gather() -> None:
    drive([c(0, "rd_a", "1"), c(1, "ext", "e"), c(2, "rd_b", "2")], release_after="rd_a:1")
    check(LOG.index("start:ext:e") > LOG.index("end:rd_a:1") and LOG.index("start:rd_b:2") > LOG.index("end:ext:e"), "external ran alone, in order")


def test_stop_after_first_call_starts_leaves_later_call_unexecuted() -> None:
    drive([c(0, "rd_stop", "1"), c(1, "rd_a", "2")], stop_first=True)
    check("start:rd_stop:1" in LOG and not any(x.startswith("start:rd_a") for x in LOG), "later call never started")


def main() -> int:
    failed = 0
    for k, t in sorted(globals().items()):
        if k.startswith("test_") and callable(t):
            try:
                t()
                print(f"ok   {k}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL {k}: {e}")
    print(f"\n{passed} assertions passed, {failed} test(s) failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
