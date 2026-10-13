"""A child runs on: its explicit model, else its agent definition's model, else the workerModel setting, else the chat model."""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="workermodel-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import app as appmod  # noqa: E402
from personal_os import llm  # noqa: E402
from personal_os.subagents import BUILTIN_ROLES  # noqa: E402

SEEN: list[str] = []
EFFORTS: list[str] = []


async def _stream(settings, model, messages, tools=None, kind="chat", effort="default", tool_choice="auto", fast=False, cancel=None):
    SEEN.append(model)
    EFFORTS.append(effort)
    yield {"type": "delta", "text": "done"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


class FakeRun:
    live = True
    run_id = "run_fake"

    def publish(self, event, data) -> None:
        pass

    def set_status(self, s) -> None:
        pass


def model_used(worker: str, *, role_model: str | None = None, explicit: str | None = None, worker_effort: str = "medium") -> str:
    appmod.db.set_settings({"autoLearn": False, "baseUrl": "", "workerModel": worker, "workerEffort": worker_effort})
    SEEN.clear()
    cid = appmod.convos.create(None, "t", "m")["id"]
    ctx = {"project_id": None, "conversation_id": cid, "message_id": None, "tainted": False, "taint_sources": [],
           "allowed_urls": set(), "settings": appmod.settings(), "modes": appmod.toolbox.effective({}, None, None),
           "mcp_modes": {}, "mcp_schemas": {}, "depth": 0, "agent_run_id": "", "model": "chat-m", "effort": "xhigh", "stop": asyncio.Event(),
           "meter": appmod.RunMeter(), "run": FakeRun()}
    args = {"task": "t", "role": "general", **({"model": explicit} if explicit else {})}
    prev_stream, prev_role = llm.stream_chat, BUILTIN_ROLES["general"]
    llm.stream_chat = _stream
    BUILTIN_ROLES["general"] = dataclasses.replace(prev_role, model=role_model)
    try:
        asyncio.run(asyncio.wait_for(appmod.toolbox.call("agent_spawn", args, ctx), 10))
    finally:
        llm.stream_chat, BUILTIN_ROLES["general"] = prev_stream, prev_role
    return SEEN[0]


def test_blank_worker_model_inherits_the_chat_model() -> None:
    assert model_used("") == "chat-m"


def test_worker_model_applies_to_children() -> None:
    assert model_used("worker-m") == "worker-m"


def test_agent_definition_model_beats_worker_model() -> None:
    assert model_used("worker-m", role_model="role-m") == "role-m"


def test_explicit_model_beats_everything() -> None:
    assert model_used("worker-m", role_model="role-m", explicit="x-m") == "x-m"


def test_worker_rounds_default_to_medium_not_the_chats_effort() -> None:
    EFFORTS.clear()
    model_used("")
    assert EFFORTS[0] == "medium"


def test_worker_effort_setting_and_same_as_chat() -> None:
    for setting, want in (("low", "low"), ("high", "high"), ("chat", "xhigh")):
        EFFORTS.clear()
        model_used("", worker_effort=setting)
        assert EFFORTS[0] == want
