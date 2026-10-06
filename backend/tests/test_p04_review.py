"""The review gate in auto mode (permissionMode): a second model looks at a call that is not known safe.

Manual -> no reviewer call. allow -> runs, verdict on the event and a log row. ask -> a normal card carrying the reason.
deny -> refused with the reason as the result. An error or unreadable answer asks. Safe tools, allow rules and tools the
user set to on are not reviewed; an alwaysAsk tool is lifted only by a high-confidence allow. Allow all logs what it ran.

Run: zsh tests/e2e/run-pytest.sh backend/tests/test_p04_review.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="p04review-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import autoreview, llm  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
store = appmod.run_store
ROUNDS: list[Any] = []
REVIEWS: list[list[dict[str, Any]]] = []
ANSWER: list[Any] = []  # what the reviewer says next: a string, or an Exception to raise


async def _scripted(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: Any = None, kind: str = "chat",
                    **_kw: Any) -> Any:
    step = ROUNDS.pop(0) if ROUNDS else ["done"]
    if isinstance(step, dict):
        yield {"type": "end", "finish_reason": "tool_calls", "tool_calls": step["tool_calls"], "usage": None}
        return
    for chunk in step:
        yield {"type": "delta", "text": chunk}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


async def _complete(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], kind: str = "other", **_kw: Any) -> str:
    assert kind == "review"
    REVIEWS.append(messages)
    a = ANSWER.pop(0) if ANSWER else json.dumps({"verdict": "allow", "reason": "ok"})
    if isinstance(a, Exception):
        raise a
    return a


@pytest.fixture(scope="module", autouse=True)
def _portal():  # type: ignore[no-untyped-def]
    real = llm.stream_chat, llm.complete
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": "", "autoTitle": False, "toolDeferAbove": 500})
        yield
    llm.stream_chat, llm.complete = real


@pytest.fixture(autouse=True)
def _script():  # type: ignore[no-untyped-def]
    llm.stream_chat, llm.complete = _scripted, _complete
    ROUNDS.clear(), REVIEWS.clear(), ANSWER.clear()
    client.put("/settings", json={"permissionMode": "auto", "permissionRules": {}, "tools": {}})
    yield
    client.put("/settings", json={"permissionMode": "auto", "permissionRules": {}, "tools": {}})


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]} (wanted {expect})"
    return r.json()


def wait_until(pred: Callable[[], Any], label: str, timeout: float = 15.0) -> Any:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if v := pred():
            return v
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {label}")


def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"tool_calls": [{"id": "c1", "name": name, "arguments": json.dumps(args)}]}


def run(name: str, args: dict[str, Any]) -> str:
    ROUNDS.extend([call(name, args), ["done"]])
    cid = j("POST", "/conversations", {})["id"]
    return j("POST", f"/conversations/{cid}/chat", {"content": "write it down"})["run_id"]


def finished(rid: str) -> dict[str, Any]:
    return wait_until(lambda: (r := store.get(rid)) and r["status"] not in ("running", "awaiting_approval") and r, "the run to finish")


def events(rid: str) -> list[dict[str, Any]]:
    run_row = store.get(rid)
    msgs = j("GET", f"/conversations/{run_row['conversation_id']}")["messages"]
    return [t for m in msgs for t in (m.get("tool_events") or [])]


WRITE = ("doc_create", {"title": "Note", "content": "hello"})


def log_rows(tool: str) -> list[dict[str, Any]]:
    return appmod.approval_log.history(appmod.db, tool=tool, limit=200)["items"]


def test_manual_never_calls_the_reviewer() -> None:
    j("PUT", "/settings", {"permissionMode": "manual"})
    finished(run(*WRITE))
    assert REVIEWS == []


def test_allow_runs_and_the_event_and_log_record_the_verdict() -> None:
    ANSWER.append('Sure: {"verdict":"allow","confidence":"high","reason":"matches the request"}')
    before = len(log_rows("doc_create"))
    rid = run(*WRITE)
    finished(rid)
    ev = events(rid)[0]
    assert len(REVIEWS) == 1 and "write it down" in REVIEWS[0][1]["content"]
    assert not ev["error"] and ev["review"]["verdict"] == "allow" and ev["review"]["reason"] == "matches the request"
    assert ev["review"]["confidence"] == "high" and ev["review"]["ms"] >= 0 and ev["review"]["model"] is not None
    new = log_rows("doc_create")[: len(log_rows("doc_create")) - before]
    assert [(r["decision"], r["scope"], r["note"], r["reviewer_verdict"]) for r in new] == [("auto", "auto-review", "mode: auto", "allow")]


def test_ask_opens_a_card_with_the_reason_and_approve_runs() -> None:
    ANSWER.append(json.dumps({"verdict": "ask", "reason": "not what was asked"}))
    rid = run(*WRITE)
    row = wait_until(lambda: store.approvals("pending", run_id=rid), "the review card")[0]
    j("POST", f"/approvals/{row['call_id']}", {"decision": "allow"})
    finished(rid)
    ev = events(rid)[0]
    assert ev["approval"] == "allow" and not ev["error"] and ev["review"]["verdict"] == "ask" and ev["review"]["reason"] == "not what was asked"
    assert {"review-ask", "allow_once"} <= {r["decision"] for r in log_rows("doc_create")}


def test_deny_refuses_with_the_reason_and_logs_it() -> None:
    ANSWER.append(json.dumps({"verdict": "deny", "confidence": "high", "reason": "the user never asked for this"}))
    rid = run(*WRITE)
    finished(rid)
    ev = events(rid)[0]
    assert ev["error"] and "refused by the safety reviewer: the user never asked for this" in json.dumps(ev)
    assert not store.approvals("pending", run_id=rid)
    deny = [r for r in log_rows("doc_create") if r["decision"] == "deny" and r["scope"] == "auto-review"]
    assert deny and deny[0]["reviewer_verdict"] == "deny" and deny[0]["note"] == "mode: auto"


def test_a_reviewer_error_or_garbage_asks() -> None:
    for bad in (RuntimeError("down"), "I think it is fine"):
        ANSWER.append(bad)
        rid = run(*WRITE)
        row = wait_until(lambda: store.approvals("pending", run_id=rid), "a card after a reviewer failure")[0]
        j("POST", f"/approvals/{row['call_id']}", {"decision": "deny"})
        finished(rid)


def test_safe_tools_are_not_reviewed_and_always_ask_needs_high_confidence() -> None:
    finished(run("current_time", {}))
    assert REVIEWS == []
    ANSWER.append(json.dumps({"verdict": "allow", "confidence": "medium", "reason": "fine"}))
    rid = run("schedule_task", {"name": "x", "prompt": "y", "in_minutes": 5})  # alwaysAsk by default
    wait_until(lambda: store.approvals("pending", run_id=rid), "the always-ask card after a medium-confidence allow")
    assert len(REVIEWS) == 1
    j("POST", f"/approvals/{store.approvals('pending', run_id=rid)[0]['call_id']}", {"decision": "deny"})
    finished(rid)


def test_an_allow_rule_or_an_explicit_on_is_not_reviewed() -> None:
    j("PUT", "/settings", {"permissionRules": {"allow": ["doc_create"]}})
    finished(run(*WRITE))
    j("PUT", "/settings", {"permissionRules": {}, "tools": {"doc_create": "on"}})
    finished(run(*WRITE))
    assert REVIEWS == []


def test_an_explicit_ask_stays_a_card_without_a_review() -> None:
    j("PUT", "/settings", {"tools": {"doc_create": "ask"}})
    rid = run(*WRITE)
    row = wait_until(lambda: store.approvals("pending", run_id=rid), "the user's own ask card")[0]
    j("POST", f"/approvals/{row['call_id']}", {"decision": "deny"})
    finished(rid)
    assert REVIEWS == []


def test_allow_all_runs_and_logs() -> None:
    j("PUT", "/settings", {"permissionMode": "allow_all"})
    before = len(log_rows("doc_create"))
    rid = run(*WRITE)
    finished(rid)
    assert REVIEWS == [] and not events(rid)[0]["error"]
    new = log_rows("doc_create")[: len(log_rows("doc_create")) - before]
    assert [(r["decision"], r["scope"], r["note"]) for r in new] == [("auto", "allow-all", "allowed (allow-all mode)")]


def test_settings_validation() -> None:
    j("PUT", "/settings", {"permissionMode": "sometimes"}, expect=422)
    j("PUT", "/settings", {"autoReviewModel": 3}, expect=422)
    j("PUT", "/settings", {"autoReview": "risky", "skipPermissions": True})  # legacy keys still accepted
    j("PUT", "/settings", {"autoReview": "off", "skipPermissions": False})


def test_the_reviewer_sees_redacted_arguments() -> None:
    body = asyncio.run(_review_body({"title": "k", "body": "key sk-abcdefghijklmnopqrstuvwxyz0123456789ABCD"}))
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in body


async def _review_body(args: dict[str, Any]) -> str:
    await autoreview.review({}, "m", name="doc_create", description="d", args=args, user_text="u", mode="on", tainted=False)
    return REVIEWS[-1][1]["content"]
