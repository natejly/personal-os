"""Prefix stability: what must stay byte-identical between model calls so a provider's prefix cache can hit.

Chat: messages[0] (the stable system prompt) and json.dumps(tools) do not change between turns, while memory
recall, the page, and the date reach the request as a later system message. Session affinity follows the chat
(and, for workers, the kind and role). Workers of one role get the same prefix whatever their task says.

Run: backend/.venv/bin/python backend/tests/test_prefix_stability.py   (or pytest -s for the report)
"""
from __future__ import annotations

import asyncio
import contextvars
import datetime as _dt
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="prefix-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod  # noqa: E402
from personal_os import llm  # noqa: E402
from personal_os import subagents as sa  # noqa: E402
from personal_os import workers as W  # noqa: E402
from personal_os.context import VOLATILE_HEADER, estimate_tokens  # noqa: E402

REPORT: dict[str, Any] = {}
CALLS: list[dict[str, Any]] = []


async def _fake(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
                kind: str = "chat", effort: str = "default", tool_choice: str = "auto", fast: bool = False,
                cancel: asyncio.Event | None = None) -> Any:
    CALLS.append({"messages": [dict(m) for m in messages], "tools": json.dumps(tools, ensure_ascii=False) if tools else "",
                  "ntools": len(tools or []), "affinity": llm.session_affinity.get()})
    yield {"type": "delta", "text": "ok"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


def _setup() -> None:
    appmod.db.set_settings({"autoLearn": False, "baseUrl": "http://127.0.0.1:9/v1", "provider": "custom", "autoTitle": False,
                            "followUps": False, "learnStyle": False, "permissionMode": "manual", "workspaceRoots": []})


class _Clock(_dt.datetime):
    base = _dt.datetime(2031, 3, 4, 9, 0)

    @classmethod
    def now(cls, tz: Any = None) -> Any:  # type: ignore[override]
        return cls.base.astimezone() if tz is None else cls.base.replace(tzinfo=tz)


def _turn(cid: str, **kw: Any) -> dict[str, Any]:
    CALLS.clear()

    async def go() -> None:
        async for _ in appmod._chat_stream(cid, appmod.ChatIn(**kw), asyncio.Event()):
            pass
    asyncio.run(go())
    assert CALLS, "the stub was never called"
    return CALLS[0]


def _vol(c: dict[str, Any]) -> str:
    return "\n".join(str(m["content"]) for m in c["messages"][1:] if m["role"] == "system" and str(m["content"]).startswith(VOLATILE_HEADER))


def test_chat_prefix_stable_across_turns(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _setup()
    monkeypatch.setattr(llm, "stream_chat", _fake)
    cid = appmod.convos.create(None, "Prefix", "test-model")["id"]
    t1 = _turn(cid, content="hello there", page_context=appmod.PageContextIn(view="todos", label="Todos page ALPHA"))
    appmod.memories.create(None, "Zanzibar ferry leaves at dawn", pinned=False)
    monkeypatch.setattr(appmod, "datetime", _Clock)
    t2 = _turn(cid, content="when does the zanzibar ferry leave", page_context=appmod.PageContextIn(view="mail", label="Mail page BETA"))

    s1, s2 = t1["messages"][0], t2["messages"][0]
    assert s1["role"] == "system" and s1["content"].encode() == s2["content"].encode(), "system prefix changed between turns"
    assert t1["tools"] == t2["tools"] and t1["ntools"] > 0, "tool list changed between turns"
    assert "Zanzibar" not in s2["content"] and "2031" not in s2["content"] and "BETA" not in s2["content"]
    v1, v2 = _vol(t1), _vol(t2)
    assert "Zanzibar" in v2 and "Zanzibar" not in v1, "memory recall did not reach the volatile block"
    assert "2031-03-04" in v2 and "2031-03-04" not in v1, "the date did not reach the volatile block"
    assert "BETA" in v2 and "ALPHA" in v1, "the page did not reach the volatile block"
    # layout: [stable] + history + [volatile] + newest user; history replay is byte-identical
    assert t1["messages"][-1]["content"] == "hello there" and t2["messages"][-1]["role"] == "user"
    assert t2["messages"][-2]["content"].startswith(VOLATILE_HEADER)
    h1 = [m for m in t1["messages"] if not str(m["content"]).startswith(VOLATILE_HEADER)]
    h2 = [m for m in t2["messages"] if not str(m["content"]).startswith(VOLATILE_HEADER)]
    assert json.dumps(h2[:len(h1)]) == json.dumps(h1), "turn 2 does not replay turn 1's request as its prefix"
    assert len(h2) > len(h1), "turn 2 should add the assistant reply and the new user message"
    # the same conversation id reached the stub as the affinity key
    assert t1["affinity"] == cid and t2["affinity"] == cid
    stable = s1["content"] + t1["tools"]
    REPORT["chat"] = {"chars": len(stable), "tokens": estimate_tokens(stable), "system_chars": len(s1["content"]),
                      "tools": t1["ntools"], "identical": True}


def test_affinity_headers() -> None:
    fw = {"baseUrl": "https://api.fireworks.ai/inference/v1", "provider": "fireworks", "apiKey": "k"}
    other = [{"baseUrl": "https://api.openai.com/v1", "provider": "openai", "apiKey": "k"},
             {"baseUrl": "https://api.anthropic.com/v1", "provider": "anthropic", "apiKey": "k"}]

    def probe(settings: dict[str, Any], sid: str | None) -> tuple[dict[str, str], dict[str, Any]]:
        def run() -> tuple[dict[str, str], dict[str, Any]]:
            tok = llm.session_affinity.set(sid)
            try:
                return llm._headers(settings), llm._with_affinity(settings, {})
            finally:
                llm.session_affinity.reset(tok)
        return contextvars.copy_context().run(run)

    h, b = probe(fw, "conv1")
    assert h["x-session-affinity"] == "conv1" and b["user"] == "conv1"
    for s in other:
        h, b = probe(s, "conv1")
        assert "x-session-affinity" not in h and "user" not in b
    h, b = probe(fw, None)
    assert "x-session-affinity" not in h and "user" not in b


def _child(task: str, mem: Any, role: str = "researcher", conv: str = "convX") -> sa.Child:
    return sa.Child(id="c" + task[:3], parent_id="p", role=sa.BUILTIN_ROLES[role], task=task, model="m", depth=1,
                    conversation_id=conv, message_id=None, desk_id=None, ctx={"project_id": None, "settings": appmod.settings()},
                    modes=appmod.toolbox.effective({}, None, None), meter=sa.Meter(), kind="worker", detached=True, prompt=W.WORKER_PROMPT)


def test_worker_siblings_share_prefix(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _setup()
    mgr = appmod.subagent_mgr

    class Mem:  # one pinned note only matches the first task's words: it must not change the prefix
        def profile(self, *_a: Any, **_k: Any) -> list[dict[str, Any]]:
            return [{"pinned": 1, "content": "prefers tea"}, {"pinned": 1, "content": "owns a zanzibar ferry"}, {"pinned": 0, "content": "unpinned"}]

        def for_context(self, _p: Any, q: str = "", *_a: Any, **_k: Any) -> list[dict[str, Any]]:
            return [m for m in self.profile() if any(w in m["content"] for w in q.split())]

    monkeypatch.setattr(mgr, "memories", Mem())
    monkeypatch.setattr(mgr, "projects", None)
    monkeypatch.setattr(llm, "stream_chat", _fake)
    a, b = _child("check the zanzibar ferry timetable", None), _child("summarise unrelated quarterly report", None)
    ma, mb = mgr._seed(a, {}, None), mgr._seed(b, {}, None)
    assert ma[0]["content"].encode() == mb[0]["content"].encode() and "prefers tea" in ma[0]["content"]
    assert ma[1]["content"] != mb[1]["content"]
    ta, tb = (json.dumps(appmod.toolbox.schemas(c.modes), ensure_ascii=False) for c in (a, b))
    assert ta == tb and ta != "null"

    async def rounds() -> list[Any]:
        out = []
        for c in (a, b):
            c.messages = mgr._seed(c, {}, None)
            CALLS.clear()
            await asyncio.create_task(mgr._model_round(c, appmod.toolbox.schemas(c.modes)))  # a task: its own context copy, like a real child
            out.append(CALLS[0])
        return out
    ca, cb = asyncio.run(rounds())
    assert ca["affinity"] == cb["affinity"] == "convX:worker:researcher"
    assert ca["messages"][0] == cb["messages"][0] and ca["tools"] == cb["tools"]
    full = ma[0]["content"] + ta
    REPORT["worker"] = {"chars": len(full), "tokens": estimate_tokens(full), "tools": len(appmod.toolbox.schemas(a.modes)), "identical": True}


def test_report() -> None:
    for k in ("chat", "worker"):
        r = REPORT.get(k)
        if r:
            print(f"\n[{k}] prefix {r['chars']} chars (~{r['tokens']} tokens), tools {r['tools']}"
                  + (f", system {r['system_chars']} chars" if "system_chars" in r else "") + f", identical across calls: {'yes' if r['identical'] else 'no'}")


if __name__ == "__main__":
    import pytest
    os.environ["PERSONAL_OS_TEST_CHILD"] = "1"  # run in this process so the report prints
    sys.exit(pytest.main([__file__, "-q", "-s"]))
