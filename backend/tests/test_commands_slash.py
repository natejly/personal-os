"""A user turn typed as `/name args` reaches the model with the saved command filled in; the stored row keeps what was typed.

Run: PYTHONPATH=backend python -m pytest backend/tests/test_commands_slash.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="slashtest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod  # noqa: E402
from personal_os import llm  # noqa: E402

appmod.db.set_settings({"autoLearn": False, "baseUrl": ""})
convos = appmod.convos
SEEN: list[list[dict[str, Any]]] = []


async def _stream(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: Any = None, kind: str = "chat",
                  effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: Any = None) -> Any:
    SEEN.append([dict(m) for m in messages])
    yield {"type": "delta", "text": "ok"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


def drive(cid: str, text: str) -> list[dict[str, Any]]:
    async def go() -> None:
        async for _ in appmod._chat_stream(cid, appmod.ChatIn(content=text), asyncio.Event()):
            pass
    prev = llm.stream_chat
    llm.stream_chat = _stream  # type: ignore[assignment]
    SEEN.clear()
    try:
        asyncio.run(go())
    finally:
        llm.stream_chat = prev
    return [m for m in SEEN[0] if m["role"] == "user"]


def _save(name: str, body: str, subtask: bool = False) -> None:
    if not appmod.command_store.get(name):
        appmod.command_store.save(f"---\nname: {name}\ndescription: d\n" + ("subtask: true\n" if subtask else "") + f"---\n{body}\n")


def test_known_command_is_filled_for_the_model_and_stored_as_typed() -> None:
    _save("standup", "Summarise $ARGUMENTS")
    cid = convos.create(None, "t", "m")["id"]
    users = drive(cid, "/standup yesterday")
    assert users[-1]["content"].startswith("/standup yesterday")
    assert "Summarise yesterday" in users[-1]["content"]
    stored = [m for m in convos.get(cid)["messages"] if m["role"] == "user"]
    assert stored[-1]["content"] == "/standup yesterday"


def test_a_later_turn_still_carries_the_earlier_expansion() -> None:
    _save("standup", "Summarise $ARGUMENTS")
    cid = convos.create(None, "t", "m")["id"]
    drive(cid, "/standup yesterday")
    users = drive(cid, "and today?")
    assert "Summarise yesterday" in users[0]["content"]
    assert users[-1]["content"] == "and today?"


def test_unknown_command_and_mid_text_slash_pass_through() -> None:
    cid = convos.create(None, "t", "m")["id"]
    assert drive(cid, "/unknown x")[-1]["content"] == "/unknown x"
    _save("standup", "Summarise $ARGUMENTS")
    assert drive(cid, "run /standup now")[-1]["content"] == "run /standup now"


def test_subtask_command_points_the_model_at_command_run() -> None:
    _save("digest", "Research $1 deeply", subtask=True)
    cid = convos.create(None, "t", "m")["id"]
    content = drive(cid, "/digest rust")[-1]["content"]
    assert "command_run" in content and "'digest'" in content and "'rust'" in content
    assert "Research rust deeply" not in content  # the child agent fills it, not this turn


def test_a_command_steered_into_a_live_run_is_filled() -> None:
    _save("standup", "Summarise $ARGUMENTS")
    cid = convos.create(None, "t", "m")["id"]
    steers: list[dict[str, Any]] = []
    calls: list[list[dict[str, Any]]] = []

    async def stream(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: Any = None, kind: str = "chat",
                     effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: Any = None) -> Any:
        calls.append([dict(m) for m in messages])
        if len(calls) == 1:  # the steer arrives while the first segment is streaming
            steers.append(convos.add_message(cid, "user", "/standup yesterday"))
        yield {"type": "delta", "text": "ok"}
        yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}

    async def go() -> None:
        async for _ in appmod._chat_stream(cid, appmod.ChatIn(content="hi"), asyncio.Event(), steers):
            pass
    prev = llm.stream_chat
    llm.stream_chat = stream  # type: ignore[assignment]
    try:
        asyncio.run(go())
    finally:
        llm.stream_chat = prev
    assert len(calls) == 2, "the steer is answered in the same run"
    last = [m for m in calls[1] if m["role"] == "user"][-1]["content"]
    assert last.startswith("/standup yesterday") and "Summarise yesterday" in last
