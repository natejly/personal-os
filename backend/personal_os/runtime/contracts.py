"""The contract suite each slot defines (design doc section 10.7.6): what any provider must do, run by the Test button in
Settings > Runtime, by `grain_sdk.testing.slot_contract` and by the core's own tests against the defaults. Each check is
async and raises AssertionError with the reason."""
from __future__ import annotations

import asyncio
import inspect
import os
import re
import subprocess
import sys
import tempfile
import time
from typing import Any

from ..osplat.base import CONFINED_MODES, SandboxRequest, shortfalls
from . import checked
from .protocols import ModelReply, ModelRequest

ROUTER_BUDGET_S = 0.05
_TOOL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}")
_ROUTE_KEYS = {"model": str, "effort": str, "reason": str, "no_think": bool}
PROBE_TIMEOUT_S = 20


def route_problem(choice: Any) -> str | None:
    """Why a router's answer is not a RouteChoice, or None."""
    if not isinstance(choice, dict) or set(choice) != set(_ROUTE_KEYS):
        return f"a route is {{{', '.join(_ROUTE_KEYS)}}}"
    bad = next((k for k, t in _ROUTE_KEYS.items() if not isinstance(choice[k], t)), None)
    if bad:
        return f"route.{bad} must be a {_ROUTE_KEYS[bad].__name__}"
    return "route.model is empty" if not choice["model"] else None


async def model_client(impl: Any) -> None:
    assert impl.locality in ("local", "remote"), "locality must be 'local' or 'remote'"
    assert impl.kinds, "a client serves at least one call kind"
    req = ModelRequest(model="contract-check", messages=[{"role": "user", "content": "Reply with the word ok."}])
    if "chat" in impl.kinds:
        events = [ev async for ev in impl.stream(req)]
        assert not (found := checked.problems(events)), "; ".join(found)
    if "complete" in impl.kinds:
        reply = await impl.complete(req)
        assert isinstance(reply, ModelReply) and isinstance(reply.text, str), "complete() must return a ModelReply"


async def model_router(impl: Any) -> None:
    turn = {"text": "hello", "attachments": False, "prior_tools": False, "effort": "default", "plan_mode": False,
            "fast_model": "", "default_model": "contract-check", "conversation_id": None, "project_id": None}
    t0 = time.perf_counter()
    choice = impl.route(turn)
    elapsed = time.perf_counter() - t0
    assert elapsed <= ROUTER_BUDGET_S, f"route() took {elapsed * 1000:.0f} ms, the budget is {ROUTER_BUDGET_S * 1000:.0f} ms"
    assert (why := route_problem(choice)) is None, why


async def tool_source(impl: Any) -> None:
    tools = impl.list()
    assert isinstance(tools, list), "list() must return a list"
    for t in tools:
        assert isinstance(t, dict) and isinstance(t.get("name"), str) and _TOOL_NAME.fullmatch(t["name"]), f"bad tool name in {t!r}"
        assert isinstance(t.get("description", ""), str) and isinstance(t.get("schema"), dict), f"{t['name']}: description is text, schema an object"
        assert t.get("danger", "external") == "external", f"{t['name']}: a source's tools are external, it cannot declare {t['danger']!r}"
    assert len({t["name"] for t in tools}) == len(tools), "tool names must be unique"


# A write outside the allowed roots, then one inside: the first must fail and the second succeed under a confining backend.
_PROBE = ("import sys\n"
          "outside, inside = sys.argv[1:3]\n"
          "def tried(path):\n"
          "    try:\n"
          "        open(path, 'w').write('x'); return True\n"
          "    except OSError:\n"
          "        return False\n"
          "print(int(tried(outside)), int(tried(inside)))\n")


def _probe(backend: Any, req: SandboxRequest, outside: str, inside: str) -> str:
    argv = backend.wrap(req, [sys.executable, "-c", _PROBE, outside, inside])
    done = subprocess.run(argv, capture_output=True, text=True, timeout=PROBE_TIMEOUT_S, cwd=req.cwd)
    return done.stdout.strip()


def _sandbox_checks(impl: Any) -> None:
    support = impl.support()
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.realpath(tmp)
        inside_dir, outside_dir = os.path.join(root, "inside"), os.path.join(root, "outside")
        os.mkdir(inside_dir)
        os.mkdir(outside_dir)
        for mode in CONFINED_MODES:
            req = SandboxRequest(mode, inside_dir, (inside_dir,) if mode == "workspace-write" else (), "none", None, (), (), "shell")
            if not shortfalls(support, mode, "none"):
                outside, inside = os.path.join(outside_dir, mode), os.path.join(inside_dir, mode)
                got = _probe(impl, req, outside, inside)
                assert got.startswith("0"), f"{mode}: the backend lists it as full but a write outside the allowed roots succeeded"
                if mode == "workspace-write":
                    assert got == "0 1", f"{mode}: a write inside the allowed roots was blocked ({got!r})"
            elif mode not in support:
                try:
                    impl.wrap(req, ["true"])
                except Exception:  # noqa: BLE001 - any refusal is the right answer for a mode it does not list
                    continue
                raise AssertionError(f"{mode} is not listed, so wrap() must raise for it")


async def sandbox_backend(impl: Any) -> None:
    await asyncio.to_thread(_sandbox_checks, impl)


async def agent_loop(impl: Any) -> None:
    assert isinstance(impl.id, str) and impl.id, "a loop has an id"
    assert impl.bounds and all(isinstance(n, int) and n > 0 for n in impl.bounds.values()), "bounds name the rounds the loop may count, each a positive number"
    assert inspect.isasyncgenfunction(impl.run), "run(turn, harness) must be an async generator of events"


async def memory_system(impl: Any) -> None:
    from .memory_api import MemoryScope
    scope = MemoryScope(None, None, {"useMemory": True, "useGraph": True})
    assert isinstance(impl.id, str) and impl.id, "a memory system has an id"
    for name in ("learn", "recall", "remember", "forget", "search"):
        assert inspect.iscoroutinefunction(getattr(impl, name)), f"{name}() must be async"
    assert not inspect.iscoroutinefunction(impl.profile), "profile() is sync: it is read while the cached prefix is built"
    blocks = await impl.recall("contract check", scope, 8000)
    assert isinstance(blocks, list) and all(b.slot == "volatile" and isinstance(b.text, str) for b in blocks), "recall() returns volatile Blocks"
    block = impl.profile(scope, 8000)
    assert block is None or (block.slot == "stable" and isinstance(block.text, str)), "profile() returns a stable Block or None"
    assert isinstance(await impl.search("contract check", scope, 5), list), "search() returns a list of rows"
    assert isinstance(impl.status(scope), dict), "status() returns a dict"
