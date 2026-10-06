"""deep_research: plan -> parallel read-only researchers -> one cited answer, and the /research command.

Offline: llm.complete (plan, gaps, leader) and llm.stream_chat (the parent and each researcher) are scripted.

Run: zsh tests/e2e/run-pytest.sh backend/tests/test_research.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="researchtest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod, commands, llm  # noqa: E402

CLAIMS: dict[str, list[dict[str, str]]] = {}   # sub-question -> what its researcher reports
COMPLETE: list[tuple[str, str]] = []           # (kind, user text) of every llm.complete call
PLAN: dict[str, list[str]] = {"plan": [], "gaps": []}
PARENT_SEEN: list[list[dict[str, Any]]] = []


async def fake_complete(settings: Any, model: str, messages: list[dict[str, Any]], kind: str = "other", **kw: Any) -> str:
    system, user = messages[0]["content"], messages[-1]["content"]
    if system.startswith("Split the research question"):
        COMPLETE.append(("plan", user))
        return json.dumps({"questions": PLAN["plan"]})
    if "follow-up sub-questions" in user:
        COMPLETE.append(("gaps", user))
        return json.dumps({"questions": PLAN["gaps"]})
    COMPLETE.append(("lead", user))
    nums = re.findall(r"^\[(\d+)\]", user, re.M)
    return "Answer: " + " ".join(f"claim [{n}]." for n in nums)


async def fake_stream(settings: Any, model: str, messages: list[dict[str, Any]], tools: Any = None, **kw: Any) -> Any:
    if messages[0]["role"] == "system" and "You are a subagent" in messages[0]["content"]:
        task = [m for m in messages if m["role"] == "user"][-1]["content"]
        sub = re.search(r"Your sub-question: (.*)", task).group(1)  # type: ignore[union-attr]
        yield {"type": "delta", "text": json.dumps({"claims": CLAIMS.get(sub, []), "gaps": ""})}
        yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}
        return
    PARENT_SEEN.append([dict(m) for m in messages])
    called = any(m["role"] == "tool" for m in messages)
    calls = [] if called else [{"id": "dr1", "name": "deep_research", "arguments": json.dumps({"question": "what changed?", "depth": "deep"})}]
    yield {"type": "delta", "text": "Here is the answer." if called else ""}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": calls, "usage": None}


@pytest.fixture(autouse=True)
def stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "complete", fake_complete)
    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    appmod.db.set_settings({"autoLearn": False, "baseUrl": "", "toolDeferAbove": 0, "subagentMaxConcurrent": 6,
                            "permissionMode": "manual"})  # the reviewer is not under test here
    COMPLETE.clear()
    PARENT_SEEN.clear()
    CLAIMS.clear()
    PLAN.update(plan=[], gaps=[])


def _ctx() -> dict[str, Any]:
    conv = appmod.convos.create(None, "Test chat", "test-model")["id"]
    cfg = appmod.settings()
    return {"project_id": None, "conversation_id": conv, "message_id": None, "tainted": False, "taint_sources": [],
            "allowed_urls": set(), "settings": cfg, "modes": appmod.toolbox.effective({}, None, None), "depth": 0,
            "agent_run_id": "", "model": "test-model", "stop": asyncio.Event(), "meter": appmod.RunMeter(), "run": None,
            "citations": []}


def _claim(text: str, url: str = "", title: str = "") -> dict[str, str]:
    return {"claim": text, "url": url, "title": title}


def test_plan_fan_out_and_leader_drop_unsourced_claims() -> None:
    PLAN["plan"] = ["a", "b", "c", "d"]  # a normal run keeps only three
    CLAIMS.update({"a": [_claim("A1", "https://x.test/1", "One"), _claim("A2 no source")],
                   "b": [_claim("B1", "https://x.test/2", "Two"), _claim("B2", "https://x.test/1", "One")],
                   "c": [_claim("C1 bad url", "not a url")]})
    ctx = _ctx()
    out = asyncio.run(appmod.toolbox.call("deep_research", {"question": "what changed?"}, ctx))
    trail = out["research"]
    assert trail["plan"] == ["a", "b", "c"]
    assert [s["status"] for s in trail["steps"]] == ["done", "done", "failed"]
    assert [s["claims"] for s in trail["steps"]] == [1, 2, 0]
    assert trail["dropped"] == 2  # A2 and C1 named no real page
    assert [s["url"] for s in trail["sources_considered"]] == ["https://x.test/1", "https://x.test/2"]
    # one leader call, and it saw only the sourced claims, numbered through the shared citation registry
    lead = [u for k, u in COMPLETE if k == "lead"]
    assert len(lead) == 1 and "A1" in lead[0] and "B2" in lead[0] and "A2" not in lead[0] and "C1" not in lead[0]
    assert [c["url"] for c in ctx["citations"]] == ["https://x.test/1", "https://x.test/2"]
    assert out["citations"] == [1, 2] and "claim [1]" in out["answer"]
    assert not any(k == "gaps" for k, _ in COMPLETE)  # normal depth is one pass


def test_deep_runs_a_second_pass_for_gaps() -> None:
    PLAN["plan"] = ["a", "b"]
    PLAN["gaps"] = ["g1"]
    CLAIMS.update({"a": [_claim("A1", "https://x.test/1")], "b": [_claim("B1", "https://x.test/2")], "g1": [_claim("G1", "https://x.test/3")]})
    out = asyncio.run(appmod.toolbox.call("deep_research", {"question": "q", "depth": "deep"}, _ctx()))
    assert [s["q"] for s in out["research"]["steps"]] == ["a", "b", "g1"]
    assert out["research"]["plan"] == ["a", "b", "g1"]
    assert [k for k, _ in COMPLETE] == ["plan", "gaps", "lead"]
    assert len(out["research"]["sources_considered"]) == 3


def test_no_sourced_claims_is_an_error_not_an_answer() -> None:
    PLAN["plan"] = ["a"]
    CLAIMS["a"] = [_claim("nothing to cite")]
    out = asyncio.run(appmod.toolbox.call("deep_research", {"question": "q"}, _ctx()))
    assert "no sourced claims" in out["error"]
    assert not any(k == "lead" for k, _ in COMPLETE)


def test_a_researcher_cannot_start_another_run() -> None:
    from personal_os import subagents
    assert "deep_research" in subagents.CHILD_BLOCK
    spec = appmod.toolbox.specs["deep_research"]
    assert spec.group == "research" and spec.danger == "network" and spec.taints


def test_the_trail_reaches_the_ui_but_never_the_model() -> None:
    PLAN["plan"] = ["a"]
    CLAIMS["a"] = [_claim("A1", "https://x.test/1", "One")]
    conv = appmod.convos.create(None, "Test chat", "test-model")["id"]

    async def go() -> list[tuple[str, Any]]:
        return [ev async for ev in appmod._chat_stream(conv, appmod.ChatIn(content="/research what changed?"), asyncio.Event())]

    events = asyncio.run(go())
    result = next(d for e, d in events if e == "tool_result" and d["name"] == "deep_research")
    assert result["research"]["steps"][0]["status"] == "done"
    assert result["tainted"]
    tool_msg = next(m for m in PARENT_SEEN[-1] if m["role"] == "tool")
    assert '"steps"' not in tool_msg["content"] and "sources_considered" not in tool_msg["content"]
    assert "/research" in PARENT_SEEN[0][-1]["content"] and "deep_research" in PARENT_SEEN[0][-1]["content"]


def test_slash_research_expands_to_a_deep_research_instruction() -> None:
    out = commands.expand("/research what changed in Python 3.14 packaging", None)
    assert out.startswith("/research what changed in Python 3.14 packaging")
    assert "deep_research" in out
