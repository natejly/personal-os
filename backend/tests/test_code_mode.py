"""Code mode (toolbridge.py): run_python(tools=...) lets a script drive app tools. These tests run real scripts through the
bridge (no OS sandbox is needed to exercise the protocol) against small tools registered on a real Toolbox, so the gate,
taint and call paths are the production ones."""
from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import toolbridge  # noqa: E402
from personal_os.tools import ToolSpec, _obj  # noqa: E402
from tests.test_toolbridge import Rig  # noqa: E402


def sched(r: Rig, **settings: Any) -> None:
    r.tb.settings = lambda: dict(settings)


def add_tool(r: Rig, name: str, fn: Any, danger: str = "safe", params: dict[str, Any] | None = None, desc: str = "", group: str = "files",
             taints: bool = False) -> None:
    r.tb.specs[name] = ToolSpec(name, desc or name, params or _obj({}, []), fn, group, danger, taints=taints)


def run(r: Rig, code: str, tools: Any, timeout: int = 20) -> Any:
    return asyncio.run(r.run(code, tools, timeout))


# ---- stubs ----
def test_stubs_are_generated_from_the_schemas_with_capped_docstrings(tmp_path: Path) -> None:
    r = Rig(tmp_path)

    async def find(ctx: dict[str, Any], query: str, limit: int = 5) -> Any:
        return {"query": query, "limit": limit}
    add_tool(r, "find_things", find, desc="Find things. " + "long " * 200,
             params=_obj({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]))
    names, _ = toolbridge.offered(r.tb, r.ctx, ["find_things"])
    b = toolbridge.Bridge(r.tb, r.ctx, names)
    mod: dict[str, Any] = {}
    os.environ["GRAIN_TOOLS_SOCK"] = "/nonexistent"
    exec(compile(b.client_source(), "grain_tools.py", "exec"), mod)
    f = mod["find_things"]
    assert f.__name__ == "find_things" and f.__doc__.startswith("Find things.")
    assert '"query"' in f.__doc__ and "describe('find_things')" in f.__doc__
    assert len(f.__doc__) < toolbridge.DOC_DESC + toolbridge.DOC_SCHEMA + 200
    import inspect
    sig = inspect.signature(f)
    assert sig.parameters["query"].default is inspect.Parameter.empty and sig.parameters["limit"].default is None
    assert sig.parameters["query"].annotation is str and sig.parameters["limit"].annotation is int
    assert all(k in mod for k in ("call", "gather", "call_later", "describe", "search", "ToolError"))


def test_a_script_sees_the_stub_docs_and_can_call_the_stub(tmp_path: Path) -> None:
    r = Rig(tmp_path)

    async def find(ctx: dict[str, Any], query: str, limit: int = 5) -> Any:
        return {"query": query, "limit": limit}
    add_tool(r, "find_things", find, params=_obj({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]))
    out = run(r, "import grain_tools as g\nprint(g.find_things(query='x'))\nprint('limit' in g.find_things.__doc__)", ["find_things"])
    assert out["exit_code"] == 0, out
    assert out["stdout"].splitlines() == ["{'query': 'x', 'limit': 5}", "True"]


# ---- which tools ----
def test_offered_selects_bridgeable_tools_and_leaves_out_the_rest(tmp_path: Path) -> None:
    r = Rig(tmp_path, modes={"fs_grep": "off", "web_search": "ask"})
    names, why = toolbridge.offered(r.tb, r.ctx, "offered", r.ctx["modes"])
    assert {"fs_glob", "fs_edit", "web_search"} <= set(names)
    assert "fs_grep" not in names  # off is off
    for never in ("run_python", "agent_spawn", "delegate", "shell_run", "gmail_send", "calendar_propose", "workflow_run", "schedule_task",
                  "tool_search", "ask_user"):
        assert never not in names, never
        ok, reason = toolbridge.bridgeable(r.tb, r.ctx, never)
        assert not ok and reason
    assert toolbridge.bridgeable(r.tb, r.ctx, "fs_grep") == (False, "turned off for this chat")
    r.ctx["deferred"] = {"fs_edit"}  # held back this round: not "offered", though a script may still name it
    names, _ = toolbridge.offered(r.tb, r.ctx, "offered", r.ctx["modes"])
    assert "fs_edit" not in names
    names, _ = toolbridge.offered(r.tb, r.ctx, ["fs_edit"], r.ctx["modes"])
    assert names == ["fs_edit"]


