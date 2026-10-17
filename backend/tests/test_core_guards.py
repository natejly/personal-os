"""The core guards (guards.py): the stateless refusals run before any extension hook, an extension cannot reach, reorder or
replace them, and every loop refuses what they refuse.

Run: python -m pytest backend/tests/test_core_guards.py
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="guards-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import guards, llm, mac, toolbridge  # noqa: E402
from personal_os.dispatch import dispatch_tool  # noqa: E402
from personal_os.extensions.hooks import Ask, Deny, HookBus  # noqa: E402
from personal_os.runs import CoreRefused  # noqa: E402
from personal_os.tools import ToolSpec, _obj  # noqa: E402

RAN: list[str] = []


async def probe(ctx: dict[str, Any], tag: str = "", destroy: bool = False) -> Any:
    RAN.append(tag)
    return {"ok": True}


# A writes tool whose call is a destructive "files" call when `destroy` is set (Toolbox.floor_kind).
_probe = ToolSpec("guard_probe", "test", _obj({"tag": {"type": "string"}, "destroy": {"type": "boolean"}}, []), probe, "test", "writes")
_probe.floor = lambda args, ctx: "files" if args.get("destroy") else None
appmod.toolbox.specs["guard_probe"] = _probe
appmod.toolbox.specs["guard_outward"] = ToolSpec("guard_outward", "test", _obj({}, []), probe, "test", "external")

NO_RULES = {"allow": [], "ask": [], "deny": []}
DATA_FILE = str(mac._app_data_dir() / "guard-secret.txt")


@pytest.fixture(autouse=True)
def fresh() -> Any:
    RAN.clear()
    appmod.db.set_settings({"autoLearn": False, "baseUrl": "", "toolDeferAbove": 0, "permissionMode": "allow_all",
                            "permissionRules": NO_RULES, "destructiveDefaults": {"files": "block"}})
    yield
    appmod.db.set_settings({"permissionMode": "auto", "permissionRules": NO_RULES})


def ctx(**extra: Any) -> dict[str, Any]:
    cid = appmod.convos.create(None, "t", "m")["id"]
    mcp_modes, mcp_schemas = appmod._mcp_tooling(None, cid)
    return {"project_id": None, "conversation_id": cid, "message_id": None, "tainted": False, "taint_sources": [],
            "allowed_urls": set(), "settings": appmod.settings(), "modes": appmod.toolbox.effective({}, None, None),
            "mcp_modes": mcp_modes, "mcp_schemas": {s["function"]["name"]: s for s in mcp_schemas}, "depth": 0, "agent_run_id": "",
            "model": "m", "stop": asyncio.Event(), "meter": appmod.RunMeter(),
            "run": SimpleNamespace(live=True, run_id="run_fake", publish=lambda *a: None, set_status=lambda s: None), **extra}


def dispatch(name: str, args: dict[str, Any], **extra: Any) -> Any:
    return asyncio.run(dispatch_tool(appmod.toolbox, ctx(**extra), name, args, call_id="g1"))


# ---- the guards, one by one ----
def test_each_guard_refuses_what_it_names_and_a_clean_call_passes() -> None:
    assert guards.run_core_guards(appmod.toolbox, ctx(), "guard_probe", {}) is None
    assert guards.run_core_guards(appmod.toolbox, ctx(), "no_such_tool", {}).guard == "unknown_or_off"
    off = ctx()
    off["modes"]["guard_probe"] = "off"
    assert guards.run_core_guards(appmod.toolbox, off, "guard_probe", {}).guard == "unknown_or_off"
    assert guards.run_core_guards(appmod.toolbox, ctx(proposal_only=True), "guard_outward", {}).guard == "proposal_only"
    assert guards.run_core_guards(appmod.toolbox, ctx(planning=True), "guard_probe", {}).guard == "plan_block"
    assert guards.run_core_guards(appmod.toolbox, ctx(), "shell_run", {"command": "rm -rf /"}).guard == "hardline"
    appmod.db.set_settings({"permissionRules": {**NO_RULES, "deny": ["guard_probe"]}})
    assert guards.run_core_guards(appmod.toolbox, ctx(settings=appmod.settings()), "guard_probe", {}).guard == "deny_rules"
    assert guards.run_core_guards(appmod.toolbox, ctx(), "read_local_file", {"path": DATA_FILE}).guard == "protected_paths"
    appmod.db.set_settings({"permissionMode": "manual", "permissionRules": NO_RULES})
    assert guards.run_core_guards(appmod.toolbox, ctx(settings=appmod.settings()), "guard_probe", {"destroy": True}).guard == "floor_block"


def test_the_guards_run_in_a_fixed_order() -> None:
    """A call that several guards refuse is named by the first."""
    c = ctx(planning=True, proposal_only=True)
    assert guards.run_core_guards(appmod.toolbox, c, "guard_outward", {}).guard == "proposal_only"
    assert guards.run_core_guards(appmod.toolbox, c, "no_such_tool", {}).guard == "unknown_or_off"


def test_a_desk_workspace_is_not_protected_though_it_lives_in_the_data_folder() -> None:
    desk = mac._app_data_dir() / "desk-x"
    box = SimpleNamespace(workspace=SimpleNamespace(desk_root=lambda did: desk))
    assert guards.protected_reason(box, {"desk_id": "d"}, "fs_edit", {"path": str(desk / "a.txt")}) is None
    assert guards.protected_reason(box, {"desk_id": "d"}, "fs_edit", {"path": str(mac._app_data_dir() / "other.txt")})


# ---- the order against extensions ----
def hostile() -> tuple[list[str], list[Any]]:
    seen: list[str] = []
    bus = appmod.toolbox.bus

    def record(hook: str) -> Any:
        def fn(ev: dict[str, Any]) -> None:
            seen.append(f"{hook}:{ev['name']}")
        return fn

    return seen, [bus.subscribe(h, record(h), owner="hostile", order=0, priority=-1000) for h in ("tool.pre", "tool.permission")]


def run_chat(name: str, args: dict[str, Any], monkeypatch: pytest.MonkeyPatch, answer: str | None = None) -> list[dict[str, Any]]:
    """One chat turn calling `name`; `answer` is what the user says to a card if one opens."""
    cid = appmod.convos.create(None, "t", "m")["id"]
    rounds = [[{"id": "c0", "name": name, "arguments": json.dumps(args)}]]

    async def stream(settings, model, messages, tools=None, kind="chat", effort="default", tool_choice="auto", fast=False, cancel=None):
        calls = rounds.pop(0) if rounds else []
        yield {"type": "delta", "text": "ok" if not calls else ""}
        yield {"type": "end", "finish_reason": "stop", "tool_calls": calls, "usage": None}

    monkeypatch.setattr(llm, "stream_chat", stream)
    events: list[dict[str, Any]] = []

    async def reply() -> None:
        for _ in range(200):
            if pending := [k for k, f in appmod._approvals.items() if not f.done()]:
                appmod.run_store.decide(pending[0], answer or "deny")
                appmod._approvals[pending[0]].set_result(answer or "deny")
                return
            await asyncio.sleep(0.02)

    async def run() -> None:
        task = asyncio.create_task(reply()) if answer else None
        async for ev in appmod._chat_stream(cid, appmod.ChatIn(content="go"), asyncio.Event()):
            if ev[0] == "tool_result":
                events.append(ev[1])
        if task:
            await task

    asyncio.run(run())
    return events


def test_extension_handlers_never_see_a_call_the_core_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    seen, subs = hostile()
    try:
        appmod.db.set_settings({"permissionRules": {**NO_RULES, "deny": ["Read(/tmp/guarded/**)"]}})
        refused = [("shell_run", {"command": "rm -rf /"}), ("read_local_file", {"path": "/tmp/guarded/a.txt"}),
                   ("read_local_file", {"path": DATA_FILE})]
        for name, args in refused:
            assert "error" in dispatch(name, args)
            assert run_chat(name, args, monkeypatch)[0]["error"]
        assert seen == []
        appmod.db.set_settings({"permissionRules": NO_RULES})
        assert run_chat("guard_probe", {"tag": "x"}, monkeypatch)[0]["error"] is None
    finally:
        for s in subs:
            s.dispose()
    # extension handlers run strictly after the core chain, the permission one first
    assert seen == ["tool.permission:guard_probe", "tool.pre:guard_probe"] and RAN == ["x"]


def test_an_extension_permission_gate_can_only_narrow(monkeypatch: pytest.MonkeyPatch) -> None:
    verdict: list[Any] = [Deny("spend cap")]
    sub = appmod.toolbox.bus.subscribe("tool.permission", lambda ev: verdict[0], owner="x", order=0)
    try:
        out = run_chat("guard_probe", {"tag": "a"}, monkeypatch)
        assert "spend cap" in out[0]["error"] and RAN == []
        verdict[0] = Ask("check first")
        out = run_chat("guard_probe", {"tag": "b"}, monkeypatch, answer="deny")
        assert RAN == [] and out[0]["error"]  # allow-all would have run it: the extension turned it into a card
        out = run_chat("guard_probe", {"tag": "c"}, monkeypatch, answer="allow")
        assert RAN == ["c"] and out[0]["error"] is None
    finally:
        sub.dispose()


def test_the_permission_payload_carries_the_settled_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    got: list[dict[str, Any]] = []
    sub = appmod.toolbox.bus.subscribe("tool.permission", lambda ev: got.append(ev), owner="x", order=0)
    try:
        run_chat("guard_probe", {"tag": "p"}, monkeypatch)
    finally:
        sub.dispose()
    assert got and got[0]["name"] == "guard_probe" and got[0]["danger"] == "writes" and got[0]["mode"] in ("on", "ask")
    assert set(got[0]) == {"name", "args", "danger", "mode", "tainted"}


# ---- nothing an extension is handed reaches the guards ----
def _reaches(obj: Any, seen: set[int], depth: int) -> bool:
    if id(obj) in seen or depth > 4:
        return False
    seen.add(id(obj))
    if obj is guards or obj is guards.CORE_GUARDS or obj is guards.Guard or isinstance(obj, guards.Guard):
        return True
    if isinstance(obj, (str, bytes, int, float, bool, type(None))) or inspect.ismodule(obj) or inspect.isroutine(obj):
        return False
    if isinstance(obj, dict):
        return any(_reaches(v, seen, depth + 1) for v in list(obj.values()))
    if isinstance(obj, (list, tuple, set, frozenset)):
        return any(_reaches(v, seen, depth + 1) for v in list(obj))
    return any(_reaches(v, seen, depth + 1) for v in list(getattr(obj, "__dict__", {}).values()))


def test_no_public_surface_returns_accepts_or_exposes_the_guards() -> None:
    import grain_sdk
    from personal_os.extensions.host import ExtensionContext, ExtensionScope, Services

    scope = next(iter(appmod.host.extensions.values()))
    for obj in (scope, scope.app, scope.services, scope.bus, HookBus(), grain_sdk):
        assert not _reaches(obj, set(), 0), type(obj).__name__
    for cls in (ExtensionScope, ExtensionContext, Services, HookBus):
        for name, member in inspect.getmembers(cls):
            if name.startswith("_") or not callable(member):
                continue
            sig = str(inspect.signature(member))
            assert "guard" not in sig.lower() and "guard" not in name.lower(), f"{cls.__name__}.{name}"
    assert "CORE_GUARDS" not in dir(grain_sdk)


def test_no_hook_is_named_after_a_guard() -> None:
    from personal_os.extensions.hooks import HOOKS
    assert not {g.id for g in guards.CORE_GUARDS} & HOOKS
    with pytest.raises(ValueError):
        HookBus().subscribe("hardline", lambda ev: None, owner="x", order=0)


# ---- the pin ----
PIN = {
    "unknown_or_off": "c2189f52913888bc",
    "proposal_only": "46c1f42a0ded442c",
    "plan_block": "362dc4de4fab91a0",
    "hardline": "4fa41c559109e24d",
    "deny_rules": "f41ab94f50e09c47",
    "protected_paths": "9c45e0f8a550e978",
    "floor_block": "82ed412f8b4b40c8",
    "plan_refusal": "6c85d9ed539305f3",
    "rule_refusal": "0357461af618da1f",
    "protected_reason": "d3658be23894fff2",
    "floor_refusal": "e4f65ec03f3845a2",
}


def test_the_guard_ids_their_order_and_their_code_are_pinned() -> None:
    """Changing a guard, adding one or reordering them fails here until this table is edited in the same diff."""
    funcs = {g.id: g.fn for g in guards.CORE_GUARDS} | {f.__name__: f for f in (guards.plan_refusal, guards.rule_refusal,
                                                                           guards.protected_reason, guards.floor_refusal)}
    now = {name: hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()[:16] for name, fn in funcs.items()}
    assert [g.id for g in guards.CORE_GUARDS] == list(PIN)[:len(guards.CORE_GUARDS)]
    assert now == PIN


# ---- tamper ----
def test_a_replaced_guard_tuple_refuses_every_call() -> None:
    original = guards.CORE_GUARDS
    guards.CORE_GUARDS = (guards.Guard("hardline", lambda c: None), *original[1:])
    try:
        assert not guards.sealed()
        out = dispatch("guard_probe", {"tag": "t"})
        assert guards.TAMPERED in out["error"] and RAN == []
    finally:
        guards.CORE_GUARDS = original
    assert guards.sealed() and dispatch("guard_probe", {"tag": "t"}).get("ok") and RAN == ["t"]


def test_a_replaced_guard_function_refuses_every_call() -> None:
    original = guards._deny_rules
    guards._deny_rules = lambda c: None  # type: ignore[assignment]
    try:
        assert not guards.sealed()
        assert guards.TAMPERED in dispatch("guard_probe", {})["error"]
    finally:
        guards._deny_rules = original  # type: ignore[assignment]
    assert guards.sealed()


def test_a_guard_with_edited_code_is_detected() -> None:
    g = next(g for g in guards.CORE_GUARDS if g.id == "hardline")
    original = g.fn.__code__
    g.fn.__code__ = (lambda c: None).__code__
    try:
        assert not guards.sealed()
    finally:
        g.fn.__code__ = original
    assert guards.sealed()


# ---- no card for a refused call ----
def test_open_approval_refuses_a_call_the_core_refuses() -> None:
    run_id = "guards-run"
    appmod.run_store.create(run_id, None)
    with pytest.raises(CoreRefused):
        appmod.run_store.open_approval("g:1", run_id, "shell_run", {"command": "rm -rf /"})
    assert appmod.run_store.approval("g:1") is None
    appmod.run_store.open_approval("g:2", run_id, "guard_probe", {})
    assert appmod.run_store.approval("g:2")["status"] == "pending"


def test_open_approval_judges_a_call_from_the_desk_it_belongs_to(monkeypatch: pytest.MonkeyPatch) -> None:
    desk = mac._app_data_dir() / "desk-card"
    monkeypatch.setattr(appmod.toolbox, "workspace", SimpleNamespace(desk_root=lambda did: desk))
    appmod.run_store.create("guards-run3", None)
    args = {"path": str(desk / "a.txt")}
    with pytest.raises(CoreRefused):
        appmod.run_store.open_approval("g:4", "guards-run3", "read_local_file", args)
    appmod.run_store.open_approval("g:5", "guards-run3", "read_local_file", args, desk_id="d")
    assert appmod.run_store.approval("g:5")["status"] == "pending"


def test_approval_requested_does_not_fire_for_a_refused_call() -> None:
    fired: list[Any] = []
    sub = appmod.host.bus.subscribe("approval.requested", fired.append, owner="x", order=0)
    appmod.run_store.create("guards-run2", None)
    try:
        with pytest.raises(CoreRefused):
            appmod.run_store.open_approval("g:3", "guards-run2", "shell_run", {"command": "mkfs.ext4 /dev/sda"})
    finally:
        sub.dispose()
    assert fired == []


# ---- every loop refuses what the guards refuse ----
def worker(name: str, args: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    rounds = [[{"id": "c0", "name": name, "arguments": json.dumps(args)}]]

    async def stream(settings, model, messages, tools=None, kind="chat", effort="default", tool_choice="auto", fast=False, cancel=None):
        calls = rounds.pop(0) if rounds else []
        yield {"type": "delta", "text": "ok" if not calls else ""}
        yield {"type": "end", "finish_reason": "stop", "tool_calls": calls, "usage": None}

    monkeypatch.setattr(llm, "stream_chat", stream)
    asyncio.run(asyncio.wait_for(appmod.toolbox.call("agent_spawn", {"task": "t", "role": "general"}, ctx()), 10))


def workflow(label: str, name: str, args: dict[str, Any]) -> str:
    store, engine = appmod.workflow_store, appmod.workflow_engine
    w = store.save(json.dumps({"name": f"guards-{label}", "steps": [{"id": "one", "tool": name, "args": args}]}))
    out: dict[str, Any] = {}

    async def go() -> None:
        r = store.create_run(w, {})
        engine.approve(r["id"], r["plan_digest"])
        for _ in range(400):
            out.update(store.get_run(r["id"]))
            if out["status"] in ("done", "failed"):
                break
            await asyncio.sleep(0.02)

    asyncio.run(go())
    return json.dumps(out, default=str)


def bridge(name: str, args: dict[str, Any]) -> str:
    b = toolbridge.Bridge(appmod.toolbox, ctx(), [name], {name: "on"}, None)
    return json.dumps(asyncio.run(b.handle(name, args)), default=str)


CASES = [
    ("hardline", "shell_run", {"command": "rm -rf /"}, {}),
    ("deny_rules", "read_local_file", {"path": "/tmp/guarded/a.txt"}, {"permissionRules": {**NO_RULES, "deny": ["Read(/tmp/guarded/**)"]}}),
    ("protected_paths", "read_local_file", {"path": DATA_FILE}, {}),
    ("floor_block", "guard_probe", {"destroy": True}, {"permissionMode": "manual"}),
]


@pytest.mark.parametrize("guard,name,args,settings", CASES, ids=[c[0] for c in CASES])
def test_the_chat_loop_a_workflow_and_the_bridge_refuse_with_the_guards_reason(
        guard: str, name: str, args: dict[str, Any], settings: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    appmod.db.set_settings(settings)
    ref = guards.run_core_guards(appmod.toolbox, ctx(settings=appmod.settings()), name, args)
    assert ref is not None and ref.guard == guard
    assert ref.reason in run_chat(name, args, monkeypatch)[0]["error"]
    if name != "shell_run":  # the workflow engine and the bridge offer no shell
        assert ref.reason in workflow(guard, name, args)
    if name in toolbridge.ALLOWED:
        assert ref.reason in bridge(name, args)
    worker(name, args, monkeypatch)
    assert RAN == []
