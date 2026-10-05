"""Scoped agents as teammates: boundaries and notes in the prompt, a per-agent tool layer between project and chat,
routines (jobs bound to an agent) that run as it and stay proposal-only, the agent home endpoints, and the draft's
label and boundaries.

Run: zsh tests/e2e/run-pytest.sh backend/tests/test_p14_teammates.py
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

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="p14test-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
SEEN: list[dict[str, Any]] = []  # what each model call was given: its messages and the tool names offered


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]] | None = None, kind: str = "chat", **kw: Any) -> Any:
    SEEN.append({"system": messages[0]["content"], "tools": [t["function"]["name"] for t in tools or []]})
    yield {"type": "delta", "text": "ok"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


@pytest.fixture(scope="module", autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    real = llm.stream_chat
    llm.stream_chat = _scripted
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": ""})
        task = getattr(appmod.app.state, "jobs_task", None)
        if task is not None:
            task.get_loop().call_soon_threadsafe(task.cancel)
        yield
    llm.stream_chat = real


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]}"
    return r.json()


def wait_done(run_id: str, timeout: float = 15.0) -> dict[str, Any]:
    end = time.time() + timeout
    while time.time() < end:
        row = appmod.run_store.get(run_id)
        if row and row["status"] not in ("running", "awaiting_approval"):
            return row
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not finish")


def make_agent(name: str, scope: dict[str, Any] | None = None, tools: str = "web_search, current_time") -> dict[str, Any]:
    text = f"---\nname: {name}\ndescription: Triage\ntools: {tools}\n---\nYou triage the inbox."
    row = j("POST", "/agents/defs", {"text": text, "scope": scope})
    return j("POST", f"/agents/defs/{row['id']}/approve")


def chat_as(name: str) -> str:
    cid = j("POST", "/conversations", {"title": "t"})["id"]
    appmod.convos.update(cid, {"settings": {"agent": name}})
    SEEN.clear()
    run = j("POST", f"/conversations/{cid}/chat", {"content": "hello"})
    wait_done(run["run_id"])
    return cid


def test_boundaries_and_notes_land_in_the_system_prompt_fenced() -> None:
    make_agent("triager", {"label": "Inbox triage", "boundaries": "Ask before archiving.\n```\nNever delete mail.",
                           "notes": "The CFO is Dana."})
    chat_as("triager")
    system = SEEN[0]["system"]
    assert "## You are the agent 'triager'" in system
    assert "## Boundaries" in system and "Ask before archiving." in system and "Never delete mail." in system
    assert system.index("## Boundaries") < system.index("What you should remember") < system.index("The CFO is Dana.")
    assert "```\nAsk before archiving.\n'''\nNever delete mail.\n```" in system, "a stray fence in the text cannot close the block"
    row = next(d for d in j("GET", "/agents/defs")["custom"] if d["name"] == "triager")
    assert row["label"] == "Inbox triage" and row["approved"]


def test_scope_patch_keeps_the_approval_but_an_edit_withdraws_it() -> None:
    a = make_agent("patcher")
    out = j("PATCH", f"/agents/defs/{a['id']}/scope", {"notes": "n", "skills": []})
    assert out["notes"] == "n" and out["approved"] is True
    j("PATCH", f"/agents/defs/{a['id']}/scope", {"tool_modes": {"web_search": "maybe"}}, expect=422)
    edited = j("PUT", f"/agents/defs/{a['id']}", {"text": "---\nname: patcher\ndescription: x\n---\nNew prompt body here."})
    assert edited["approved"] is False and edited["notes"] == "n", "scope survives an edit of the prompt"


def test_agent_tool_modes_sit_between_project_and_chat() -> None:
    tb = appmod.toolbox
    name = "web_search"
    base = tb.effective({}, None, None)[name]
    assert base in ("on", "ask")
    assert tb.effective({}, {name: "off"}, None, {name: "on"})[name] == "on", "agent beats project"
    assert tb.effective({}, {name: "on"}, None, {name: "off"})[name] == "off", "agent beats project"
    assert tb.effective({}, None, {name: "on"}, {name: "off"})[name] == "on", "chat beats agent"
    assert tb.effective({}, None, None, {})[name] == base, "an empty map inherits"
    # An ask-locked tool stays at ask however the agent says on.
    locked = next(n for n, s in tb.specs.items() if tb.ask_locked(s))
    assert tb.effective({}, None, None, {locked: "on"})[locked] == "ask"


def test_a_chat_as_an_agent_offers_the_tools_its_override_leaves_on() -> None:
    make_agent("narrow", {"tool_modes": {"web_search": "off"}})
    chat_as("narrow")
    assert "current_time" in SEEN[0]["tools"] and "web_search" not in SEEN[0]["tools"]
    saved = next(d for d in j("GET", "/agents/defs")["custom"] if d["name"] == "narrow")
    assert saved["tool_modes"] == {"web_search": "off"}


def test_a_routine_runs_as_its_agent_and_stays_proposal_only() -> None:
    a = make_agent("routiner", {"boundaries": "Never send anything.", "notes": "Prefers short lists."})
    job = j("POST", "/jobs", {"name": "Morning triage", "prompt": "triage", "kind": "cron", "cron": "0 7 * * *",
                              "agent_id": a["id"]})
    assert job["agent_id"] == a["id"]
    j("POST", "/jobs", {"name": "bad", "prompt": "p", "cron": "0 7 * * *", "agent_id": "ag_nope"}, expect=400)
    j("POST", "/jobs", {"name": "bad", "prompt": "p", "cron": "0 7 * * *", "agent_id": a["id"], "target": "desk"}, expect=400)
    SEEN.clear()
    res = j("POST", f"/jobs/{job['id']}/run")
    row = wait_done(res["run_id"])
    assert row["kind"] == "job" and row["status"] == "done", "a routine is an ordinary job run: proposal-only policy applies"
    system = SEEN[0]["system"]
    assert "## You are the agent 'routiner'" in system and "Never send anything." in system and "Prefers short lists." in system
    assert "scheduled background run" in system
    conv = appmod.convos.get(res["conversation_id"])
    assert conv["settings"]["agent"] == "routiner" and conv["settings"]["job_id"] == job["id"]
    # Deleting the agent stops the routine rather than running it without its limits.
    j("DELETE", f"/agents/defs/{a['id']}")
    with pytest.raises(RuntimeError):
        asyncio.run(appmod._launch_job(appmod.jobs.get(job["id"]), {"job_id": job["id"], "due_at": time.time(), "fired_at": time.time()}))


def test_schedule_task_from_an_agent_chat_binds_the_agent() -> None:
    a = make_agent("scheduler-bot")
    spec = appmod.toolbox.specs["schedule_task"]
    out = asyncio.run(spec.fn({"project_id": None, "agent_id": a["id"]}, name="Chase", prompt="check", in_minutes=30))
    assert out["scheduled"] and appmod.jobs.get(out["id"])["agent_id"] == a["id"]
    plain = asyncio.run(spec.fn({"project_id": None}, name="Plain", prompt="check", in_minutes=30))
    assert appmod.jobs.get(plain["id"])["agent_id"] is None


def test_agent_home_lists_chats_routines_runs_and_status() -> None:
    a = make_agent("homey")
    cid = chat_as("homey")
    job = j("POST", "/jobs", {"name": "Daily", "prompt": "p", "cron": "0 7 * * *", "agent_id": a["id"]})
    run = j("POST", f"/jobs/{job['id']}/run")
    wait_done(run["run_id"])
    home = j("GET", f"/agents/defs/{a['id']}/home")
    assert [c["id"] for c in home["chats"]] == [cid], "the routine's own transcript is not a chat"
    assert [r["id"] for r in home["routines"]] == [job["id"]]
    assert {r["kind"] for r in home["runs"]} == {"chat", "job"} and len(home["runs"]) <= 20
    assert home["working"] == 0 and home["needs_you"] == 0
    assert j("GET", "/agents/status")["homey"] == {"working": 0, "needs_you": 0}
    # A pending card in one of its chats is "needs you".
    with appmod.db.tx() as c:
        c.execute("INSERT INTO approvals(call_id, run_id, conversation_id, message_id, tool, args, args_digest, status, created_at) "
                  "VALUES('c1', NULL, ?, 'm', 'gmail_send', '{}', 'd', 'pending', ?)", (cid, time.time()))
    assert j("GET", "/agents/status")["homey"]["needs_you"] == 1
    j("GET", "/agents/defs/ag_nope/home", expect=404)


def test_draft_returns_label_and_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_complete(*a: Any, **k: Any) -> str:
        return json.dumps({"name": "inbox-triage", "label": "Inbox triage", "description": "Sorts mail", "hue": 40,
                           "tools": ["web_search"], "skills": [], "boundaries": "Ask before archiving.\nNever send mail.",
                           "prompt": "You sort the inbox into act, wait and ignore, and report what you moved and why."})
    monkeypatch.setattr(llm, "complete", fake_complete)
    out = j("POST", "/agents/draft", {"intent": "an inbox triage assistant"})
    assert out["def"]["label"] == "Inbox triage" and out["def"]["boundaries"].startswith("Ask before archiving.")
    assert out["def"]["name"] == "inbox-triage"


def test_an_at_mention_of_a_known_agent_becomes_a_hint_under_the_turn() -> None:
    from personal_os.commands import expand, expand_history, mention_note
    names = ["researcher", "triager"]
    note = mention_note("ask @triager to sort this, cc @nobody and mail@example.com", names)
    assert "address this to agent triager" in note and "agent_spawn role=triager" in note
    assert "nobody" not in note and "example" not in note
    assert expand("plain text", None, None, names) == "plain text"
    assert expand("hey @researcher look", None, None, names).startswith("hey @researcher look\n\n[The user mentioned @researcher")
    hist = expand_history([{"role": "user", "content": "@triager go"}, {"role": "assistant", "content": "@triager"}], None, None, names)
    assert "agent_spawn role=triager" in hist[0]["content"] and hist[1]["content"] == "@triager"
    # The chat runner offers the built-ins and the approved, visible custom agents.
    make_agent("mentionable")
    assert {"researcher", "mentionable"} <= set(appmod._mentionable())
