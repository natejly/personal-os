"""Tool arguments are checked against the tool's JSON schema before a call runs (tools.validate_args), the schemas and
signatures of every registered tool agree, and the deferred-tool index names each tool's parameters.
Run: pytest backend/tests/test_tool_validation.py
"""
from __future__ import annotations

import asyncio
import inspect
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="toolvalid-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod  # noqa: E402
from personal_os import mcp_search, tools  # noqa: E402

tb = appmod.toolbox
EMPTY = inspect.Parameter.empty
# Old names a function still takes beside the schema's: the model may have them in an earlier transcript.
ALIASES = {"skill_view": {"id"}, "skill_revise": {"id"}, "agent_stop": {"id"}, "agent_wait": {"ids"}}
# Tools that take **kwargs: not callable by a model at all.
KWARGS_OK = {"claude_code_permission"}


def test_missing_required_names_the_field_and_lists_parameters() -> None:
    err = tb.precheck("message_worker", {"worker_id": "sa_1"})
    assert err and err["field"] == "text" and "text is required" in err["error"]
    assert "worker_id* (string)" in err["expected"] and "text* (string)" in err["expected"]
    assert tb.precheck("message_worker", {"worker_id": "sa_1", "text": "hi"}) is None


def test_unknown_key_is_rejected_with_a_suggestion() -> None:
    err = tb.precheck("agent_create", {"name": "a", "brief": "b", "tools": ["fs_read"]})
    assert err and err["field"] == "tools" and "unknown parameter" in err["error"]
    err = tb.precheck("read_chat", {"chat_id": "c", "lst": 5})
    assert err and "did you mean 'last'" in err["error"]
    assert tb.precheck("agent_spawn", {"task": "t", "colour": "red"})["field"] == "colour"  # type: ignore[index]
    assert tb.precheck("coding_recipe_save", {"name": "n", "bogus": 1, "prompt": "p", "repo_path": "/r", "verify": {"command": "x"}})["field"] == "bogus"  # type: ignore[index]


def test_wrong_enum_and_type_are_rejected_and_a_cased_enum_is_fixed() -> None:
    err = tb.precheck("permissions_update", {"kind": "nonsense", "value": "on"})
    assert err and err["field"] == "kind" and "one of" in err["error"]
    args = {"kind": "MODE", "value": "auto"}
    assert tb.precheck("permissions_update", args) is None and args["kind"] == "mode"
    err = tb.precheck("agent_spawn", {"task": "t", "tools": {"a": 1}})
    assert err and err["field"] == "tools" and "list" in err["error"]
    err = tb.precheck("agent_spawn", {"task": ["t"]})
    assert err and err["field"] == "task"
    args = {"chat_id": 12, "last": 5.0}
    assert tb.precheck("read_chat", args) is None and args["last"] == 5 and isinstance(args["last"], int)


def test_a_documented_alias_still_passes() -> None:
    assert tb.precheck("skill_view", {"id": "sk_1"}) is None
    assert tb.precheck("agent_stop", {"id": "sa_1"}) is None
    assert tb.precheck("agent_wait", {"ids": ["sa_1"]}) is None
    assert tb.precheck("agent_stop", {})["field"] == "agent_id"  # type: ignore[index]


def test_stop_and_wait_take_the_name_spawn_returns() -> None:
    class Sub:
        async def stop_tool(self, ctx: dict[str, Any], i: str) -> Any:
            return {"stopped": i}

        async def wait_tool(self, ctx: dict[str, Any], ids: Any, t: float) -> Any:
            return {"waited": ids}

    old = getattr(tb, "subagents", None)
    tb.subagents = Sub()
    try:
        run = lambda n, a: asyncio.run(tb.specs[n].fn({}, **a))  # noqa: E731
        assert run("agent_stop", {"agent_id": "sa_1"}) == run("agent_stop", {"id": "sa_1"}) == {"stopped": "sa_1"}
        assert run("agent_wait", {"agent_ids": ["a"]}) == run("agent_wait", {"ids": ["a"]}) == {"waited": ["a"]}
    finally:
        tb.subagents = old


def test_coding_session_examples_fit_their_tools() -> None:
    for n in [n for n in tb.specs if n.startswith("coding_session_") and tb.specs[n].examples]:
        for ex in tb.specs[n].examples:
            assert tb.precheck(n, dict(ex)) is None, (n, ex)
    assert "message" in tb.specs["coding_session_send"].examples[0]


def test_tool_index_names_parameters_required_first() -> None:
    line = mcp_search.tool_index([("g", "agent_stop", "Cancel a subagent. More.", {"properties": {"x": {}, "agent_id": {}}, "required": ["agent_id"]})])
    assert "agent_stop(agent_id*, x) — Cancel a subagent" in line
    many = {"properties": {f"p{i}": {} for i in range(9)}, "required": ["p8"]}
    assert mcp_search.signature("t", many) == "t(p8*, p0, p1, p2, p3, p4, …)"
    assert mcp_search.signature("t", {}) == "t"


def test_only_argument_errors_and_crashes_count_toward_the_block() -> None:
    assert tools.counts_toward_block(tb.precheck("message_worker", {"worker_id": "x"}))
    assert tools.counts_toward_block({"error": "gmail_read: ValueError: boom"})
    assert tools.counts_toward_block({"error": "gmail_read: HTTP 500 from example.com"})
    assert not tools.counts_toward_block(tools.tool_error("No chat 'c9' to read.", field="chat_id", alternative="list_chats"))
    assert not tools.counts_toward_block({"error": "not an approved procedure", "procedures": []})
    assert not tools.counts_toward_block({"ok": True})


def test_every_schema_agrees_with_its_function() -> None:
    problems: list[str] = []
    for name, spec in sorted(tb.specs.items()):
        if name in KWARGS_OK:
            continue
        params = {k: p for k, p in list(inspect.signature(spec.fn).parameters.items())[1:]}
        if any(p.kind is p.VAR_KEYWORD for p in params.values()) and name not in KWARGS_OK:
            problems.append(f"{name} takes **kwargs")
        props = spec.parameters.get("properties") or {}
        required = set(spec.parameters.get("required") or [])
        for k, sp in props.items():
            p = params.get(k)
            if p is None:
                problems.append(f"{name}.{k} is in the schema but not a parameter")
                continue
            if k in required and p.default is not EMPTY and not ALIASES.get(name):
                problems.append(f"{name}.{k} is required but defaults to {p.default!r}")
            if "default" in sp and p.default is not EMPTY and p.default != sp["default"]:
                problems.append(f"{name}.{k} schema default {sp['default']!r} != {p.default!r}")
        for k, p in params.items():
            if p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL) or k in ALIASES.get(name, ()):
                continue
            if k not in props:
                problems.append(f"{name}.{k} is a parameter but not in the schema")
            elif p.default is EMPTY and k not in required:
                problems.append(f"{name}.{k} has no default but is optional in the schema")
    assert not problems, "\n".join(problems)