def test_a_spec_can_opt_out_of_the_bridge(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.tb.specs["fs_glob"].bridge = False
    assert toolbridge.bridgeable(r.tb, r.ctx, "fs_glob") == (False, "cannot be called from a script")


def test_naming_a_deferred_tool_loads_it(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.ctx["deferred"] = {"fs_glob"}
    out = run(r, f"import grain_tools\nprint(len(grain_tools.call('fs_glob', pattern='*.py', root={str(r.root)!r})['files']))", ["fs_glob"])
    assert out["stdout"].strip() == "2" and "fs_glob" in r.ctx["tool_loaded"]


def test_tools_offered_runs_a_script_end_to_end(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    out = run(r, f"import grain_tools as g\nprint(g.fs_grep(pattern='TODO', root={str(r.root)!r})['matches'][0]['line'])", "offered")
    assert out["exit_code"] == 0 and out["stdout"].strip() == "1", out


def test_code_mode_off_refuses_tools(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    sched(r, codeMode=False)
    out = run(r, "print(1)", "offered")
    assert "Code mode is turned off" in out["error"] and not r.seen
    out = run(r, "print(1)", ["fs_glob"])
    assert "Code mode is turned off" in out["error"]
    assert asyncio.run(r.tb.call("run_python", {"code": "print(6*7)"}, r.ctx))["stdout"].strip() == "42"  # plain run_python is untouched


# ---- protocol ----
def test_the_older_request_shape_and_every_op_work(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    b = toolbridge.Bridge(r.tb, r.ctx, ["fs_glob", "fs_grep"])
    res = asyncio.run(b.request({"tool": "fs_glob", "args": {"pattern": "*.py", "root": str(r.root)}}))
    assert res["ok"] and len(res["result"]["files"]) == 2
    assert asyncio.run(b.request({"op": "describe", "tool": "fs_glob"}))["result"]["name"] == "fs_glob"
    assert asyncio.run(b.request({"op": "describe", "tool": "nope"}))["ok"] is False
    assert asyncio.run(b.request("junk"))["ok"] is False and asyncio.run(b.request({"op": "batch", "calls": 3}))["ok"] is False


def test_search_ranks_tools_and_a_hit_becomes_callable(tmp_path: Path) -> None:
    r = Rig(tmp_path)

    async def thaw(ctx: dict[str, Any], what: str = "") -> Any:
        return {"thawed": what}
    add_tool(r, "freezer_thaw", thaw, desc="Thaw frozen food from the freezer", params=_obj({"what": {"type": "string"}}, []))
    b = toolbridge.Bridge(r.tb, r.ctx, ["fs_glob"])  # no stub for freezer_thaw
    hits = b.search("thaw the freezer")
    assert hits[0]["name"] == "freezer_thaw" and set(hits[0]) == {"name", "description"}
    assert asyncio.run(b.handle("freezer_thaw", {"what": "peas"}))["result"] == {"thawed": "peas"}
    assert asyncio.run(b.handle("fs_edit", {}))["ok"] is False  # never found, never offered
    out = run(r, "import grain_tools as g\nprint(g.search('thaw freezer')[0]['name'])\nprint(g.call('freezer_thaw', what='a'))", ["fs_glob"])
    assert out["stdout"].splitlines() == ["freezer_thaw", "{'thawed': 'a'}"], out


# ---- gather: parallel reads, serialised writes ----
def test_gather_runs_reads_together_and_serialises_the_rest(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    sched(r, parallelReads=4)
    live = {"n": 0, "peak": 0, "edit_with_others": False}

    async def slow(ctx: dict[str, Any], tag: str) -> Any:
        live["n"] += 1
        live["peak"] = max(live["peak"], live["n"])
        await asyncio.sleep(0.3)
        live["n"] -= 1
        return {"tag": tag}

    async def writer(ctx: dict[str, Any], tag: str) -> Any:
        live["n"] += 1
        if live["n"] > 1:
            live["edit_with_others"] = True
        await asyncio.sleep(0.1)
        live["n"] -= 1
        return {"wrote": tag}
    params = _obj({"tag": {"type": "string"}}, ["tag"])
    add_tool(r, "slow_read", slow, "safe", params)
    add_tool(r, "slow_write", writer, "writes", params)
    code = """
import grain_tools as g
res = g.gather(('slow_read', {'tag': 'a'}), g.call_later('slow_read', tag='b'), g.call_later('slow_write', tag='w1'),
               g.call_later('slow_write', tag='w2'), ('slow_read', {'tag': 'c'}), ('slow_read', {'tag': 'd'}), ('nope', {}))
print([r if isinstance(r, g.ToolError) else list(r.values())[0] for r in res][:6], isinstance(res[6], g.ToolError))
"""
    t0 = time.monotonic()
    out = run(r, code, ["slow_read", "slow_write"])
    assert out["exit_code"] == 0, out
    assert out["stdout"].strip() == "['a', 'b', 'w1', 'w2', 'c', 'd'] True"  # in order, the unknown call an error in place
    assert live["peak"] == 2 and not live["edit_with_others"]  # reads paired, writes alone
    assert time.monotonic() - t0 < 1.2  # 2 + 2 read pairs would be 1.2 s sequentially with 4 reads + 2 writes
    assert [c["name"] for c in out["calls"]] == ["slow_read", "slow_read", "slow_write", "slow_write", "slow_read", "slow_read", "nope"]


def test_gather_is_sequential_when_parallel_reads_is_one(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    sched(r, parallelReads=1)
    live = {"n": 0, "peak": 0}

    async def slow(ctx: dict[str, Any]) -> Any:
        live["n"] += 1
        live["peak"] = max(live["peak"], live["n"])
        await asyncio.sleep(0.05)
        live["n"] -= 1
        return {}
    add_tool(r, "slow_read", slow)
    out = run(r, "import grain_tools as g\nprint(len(g.gather(('slow_read', {}), ('slow_read', {}), ('slow_read', {}))))", ["slow_read"])
    assert out["stdout"].strip() == "3" and live["peak"] == 1


# ---- asks ----
def test_an_ask_inner_call_can_be_approved_or_denied_and_the_script_continues(tmp_path: Path) -> None:
    r = Rig(tmp_path, modes={"fs_edit": "ask"})
    answers = [True, False]
    asked: list[str] = []

    async def approve(name: str, args: dict[str, Any], forced: bool) -> bool:
        asked.append(name)
        return answers.pop(0)
    r.ctx["bridge_approve"] = approve
    p = str(r.root / "c.txt")
    code = f"""
import grain_tools as g
print(g.fs_edit(path={p!r}, old='nothing', new='one'))
try:
    g.fs_edit(path={p!r}, old='one', new='two')
except g.ToolError as e:
    print('declined:', e)
print(g.fs_glob(pattern='*.txt', root={str(r.root)!r})['files'][0].split('/')[-1])
"""
    out = run(r, code, ["fs_edit", "fs_glob"])
    assert out["exit_code"] == 0, out
    assert "declined: the user declined fs_edit." in out["stdout"] and out["stdout"].splitlines()[-1] == "c.txt"
    assert (r.root / "c.txt").read_text() == "one\n" and asked == ["fs_edit", "fs_edit"]
    by = {i: c for i, c in enumerate(out["calls"])}
    assert by[0] == {"name": "fs_edit", "ok": True, "ms": by[0]["ms"], "approved": True}
    assert by[1]["ok"] is False and by[1]["approved"] is False
    assert "approved" not in by[2] and by[2]["name"] == "fs_glob"  # it never asked
    assert out["summary"].startswith("3 tool calls, 2 asked, 1 refused, ")


# ---- connectors ----
def test_an_mcp_tool_is_bridged_through_the_connector_path(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    slug = "mcp__srv__ping"
    r.ctx["modes"][slug] = "on"
    r.ctx["mcp_modes"] = {slug: "on"}
    r.ctx["mcp_schemas"] = {slug: {"function": {"name": slug, "description": "Ping the server",
                                                "parameters": _obj({"host": {"type": "string"}}, ["host"])}}}
    calls: list[tuple[str, dict[str, Any]]] = []

    async def connector(name: str, args: dict[str, Any], hctx: Any = None) -> Any:
        calls.append((name, dict(args)))
        return {"pong": args["host"]}
    r.ctx["connector_call"] = connector
    names, _ = toolbridge.offered(r.tb, r.ctx, "offered", r.ctx["modes"])
    assert slug in names
    out = run(r, "import grain_tools as g\nprint(g.mcp__srv__ping(host='h'))\nprint(g.call('mcp__srv__ping', host='k'))", "offered")
    assert out["exit_code"] == 0 and out["stdout"].splitlines() == ["{'pong': 'h'}", "{'pong': 'k'}"], out
    assert calls == [(slug, {"host": "h"}), (slug, {"host": "k"})]
    assert r.ctx["tainted"] is True and r.ctx["taint_sources"].count(slug) == 2  # third-party text taints the run
    assert out["calls"][0]["name"] == slug
    r.ctx["mcp_modes"][slug] = r.ctx["modes"][slug] = "off"
    assert toolbridge.bridgeable(r.tb, r.ctx, slug) == (False, "turned off for this chat")


# ---- limits, taint, output ----
def test_the_call_cap_refuses_beyond_it_and_the_script_can_stop(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    sched(r, codeModeMaxCalls=3)
    code = f"""
import grain_tools as g
n = 0
for i in range(6):
    try:
        g.fs_glob(pattern='*.py', root={str(r.root)!r})
        n += 1
    except g.ToolError as e:
        print('stopped:', e)
        break
print('ok', n)
"""
    out = run(r, code, ["fs_glob"])
    assert "ok 3" in out["stdout"] and "limit of 3 tool calls" in out["stdout"], out
    assert len(r.seen) == 3 and out["summary"].startswith("4 tool calls, 1 refused")
    assert toolbridge.Bridge(r.tb, r.ctx, [])._max_calls() == 3
    sched(r)
    assert toolbridge.Bridge(r.tb, r.ctx, [])._max_calls() == 100


def test_a_tainting_tool_inside_a_script_taints_the_run(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    assert r.ctx["tainted"] is False
    out = run(r, "import grain_tools as g\nprint(g.web_search(query='q')['results'][0]['title'])\nprint(g.fs_glob(pattern='*', root='.') is not None)", ["web_search", "fs_glob"])
    assert out["exit_code"] == 0 and out["stdout"].splitlines()[0] == "t"
    assert r.ctx["tainted"] is True and "web_search" in r.ctx["taint_sources"]


def test_a_tainted_run_asks_for_the_writes_a_script_makes(tmp_path: Path) -> None:
    r = Rig(tmp_path, modes={"web_search": "on", "fs_edit": "on"})
    r.tb.specs["fs_edit"].danger = "external"
    r.tb.settings = lambda: {"alwaysAsk": ["fs_edit"]}
    r.ctx["permission_mode"] = "manual"
    asked: list[str] = []

    async def approve(name: str, args: dict[str, Any], forced: bool) -> bool:
        asked.append(name)
        return False
    r.ctx["bridge_approve"] = approve
    code = f"""
import grain_tools as g
g.web_search(query='q')
try:
    g.fs_edit(path={str(r.root / 'a.py')!r}, old='1', new='9')
except g.ToolError as e:
    print('refused')
"""
    out = run(r, code, ["web_search", "fs_edit"])
    assert out["stdout"].strip() == "refused", out
    assert asked == ["fs_edit"] and not [n for n, _ in r.seen if n == "fs_edit"]


def test_output_to_the_model_is_stdout_summary_and_a_payload_free_trace(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    code = f"""
import grain_tools as g
rs = g.gather(('fs_grep', {{'pattern': 'TODO', 'root': {str(r.root)!r}}}), ('fs_glob', {{'pattern': '*.py', 'root': {str(r.root)!r}}}))
print(sum(len(r['matches']) for r in rs[:1]), 'matches')
"""
    out = run(r, code, ["fs_grep", "fs_glob"])
    assert out["stdout"].strip() == "3 matches"
    assert [set(c) for c in out["calls"]] == [{"name", "ok", "ms"}] * 2 and [c["ok"] for c in out["calls"]] == [True, True]
    assert re.fullmatch(r"2 tool calls, \d+\.\d s", out["summary"]), out["summary"]
    assert "matches" not in str(out["calls"]) and out["bridged_calls"] == 2
    assert out["bridge_log"][0]["tool"] == "fs_grep"  # the older fields stay


def test_a_huge_inner_result_is_cut_for_the_script_with_a_note() -> None:
    assert toolbridge._fit({"a": 1}) == {"a": 1}
    s = toolbridge._fit("x" * 3000, 1000)
    assert s.startswith("x" * 1000) and "cut to fit 1000" in s
    lst = toolbridge._fit([{"i": i, "pad": "p" * 50} for i in range(500)], 4000)
    assert lst["truncated"] is True and lst["total_items"] == 500 and 0 < len(lst["items"]) < 500
    d = toolbridge._fit({"small": 1, "big": "y" * 10_000, "also": ["z" * 100] * 50}, 3000)
    assert d["truncated"] is True and "big" in d["truncated_fields"] and d["small"] == 1 and len(d["big"]) < 4000
