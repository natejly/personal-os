"""Internal control messages (desk nudges, continues, resumes, hand-offs, worker wakes) are stored with a `kind`:
the model reads them, the user never does.

(a) the kind rule: `is_internal`, `internal_kind`, `_said`;
(b) a real desk chain offline: the nudge turn is persisted hidden, replayed to the model, kept out of search, export,
    titles and the learned transcript;
(c) `_answered` / `_chain_kind` look at the plan's unfinished steps, not at whether a plan ever existed;
(d) migration 26 marks the four leaked prefixes;
(e) the phone is never sent a nudge turn's reply.

Run: backend/.venv/bin/python -m pytest backend/tests/test_internal_messages.py
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
import time
import types
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="internalmsg-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import backups, learn, llm, migrations  # noqa: E402
from personal_os.cowork import CHAT_HANDOFF, DESK_CONTINUE, DESK_NUDGE, DESK_RESUME, internal_kind  # noqa: E402
from personal_os.db import Database
from personal_os.kinds import is_internal  # noqa: E402
from personal_os.runs import Run  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
SEEN: list[list[dict[str, Any]]] = []
SCRIPT: list[str] = []


async def _fake(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: Any = None, kind: str = "chat",
                effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: Any = None) -> Any:
    SEEN.append([dict(m) for m in messages])
    yield {"type": "delta", "text": SCRIPT.pop(0) if SCRIPT else "ok"}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None}


@pytest.fixture(scope="module", autouse=True)
def _app():  # type: ignore[no-untyped-def]
    with client:
        client.put("/settings", json={"autoLearn": False, "baseUrl": "", "permissionMode": "manual", "autoTitle": False,
                                      "followUps": False, "learnStyle": False})
        yield


@pytest.fixture(autouse=True)
def _llm(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    SEEN.clear()
    SCRIPT.clear()
    monkeypatch.setattr(llm, "stream_chat", _fake)


def wait(pred: Callable[[], Any], what: str, timeout: float = 10.0) -> Any:
    end = time.time() + timeout
    while time.time() < end:
        if v := pred():
            return v
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def settled(cid: str) -> list[dict[str, Any]]:
    wait(lambda: not (appmod.bus.get(cid) and appmod.bus.get(cid).live), "the chat to settle")  # type: ignore[union-attr]
    time.sleep(0.1)
    wait(lambda: not (appmod.bus.get(cid) and appmod.bus.get(cid).live), "the chat to stay settled")  # type: ignore[union-attr]
    return client.get(f"/conversations/{cid}").json()["messages"]


# ---------------- (a) the rule ----------------
def test_the_kind_rule() -> None:
    assert not is_internal({"kind": None}) and not is_internal({}) and is_internal({"kind": "nudge"}) and is_internal({"kind": "wake"})
    assert appmod._said({"role": "user", "kind": None}) and not appmod._said({"role": "user", "kind": "continue"})
    assert not appmod._said({"role": "assistant", "kind": None})
    assert internal_kind(DESK_NUDGE) == "nudge" and internal_kind(f"{DESK_CONTINUE}\n\nnotes") == "continue"
    assert internal_kind(DESK_RESUME) == "resume" and internal_kind(CHAT_HANDOFF) == "handoff"
    assert internal_kind("What is on my calendar?") is None and internal_kind(None) is None


def test_a_launch_names_the_kind_of_what_it_stores() -> None:
    seen: list[Any] = []
    real = appmod.bus.start, appmod._desk_supervisor

    def start(conv_id: str, runner: Any, **kw: Any) -> Any:
        seen.append(kw["input"]["content"])
        return types.SimpleNamespace(run_id="r", seq=0)

    async def supervisor(desk_id: str, run: Any) -> None:
        return None

    bodies: list[Any] = []
    real_chat_in = appmod.ChatIn

    def chat_in(**kw: Any) -> Any:
        b = real_chat_in(**kw)
        bodies.append(b)
        return b

    appmod.bus.start, appmod._desk_supervisor, appmod.ChatIn = start, supervisor, chat_in  # type: ignore[assignment]

    async def go() -> None:
        for status, content, kwargs, kind in (
            ("draft", "Write the brief", {}, None),  # a person's brief
            ("interrupted", DESK_RESUME, {"kind": "resume"}, "resume"),
            ("draft", DESK_CONTINUE, {}, "continue"),  # a queued turn lost its kind: the leading text names it
            ("draft", "Background shell job j1 finished.", {"kind": "report"}, "report"),
        ):
            conv = appmod.convos.create(None, "d", "test-model")
            d = appmod.desks.create(conversation_id=conv["id"], brief="Write the brief")
            if status != "draft":
                appmod.desks.set_status(d["id"], status)
            appmod._launch_desk(d["id"], content, ("draft", "interrupted"), **kwargs)
            assert bodies[-1].kind == kind, (content, bodies[-1].kind)
            appmod.desks.set_status(d["id"], "stopped")

    try:
        asyncio.run(go())
    finally:
        appmod.bus.start, appmod._desk_supervisor, appmod.ChatIn = real[0], real[1], real_chat_in  # type: ignore[assignment]


# ---------------- (b) a real chain ----------------
def _nudged_desk() -> tuple[str, list[dict[str, Any]]]:
    """A propose-autonomy desk whose first reply just ends: the supervisor chains one nudge turn."""
    cid = appmod.convos.create(None, "New chat", "test-model")["id"]
    SCRIPT[:] = ["First answer.", "Second answer."]
    r = client.post("/cowork/desks", json={"conversation_id": cid, "autonomy": "propose", "brief": "look at the thing", "start": False})
    assert r.status_code == 200, r.text
    did = r.json()["desk"]["id"]
    r = client.post(f"/cowork/desks/{did}/message", json={"content": "look at the thing"})
    assert r.status_code == 200, r.text
    return cid, settled(cid)


def test_the_nudge_turn_is_stored_hidden_and_still_sent_to_the_model() -> None:
    cid, msgs = _nudged_desk()
    users = [m for m in msgs if m["role"] == "user"]
    assert [m.get("kind") for m in users] == [None, "nudge"], [(m["role"], m.get("kind"), m["content"][:30]) for m in msgs]
    assert users[1]["content"].startswith(DESK_NUDGE)
    assert len(SEEN) == 2 and any(m["role"] == "user" and str(m["content"]).startswith(DESK_NUDGE) for m in SEEN[1]), \
        "the model reads the nudge in its history"
    assert not appmod._said(users[1]) and appmod._said(users[0])
    assert appmod.convos.history(cid)[-2]["content"].startswith(DESK_NUDGE), "the history builder does not filter it"

    # search, export and the learned transcript skip it
    hits = client.get("/conversations/search", params={"q": "desk_done"}).json()
    assert cid not in {h["id"] for h in hits}
    md = client.get(f"/conversations/{cid}/export").json()["text"]
    assert "desk_done" not in md and md.count("## You") == 1 and md.count("## Grain") == 2
    transcript, _ = learn.run_transcript(msgs)
    assert transcript and "desk_done" not in transcript and "look at the thing" in transcript


def test_whole_app_export_skips_internal_rows() -> None:
    cid, _ = _nudged_desk()
    dest = Path(tempfile.mkdtemp(prefix="internal-export-")) / "snap.db"
    with appmod.db.tx() as c:
        c.execute("VACUUM INTO ?", (str(dest),))
    md, rows = backups.human_export(dest)["conversations"]
    mine = next(r for r in rows if r["id"] == cid)
    assert [m["role"] for m in mine["messages"]] == ["user", "assistant", "assistant"]
    assert "desk_done" not in md


def test_an_internal_first_turn_never_becomes_the_title() -> None:
    cid = appmod.convos.create(None, "New chat", "test-model")["id"]
    r = client.post(f"/conversations/{cid}/chat", json={"content": DESK_CONTINUE, "kind": "continue"})
    assert r.status_code == 200, r.text
    msgs = settled(cid)
    assert [m.get("kind") for m in msgs if m["role"] == "user"] == ["continue"]
    assert client.get(f"/conversations/{cid}").json()["title"] == "New chat"


# ---------------- (c) the nudge fires only for a stalled plan ----------------
BASE: dict[str, Any] = {"status": "working", "turn": 0, "cost": 0.0, "plan_id": None, "budget": None, "autonomy": "ask"}


def _run(*, steps: int = 0, content: str = "show me my calendar") -> Run:
    r = Run("unused", input={"content": content})
    r.partial, r.steps_consumed = None, steps
    return r


def _plan(statuses: list[str]) -> str:
    cid = appmod.convos.create(None, "p", "test-model")["id"]
    call = f"call-{time.time_ns()}"
    plan = appmod.plans.open(call, {"title": "t", "steps": [{"tool": "gmail_send", "arguments": {"i": i}} for i, _ in enumerate(statuses)]},
                             conversation_id=cid)
    with appmod.db.tx() as c:
        for i, s in enumerate(statuses):
            c.execute("UPDATE plan_steps SET status=? WHERE plan_id=? AND idx=?", (s, plan["plan_id"], i))
        c.execute("UPDATE action_plans SET status='approved' WHERE plan_id=?", (plan["plan_id"],))
    return plan["plan_id"]


def test_a_finished_plan_does_not_make_every_later_reply_a_stalled_one() -> None:
    done = {**BASE, "plan_id": _plan(["consumed", "consumed", "dropped"])}
    assert appmod._chain_kind(done, _run()) is None, "plain reply on an ask desk whose plan is fully carried out: answered"
    assert appmod._chain_kind({**BASE}, _run()) is None, "no plan: answered (unchanged)"
    pending = {**BASE, "plan_id": _plan(["consumed", "approved"])}
    assert appmod._chain_kind(pending, _run()) == "nudge", "an unclaimed approved step: the turn stalled mid-plan"
    assert appmod._chain_kind(pending, _run(steps=1)) == "nudge"
    assert appmod._chain_kind(done, _run(steps=1)) == "nudge", "a turn that consumed a step still picks up work"
    assert appmod._chain_kind(pending, _run(content=appmod.continue_message("nudge", "n"))) is None, "never two nudges in a row"


# ---------------- (d) migration 26 ----------------
def test_migration_26_marks_the_leaked_prefixes_and_nothing_else() -> None:
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, role TEXT, content TEXT, kind TEXT)")
    rows = [("1", "user", "You ended your reply without calling `desk_done` or `desk_ask`. If", None),
            ("2", "user", "Continuing this desk. The approved plan", None),
            ("3", "user", "Resuming this desk after an interruption. Check", None),
            ("4", "user", "The user has asked you to carry on with the task in this conversation on your own.", None),
            ("5", "user", "Continuing this desk is what I want", None),  # a person's words that merely resemble one: only 'desk.' is the prefix
            ("6", "user", "please check: You ended your reply without calling", None),
            ("7", "assistant", "You ended your reply without calling anything", None),
            ("8", "user", "You ended your reply without calling x", "wake")]
    c.executemany("INSERT INTO messages VALUES(?,?,?,?)", rows)
    c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")  # later steps read it
    c.execute("PRAGMA user_version = 25")
    assert migrations.run(c) == list(range(26, migrations.latest() + 1))
    kinds = dict(c.execute("SELECT id, kind FROM messages"))
    assert kinds == {"1": "nudge", "2": "continue", "3": "resume", "4": "handoff", "5": None, "6": None, "7": None, "8": "wake"}
    assert c.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 8, "nothing is deleted"


def test_a_fresh_database_is_at_the_latest_version() -> None:
    assert migrations.latest() >= 26
    with tempfile.TemporaryDirectory() as td:
        with Database(td).connect() as c:
            assert migrations.current(c) == migrations.latest()


# ---------------- (e) the phone ----------------
def test_a_nudge_turns_reply_never_reaches_the_phone(tmp_path: Path) -> None:
    from test_telegram import Env, until

    async def go() -> None:
        env = Env(tmp_path)
        env.texts["m-first"], env.texts["m-nudge"] = "First answer.", "Second answer."
        await env.feed()  # takes the poll lock, as the poller does
        env.bridge._runs.add("run-phone")  # the turn the phone itself started
        env.bridge.on_run_change(env.run(status="done", live=False, replied=True, run_id="run-phone", message_id="m-first", kind="desk"))
        await until(lambda: env.sent())
        # the chained nudge turn is another run the phone did not start: its reply is not pushed, and it is no "long run" either
        env.settings["telegramNotifyLongRuns"] = True
        env.bridge.on_run_change(env.run(status="done", live=False, replied=True, run_id="run-nudge", message_id="m-nudge", kind="desk",
                                         started_at=time.time() - 900, ended_at=time.time()))
        await asyncio.sleep(0.1)
        assert env.sent() == ["First answer."]

    asyncio.run(go())
