"""Workflows (workflows.py) and commands (commands.py).

Offline: subagent children answer from a scripted llm.stream_chat, tools are small stubs registered on the real
toolbox. The claims worth a test:
  - validation reports every problem a definition can have (unknown tool, duplicate id, a need on a later step,
    a blocked tool, a template that points nowhere);
  - templates are substituted, never evaluated, and keep a value's type when they are the whole string;
  - the digest changes when the definition or a parameter does; a run never starts without its approved digest,
    and an edit after the proposal invalidates the approval; workflow_run only records a run;
  - a fan-out honours max_parallel; a step marked approval: required parks on a durable card and continues once
    it is allowed (and fails when denied); untrusted agent text makes a later external tool ask;
  - after a crash, resume skips every done step, repeats no side effect that began, and a failed step blocks
    only the steps that need it;
  - commands: `$ARGUMENTS` and `$1..$n` only, no other expansion, subtask runs as a child.

Run: PERSONAL_OS_DATA_DIR=/tmp/wftest python backend/tests/test_workflows.py
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

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="wftest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import llm  # noqa: E402
from personal_os import app as appmod  # noqa: E402
from personal_os import commands as cmds  # noqa: E402
from personal_os import workflows as wf  # noqa: E402
from personal_os.tools import ToolSpec, _obj  # noqa: E402

passed = 0
tb = appmod.toolbox
store = appmod.workflow_store
engine = appmod.workflow_engine
runs = appmod.run_store


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


appmod.db.set_settings({"autoLearn": False, "baseUrl": ""})

# ---- a scripted model for the children, and stub tools -------------------------------------------
LIVE = {"now": 0, "peak": 0}
DELAY = {"s": 0.0}
TASKS: list[str] = []
CALLS: dict[str, int] = {}
FILES = {"files": ["a.md", "b.md", "c.md", "d.md"]}
HANG = {"on": False}


async def _stream(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
                  kind: str = "chat", effort: str = "default", tool_choice: str = "auto", fast: bool = False,
                  cancel: asyncio.Event | None = None) -> Any:
    task = [m for m in messages if m["role"] == "user"][-1]["content"]
    TASKS.append(task)
    LIVE["now"] += 1
    LIVE["peak"] = max(LIVE["peak"], LIVE["now"])
    try:
        if DELAY["s"]:
            await asyncio.sleep(DELAY["s"])
    finally:
        LIVE["now"] -= 1
    yield {"type": "delta", "text": f"summary of {task}"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


_missing = set(inspect.signature(llm.stream_chat).parameters) - set(inspect.signature(_stream).parameters)
assert not _missing, f"_stream is missing {sorted(_missing)} from llm.stream_chat"


def _tool(name: str, danger: str, fn: Any, taints: bool = False) -> None:
    tb.specs[name] = ToolSpec(name, f"test tool {name}", _obj({}, []), fn, "test", danger, taints=taints)


async def wf_list(ctx: dict[str, Any], folder: str = "") -> Any:
    CALLS["wf_list"] = CALLS.get("wf_list", 0) + 1
    return {"files": [f"{folder}/{f}" for f in FILES["files"]]}


async def wf_write(ctx: dict[str, Any], path: str = "", text: Any = None) -> Any:
    CALLS["wf_write"] = CALLS.get("wf_write", 0) + 1
    if HANG["on"]:
        await asyncio.Event().wait()
    return {"ok": True, "path": path, "chars": len(json.dumps(text))}


async def wf_boom(ctx: dict[str, Any], **a: Any) -> Any:
    CALLS["wf_boom"] = CALLS.get("wf_boom", 0) + 1
    return {"error": "boom"}


async def wf_mail(ctx: dict[str, Any], **a: Any) -> Any:
    CALLS["wf_mail"] = CALLS.get("wf_mail", 0) + 1
    return {"sent": True}


_tool("wf_list", "safe", wf_list)
_tool("wf_write", "writes", wf_write)
_tool("wf_boom", "safe", wf_boom)
_tool("wf_mail", "external", wf_mail)

DIGEST = {
    "name": "folder-digest", "description": "Summarize a folder into digest.md",
    "params": {"folder": {"type": "string", "required": True}, "limit": {"type": "integer", "default": 3}},
    "steps": [
        {"id": "scan", "tool": "wf_list", "args": {"folder": "{{folder}}"}},
        {"id": "sums", "fan_out": {"over": "{{scan.result.files}}", "max_parallel": 2,
                                   "agent": {"role": "researcher", "task": "Summarize {{item}} (#{{index}})"}}},
        {"id": "write", "tool": "wf_write", "approval": "required",
         "args": {"path": "{{folder}}/digest.md", "text": "{{sums.result}}"}},
    ],
    "output": "{{write.result.path}}",
}


def reset() -> None:
    LIVE.update(now=0, peak=0)
    DELAY["s"] = 0.0
    TASKS.clear()
    CALLS.clear()
    HANG["on"] = False
    FILES["files"] = ["a.md", "b.md", "c.md", "d.md"]
    appmod.subagent_mgr.children.clear()
    engine.tasks.clear()
    engine.stops.clear()
    appmod.db.set_settings({"workflowMaxFanOut": 50, "subagentMaxConcurrent": 4, "workspaceRoots": []})
    with appmod.db.tx() as c:
        c.execute("DELETE FROM workflow_runs")
        c.execute("DELETE FROM workflows")


def run(coro: Any) -> Any:
    prev = llm.stream_chat
    llm.stream_chat = _stream
    try:
        return asyncio.run(coro)
    finally:
        llm.stream_chat = prev


async def until(pred: Any, timeout: float = 8.0) -> Any:
    t = 0.0
    while t < timeout:
        v = pred()
        if v:
            return v
        await asyncio.sleep(0.02)
        t += 0.02
    raise AssertionError("timed out waiting")


def save(defn: dict[str, Any]) -> dict[str, Any]:
    return store.save(json.dumps(defn))


def errs(defn: Any) -> list[str]:
    return wf.validate(defn, set(tb.specs), lambda n: appmod.subagent_mgr.role_for(n) is not None)


def pending(run_id: str) -> list[dict[str, Any]]:
    return runs.approvals("pending", run_id=run_id)


# ---- validation ------------------------------------------------------------------------------------

def test_validation() -> None:
    reset()
    check(errs(DIGEST) == [], "the folder digest validates")
    bad = {"name": "Bad Name", "steps": [
        {"id": "a", "tool": "nope"},
        {"id": "a", "tool": "wf_list"},
        {"id": "b", "tool": "wf_list", "needs": ["c"]},
        {"id": "c", "tool": "wf_list"},
        {"id": "d", "tool": "wf_list", "needs": ["d"]},
        {"id": "e", "tool": "agent_spawn"},
        {"id": "f", "tool": "wf_list", "args": {"x": "{{ghost}}", "y": "{{item}}", "z": "{{a}}"}},
        {"id": "g", "tool": "wf_list", "agent": {"task": "x"}},
        {"id": "h", "agent": {"role": "wizard", "task": "x"}},
        {"id": "i", "agent": {"role": "researcher"}},
        {"id": "j", "fan_out": {"agent": {"task": "t"}, "max_parallel": 99}},
    ]}
    text = " | ".join(errs(bad))
    for frag in ("name must be", "unknown tool 'nope'", "duplicate id", "comes later", "needs itself", "cannot be a workflow step",
                 "{{ghost}} is not a parameter", "only exists inside a fan_out", "write {{a.result}}", "exactly one of tool, agent or fan_out",
                 "unknown agent role", "needs a task", "max_parallel must be", "fan_out needs `over`"):
        check(frag in text, f"validation reports: {frag}")
    check(errs({"name": "x", "steps": []}) == ["steps must be a non-empty list"], "no steps is an error")
    check(any("item" in e for e in errs({"name": "x", "steps": [{"id": "s", "fan_out": {"over": "{{item}}", "agent": {"task": "t"}}}]})),
          "`over` cannot use item")
    check(any("output" in e for e in errs({**DIGEST, "output": "{{nothing.result}}"})), "output templates are checked")
    try:
        store.save(json.dumps(bad))
        check(False, "an invalid definition is refused")
    except ValueError:
        check(True, "an invalid definition is refused")
    check(wf.parse(json.dumps(DIGEST))["name"] == "folder-digest", "JSON parses")
    try:
        wf.parse("not: [valid")
        check(False, "garbage is refused")
    except ValueError:
        check(True, "garbage is refused with a readable error")


def test_library_template_validates() -> None:
    """The definition the Library offers as a starting point must pass the real validator against real tools."""
    d = {"name": "folder-digest", "params": {"folder": {"type": "string", "required": True}}, "steps": [
        {"id": "scan", "tool": "read_local_file", "args": {"path": "{{folder}}"}},
        {"id": "summaries", "fan_out": {"over": "{{scan.result.entries}}", "max_parallel": 3,
                                        "agent": {"role": "researcher", "task": "Read {{folder}}/{{item}} and summarize it."}}},
        {"id": "write", "tool": "write_local_file", "approval": "required", "args": {"path": "{{folder}}/digest.md", "content": "{{summaries.result}}"}}],
        "output": "{{write.result}}"}
    check(errs(d) == [], "the starter workflow validates")


def test_params() -> None:
    d = DIGEST
    check(wf.resolve_params(d, {"folder": "/x"}) == {"folder": "/x", "limit": 3}, "defaults are applied")
    check(wf.resolve_params(d, {"folder": "/x", "limit": "7"})["limit"] == 7, "values are coerced to the declared type")
    for given, frag in (({}, "required"), ({"folder": "/x", "zzz": 1}, "unknown parameter"), ({"folder": "/x", "limit": "many"}, "integer")):
        try:
            wf.resolve_params(d, given)
            check(False, f"refused: {frag}")
        except ValueError as e:
            check(frag in str(e), f"parameter error mentions {frag}")


# ---- templates -------------------------------------------------------------------------------------

def test_templates() -> None:
    p, st = {"folder": "/docs", "n": 3}, {"scan": {"files": ["a", "b"], "meta": {"k": "v"}}}
    check(wf.render("{{folder}}/x", p) == "/docs/x", "a parameter is substituted into text")
    check(wf.render("{{scan.result.files}}", p, st) == ["a", "b"], "a lone template keeps the value's type")
    check(wf.render("{{scan.result.files.1}}", p, st) == "b", "a path may index a list")
    check(wf.render("got {{scan.result.meta}}", p, st) == 'got {"k": "v"}', "a structure inside text is JSON")
    check(wf.render({"a": ["{{n}}", "{{folder}}"]}, p) == {"a": [3, "/docs"]}, "templates inside nested values")
    check(wf.render("{{item}}-{{index}}", p, vars={"item": "z", "index": 2}) == "z-2", "fan-out variables")
    check(wf.render("{{scan.result}} {{x.result}}", p, st, keep_steps=True).endswith("{{x.result}}"), "keep_steps leaves unknown results")
    check(wf.render("{{item}}", p, keep_steps=True) == "{{item}}", "the plan shows {{item}} as written")
    for bad in ("{{missing}}", "{{x.result}}", "{{scan.result.nope}}"):
        try:
            wf.render(bad, p, st)
            check(False, f"{bad} raises")
        except wf.TemplateError:
            check(True, f"{bad} raises")
    # nothing is evaluated: expressions are not templates, and a value that looks like one is not re-expanded
    check(wf.render("{{ 1+1 }} {{__import__('os')}}", p) == "{{ 1+1 }} {{__import__('os')}}", "expressions are left alone")
    check(wf.render("{{folder}}", {"folder": "{{n}}", "n": 9}) == "{{n}}", "substituted values are not expanded again")
    check(wf.truthy("false") is False and wf.truthy("") is False and wf.truthy("yes") and wf.truthy([1]), "when: truthiness")


# ---- digest and the approval gate ---------------------------------------------------------------

def test_digest_and_gate() -> None:
    reset()
    w = save(DIGEST)
    d0 = wf.digest(w["definition"], {"folder": "/a", "limit": 3})
    check(d0 == wf.digest(DIGEST, {"folder": "/a", "limit": 3}), "the digest does not depend on optional keys left out")
    check(d0 != wf.digest(w["definition"], {"folder": "/b", "limit": 3}), "a different parameter changes the digest")
    edited = json.loads(json.dumps(DIGEST))
    edited["steps"][2]["args"]["path"] = "/etc/digest.md"
    check(wf.digest(edited, {"folder": "/a", "limit": 3}) != d0, "an edit to a step changes the digest")
    check(wf.def_digest(wf.normalize(w["definition"])) == w["digest"], "normalizing twice is stable")

    async def go() -> None:
        r = store.create_run(w, {"folder": "/a"})
        check(r["status"] == "awaiting_approval" and r["plan_digest"] == d0, "a run is recorded awaiting approval")
        check([s["id"] for s in r["plan"]] == ["scan", "sums", "write"], "the plan lists the expanded steps")
        check(r["plan"][0]["args"] == {"folder": "/a"} and "{{item}}" in json.dumps(r["plan"][1]), "parameters are filled in, per-item values are not")
        check(r["plan"][2]["approval"] == "required" and r["plan"][2]["needs"] == ["sums"], "dependencies and approvals are shown")
        for fn, label in ((lambda: engine.start(r["id"]), "start without approval"), (lambda: engine.approve(r["id"], "0" * 64), "a wrong digest"),
                          (lambda: engine.resume(r["id"]), "resume before approval")):
            try:
                fn()
                check(False, f"{label} is refused")
            except wf.ApprovalError:
                check(True, f"{label} is refused")
        check(not engine.tasks, "nothing ran")
        check(CALLS == {}, "no tool was called")
        # editing the saved workflow after the proposal invalidates the approval
        store.save(json.dumps({**DIGEST, "steps": edited["steps"]}), w["id"])
        try:
            engine.approve(r["id"], r["plan_digest"])
            check(False, "approving a stale plan is refused")
        except wf.ApprovalError as e:
            check("edited" in str(e), "approving a plan whose workflow was edited is refused")
        check(store.get_run(r["id"])["status"] == "stale" and CALLS == {}, "the run is marked stale and nothing ran")

        # a tampered snapshot no longer matches the digest it was proposed with
        w2 = store.get("folder-digest")
        r2 = store.create_run(w2, {"folder": "/a"})
        with appmod.db.tx() as c:
            c.execute("UPDATE workflow_runs SET params=? WHERE id=?", (json.dumps({"folder": "/etc", "limit": 3}), r2["id"]))
        try:
            engine.approve(r2["id"], r2["plan_digest"])
            check(False, "a tampered run is refused")
        except wf.ApprovalError:
            check(True, "a run whose params changed under its digest is refused")

    run(go())


def test_workflow_run_tool_only_proposes() -> None:
    reset()
    save(DIGEST)

    async def go() -> None:
        ctx = {"project_id": None, "conversation_id": None, "tainted": False, "taint_sources": [], "settings": appmod.settings()}
        out = await tb.call("workflow_run", {"name": "folder-digest", "params": {"folder": "/a"}}, ctx)
        check(out["status"] == "awaiting_approval" and out["plan_digest"] and len(out["plan"]) == 3, "workflow_run returns the plan to approve")
        check(store.get_run(out["run_id"])["status"] == "awaiting_approval" and not engine.tasks, "it started nothing")
        bad = await tb.call("workflow_run", {"name": "folder-digest", "params": {}}, ctx)
        check("error" in bad and "required" in bad["error"], "a missing parameter is reported")
        missing = await tb.call("workflow_run", {"name": "nope"}, ctx)
        check("error" in missing and "folder-digest" in missing["expected"], "an unknown workflow lists the saved ones")
        listing = await tb.call("workflow_list", {}, ctx)
        check(listing["workflows"][0]["name"] == "folder-digest" and "folder" in listing["workflows"][0]["params"], "workflow_list")
        # a scheduled (proposal-only) run may propose but cannot resume one
        r = store.get_run(out["run_id"])
        job_ctx = {**ctx, "proposal_only": True}
        out2 = await tb.call("workflow_run", {"name": "folder-digest", "params": {"folder": "/b"}}, job_ctx)
        check(out2["status"] == "awaiting_approval" and store.get_run(out2["run_id"])["source"] == "job", "a job's proposal is marked as one")
        res = await tb.call("workflow_resume", {"run_id": r["id"]}, job_ctx)
        check("error" in res, "a proposal-only run cannot resume a workflow")
    run(go())
    check(tb.specs["workflow_resume"].danger == "plan" and tb.specs["workflow_run"].danger == "writes", "resume always asks; run only records")


def test_run_reports_to_its_chat() -> None:
    reset()
    w = save(DIGEST)
    cid = appmod.convos.create(None, "t", "m")["id"]
    said = lambda: [m["content"] for m in appmod.convos.get(cid)["messages"] if m["role"] == "assistant"]
    r = store.create_run(w, {"folder": "/a"}, conversation_id=cid)
    check(len(said()) == 1 and "waiting for approval" in said()[0] and "Library -> Workflows" in said()[0], "a new run tells its chat to approve it")
    store.set_run(r["id"], status="awaiting_approval")
    store.set_run(r["id"], status="running")
    check(len(said()) == 1, "repeating a status or moving to running posts nothing")
    store.set_run(r["id"], status="done", result="/a/digest.md")
    store.set_run(r["id"], status="done")
    check(len(said()) == 2 and "finished" in said()[1] and "/a/digest.md" in said()[1], "done posts once, with the result")
    store.create_run(w, {"folder": "/b"})
    check(len(said()) == 2, "a run with no chat posts nowhere")


def test_step_sees_the_chats_working_folder() -> None:
    reset()
    folder = tempfile.mkdtemp(prefix="wf_folder_", dir=str(Path.home()))
    try:
        cid = appmod.convos.create(None, "t", "m")["id"]
        appmod.convos.update(cid, {"settings": {"workingFolder": folder}})
        w = save(DIGEST)
        r = store.create_run(w, {"folder": "/a"}, conversation_id=cid)
        roots = engine._ctx(store.get_run(r["id"]), asyncio.Event())["settings"]["workspaceRoots"]
        check(roots and roots[0] == str(Path(folder).resolve()), "a step run from a chat with a working folder sees it in workspaceRoots")
        r2 = store.create_run(w, {"folder": "/a"})
        check(folder not in (engine._ctx(store.get_run(r2["id"]), asyncio.Event())["settings"].get("workspaceRoots") or []), "a run with no chat does not")
    finally:
        os.rmdir(folder)


# ---- the engine ----------------------------------------------------------------------------------

def test_full_run_with_fan_out_and_approval() -> None:
    reset()
    DELAY["s"] = 0.12
    w = save(DIGEST)

    async def go() -> None:
        r = store.create_run(w, {"folder": "/docs"})
        engine.approve(r["id"], r["plan_digest"])
        cur = await until(lambda: (g := store.get_run(r["id"]))["status"] == "waiting_approval" and g)
        by = {s["step_id"]: s for s in cur["steps"]}
        check(by["scan"]["status"] == "done" and by["sums"]["status"] == "done", "steps before the gate finished")
        check(by["write"]["status"] == "waiting_approval", "the step marked approval: required is parked")
        check(CALLS.get("wf_write") is None, "the write has not run while the card is pending")
        check(LIVE["peak"] == 2, "fan-out ran two at a time (max_parallel)")
        check(sorted(TASKS) == [f"Summarize /docs/{n}.md (#{i})" for i, n in enumerate("abcd")], "each item rendered into its own task")
        cards = pending(r["id"])
        check(len(cards) == 1 and cards[0]["tool"] == "wf_write", "one durable approval row names the tool")
        check(cards[0]["args"]["path"] == "/docs/digest.md" and "summary of" in json.dumps(cards[0]["args"]["text"]), "the card shows the real arguments")
        check(runs.get(r["id"])["status"] == "awaiting_approval", "the run tape shows it is waiting on the user")
        runs.decide(cards[0]["call_id"], "allow")
        done = await until(lambda: (g := store.get_run(r["id"]))["status"] == "done" and g)
        check(CALLS["wf_write"] == 1 and done["result"] == "/docs/digest.md", "approved: the write ran once and the output rendered")
        check(all(s["status"] == "done" for s in done["steps"]), "every step is done")
        sums = next(s for s in done["steps"] if s["step_id"] == "sums")
        check(len(sums["result"]) == 4 and sums["result"][2].startswith("summary of Summarize /docs/c.md"), "fan-out results keep the item order")
        check(done["steps"][0]["idempotency_key"] == f"{r['id']}:scan", "idempotency key is run_id:step_id")
        check(runs.get(r["id"])["status"] == "done", "the tape closes")

    run(go())


def test_denied_step_fails_and_blocks_dependants() -> None:
    reset()
    d = {"name": "deny-flow", "steps": [
        {"id": "w", "tool": "wf_write", "approval": "required", "args": {"path": "/x"}},
        {"id": "after", "tool": "wf_list", "needs": ["w"]},
        {"id": "indep", "tool": "wf_list", "args": {"folder": "/i"}},
        {"id": "bad", "tool": "wf_boom"},
        {"id": "dep_bad", "tool": "wf_list", "args": {"folder": "{{bad.result}}"}},
    ]}
    w = save(d)

    async def go() -> None:
        r = store.create_run(w, {})
        engine.approve(r["id"], r["plan_digest"])
        card = await until(lambda: pending(r["id"]))
        runs.decide(card[0]["call_id"], "deny")
        cur = await until(lambda: (g := store.get_run(r["id"]))["status"] == "failed" and g)
        by = {s["step_id"]: s for s in cur["steps"]}
        check(by["w"]["status"] == "failed" and "declined" in by["w"]["error"] and CALLS.get("wf_write") is None, "a denied step fails without running")
        check(by["after"]["status"] == "blocked", "a step that needs a failed one is blocked")
        check(by["indep"]["status"] == "done", "an independent step still finishes")
        check(by["bad"]["status"] == "failed" and "boom" in by["bad"]["error"], "a tool error fails its step")
        check(by["dep_bad"]["status"] == "blocked" and CALLS.get("wf_list") == 1, "a step reading a failed step's result is blocked too")
        check("did not run" in cur["error"], "the run says what did not run")

    run(go())


def test_permission_rules_apply_to_tool_steps() -> None:
    """A deny rule refuses a tool step before the tool runs, even one the approved plan named."""
    reset()
    appmod.db.set_settings({"permissionRules": {"allow": [], "ask": [], "deny": ["wf_list"]}})
    w = save({"name": "rule-flow", "steps": [{"id": "scan", "tool": "wf_list", "args": {"folder": "/r"}}]})

    async def go() -> None:
        r = store.create_run(w, {})
        engine.approve(r["id"], r["plan_digest"])
        cur = await until(lambda: (g := store.get_run(r["id"]))["status"] in ("failed", "done") and g)
        step = cur["steps"][0]
        check(step["status"] == "failed" and "refused" in (step["error"] or "") and CALLS.get("wf_list") is None,
              f"a denied tool step fails without running, got {step['status']} / {step['error']}")

    try:
        run(go())
    finally:
        appmod.db.set_settings({"permissionRules": {"allow": [], "ask": [], "deny": []}})


def test_when_and_skipped() -> None:
    reset()
    d = {"name": "cond", "params": {"go": {"type": "boolean", "default": False}}, "steps": [
        {"id": "a", "tool": "wf_list", "when": "{{go}}"},
        {"id": "b", "tool": "wf_list", "args": {"folder": "/b"}}]}
    w = save(d)

    async def go() -> None:
        r = store.create_run(w, {})
        engine.approve(r["id"], r["plan_digest"])
        cur = await until(lambda: (g := store.get_run(r["id"]))["status"] == "done" and g)
        by = {s["step_id"]: s["status"] for s in cur["steps"]}
        check(by == {"a": "skipped", "b": "done"} and CALLS["wf_list"] == 1, "a step whose `when` is false is skipped")

    run(go())


def test_resume_after_crash() -> None:
    reset()
    DELAY["s"] = 0.01
    w = save(DIGEST)

    async def go() -> None:
        r = store.create_run(w, {"folder": "/docs"})
        engine.approve(r["id"], r["plan_digest"])
        await until(lambda: (g := store.get_run(r["id"]))["status"] == "waiting_approval" and g)
        n_tasks = len(TASKS)
        check(CALLS["wf_list"] == 1, "scan ran once")
        # the process dies: the task is gone, the rows still say running
        engine.tasks[r["id"]].cancel()
        await asyncio.sleep(0.1)
        store.set_run(r["id"], status="running")
        check(store.recover() >= 1, "startup recovery finds the run left running")
        cur = store.get_run(r["id"])
        check(cur["status"] == "interrupted", "it becomes interrupted")
        check({s["step_id"]: s["status"] for s in cur["steps"]} == {"scan": "done", "sums": "done", "write": "pending"}, "done steps stay done, the rest wait")
        res = engine.resume(r["id"])
        check(res["status"] == "running", "resume starts it again")
        await until(lambda: store.get_run(r["id"])["status"] == "waiting_approval")
        card = await until(lambda: pending(r["id"]))
        check(len(card) == 1, "the card that was pending before the crash is the one asked again")
        runs.decide(card[0]["call_id"], "allow")
        done = await until(lambda: (g := store.get_run(r["id"]))["status"] == "done" and g)
        check(CALLS["wf_list"] == 1 and len(TASKS) == n_tasks, "resume did not re-run the scan or the agents")
        check(CALLS["wf_write"] == 1 and done["result"] == "/docs/digest.md", "it continued from the first step that was not done")
        try:
            engine.resume(r["id"])
            check(False, "a finished run does not resume")
        except wf.ApprovalError:
            check(True, "a finished run does not resume")

    run(go())


def test_side_effect_that_began_is_not_repeated() -> None:
    reset()
    HANG["on"] = True
    d = {"name": "one-write", "steps": [{"id": "w", "tool": "wf_write", "args": {"path": "/x"}}]}
    w = save(d)

    async def go() -> None:
        r = store.create_run(w, {})
        engine.approve(r["id"], r["plan_digest"])
        await until(lambda: CALLS.get("wf_write") == 1)
        engine.tasks[r["id"]].cancel()  # the process dies in the middle of the write
        await asyncio.sleep(0.1)
        store.set_run(r["id"], status="running")
        store.recover()
        HANG["on"] = False
        engine.resume(r["id"])
        cur = await until(lambda: (g := store.get_run(r["id"]))["status"] == "failed" and g)
        check(CALLS["wf_write"] == 1, "the write that began before the crash was not run a second time")
        check("outcome was never recorded" in cur["steps"][0]["error"], "the step says its outcome is unknown")

    run(go())


def test_untrusted_agent_text_makes_external_tools_ask() -> None:
    reset()
    d = {"name": "tainted", "steps": [
        {"id": "ag", "agent": {"role": "researcher", "task": "look it up"}},
        {"id": "mail", "tool": "wf_mail", "args": {"body": "{{ag.result}}"}}]}
    w = save(d)
    appmod.db.set_settings({"tools": {"wf_mail": "on"}, "alwaysAsk": ["wf_mail"]})

    async def go() -> None:
        r = store.create_run(w, {})
        engine.approve(r["id"], r["plan_digest"])
        card = await until(lambda: pending(r["id"]))
        check(card[0]["tool"] == "wf_mail" and card[0]["forced"], "after an agent step, an external tool asks even when it would be on")
        check(CALLS.get("wf_mail") is None, "and has not run")
        runs.decide(card[0]["call_id"], "allow")
        done = await until(lambda: (g := store.get_run(r["id"]))["status"] == "done" and g)
        check(CALLS["wf_mail"] == 1 and done["status"] == "done", "it runs once allowed")
        appmod.db.set_settings({"tools": {}, "alwaysAsk": llm.DEFAULT_SETTINGS["alwaysAsk"]})

    run(go())


def test_tool_off_and_cancel_and_fanout_limit() -> None:
    reset()
    d = {"name": "off-tool", "steps": [{"id": "w", "tool": "wf_list"}]}
    w = save(d)
    appmod.db.set_settings({"tools": {"wf_list": "off"}})

    async def go() -> None:
        r = store.create_run(w, {})
        engine.approve(r["id"], r["plan_digest"])
        cur = await until(lambda: (g := store.get_run(r["id"]))["status"] == "failed" and g)
        check("not available" in cur["steps"][0]["error"] and CALLS.get("wf_list") is None, "a tool the user has off cannot be called by a workflow")

    run(go())
    appmod.db.set_settings({"tools": {}})

    reset()
    FILES["files"] = [f"{i}.md" for i in range(5)]
    appmod.db.set_settings({"workflowMaxFanOut": 3})
    w = save(DIGEST)

    async def go2() -> None:
        r = store.create_run(w, {"folder": "/d"})
        engine.approve(r["id"], r["plan_digest"])
        cur = await until(lambda: (g := store.get_run(r["id"]))["status"] == "failed" and g)
        check("workflowMaxFanOut" in next(s for s in cur["steps"] if s["step_id"] == "sums")["error"], "a fan-out over too many items is refused")
        check(not TASKS, "no agent was started")
        # cancelling a run that waits on a card
        reset()
        r2 = store.create_run(save(DIGEST), {"folder": "/d"})
        engine.approve(r2["id"], r2["plan_digest"])
        await until(lambda: pending(r2["id"]))
        check(engine.cancel(r2["id"]), "cancel accepts a running run")
        cur2 = await until(lambda: (g := store.get_run(r2["id"]))["status"] == "cancelled" and g)
        check(CALLS.get("wf_write") is None and not pending(r2["id"]), "cancelled: the card is settled and the write never ran")
        check(engine.cancel(r2["id"]) is False, "a cancelled run cannot be cancelled again")

    run(go2())


# ---- commands ------------------------------------------------------------------------------------

def test_commands() -> None:
    check(cmds.fill("Review $1 against $2 then $ARGUMENTS", 'a.md "b c.md" extra') == "Review a.md against b c.md then a.md \"b c.md\" extra",
          "$1..$n and $ARGUMENTS")
    check(cmds.fill("Only $3 and $1", "x") == "Only  and x", "a missing positional is empty")
    check(cmds.fill("Plain", "tail words") == "Plain\n\nARGUMENTS: tail words", "arguments are appended when the template has no placeholder")
    check(cmds.fill("$1", "$2 hello") == "$2", "an argument is not expanded a second time")
    check(cmds.fill("run !`rm -rf ~` and @file.txt ${HOME} $(whoami)", "") == "run !`rm -rf ~` and @file.txt ${HOME} $(whoami)",
          "no shell or file expansion at template time")
    text = "---\nname: summarize-folder\ndescription: Summarize a folder\nsubtask: true\nrole: researcher\n---\nSummarize the notes in $1.\n"
    f = cmds.parse(text)
    check(f["name"] == "summarize-folder" and f["subtask"] and f["role"] == "researcher" and f["body"].startswith("Summarize"), "frontmatter parses")
    for bad, frag in (("no frontmatter", "frontmatter"), ("---\nname: Bad Name\n---\nx", "name must be"), ("---\nname: ok\n---\n", "template"),
                      ("---\nname: ok\nsubtask: maybe\n---\nx", "subtask")):
        try:
            cmds.parse(bad)
            check(False, f"refused: {frag}")
        except ValueError as e:
            check(frag in str(e), f"command error mentions {frag}")

    with appmod.db.tx() as c:
        c.execute("DELETE FROM commands")
    saved = appmod.command_store.save(text)
    plain = appmod.command_store.save("---\nname: tidy\ndescription: Tidy\n---\nTidy up $ARGUMENTS\n")
    check(appmod.command_store.get("tidy")["id"] == plain["id"], "commands are stored by name")
    try:
        appmod.command_store.save(text)
        check(False, "duplicate name refused")
    except ValueError:
        check(True, "a duplicate name is refused")

    async def go() -> None:
        reset()
        ctx = {"project_id": None, "conversation_id": appmod.convos.create(None, "t", "m")["id"], "message_id": None, "tainted": False,
               "taint_sources": [], "allowed_urls": set(), "settings": appmod.settings(), "modes": tb.effective({}, None, None), "depth": 0,
               "agent_run_id": "", "model": "test-model", "stop": asyncio.Event(), "budget": appmod.Budget(appmod.settings()), "run": None}
        out = await tb.call("command_run", {"name": "tidy", "arguments": "my desk"}, ctx)
        check(out["instructions"] == "Tidy up my desk" and not ctx["tainted"], "a plain command returns its filled template as instructions")
        out = await tb.call("command_run", {"name": "/summarize-folder", "arguments": "~/notes"}, ctx)
        check(TASKS == ["Summarize the notes in ~/notes."] and out["state"] == "completed" and ctx["tainted"], "a subtask command runs as a child agent")
        listing = await tb.call("command_list", {}, ctx)
        check({c["name"] for c in listing["commands"]} == {"tidy", "summarize-folder"}, "command_list")
        miss = await tb.call("command_run", {"name": "nope"}, ctx)
        check("tidy" in miss["expected"], "an unknown command lists the saved ones")

    run(go())
    check(appmod.command_store.delete(saved["id"]) and appmod.command_store.delete(plain["id"]), "commands delete")


# ---- routes ------------------------------------------------------------------------------------

def test_routes() -> None:
    reset()
    ok = appmod.validate_workflow(appmod.WorkflowIn(text=json.dumps(DIGEST)))
    check(ok == {"ok": True, "errors": []}, "POST /workflows/validate accepts a good definition")
    bad = appmod.validate_workflow(appmod.WorkflowIn(text=json.dumps({"name": "x", "steps": [{"id": "a", "tool": "nope"}]})))
    check(bad["ok"] is False and "unknown tool" in bad["errors"][0], "and reports problems without saving")
    check(appmod.validate_workflow(appmod.WorkflowIn(text="{{"))["ok"] is False, "unparseable text is a validation error, not a 500")
    saved = appmod.create_workflow(appmod.WorkflowIn(text=json.dumps(DIGEST)))
    check(saved["name"] == "folder-digest" and saved["params"]["folder"]["required"], "POST /workflows saves")
    check([w["name"] for w in appmod.list_workflows()] == ["folder-digest"], "GET /workflows lists")
    try:
        appmod.create_workflow(appmod.WorkflowIn(text=json.dumps(DIGEST)))
        check(False, "duplicate refused")
    except Exception as e:  # noqa: BLE001
        check(getattr(e, "status_code", 0) == 400, "a duplicate name is a 400")
    r = appmod.propose_workflow_run(saved["id"], appmod.WorkflowRunIn(params={"folder": "/z"}))
    check(r["status"] == "awaiting_approval" and appmod.get_workflow_run(r["id"])["plan_digest"] == r["plan_digest"], "proposing a run records it")
    check([x["id"] for x in appmod.list_workflow_runs(saved["id"])] == [r["id"]], "run history")
    try:
        appmod.propose_workflow_run(saved["id"], appmod.WorkflowRunIn(params={}))
        check(False, "missing param refused")
    except Exception as e:  # noqa: BLE001
        check(getattr(e, "status_code", 0) == 400, "a missing parameter is a 400")
    try:
        asyncio.run(appmod.approve_workflow_run(r["id"], appmod.WorkflowApproveIn(plan_digest="bad")))
        check(False, "bad digest refused")
    except Exception as e:  # noqa: BLE001
        check(getattr(e, "status_code", 0) == 409, "approving the wrong digest is a 409")
    edited = {**DIGEST, "description": "changed"}
    appmod.update_workflow(saved["id"], appmod.WorkflowIn(text=json.dumps(edited)))
    try:
        asyncio.run(appmod.approve_workflow_run(r["id"], appmod.WorkflowApproveIn(plan_digest=r["plan_digest"])))
        check(False, "stale approval refused")
    except Exception as e:  # noqa: BLE001
        check(getattr(e, "status_code", 0) == 409, "an edit after the proposal makes the old approval a 409")
    check(appmod.delete_workflow(saved["id"]) == {"ok": True}, "DELETE /workflows")


def test_waves_record_agents_and_announce() -> None:
    """Independent steps run side by side, each agent step remembers the children it spawned, every write is
    announced through on_change, and GET /crew/{run} returns the tree the Spaces widget draws."""
    reset()
    DELAY["s"] = 0.15
    d = {"name": "two-up", "steps": [
        {"id": "a", "agent": {"role": "researcher", "task": "Look into A"}},
        {"id": "b", "agent": {"role": "researcher", "task": "Look into B"}},
        {"id": "join", "tool": "wf_write", "args": {"path": "/x", "text": "{{a.result}} {{b.result}}"}},
    ]}
    w = save(d)
    seen: list[dict[str, Any]] = []
    prev = store.on_change
    store.on_change = seen.append

    async def go() -> None:
        r = store.create_run(w, {})
        engine.approve(r["id"], r["plan_digest"])
        mid = await until(lambda: (g := store.get_run(r["id"])) and sum(1 for s in g["steps"] if s["status"] == "running") == 2 and g)
        check([s["step_id"] for s in mid["steps"] if s["status"] == "running"] == ["a", "b"], "both independent agent steps run in one wave")
        crew = appmod.crew_view(r["id"])
        check(crew["root"]["kind"] == "workflow_run" and crew["root"]["status"] == "running", "the crew route finds the run")
        check(crew["root"]["now"] == "a, b", "the root's now line names the live steps")
        live = [a for a in crew["agents"] if a["state"] == "running"]
        check(len(live) == 2 and all(a["parent_id"] is None and a["now"] == "thinking" for a in live), "two live children hang off the root, each saying what it does")
        done = await until(lambda: (g := store.get_run(r["id"]))["status"] == "done" and g)
        check(LIVE["peak"] == 2, "the two agents answered at the same time")
        by = {s["step_id"]: s for s in done["steps"]}
        check(len(by["a"]["agents"]) == 1 and len(by["b"]["agents"]) == 1 and by["join"]["agents"] is None, "agent steps record their child; a tool step records none")
        check(by["a"]["agents"][0] != by["b"]["agents"][0] and by["a"]["agents"][0].startswith("sa_"), "each step names its own subagent run")
        check(CALLS["wf_write"] == 1, "the join ran once, after both")
        crew = appmod.crew_view(r["id"])
        ids = {a["id"] for a in crew["agents"]}
        check(ids == {by["a"]["agents"][0], by["b"]["agents"][0]}, "the finished tree lists exactly the recorded children")
        check(all(a["status"] == "done" and a["task"].startswith("Look into") for a in crew["agents"]), "finished children come from their rows")
        check(seen and seen[-1]["id"] == r["id"] and seen[-1]["status"] == "done", "every write is announced; the last says done")
        check("result" not in seen[-1] and "result" not in seen[-1]["steps"][0], "the announcement carries no payloads")
        by_wf = appmod.crew_view(w["id"])
        check(by_wf["root"]["kind"] == "workflow_run" and by_wf["workflow"]["id"] == w["id"], "a saved workflow resolves to its latest run")

    try:
        run(go())
    finally:
        store.on_change = prev


def test_sibling_approvals_keep_the_run_waiting() -> None:
    """Two steps in one wave both park on a card: answering one leaves the run waiting for the other."""
    reset()
    d = {"name": "two-cards", "steps": [
        {"id": "w1", "tool": "wf_write", "approval": "required", "args": {"path": "/one"}},
        {"id": "w2", "tool": "wf_write", "approval": "required", "args": {"path": "/two"}},
    ]}
    w = save(d)

    async def go() -> None:
        r = store.create_run(w, {})
        engine.approve(r["id"], r["plan_digest"])
        await until(lambda: len(pending(r["id"])) == 2)
        cards = sorted(pending(r["id"]), key=lambda c: c["args"]["path"])
        runs.decide(cards[0]["call_id"], "allow")
        await until(lambda: CALLS.get("wf_write") == 1)
        await asyncio.sleep(0.1)
        cur = store.get_run(r["id"])
        check(cur["status"] == "waiting_approval", "the run still waits on the other card")
        runs.decide(cards[1]["call_id"], "allow")
        done = await until(lambda: (g := store.get_run(r["id"]))["status"] == "done" and g)
        check(CALLS["wf_write"] == 2 and all(s["status"] == "done" for s in done["steps"]), "both writes ran once each")

    run(go())


def test_reserved_names() -> None:
    from personal_os.mcp_servers import RESERVED_TOOL_NAMES
    for n in ("workflow_run", "workflow_resume", "workflow_list", "command_run", "command_list"):
        check(n in RESERVED_TOOL_NAMES and n in tb.specs, f"{n} is a registered, reserved tool name")
    for n in wf.TOOL_BLOCK:
        check(n not in {"workflow_run"} or n in tb.specs, "block list names real tools")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"FAIL {t.__name__}: {e!r}")
    print(f"\n{passed} assertions passed, {failed} test(s) failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
