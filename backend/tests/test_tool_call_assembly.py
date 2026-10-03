"""Streamed tool-call fragments assemble by slot with unique ids; a stream that dies without a finish is incomplete.

Run: python backend/tests/test_tool_call_assembly.py   (or pytest)
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="toolasm-"))

import httpx  # noqa: E402

from personal_os import llm as app_llm  # noqa: E402

_spec = importlib.util.spec_from_file_location("personal_os._llm_asm", app_llm.__file__)
llm = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(llm)  # type: ignore[union-attr]

SETTINGS: dict[str, Any] = {"baseUrl": "http://provider.test", "apiKey": "k", "llmRetries": 0}


def tc(index: Any = "omit", id: str | None = None, name: str | None = None, args: str | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {}
    if index != "omit":
        d["index"] = index
    if id:
        d["id"] = id
    fn: dict[str, Any] = {}
    if name is not None:
        fn["name"] = name
    if args is not None:
        fn["arguments"] = args
    if fn:
        d["function"] = fn
    return d


def body(frags: list[dict[str, Any]], finish: str | None = "tool_calls", done: bool = True) -> bytes:
    out = "".join(f'data: {json.dumps({"choices": [{"delta": {"tool_calls": [f]}}]})}\n\n' for f in frags)
    if finish:
        out += f'data: {json.dumps({"choices": [{"delta": {}, "finish_reason": finish}]})}\n\n'
    if done:
        out += "data: [DONE]\n\n"
    return out.encode()


def end_of(content: bytes, tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=content))
    real = httpx.AsyncClient

    class Client(real):  # type: ignore[valid-type, misc]
        def __init__(self, *a: Any, **k: Any) -> None:
            super().__init__(*a, transport=transport, **k)

    async def go() -> dict[str, Any]:
        return [e async for e in llm.stream_chat(SETTINGS, "m", [{"role": "user", "content": "x"}], tools=tools)][-1]

    old = llm.httpx.AsyncClient
    llm.httpx.AsyncClient = Client  # type: ignore[misc]
    try:
        return asyncio.run(go())
    finally:
        llm.httpx.AsyncClient = old  # type: ignore[misc]


def shape(end: dict[str, Any]) -> list[tuple[str, str]]:
    return [(c["name"], c["arguments"]) for c in end["tool_calls"]]


def schema(*names: str) -> list[dict[str, Any]]:
    return [{"type": "function", "function": {"name": n, "parameters": {}}} for n in names]


class Assembly(unittest.TestCase):
    def test_indexed_parallel_calls(self) -> None:
        end = end_of(body([tc(0, "a", "todo_add", '{"t'), tc(1, "b", "todo_list", ""), tc(0, args='itle":1}'), tc(1, args="{}")]))
        self.assertEqual(shape(end), [("todo_add", '{"title":1}'), ("todo_list", "{}")])
        self.assertEqual([c["id"] for c in end["tool_calls"]], ["a", "b"])
        self.assertEqual(set(end["tool_calls"][0]), {"id", "name", "arguments"})

    def test_null_index_parallel_calls(self) -> None:
        end = end_of(body([tc(None, "a", "one", "{}"), tc(None, "b", "two", "{}")]))
        self.assertEqual(shape(end), [("one", "{}"), ("two", "{}")])

    def test_same_index_new_id_opens_a_new_call(self) -> None:
        end = end_of(body([tc(0, "a", "one", "{}"), tc(0, "b", "two", "{}")]))
        self.assertEqual([(c["id"], c["name"]) for c in end["tool_calls"]], [("a", "one"), ("b", "two")])

    def test_no_index_no_id_new_name_after_arguments(self) -> None:
        end = end_of(body([tc(name="one", args="{}"), tc(name="two", args="{}")]))
        self.assertEqual(shape(end), [("one", "{}"), ("two", "{}")])

    def test_repeated_full_name_is_not_doubled(self) -> None:
        end = end_of(body([tc(0, "a", "todo_add", '{"a":'), tc(0, name="todo_add", args="1}")]), schema("todo_add"))
        self.assertEqual(shape(end), [("todo_add", '{"a":1}')])

    def test_split_name_still_joins(self) -> None:
        end = end_of(body([tc(0, "a", "todo_", ""), tc(0, name="add", args="{}")]), schema("todo_add"))
        self.assertEqual(shape(end), [("todo_add", "{}")])

    def test_nameless_slot_is_dropped(self) -> None:
        end = end_of(body([tc(0, "a", "one", "{}"), tc(1, "b", None, '{"x":1}')]))
        self.assertEqual(shape(end), [("one", "{}")])

    def test_id_fallback_is_unique_across_streams(self) -> None:
        ids = [end_of(body([tc(0, None, "one", "{}")]))["tool_calls"][0]["id"] for _ in range(2)]
        self.assertNotEqual(ids[0], ids[1])
        for i in ids:
            self.assertRegex(i, r"^call_[0-9a-f]{12}$")

    def test_provider_id_arriving_late_is_kept(self) -> None:
        end = end_of(body([tc(0, None, "one", "{"), tc(0, "real", None, "}")]))
        self.assertEqual([(c["id"], c["arguments"]) for c in end["tool_calls"]], [("real", "{}")])

    def test_duplicate_provider_ids_are_made_unique(self) -> None:
        end = end_of(body([tc(0, "a", "one", "{}"), tc(1, "a", "two", "{}")]))
        self.assertEqual(len({c["id"] for c in end["tool_calls"]}), 2)


class Incomplete(unittest.TestCase):
    def test_dropped_stream_clears_calls(self) -> None:
        end = end_of(body([tc(0, "a", "one", '{"x')], finish=None, done=False))
        self.assertTrue(end["incomplete"])
        self.assertEqual(end["tool_calls"], [])

    def test_finish_or_done_is_complete(self) -> None:
        self.assertFalse(end_of(body([tc(0, "a", "one", "{}")], finish="tool_calls", done=False))["incomplete"])
        end = end_of(body([tc(0, "a", "one", "{}")], finish=None, done=True))
        self.assertFalse(end["incomplete"])
        self.assertEqual(len(end["tool_calls"]), 1)


if __name__ == "__main__":
    unittest.main()
