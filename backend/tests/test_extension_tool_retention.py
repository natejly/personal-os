"""An extension turned off mid-turn keeps the tools the turn was already offered until the turn ends (design doc 3.3).

Run: PYTHONPATH=<repo>/backend pytest backend/tests/test_extension_tool_retention.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="toolkeep-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm  # noqa: E402
from personal_os.tools import ToolSpec  # noqa: E402

appmod.db.set_settings({"toolDeferAbove": 0})
client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
OWNER = "keepdemo"
OFFERED: list[set[str]] = []  # the tool names each model request carried
ROUNDS: list[Any] = []


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, **kw: Any) -> Any:
    OFFERED.append({t["function"]["name"] for t in tools or ()})
    if ROUNDS:
        yield {"type": "end", "finish_reason": "tool_calls", "tool_calls": ROUNDS.pop(0), "usage": None}
        return
    yield {"type": "delta", "text": "ok"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


async def _turn_off(ctx: dict[str, Any]) -> dict[str, Any]:
    appmod.db.set_settings({"hiddenViews": [OWNER]})
    return {"ok": True}


async def _plain(ctx: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True}


@pytest.fixture(autouse=True)
def _tools():  # type: ignore[no-untyped-def]
    real = llm.stream_chat
    obj = {"type": "object", "properties": {}}
    for name, fn in (("keep_flip", _turn_off), ("keep_other", _plain)):
        appmod.toolbox.specs[name] = ToolSpec(name, name, obj, fn, "test")
        appmod.toolbox.tool_owner[name] = OWNER
    llm.stream_chat = _scripted
    OFFERED.clear()
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": "", "hiddenViews": []})
        yield
    llm.stream_chat = real
    for name in ("keep_flip", "keep_other"):
        appmod.toolbox.specs.pop(name, None)
        appmod.toolbox.tool_owner.pop(name, None)
    appmod.db.set_settings({"hiddenViews": []})


def chat() -> None:
    cid = client.post("/conversations", json={}).json()["id"]
    client.patch(f"/conversations/{cid}", json={"settings": {"tools": {"keep_flip": "on", "keep_other": "on"}}})
    run_id = client.post(f"/conversations/{cid}/chat", json={"content": "go"}).json()["run_id"]
    deadline = time.time() + 15
    while (client.get(f"/runs/{run_id}").json().get("status") in ("running", "awaiting_approval")) and time.time() < deadline:
        time.sleep(0.02)


def test_tools_offered_to_a_turn_stay_until_it_ends_and_leave_the_next_one() -> None:
    ROUNDS.append([{"id": "a", "name": "keep_flip", "arguments": json.dumps({})}])
    chat()
    first, second = OFFERED[0], OFFERED[1]
    assert {"keep_flip", "keep_other"} <= first
    assert {"keep_flip", "keep_other"} <= second  # the extension went off during round 1; round 2 still carries its tools
    OFFERED.clear()
    chat()
    assert not {"keep_flip", "keep_other"} & OFFERED[0]


def test_a_recomputed_schema_list_keeps_the_retained_tools_of_an_extension_that_went_off() -> None:
    modes = {"keep_flip": "on", "keep_other": "on"}
    names = lambda retained=(): {s["function"]["name"] for s in appmod.toolbox.schemas(modes, retained)}  # noqa: E731
    assert names() == {"keep_flip", "keep_other"}
    appmod.db.set_settings({"hiddenViews": [OWNER]})
    assert names() == set()
    assert names({"keep_flip"}) == {"keep_flip"}
