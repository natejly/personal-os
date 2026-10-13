"""Tool arguments are fitted to their schema's scalar types before anything gates or runs on them (tools.coerce_args).

An open model's "false" is a truthy string: unsandboxed="false" would run outside the sandbox, replace_all="false"
would replace every match. Run: pytest backend/tests/test_tool_arg_coerce.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="argcoerce-"))

from personal_os.tools import Toolbox, coerce_args  # noqa: E402

tb = Toolbox(None, None, None, lambda: {})  # type: ignore[arg-type]


def test_string_booleans_and_numbers_become_their_types() -> None:
    args = {"command": "ls", "unsandboxed": "false"}
    assert tb.precheck("shell_run", args) is None and args["unsandboxed"] is False
    args = {"path": "a.txt", "old": "x", "new": "y", "replace_all": "FALSE"}
    assert coerce_args(tb.specs["fs_edit"], args) is None and args["replace_all"] is False
    args = {"query": "q", "limit": "5"}
    assert coerce_args(tb.specs["search_documents"], args) is None and args["limit"] == 5


def test_null_optional_is_dropped_and_a_misfit_is_an_error() -> None:
    args = {"query": None, "queries": ["a"]}
    assert coerce_args(tb.specs["search_documents"], args) is None and "query" not in args
    err = coerce_args(tb.specs["shell_run"], {"command": "ls", "unsandboxed": "maybe"})
    assert err and err["field"] == "unsandboxed" and err["expected"] == "boolean"
    assert tb.precheck("search_documents", {"query": "q", "limit": "lots"})["field"] == "limit"  # type: ignore[index]


def test_gate_sees_the_coerced_value() -> None:
    args = {"command": "ls", "unsandboxed": "false"}
    tb.gate("shell_run", "on", {}, args)
    assert args["unsandboxed"] is False
