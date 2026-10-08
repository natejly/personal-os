"""The orchestrator is quiet: no "On it" line, no narrated delegation, and a turn that only handed work on leaves no reply row.

Harness and scripted model come from test_orchestration.py (fixtures re-exported below); Telegram cases use test_telegram.py's fake API.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402

import test_orchestration as T  # noqa: E402
from personal_os import workers as W  # noqa: E402
from test_orchestration import FRONT, WORKER, _app, _fresh, call, client, delegate, final_done, new_conv, search, settle, stream  # noqa: E402,F401
from test_telegram import OWNER, Env, case, until  # noqa: E402


def quiet(goal: str, text: str = "") -> list[dict]:
    """The delegate round (no words) and the final round after its result (`text`)."""
    T.WAKE[:] = [{"text": "NO_REPLY"}]  # the worker's report wakes the chat: nothing to add
    return [{**delegate(goal), "text": ""}, {"text": text}, {"text": text}]  # the last step answers an empty-reply nudge, if any


def run_quietly(cid: str, text: str) -> list:
    """One reply, then wait out the worker's wake turn, so nothing leaks into the next test."""
    events = stream(cid, text)
    settle(cid)
    return events


def messages(cid: str) -> list[dict]:
    return [m for m in client.get(f"/conversations/{cid}").json()["messages"] if m.get("kind") != "wake"]


def test_no_prompt_tells_the_model_to_announce_what_it_started() -> None:
    for text in (W.FRONT_AGENT_HINT, W.FORCE_DELEGATE_NUDGE, json.dumps(W.forced_refusal("x"))):
        assert "what you started" not in text and "On it" not in text
    assert "NO_REPLY" in W.FRONT_AGENT_HINT and "never announce" in W.FRONT_AGENT_HINT
    assert not hasattr(W, "ACK_LINE") and not hasattr(W, "ACK_NUDGE")


def test_the_delegate_result_does_not_ask_for_narration() -> None:
    cid = new_conv()
    WORKER["Quiet job"] = [{"text": "done"}]
    FRONT[:] = quiet("Quiet job")
    run_quietly(cid, "go")
    note = next(m["content"] for s in T.seat("front") for m in s["messages"] if m["role"] == "tool" and "worker_id" in str(m["content"]))
    assert "what you started" not in note and "Say nothing" in note


def test_dispatch_only() -> None:
    assert W.dispatch_only(["delegate"]) and W.dispatch_only(["todo_write", "message_worker"]) and W.dispatch_only(["todo_write"])
    assert not W.dispatch_only([]) and not W.dispatch_only(["delegate", "search_memory"])


@pytest.mark.parametrize("text", ["", "   ", "NO_REPLY", "`no_reply`."])
def test_a_turn_that_only_delegated_leaves_no_reply_row(text: str) -> None:
    cid = new_conv()
    WORKER["Quiet job"] = [{"text": "finished the quiet job"}]
    FRONT[:] = quiet("Quiet job", text)
    events = run_quietly(cid, "do it")
    assert any(e == "removed_message" for e, _ in events)
    done = final_done(events)
    assert done["id"] is None and done["error"] is None, "the run ends normally"
    assert [m["role"] for m in messages(cid)] == ["user"]


def test_a_delegating_turn_with_words_keeps_them() -> None:
    cid = new_conv()
    WORKER["Quiet job"] = [{"text": "finished"}]
    FRONT[:] = quiet("Quiet job", "Your flight is at 9. The rest is running.")
    events = run_quietly(cid, "do it")
    assert not any(e == "removed_message" for e, _ in events) and final_done(events)["id"]
    assert messages(cid)[-1]["content"] == "Your flight is at 9. The rest is running."


def test_real_tool_calls_without_words_are_not_deleted() -> None:
    cid = new_conv()
    FRONT[:] = [{"text": "", "calls": [search(1)]}, {"text": ""}]
    events = run_quietly(cid, "look")
    assert not any(e == "removed_message" for e, _ in events)
    done = final_done(events)
    assert done["id"] and done["error"] is None and [e["name"] for e in done["tool_events"]] == ["search_memory"]
    # mixed with a hand-off: the visible tool card keeps the row
    FRONT[:] = [{"text": "", "calls": [search(2), call("d2", "delegate", {"goal": "x"})]}, {"text": ""}]
    assert final_done(run_quietly(cid, "again"))["id"]


