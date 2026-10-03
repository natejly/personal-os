"""Inline <think> blocks become reasoning, never answer text; list-shaped content is joined.

Run: python backend/tests/test_think_split.py   (or pytest)
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
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="think-"))

import httpx  # noqa: E402

from personal_os import llm as app_llm  # noqa: E402

_spec = importlib.util.spec_from_file_location("personal_os._llm_think", app_llm.__file__)
llm = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(llm)  # type: ignore[union-attr]

SETTINGS: dict[str, Any] = {"baseUrl": "http://provider.test", "apiKey": "k", "llmRetries": 0}


def split(chunks: list[str]) -> tuple[str, str]:
    s = llm.ThinkSplitter()
    pieces: list[tuple[str, str]] = []
    for c in chunks:
        pieces += s.feed(c)
    pieces += s.flush()
    return "".join(p for k, p in pieces if k == "reasoning"), "".join(p for k, p in pieces if k == "delta")


def every_split(text: str) -> list[list[str]]:
    return [[text[:i], text[i:]] for i in range(len(text) + 1)] + [list(text)]


class Splitter(unittest.TestCase):
    def test_every_split_of_a_think_block(self) -> None:
        for text in ("<think>plan</think>\n\nHello", "<THINKING>plan</Thinking>\nHello", "  \n<think>\nplan</think>Hello"):
            for chunks in every_split(text):
                self.assertEqual(split(chunks), ("plan", "Hello"), chunks)

    def test_probe_holds_a_partial_open_tag(self) -> None:
        s = llm.ThinkSplitter()
        self.assertEqual(s.feed("<thi"), [])
        self.assertEqual(s.feed("nk>x</think>y"), [("reasoning", "x"), ("delta", "y")])

    def test_false_prefix_passes_through(self) -> None:
        for text in ("<table><tr>", "<th>cell", "Hello <think>x</think>"):
            for chunks in every_split(text):
                self.assertEqual(split(chunks), ("", text), chunks)

    def test_literal_tag_mid_answer_is_untouched(self) -> None:
        text = "Use the <think> tag like this: <think>x</think>"
        for chunks in every_split(text):
            self.assertEqual(split(chunks), ("", text))

    def test_unclosed_block_is_reasoning(self) -> None:
        for chunks in every_split("<think>never ends"):
            self.assertEqual(split(chunks), ("never ends", ""))
        self.assertEqual(split(["<think>half </thi"]), ("half </thi", ""))

    def test_empty_stream(self) -> None:
        self.assertEqual(split([]), ("", ""))
        self.assertEqual(split(["", ""]), ("", ""))


class Helpers(unittest.TestCase):
    def test_content_text(self) -> None:
        self.assertEqual(llm._content_text({"content": "a"}), "a")
        self.assertEqual(llm._content_text({"content": ["a", {"type": "text", "text": "b"}, {"type": "thinking"}, 3]}), "ab")
        self.assertEqual(llm._content_text({"content": None}), "")
        self.assertEqual(llm._content_text({"content": 5}), "")
        self.assertEqual(llm._content_text({}), "")

    def test_strip_think(self) -> None:
        self.assertEqual(llm.strip_think("<think>notes</think>\nSummary"), "Summary")
        self.assertEqual(llm.strip_think("  <Thinking>n</Thinking> Hi"), "Hi")
        self.assertEqual(llm.strip_think("<think>never closed"), "")
        self.assertEqual(llm.strip_think("plain <think>x</think>"), "plain <think>x</think>")


def sse(chunks: list[Any]) -> bytes:
    lines = [f'data: {json.dumps({"choices": [{"delta": {"content": c}}]})}\n\n' for c in chunks]
    return ("".join(lines) + 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n').encode()


class FakeProvider:
    def __init__(self, response: httpx.Response) -> None:
        transport = httpx.MockTransport(lambda req: response)
        real = httpx.AsyncClient

        class Client(real):  # type: ignore[valid-type, misc]
            def __init__(self, *a: Any, **k: Any) -> None:
                super().__init__(*a, transport=transport, **k)

        self.client = Client

    def __enter__(self) -> "FakeProvider":
        self.old = llm.httpx.AsyncClient
        llm.httpx.AsyncClient = self.client  # type: ignore[misc]
        return self

    def __exit__(self, *a: Any) -> None:
        llm.httpx.AsyncClient = self.old  # type: ignore[misc]


async def collect() -> list[dict[str, Any]]:
    return [e async for e in llm.stream_chat(SETTINGS, "m", [{"role": "user", "content": "hi"}])]


class Streaming(unittest.TestCase):
    def test_tag_split_across_chunks(self) -> None:
        with FakeProvider(httpx.Response(200, content=sse(["<thi", "nk>pla", "n</th", "ink>\n\nHel", "lo"]))):
            ev = asyncio.run(collect())
        self.assertEqual("".join(e["text"] for e in ev if e["type"] == "reasoning"), "plan")
        self.assertEqual("".join(e["text"] for e in ev if e["type"] == "delta"), "Hello")
        self.assertEqual(ev[-1]["type"], "end")

    def test_unclosed_block_counts_toward_usage_est(self) -> None:
        with FakeProvider(httpx.Response(200, content=sse(["<think>" + "x" * 40]))):
            ev = asyncio.run(collect())
        self.assertFalse([e for e in ev if e["type"] == "delta"])
        self.assertEqual("".join(e["text"] for e in ev if e["type"] == "reasoning"), "x" * 40)
        self.assertEqual(ev[-1]["usage_est"]["completion_tokens"], 10)

    def test_list_content_parts(self) -> None:
        with FakeProvider(httpx.Response(200, content=sse([[{"type": "text", "text": "Hi"}], 7, None]))):
            ev = asyncio.run(collect())
        self.assertEqual([e["text"] for e in ev if e["type"] == "delta"], ["Hi"])

    def test_complete_strips_and_joins(self) -> None:
        for content, want in (("<think>notes</think>\nSummary", "Summary"), ([{"type": "text", "text": "Hi"}], "Hi"), ("plain", "plain")):
            resp = httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
            with FakeProvider(resp):
                self.assertEqual(asyncio.run(llm.complete(SETTINGS, "m", [{"role": "user", "content": "x"}])), want)


if __name__ == "__main__":
    unittest.main()
