"""ask_user: a question card in an ordinary chat.

Offered in chat and while a plan is being drafted (never a desk tool there), answered with a note that comes back
as the result, approved bare means no_answer, a message written while it waits is the answer (not a decline, and
not a second user turn), and no 'Always' click or "on" mode ever stands in for the card.

Run: PYTHONPATH=<repo>/backend pytest backend/tests/test_ask_user.py
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="askuser-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import llm, permrules  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
store = appmod.run_store
ROUNDS: list[Any] = []
SEEN: list[list[dict[str, Any]]] = []
OFFERED: list[set[str]] = []
Q = json.dumps({"question": "Which one?", "options": ["Option A", "Option B"]})


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]] | None = None, kind: str = "chat",
                    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None) -> Any:
    SEEN.append(copy.deepcopy(messages))
    OFFERED.append({t["function"]["name"] for t in tools or []})
    step = ROUNDS.pop(0) if ROUNDS else ["done"]
    if isinstance(step, dict):
        yield {"type": "end", "finish_reason": "tool_calls", "tool_calls": step["tool_calls"], "usage": None}
        return
    for chunk in step:
        yield {"type": "delta", "text": chunk}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


@pytest.fixture(scope="module", autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    real = llm.stream_chat
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": ""})
        yield
    llm.stream_chat = real


@pytest.fixture(autouse=True)
def _script():  # type: ignore[no-untyped-def]
    llm.stream_chat = _scripted
    ROUNDS.clear()
    SEEN.clear()
    OFFERED.clear()
    yield
    ROUNDS.clear()


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]} (wanted {expect})"
    return r.json()


def wait_until(pred: Callable[[], Any], label: str, timeout: float = 15.0) -> Any:
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {label}")


def start(settings: dict[str, Any] | None = None) -> tuple[str, str]:
    cid = j("POST", "/conversations", {})["id"]
    if settings:
        j("PATCH", f"/conversations/{cid}", {"settings": settings})
    return cid, j("POST", f"/conversations/{cid}/chat", {"content": "go"})["run_id"]


def finished(rid: str) -> dict[str, Any]:
    return wait_until(lambda: (r := store.get(rid)) and r["status"] not in ("running", "awaiting_approval") and r, "the run to finish")


def ask(cid: str = "c1") -> dict[str, Any]:
    return {"tool_calls": [{"id": cid, "name": "ask_user", "arguments": Q}]}


def tool_result(i: int = 1) -> dict[str, Any]:
    return json.loads([m for m in SEEN[i] if m["role"] == "tool"][-1]["content"])


def card(rid: str) -> dict[str, Any]:
    return wait_until(lambda: store.approvals("pending", run_id=rid), "the question card")[0]


def test_offered_in_chat_and_while_planning_and_desk_tools_are_not() -> None:
    ROUNDS.append(["hi"])
    _, rid = start()
    finished(rid)
    assert "ask_user" in OFFERED[0]
    assert not {"desk_ask", "desk_done", "desk_write_file"} & OFFERED[0], "desk workspace tools stay inside desks"
    ROUNDS.append(["hi"])
    _, rid = start({"planMode": "always"})
    finished(rid)
    assert {"ask_user", "propose_plan"} <= OFFERED[1], "plan mode can clarify before it proposes"
    assert "gmail_send" not in OFFERED[1]


def test_an_answer_on_the_card_comes_back_as_the_result() -> None:
    ROUNDS.extend([ask(), ["thanks"]])
    _, rid = start()
    row = card(rid)
    j("POST", f"/approvals/{row['call_id']}", {"decision": "allow", "note": "Option B"})
    finished(rid)
    res = tool_result()
    assert res["status"] == "answered" and res["answer"] == "Option B" and res["choice"] == "Option B", res
    ROUNDS.extend([ask(), ["thanks"]])
    _, rid = start()
    j("POST", f"/approvals/{card(rid)['call_id']}", {"decision": "allow", "note": "Neither, use C"})
    finished(rid)
    res = tool_result(3)
    assert res["answer"] == "Neither, use C" and "choice" not in res, res


def test_approved_without_an_answer_is_no_answer() -> None:
    ROUNDS.extend([ask(), ["ok"]])
    _, rid = start()
    j("POST", f"/approvals/{card(rid)['call_id']}", {"decision": "allow"})
    finished(rid)
    assert tool_result()["status"] == "no_answer"


def test_a_message_written_while_it_waits_is_the_answer_and_not_a_second_turn() -> None:
    ROUNDS.extend([ask(), ["going with B"]])
    cid, rid = start()
    row = card(rid)
    j("POST", f"/conversations/{cid}/steer", {"content": "Option B please"})
    finished(rid)
    after = store.approval(row["call_id"])
    assert after["status"] == "approved" and after["decided_by"] == "steer" and after["note"] == "Option B please", after
    res = tool_result()
    assert res["status"] == "answered" and res["answer"] == "Option B please", res
    assert [m["content"] for m in SEEN[1] if m["role"] == "user"] == ["go"], "the answer is not folded in again as a new turn"


def test_no_always_click_or_on_mode_ever_answers_the_card() -> None:
    assert "ask_user" in permrules.STILL_ASK
    ROUNDS.extend([ask(), ["ok"]])
    cid, rid = start()
    j("POST", f"/approvals/{card(rid)['call_id']}", {"decision": "always_chat", "note": "Option A"})
    finished(rid)
    assert tool_result()["answer"] == "Option A"
    assert "ask_user" not in (j("GET", f"/conversations/{cid}")["settings"].get("tools") or {}), "no standing grant"
    # Even switched on by hand, the question still waits on its card.
    ROUNDS.extend([ask("c2"), ["ok"]])
    _, rid = start({"tools": {"ask_user": "on"}})
    row = card(rid)
    assert row["tool"] == "ask_user" and row["forced"]
    j("POST", f"/approvals/{row['call_id']}", {"decision": "deny"})
    finished(rid)