def test_a_plain_no_reply_answer_leaves_no_reply_row() -> None:
    """A front turn that answers with only the sentinel (no tools at all) is as silent as a dispatch-only one."""
    cid = new_conv()
    FRONT[:] = [{"text": "NO_REPLY"}]
    events = run_quietly(cid, "anything?")
    assert any(e == "removed_message" for e, _ in events)
    done = final_done(events)
    assert done["id"] is None and done["error"] is None, "the run ends normally"
    assert [m["role"] for m in messages(cid)] == ["user"]


def test_a_reply_cut_off_inside_the_marker_leaves_no_row() -> None:
    """A reply that stopped partway through the sentinel ("NO_REP") is the sentinel: nothing is stored or sent."""
    cid = new_conv()
    FRONT[:] = [{"text": "NO_REP"}]
    events = run_quietly(cid, "anything?")
    assert any(e == "removed_message" for e, _ in events)
    assert [m["role"] for m in messages(cid)] == ["user"]


def test_a_real_answer_trailing_the_marker_is_stored_without_it() -> None:
    cid = new_conv()
    FRONT[:] = [{"text": "Done.\nNO_REPLY"}]
    final_done(run_quietly(cid, "go"))
    assert messages(cid)[-1]["content"] == "Done."


def test_real_tool_calls_with_no_reply_keep_the_row_but_not_the_marker() -> None:
    """A reply that did its own work and then answered NO_REPLY keeps its tool cards; the marker is never stored."""
    cid = new_conv()
    FRONT[:] = [{"text": "", "calls": [search(1)]}, {"text": "NO_REPLY"}]
    events = run_quietly(cid, "look")
    assert not any(e == "removed_message" for e, _ in events)
    done = final_done(events)
    assert done["id"] and done["error"] is None and [e["name"] for e in done["tool_events"]] == ["search_memory"]
    last = messages(cid)[-1]
    assert last["role"] == "assistant" and last["content"] == "", "the sentinel itself is never stored"


def test_a_worker_result_after_a_silent_dispatch_is_a_visible_reply() -> None:
    cid = new_conv()
    WORKER["Quiet job"] = [{"text": "FOUND 42"}]
    FRONT[:] = quiet("Quiet job")
    T.WAKE[:] = [{"text": "The answer is 42."}]
    T.say(cid)
    msgs = settle(cid)
    assert [m["role"] for m in msgs if m.get("kind") != "wake"] == ["user", "assistant"]
    assert msgs[-1]["content"] == "The answer is 42."
    assert not any("On it, working on that now." in m["content"] for m in msgs)


def test_the_run_is_marked_silent_for_the_phone() -> None:
    cid = new_conv()
    T.GATES["Quiet job"] = threading.Event()  # holds the worker, so no wake run replaces this one on the bus
    FRONT[:] = quiet("Quiet job")
    rid = T.say(cid)
    T.wait(lambda: (r := T.appmod.bus.get(cid)) and r.run_id == rid and not r.live, "the first run to end")
    assert T.appmod.bus.get(cid).silent is True
    T.GATES["Quiet job"].set()
    settle(cid)


# ---------------- Telegram ----------------
@case
async def test_a_silent_dispatch_sends_nothing_to_the_phone(env: Env) -> None:
    env.texts["m1"] = "A visible reply"
    await env.bridge.poll_once()
    env.bridge._runs.add("run-1")
    env.bridge.on_run_change(env.run(status="done", live=False, replied=True, message_id="gone", run_id="run-1", silent=True))
    await env.bridge._reply_final(env.run(live=False, message_id="gone", silent=True), OWNER)
    assert env.sent() == []
    await env.bridge._reply_final(env.run(live=False, message_id="m1"), OWNER)
    assert env.sent() == ["A visible reply"], "an ordinary reply still goes out"


@case
async def test_a_worker_result_reply_still_reaches_the_phone(env: Env) -> None:
    await env.bridge.poll_once()  # takes the poller lock
    env.bridge.push("The answer is 42.")
    await until(lambda: env.sent())
    assert env.sent() == ["The answer is 42."]
