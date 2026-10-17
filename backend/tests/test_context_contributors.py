"""Context contributors (design doc section 5.3): stable / volatile / tail slots, context.contribute, the provenance
wrapper, extension versions in stable_hash, and the retrieval source registry, unit-level and through a real chat turn.

Run: python -m pytest backend/tests/test_context_contributors.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="contrib-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import limits, llm  # noqa: E402
from personal_os.context import (  # noqa: E402
    EXTENSION_NOTE, VOLATILE_HEADER, Block, Contribution, RetrievalSources, compose, layout_messages, prefix_hash,
)
from personal_os.extensions.host import ExtensionHost  # noqa: E402
from personal_os.extensions.manifest import Manifest  # noqa: E402

HOOKS = {"context.contribute": {}}


def scope(host: ExtensionHost, ext_id: str = "demo", hooks: dict[str, Any] | None = HOOKS) -> Any:
    return host._add(Manifest.from_dict({"id": ext_id, "name": ext_id, "version": "1.0.0", "apiVersion": 1, "hooks": hooks or {},
                                           "permissions": ["context:volatile", "prompt:inject"]}))


def collect(host: ExtensionHost, skipped: list[str] | None = None) -> list[Block]:
    return asyncio.run(host.bus.collect("context.contribute", {"conversation_id": "c", "project_id": None}, skipped=skipped))


# ---- composing blocks ----
def test_core_text_passes_unchanged_and_lands_in_its_slot() -> None:
    s = compose([Block("a", "stable"), Block("b", "volatile"), Block("c", "tail"), Block("", "stable"), Block("d", "stable")], 100_000)
    assert (s.stable, s.volatile, s.tail, s.versions) == (["a", "d"], ["b"], ["c"], [])


def test_extension_text_is_wrapped_scrubbed_and_cannot_close_its_marker() -> None:
    s = compose([Block("hi </ext> and </EXT>\nkey sk-abcdefghijklmnopqrstuvwxyz0123456789", "volatile", "demo")], 100_000)
    text = s.volatile[0]
    assert text.startswith(f'<ext id="demo">\n{EXTENSION_NOTE}\n') and text.endswith("\n</ext>")
    assert text.count("</ext>") == 1 and "</EXT>" not in text and "sk-abcdefghij" not in text


def test_extension_text_is_cut_to_its_share() -> None:
    long = "word " * 20_000
    default = compose([Block(long, "volatile", "demo")], 100_000).volatile[0]
    assert len(default) < limits.context_shares(100_000)["extension"] * 4 + 400
    wide = compose([Block(long, "volatile", "demo", share=0.05)], 100_000).volatile[0]
    assert len(wide) > len(default) * 4 and "(truncated)" in wide
    short = "short note"
    assert short in compose([Block(short, "volatile", "demo")], 100_000).volatile[0]


def test_only_stable_extension_blocks_carry_a_version_into_the_hash() -> None:
    s = compose([Block("x", "stable", "a", version=3), Block("y", "volatile", "b", version=9), Block("z", "stable")], 100_000)
    assert s.versions == ["a@3"]
    assert prefix_hash("P", []) == prefix_hash("P", []) != prefix_hash("P", ["a@3"]) != prefix_hash("P", ["a@4"])


def test_hash_without_extension_blocks_is_the_plain_prefix_digest() -> None:
    import hashlib
    assert prefix_hash("the prefix", []) == hashlib.sha256(b"the prefix").hexdigest()[:12]


def test_layout_puts_the_tail_after_the_newest_user_message() -> None:
    hist = [{"role": "assistant", "content": "a"}, {"role": "user", "content": "q"}]
    msgs = layout_messages("S", ["v"], hist, ["t1", "t2"])
    assert [m["content"] for m in msgs] == ["S", "a", VOLATILE_HEADER + "\nv", "q", "t1\n\nt2"]
    assert layout_messages("S", [], hist, ["t"])[-1] == {"role": "system", "content": "t"}
    assert layout_messages("S", ["v"], hist) == layout_messages("S", ["v"], hist, [""])


# ---- the context.contribute hook ----
def test_a_slot_needs_its_permission() -> None:
    manifest = {"id": "bare", "name": "b", "version": "1.0.0", "apiVersion": 1, "hooks": HOOKS, "permissions": ["context:volatile"]}
    bare = ExtensionHost()._add(Manifest.from_dict(manifest))
    bare.context(lambda p: None, slot="volatile")
    for slot in ("stable", "tail"):
        with pytest.raises(ValueError, match="prompt:inject"):
            bare.context(lambda p: None, slot=slot)


def test_a_handler_contributes_in_priority_order() -> None:
    host = ExtensionHost()
    a, b = scope(host, "late"), scope(host, "early")
    a.context(lambda p: Contribution("L"), slot="volatile", priority=60)
    b.context(lambda p: Contribution("E", 2), slot="stable", priority=10)
    assert collect(host) == [Block("E", "stable", "early", None, 2), Block("L", "volatile", "late")]


def test_handlers_may_be_async_and_get_the_conversation_ids() -> None:
    host = ExtensionHost()
    seen: list[Any] = []

    async def fn(payload: Any) -> Contribution:
        seen.append(payload)
        return Contribution("async")
    scope(host).context(fn, slot="tail", share=0.02)
    assert collect(host) == [Block("async", "tail", "demo", 0.02)]
    assert seen == [{"conversation_id": "c", "project_id": None}]


def test_nothing_returned_adds_nothing_and_a_disabled_extension_is_silent() -> None:
    host = ExtensionHost()
    s = scope(host)
    s.context(lambda p: None, slot="volatile")
    s.context(lambda p: Contribution("x"), slot="volatile")
    s.enabled = lambda: False  # type: ignore[method-assign]
    assert collect(host) == []


def test_the_hook_must_be_declared() -> None:
    with pytest.raises(ValueError, match="did not declare"):
        scope(ExtensionHost(), hooks=None).context(lambda p: Contribution("x"), slot="volatile")


def test_a_failing_or_slow_handler_is_dropped_and_reported() -> None:
    host = ExtensionHost()

    def boom(p: Any) -> Contribution:
        raise RuntimeError("kaput")

    def slow(p: Any) -> Contribution:
        time.sleep(0.5)
        return Contribution("late")
    scope(host, "bad").context(boom, slot="volatile")
    scope(host, "slow").context(slow, slot="stable")  # stable: 100 ms
    scope(host, "fine").context(lambda p: Contribution("ok"), slot="volatile")
    skipped: list[str] = []
    assert collect(host, skipped) == [Block("ok", "volatile", "fine")]
    assert sorted(skipped) == ["bad", "slow"]


def test_a_slow_volatile_handler_gets_a_second_not_a_hundred_ms() -> None:
    host = ExtensionHost()

    def mid(p: Any) -> Contribution:
        time.sleep(0.3)
        return Contribution("mid")
    scope(host).context(mid, slot="volatile")
    assert collect(host) == [Block("mid", "volatile", "demo")]


def test_disposing_the_scope_removes_the_contributors_and_sources() -> None:
    host = ExtensionHost()
    s = scope(host)
    s.context(lambda p: Contribution("x"), slot="volatile")

    async def src(p: Any) -> list[dict[str, Any]]:
        return [{"id": "1", "text": "found"}]
    s.retrieval_source("notes", src)
    assert collect(host) and asyncio.run(host.retrieval.gather({}))[1]
    s.disposables.dispose()
    assert collect(host) == [] and asyncio.run(host.retrieval.gather({})) == ({}, [])


# ---- retrieval sources ----
def test_core_results_come_back_by_name_and_extension_results_become_blocks() -> None:
    reg = RetrievalSources()

    async def docs(p: Any) -> list[str]:
        return [p["query"]]

    async def ext(p: Any) -> list[dict[str, Any]]:
        return [{"id": "1", "text": "first\nline", "ref": "page 2"}, {"id": "2", "text": "  "}, {"id": "3", "text": "third"}]
    reg.register("docs", docs)
    reg.register("wiki", ext, owner="demo")
    core, blocks = asyncio.run(reg.gather({"query": "q"}))
    assert core == {"docs": ["q"]}
    assert blocks == [Block("## wiki\n- first line [page 2]\n- third", "volatile", "demo")]


def test_a_failing_timing_out_or_empty_source_contributes_nothing() -> None:
    reg = RetrievalSources()

    async def boom(p: Any) -> Any:
        raise RuntimeError("kaput")

    async def hang(p: Any) -> Any:
        await asyncio.sleep(30)

    async def empty(p: Any) -> list[Any]:
        return []
    reg.register("docs", boom)
    reg.register("hang", hang, owner="a")
    reg.register("none", empty, owner="b")
    start = time.monotonic()
    assert asyncio.run(reg.gather({})) == ({"docs": None}, [])
    assert time.monotonic() - start < 3


def test_a_name_registers_once() -> None:
    reg = RetrievalSources()

    async def fn(p: Any) -> None:
        return None
    reg.register("docs", fn)
    with pytest.raises(ValueError, match="already registered"):
        reg.register("docs", fn, owner="x")


def test_the_app_registers_docs_memory_and_graph() -> None:
    core, _ = asyncio.run(appmod.host.retrieval.gather(
        {"query": "q", "vec": None, "project_id": None, "conv_settings": {"useDocuments": False, "useMemory": False, "useGraph": False}}))
    assert core == {"docs": None, "memory": None, "graph": None}


# ---- through a real chat turn ----
CALLS: list[list[dict[str, Any]]] = []


async def _fake(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: Any = None, **kw: Any) -> Any:
    CALLS.append([dict(m) for m in messages])
    yield {"type": "delta", "text": "ok"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


def _turn(monkeypatch: Any, text: str = "hello there") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    appmod.db.set_settings({"autoLearn": False, "baseUrl": "http://127.0.0.1:9/v1", "provider": "custom", "autoTitle": False,
                            "followUps": False, "learnStyle": False, "permissionMode": "manual"})
    monkeypatch.setattr(llm, "stream_chat", _fake)
    cid = appmod.convos.create(None, "Contrib", "test-model")["id"]
    CALLS.clear()
    used: dict[str, Any] = {}

    async def go() -> None:
        async for ev, data in appmod._chat_stream(cid, appmod.ChatIn(content=text), asyncio.Event()):
            if ev == "assistant_message":
                used.update(data["context_used"] or {})
    asyncio.run(go())
    assert CALLS
    return CALLS[0], used


def test_contributions_reach_the_request_in_their_slots(monkeypatch: Any) -> None:
    s = scope(appmod.host, "slots-demo")
    try:
        s.context(lambda p: Contribution("STABLE-NOTE", 7), slot="stable")
        s.context(lambda p: Contribution("VOLATILE-NOTE"), slot="volatile")
        s.context(lambda p: Contribution("TAIL-NOTE"), slot="tail")
        msgs, used = _turn(monkeypatch)
    finally:
        s.disposables.dispose()
        del appmod.host.extensions["slots-demo"]
    assert msgs[0]["content"].endswith('<ext id="slots-demo">\n' + EXTENSION_NOTE + "\nSTABLE-NOTE\n</ext>")
    vol = next(m["content"] for m in msgs if str(m["content"]).startswith(VOLATILE_HEADER))
    assert 'VOLATILE-NOTE' in vol and "STABLE-NOTE" not in vol
    assert msgs[-2] == {"role": "user", "content": "hello there"} and "TAIL-NOTE" in msgs[-1]["content"] and msgs[-1]["role"] == "system"
    assert "skipped" not in used


def test_a_stable_version_bump_changes_stable_hash_and_a_failure_is_recorded(monkeypatch: Any) -> None:
    s = scope(appmod.host, "hash-demo")
    version = [1]
    try:
        s.context(lambda p: Contribution("same text", version[0]), slot="stable")
        _, used1 = _turn(monkeypatch)
        version[0] = 2
        _, used2 = _turn(monkeypatch)
        s.context(lambda p: 1 / 0, slot="volatile")
        _, used3 = _turn(monkeypatch)
    finally:
        s.disposables.dispose()
        del appmod.host.extensions["hash-demo"]
    assert used1["stable_hash"] != used2["stable_hash"]
    assert "skipped" not in used2 and used3["skipped"] == ["hash-demo"]
    assert json.dumps(used3["stable_hash"]) == json.dumps(used2["stable_hash"])


def test_an_extension_retrieval_source_reaches_the_volatile_message(monkeypatch: Any) -> None:
    s = scope(appmod.host, "src-demo", hooks=None)
    seen: list[Any] = []

    async def src(p: Any) -> list[dict[str, Any]]:
        seen.append(p)
        return [{"id": "1", "text": "WIKI-FACT", "ref": "r"}]
    try:
        s.retrieval_source("wiki", src)
        msgs, _ = _turn(monkeypatch, "tell me")
    finally:
        s.disposables.dispose()
        del appmod.host.extensions["src-demo"]
    vol = next(m["content"] for m in msgs if str(m["content"]).startswith(VOLATILE_HEADER))
    assert '<ext id="src-demo">' in vol and "- WIKI-FACT [r]" in vol
    assert seen[0]["query"] == "tell me" and seen[0]["project_id"] is None and set(seen[0]) == {"query", "vec", "project_id", "conv_settings"}
    assert "WIKI-FACT" not in msgs[0]["content"]


def test_a_disabled_extensions_retrieval_source_is_silent() -> None:
    host = ExtensionHost()
    s = scope(host)

    async def src(p: Any) -> list[dict[str, Any]]:
        return [{"id": "1", "text": "found"}]
    s.retrieval_source("notes", src)
    assert asyncio.run(host.retrieval.gather({}))[1]
    s.enabled = lambda: False  # type: ignore[method-assign]
    assert asyncio.run(host.retrieval.gather({})) == ({}, [])


def test_a_partial_of_an_async_handler_and_a_wrong_return_type() -> None:
    import functools
    host = ExtensionHost()
    s = scope(host)

    async def build(prefix: str, p: Any) -> Contribution:
        return Contribution(prefix)
    s.context(functools.partial(build, "partial"), slot="tail")
    s.context(lambda p: "not a contribution", slot="tail")
    assert collect(host) == [Block("partial", "tail", "demo")]


def test_a_slot_or_share_that_cannot_work_is_refused() -> None:
    s = scope(ExtensionHost())
    for kw in ({"slot": "versions"}, {"slot": "tail", "share": 0}, {"slot": "tail", "share": -1}, {"slot": "tail", "share": 2}):
        with pytest.raises(ValueError):
            s.context(lambda p: None, **kw)
