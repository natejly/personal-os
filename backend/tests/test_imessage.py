"""The iMessage bridge against a fixture Messages database and a fake script runner.

Nothing here opens a real Messages database or runs a real script: the chat DB is a temp SQLite file with the
tables and column names the poller reads, and every send goes through an injected runner.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from personal_os import imessage as im  # noqa: E402

ME = "+15551234567"
STRANGER = "+15559998888"
BODY = "ZEBRA-SECRET-BODY please keep this out of the logs"
SELF_GUID = f"iMessage;-;{ME}"  # the note-to-self chat: the user's own number (also on the allowlist)
MARK = im.DEFAULT_MARKER
HEAD = (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00"
        b"\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+")


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class ChatDB:
    """The subset of the Messages schema the poller reads, with the real column names."""

    def __init__(self, path: Path, accounts: bool = False) -> None:
        self.path = str(path)
        c = sqlite3.connect(self.path)
        c.executescript("""
            CREATE TABLE message (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT, text TEXT, attributedBody BLOB,
                handle_id INTEGER DEFAULT 0, date INTEGER, is_from_me INTEGER DEFAULT 0, service TEXT,
                associated_message_type INTEGER, cache_has_attachments INTEGER);
            CREATE TABLE handle (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT, service TEXT);
            CREATE TABLE chat (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT, style INTEGER, chat_identifier TEXT, service_name TEXT);
            CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
            CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
        """)
        if accounts:  # newer macOS: which of our own addresses a chat/message belongs to
            c.executescript("ALTER TABLE chat ADD COLUMN account_login TEXT; ALTER TABLE message ADD COLUMN account TEXT; "
                            "ALTER TABLE message ADD COLUMN destination_caller_id TEXT;")
        c.commit()
        c.close()

    def sql(self, q: str, *args: Any) -> None:
        c = sqlite3.connect(self.path)
        c.execute(q, args)
        c.commit()
        c.close()

    def _one(self, c: sqlite3.Connection, sql: str, args: tuple, insert: str, ins_args: tuple) -> int:
        r = c.execute(sql, args).fetchone()
        return r[0] if r else c.execute(insert, ins_args).lastrowid  # type: ignore[return-value]

    def add(self, handle: str, text: str | None, *, from_me: int = 0, assoc: int | None = 0, att: int = 0,
            body: bytes | None = None, group: str | None = None, age: float = 0, join: bool = True,
            service: str | None = "iMessage", date: Any = "auto", chat: str | None = None, guid: str | None = None) -> int:
        c = sqlite3.connect(self.path)
        hid = self._one(c, "SELECT ROWID FROM handle WHERE id=?", (handle,), "INSERT INTO handle(id, service) VALUES(?, 'iMessage')", (handle,))
        cguid, style, ident = (f"iMessage;+;{group}", 43, group) if group else (f"iMessage;-;{handle}", 45, handle)
        if chat:  # a 1:1 chat that is not named after the sender, e.g. a row from_me placed in a given chat
            cguid, style, ident = chat, 45, chat.rsplit(";", 1)[-1]
        row = c.execute("SELECT ROWID FROM chat WHERE guid=?", (cguid,)).fetchone()
        if row:
            cid = row[0]
        else:
            cid = c.execute("INSERT INTO chat(guid, style, chat_identifier, service_name) VALUES(?,?,?,'iMessage')", (cguid, style, ident)).lastrowid
            for h in ((hid, hid + 100) if group else (hid,)):
                c.execute("INSERT INTO chat_handle_join VALUES(?,?)", (cid, h))
        if date == "auto":
            date = int((time.time() - age - im.APPLE_EPOCH) * 1e9)
        mid = c.execute("INSERT INTO message(guid, text, attributedBody, handle_id, date, is_from_me, associated_message_type, cache_has_attachments, service) "
                        "VALUES(?,?,?,?,?,?,?,?,?)", (guid, text, body, hid, date, from_me, assoc, att, service)).lastrowid
        if guid is None:
            c.execute("UPDATE message SET guid=? WHERE ROWID=?", (f"msg-{mid}", mid))
        if join:
            c.execute("INSERT INTO chat_message_join VALUES(?,?)", (cid, mid))
        c.commit()
        c.close()
        return mid  # type: ignore[return-value]


class FakeRun:
    def __init__(self, run_id: str, conv: str = "conv-1", **kw: Any) -> None:
        self.run_id, self.conversation_id = run_id, conv
        self.kind, self.started_at, self.ended_at = "chat", time.time() - 10, None
        self.live, self.replied, self.status, self.message_id, self.error = True, False, "running", None, None
        self.__dict__.update(kw)


class Env:
    def __init__(self, tmp_path: Path, **settings: Any) -> None:
        self.chat = ChatDB(tmp_path / "chat.db")
        self.tmp = tmp_path
        self.settings: dict[str, Any] = {"imessageEnabled": True, "imessageHandles": [ME], "imessageConversationId": None,
                                         "imessageNotifyLongRuns": False, "imessageLongRunMinutes": 3, **settings}
        self.state: dict[str, Any] = {"textsConversationId": "conv-1"}
        self.saves: list[dict[str, Any]] = []
        self.dead: set[str] = set()  # approvals whose run is gone
        self.decide_live = True
        self.sent: list[list[str]] = []
        self.rc = {"chat": 0, "participant": 0}
        self.turns: list[tuple[str, str]] = []
        self.decisions: list[tuple[str, str]] = []
        self.pending: list[dict[str, Any]] = []
        self.messages: dict[str, str] = {}
        self.convs = {"conv-1": "Texts"}
        self.active: list[dict[str, Any]] = []
        self.stopped: list[str] = []
        self.stop_result = True
        self.created = 0
        self.bridge = im.IMessageBridge(self.deps(), poll_seconds=0.02, send_gap=0)
        self.bridge.record_delays = (0.0,)
        self.bridge._code_seq = 0  # codes are random per process; the tests want 1, 2, ...

    def deps(self) -> im.Deps:
        async def runner(argv: list[str]) -> tuple[int, str]:
            self.sent.append(argv)
            return (self.rc["chat"] if argv[2] == im.SCRIPT_CHAT else self.rc["participant"]), ""

        def turn(conv: str, text: str) -> dict[str, Any]:
            self.turns.append((conv, text))
            return {"run_id": f"run-{len(self.turns)}"}

        def decide(call_id: str, decision: str) -> dict[str, Any]:
            self.decisions.append((call_id, decision))
            self.pending = [a for a in self.pending if a["call_id"] != call_id]
            return {"ok": True, "live": self.decide_live}

        def create() -> str:
            self.created += 1
            cid = f"texts-{self.created}"
            self.convs[cid] = "Texts"
            return cid

        def stop(conv: str) -> bool:
            self.stopped.append(conv)
            return self.stop_result

        return im.Deps(
            get_settings=lambda: self.settings, load_state=lambda: dict(self.state),
            save_state=self.save, chat_db_path=self.chat.path,
            runner=runner, start_turn=turn, stop=stop, decide=decide, pending_approvals=lambda: list(self.pending), is_live=lambda c: c not in self.dead,
            active_runs=lambda: list(self.active), create_texts_conversation=create,
            conversation_exists=lambda c: c in self.convs, message_text=self.messages.get,
            conversation_title=lambda c: self.convs.get(c))

    def save(self, st: dict[str, Any]) -> None:
        self.saves.append(dict(st))
        self.state.update(st)

    # one poll's worth of inbound, then every reply it queued
    async def poll(self) -> None:
        await self.bridge.poll_once()
        await self.settle()

    async def settle(self) -> None:
        while self.bridge._tasks:
            await asyncio.gather(*list(self.bridge._tasks))

    async def arm(self) -> None:
        """First poll: the cursor lands at the newest row, as it does when the toggle goes on."""
        await self.bridge.poll_once()

    def texts(self) -> list[str]:
        """What was sent, minus the marker every outbound text carries."""
        return [a[4].removeprefix(MARK) for a in self.sent]

    def pend(self, call_id: str, forced: bool = False, tool: str = "send_email", args: Any = None, conv: str = "conv-1",
             run_id: str = "run-1") -> None:
        self.pending.append({"call_id": call_id, "run_id": run_id, "conversation_id": conv, "tool": tool,
                             "args": args or {"to": "a@b.co", "subject": "Hello"}, "forced": forced, "danger": "external"})

    async def ask(self, text: str, **kw: Any) -> None:
        self.chat.add(ME, text, **kw)
        await self.poll()

    async def awaiting(self, run_id: str = "run-1", conv: str = "conv-1") -> None:
        self.bridge.on_run_change(FakeRun(run_id, conv, status="awaiting_approval"))
        await self.settle()


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def test_cursor_starts_at_max_and_history_is_never_replayed(env: Env) -> None:
    env.chat.add(ME, "old one")
    last = env.chat.add(ME, "old two")

    async def go() -> None:
        await env.arm()
        assert env.state["cursor"] == last
        await env.poll()
        assert env.turns == [] and env.sent == []
        env.chat.add(ME, "fresh")
        await env.poll()
        assert env.turns == [("conv-1", "fresh")]

    run(go())


def test_fresh_enable_resets_a_saved_cursor_to_the_newest_row(env: Env) -> None:
    env.chat.add(ME, "backlog")
    env.state["cursor"] = 0

    async def go() -> None:
        await env.bridge.start(fresh=True)
        for _ in range(100):
            await asyncio.sleep(0.02)
            if env.state["cursor"] == 1:
                break
        await env.bridge.stop()
        assert env.state["cursor"] == 1 and env.turns == []

    run(go())


def test_a_restart_resumes_from_the_saved_cursor_and_skips_stale_messages(env: Env) -> None:
    env.state["cursor"] = 0
    env.chat.add(ME, "an hour ago", age=3600)
    env.chat.add(ME, "just now", age=5)

    async def go() -> None:
        await env.poll()
        assert [t[1] for t in env.turns] == ["just now"]
        assert env.state["cursor"] == 2

    run(go())


def test_own_messages_are_never_acted_on_even_in_an_allowlisted_chat(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        env.chat.add(ME, "status", from_me=1)
        env.chat.add(ME, "do the thing", from_me=1)
        await env.poll()
        assert env.turns == [] and env.sent == [] and env.state.get("ignoredCount", 0) == 0

    run(go())


def test_tapbacks_are_skipped(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        env.chat.add(ME, "Liked “hello”", assoc=2000)
        env.chat.add(ME, "Loved “hello”", assoc=2001)
        env.chat.add(ME, "NULL type means a normal message", assoc=None)
        await env.poll()
        assert [t[1] for t in env.turns] == ["NULL type means a normal message"]

    run(go())


def test_a_stranger_is_ignored_silently_and_counted(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        env.chat.add(STRANGER, BODY)
        env.chat.add(STRANGER, "status")
        await env.poll()
        assert env.turns == [] and env.sent == []
        assert env.state["ignoredCount"] == 2 and env.state["lastIgnoredAt"] > 0

    run(go())


def test_group_chats_need_their_own_allowlist_entry(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        env.chat.add(ME, "hi group", group="chat123")
        await env.poll()
        assert env.turns == [] and env.state["ignoredCount"] == 1
        env.settings["imessageHandles"] = [ME, "chat123"]
        env.chat.add(ME, "hi again", group="chat123")
        await env.poll()
        assert [t[1] for t in env.turns] == ["hi again"]
        env.chat.add(STRANGER, "intruder", group="chat123")  # the group is allowed, this sender is not
        await env.poll()
        assert len(env.turns) == 1 and env.state["ignoredCount"] == 2

    run(go())


def test_attributed_body_only_messages_are_decoded_and_dispatched(env: Env) -> None:
    text = "typed on a new phone 🎉"
    body = HEAD + bytes([len(text.encode())]) + text.encode() + b"\x86\x84\x02iI"

    async def go() -> None:
        await env.arm()
        env.chat.add(ME, None, body=body)
        await env.poll()
        assert [t[1] for t in env.turns] == [text]

    run(go())


def test_attachment_only_gets_the_unsupported_reply(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        env.chat.add(ME, None, body=HEAD + b"\x03\xef\xbf\xbc", att=1)
        env.chat.add(ME, "", att=0)  # nothing at all: skipped
        await env.poll()
        assert env.turns == [] and env.texts() == ["Attachments aren't supported yet — send text."]

    run(go())


def test_a_message_is_started_in_a_texts_conversation_made_on_first_use(env: Env) -> None:
    env.convs.clear()

    async def go() -> None:
        await env.arm()
        await env.ask("hello grain")
        assert env.created == 1 and env.turns == [("texts-1", "hello grain")]
        assert env.state["textsConversationId"] == "texts-1" and env.state["homeChat"]["handle"] == ME
        await env.ask("again")
        assert env.created == 1 and env.turns[-1][0] == "texts-1"

    run(go())


def test_an_explicit_conversation_wins_over_texts(env: Env) -> None:
    env.convs["pinned"] = "My chat"
    env.settings["imessageConversationId"] = "pinned"

    async def go() -> None:
        await env.arm()
        await env.ask("hello")
        assert env.turns == [("pinned", "hello")] and env.created == 0
        await env.ask("new")
        assert env.created == 1 and "keeps getting your messages" in env.texts()[-1]
        await env.ask("again")
        assert env.turns[-1][0] == "pinned"
        env.settings["imessageConversationId"] = "deleted-id"  # a pin that no longer exists falls back
        await env.ask("once more")
        assert env.turns[-1][0] == "texts-1"

    run(go())


def test_help_status_stop_and_new(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        await env.ask("help")
        assert "status" in env.texts()[-1] and "stop" in env.texts()[-1]
        env.active = [{"run_id": "r9", "conversation_id": "conv-1", "started_at": time.time() - 125, "status": "running"},
                      {"run_id": "r8", "conversation_id": "other", "started_at": time.time(), "status": "running"}]
        await env.ask("status")
        assert env.texts()[-1].startswith("2 runs active. This chat: running for 2m")
        await env.ask("Stop.")
        assert env.stopped == ["conv-1"] and env.texts()[-1] == "Stopped."
        env.stop_result = False
        await env.ask("stop")
        assert env.texts()[-1] == "Nothing is running."
        await env.ask("new")
        assert env.created == 1 and env.texts()[-1] == "Started a new Texts conversation."
        await env.ask("hi")
        assert env.turns == [("texts-1", "hi")]
        assert not [t for t in env.turns if t[1] in ("help", "status", "stop", "new")]

    run(go())


def test_approval_text_and_single_yes(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        await env.ask("send the mail")
        env.pend("c1", args={"to": "a@b.co", "token": "sk-abcdefghijklmnop1234", "body": "x" * 30})
        await env.awaiting()
        msg = env.texts()[-1]
        assert msg.startswith("Approval needed [1]: send_email — external action\n") and msg.endswith("Reply yes or no")
        assert "sk-abcdefghijklmnop1234" not in msg and "to=a@b.co" in msg and len(msg.splitlines()[1]) <= 200
        await env.awaiting()  # the same card is never texted twice
        assert sum("Approval needed" in t for t in env.texts()) == 1
        await env.ask("yes")
        assert env.decisions == [("c1", "allow")] and env.texts()[-1] == "Approved."

    run(go())


def test_no_denies_and_a_bare_yes_with_nothing_waiting_is_a_normal_message(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        await env.ask("go")
        await env.ask("yes")  # nothing is waiting: Grain asked a question in chat, this is the answer
        assert [t[1] for t in env.turns] == ["go", "yes"]
        env.pend("c1")
        await env.awaiting()
        await env.ask("n")
        assert env.decisions == [("c1", "deny")] and env.texts()[-1] == "Denied."
        await env.ask("yes 7")
        assert env.texts()[-1] == "No pending approval with code 7."
        await env.ask("approve")
        assert env.texts()[-1] == "No approvals waiting."

    run(go())


def test_two_pending_need_codes_and_a_bare_yes_is_refused(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        await env.ask("go")
        env.pend("c1")
        await env.awaiting()
        env.pend("c2", tool="shell")
        await env.awaiting()
        assert env.texts()[-1].endswith("Reply yes 2 or no 2") and env.texts()[-1].startswith("Approval needed [2]: shell")
        await env.ask("yes")
        assert env.decisions == [] and "Codes: 1, 2" in env.texts()[-1]
        await env.ask("yes 2")
        assert env.decisions == [("c2", "allow")]
        await env.ask("no 1")
        assert env.decisions == [("c2", "allow"), ("c1", "deny")]

    run(go())


def test_a_forced_approval_always_needs_its_code(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        await env.ask("go")
        env.pend("c1", forced=True, args={"url": "https://x.dev"})
        await env.awaiting()
        assert "Reply yes 1 or no 1" in env.texts()[-1] and "extra confirmation" in env.texts()[-1]
        await env.ask("yes")
        assert env.decisions == [] and "yes 1" in env.texts()[-1]
        await env.ask("yes 1")
        assert env.decisions == [("c1", "allow")]

    run(go())


def test_a_decision_that_cannot_be_recorded_is_reported(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        await env.ask("go")
        env.pend("c1")
        await env.awaiting()

        def boom(call_id: str, decision: str) -> None:
            raise RuntimeError("404")

        env.bridge.deps.decide = boom
        await env.ask("yes")
        assert env.texts()[-1] == "That approval is no longer pending."

    run(go())


def test_approvals_of_other_conversations_are_only_texted_with_the_notify_toggle(env: Env) -> None:
    env.convs["other"] = "Other chat"

    async def go() -> None:
        await env.arm()
        await env.ask("go")
        env.pend("c9", conv="other", run_id="run-x")
        await env.awaiting("run-x", "other")
        assert not any("Approval needed" in t for t in env.texts())
        env.settings["imessageNotifyLongRuns"] = True
        await env.awaiting("run-x", "other")
        assert "Approval needed [1]" in env.texts()[-1]

    run(go())


def test_final_reply_is_flattened_and_sent_to_the_chat_it_came_from(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        await env.ask("question")
        env.messages["m1"] = "# Answer\n\nHere is **bold** and `code`.\n\n- a\n- b"
        env.bridge.on_run_change(FakeRun("run-1", replied=True, message_id="m1"))
        await env.settle()
        assert env.texts()[-1] == "Answer\n\nHere is bold and code.\n\n• a\n• b"
        assert env.sent[-1][3] == f"iMessage;-;{ME}" and env.sent[-1][2] == im.SCRIPT_CHAT
        env.bridge.on_run_change(FakeRun("run-1", replied=True, message_id="m1", live=False, ended_at=time.time()))
        await env.settle()
        assert sum("Answer" in t for t in env.texts()) == 1  # the end of the run does not repeat it

    run(go())


def test_long_reply_is_chunked_with_markers_and_capped(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        await env.ask("question")
        env.messages["m1"] = "A sentence goes here. " * 90  # ~2000 chars: two bubbles
        env.bridge.on_run_change(FakeRun("run-1", replied=True, message_id="m1"))
        await env.settle()
        parts = env.texts()[-2:]
        assert parts[0].startswith("(1/2) ") and parts[1].startswith("(2/2) ") and all(len(p) <= 1500 for p in parts)
        await env.ask("again")
        env.messages["m2"] = "Another sentence here. " * 800
        before = len(env.sent)
        env.bridge.on_run_change(FakeRun("run-2", replied=True, message_id="m2"))
        await env.settle()
        assert len(env.sent) - before == 4 and env.texts()[-1].endswith(im.TRUNCATED)

    run(go())


def test_empty_reply_and_error_reply(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        await env.ask("one")
        env.bridge.on_run_change(FakeRun("run-1", live=False, ended_at=time.time(), status="error", error="Provider said no\nstack trace"))
        await env.settle()
        assert env.texts()[-1] == im.RUN_ERROR and not any("Provider" in t for t in env.texts())
        await env.ask("two")
        env.bridge.on_run_change(FakeRun("run-2", replied=True, message_id=None))
        await env.settle()
        assert env.texts()[-1] == "Stopped."

    run(go())


def test_a_stop_by_text_does_not_also_send_the_partial_reply(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        await env.ask("long job")
        env.active = [{"run_id": "run-1", "conversation_id": "conv-1", "started_at": time.time(), "status": "running"}]
        await env.ask("stop")
        env.messages["m1"] = "half an answer"
        env.bridge.on_run_change(FakeRun("run-1", replied=True, message_id="m1"))
        await env.settle()
        assert env.texts()[-1] == "Stopped." and "half an answer" not in env.texts()

    run(go())


def test_long_run_notice_only_for_runs_not_started_by_text(env: Env) -> None:
    env.settings["imessageNotifyLongRuns"] = True
    env.convs["conv-2"] = "Quarterly report"

    async def go() -> None:
        await env.arm()
        await env.ask("hello")  # sets the home chat and holds the lock
        now = time.time()
        env.bridge.on_run_change(FakeRun("ui-1", "conv-2", started_at=now - 400, ended_at=now, live=False, status="done"))
        env.bridge.on_run_change(FakeRun("ui-2", "conv-2", started_at=now - 30, ended_at=now, live=False, status="done"))
        env.bridge.on_run_change(FakeRun("run-1", "conv-1", started_at=now - 900, ended_at=now, live=False, status="done"))  # ours
        await env.settle()
        assert [t for t in env.texts() if t.startswith("Grain finished")] == ["Grain finished: Quarterly report (7 min)"]
        env.settings["imessageNotifyLongRuns"] = False
        env.bridge.on_run_change(FakeRun("ui-3", "conv-2", started_at=now - 900, ended_at=now, live=False, status="done"))
        await env.settle()
        assert sum(t.startswith("Grain finished") for t in env.texts()) == 1

    run(go())


def test_the_long_run_notice_falls_back_to_the_first_handle_without_a_home_chat(env: Env) -> None:
    env.settings["imessageNotifyLongRuns"] = True

    async def go() -> None:
        await env.arm()
        now = time.time()
        env.bridge.on_run_change(FakeRun("ui-1", started_at=now - 500, ended_at=now, live=False))
        await env.settle()
        assert env.sent[-1][2] == im.SCRIPT_PARTICIPANT and env.sent[-1][3] == ME

    run(go())


def test_send_falls_back_to_the_participant_script(env: Env) -> None:
    env.rc["chat"] = 1

    async def go() -> None:
        await env.arm()
        await env.ask("help")
        assert [a[2] for a in env.sent] == [im.SCRIPT_CHAT, im.SCRIPT_PARTICIPANT]
        assert env.sent[1][3] == ME and env.sent[0][4] == env.sent[1][4]

    run(go())


def test_message_text_cannot_inject_into_the_script(env: Env) -> None:
    nasty = '" & do shell script "rm -rf ~" & "'

    async def go() -> None:
        await env.arm()
        await env.ask("go")
        env.messages["m1"] = nasty
        env.bridge.on_run_change(FakeRun("run-1", replied=True, message_id="m1"))
        await env.settle()
        argv = env.sent[-1]
        assert argv[:2] == ["osascript", "-e"] and argv[2] in (im.SCRIPT_CHAT, im.SCRIPT_PARTICIPANT)
        assert argv[4] == MARK + nasty and "rm -rf" not in argv[2]
        assert "rm -rf" not in im.SCRIPT_CHAT and "rm -rf" not in im.SCRIPT_PARTICIPANT
        assert "on run argv" in argv[2] and "item 2 of argv" in argv[2]
        run_dash = im._argv(im.SCRIPT_CHAT, "t", "-5 degrees")
        assert run_dash[4] == " -5 degrees"  # never a bare leading dash an option parser could take

    run(go())


def test_inbound_rate_limit_warns_once_then_goes_quiet(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        for i in range(12):
            env.chat.add(ME, f"m{i}")
        await env.poll()
        assert len(env.turns) == 10
        assert env.texts() == ["Slow down: too many messages in the last minute. I'll pick up again shortly."]

    run(go())


def test_outbound_pacing_drops_past_twenty_a_minute(env: Env) -> None:
    async def go() -> None:
        to = im.Target(f"iMessage;-;{ME}", ME)
        results = [await env.bridge._send(to, f"n{i}") for i in range(22)]
        assert results.count(True) == 20 and len(env.sent) == 20

    run(go())


def test_second_bridge_on_the_same_data_dir_is_locked_until_the_first_stops(env: Env) -> None:
    other = im.IMessageBridge(env.deps(), poll_seconds=0.02, send_gap=0)

    async def go() -> None:
        await env.arm()
        await other.poll_once()
        assert other.status_code == "locked"
        other.on_run_change(FakeRun("x", started_at=time.time() - 900, ended_at=time.time(), live=False))  # not the poller: silent
        await env.bridge.stop()
        await other.poll_once()
        assert other.status_code != "locked" and other._lock_fd is not None
        await other.stop()
        assert other._lock_fd is None

    run(go())


def _wait(pred: Any, what: str, timeout: float = 5.0) -> Any:
    async def go() -> None:
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return
            await asyncio.sleep(0.02)
        raise AssertionError(f"timed out waiting for {what}")
    return go()


def test_poller_task_runs_and_reports_status(env: Env) -> None:
    async def go() -> None:
        assert env.bridge.status()["status"] == "off"
        await env.bridge.start(fresh=True)
        await _wait(lambda: "cursor" in env.state, "the first poll")
        env.chat.add(ME, "via the task")
        await _wait(lambda: env.turns, "the message")
        s = env.bridge.status()
        assert s["enabled"] and s["running"] and s["status"] == "running" and s["fda_ok"] is True and s["last_poll_at"]
        assert s["target_conversation"] == {"id": "conv-1", "title": "Texts"}
        await env.bridge.stop()
        assert env.bridge.status()["status"] == "off" and not env.bridge.status()["running"]

    run(go())


def test_reconcile_follows_the_toggle(env: Env) -> None:
    env.settings["imessageEnabled"] = False

    async def go() -> None:
        await env.bridge.reconcile()
        assert not env.bridge.status()["running"]
        env.settings["imessageEnabled"] = True
        await env.bridge.reconcile()
        assert env.bridge.status()["running"]
        env.settings["imessageEnabled"] = False
        await env.bridge.reconcile()
        assert env.bridge.status()["status"] == "off"

    run(go())


def test_missing_database_means_full_disk_access(env: Env, tmp_path: Path) -> None:
    with pytest.raises(im.NeedsFullDiskAccess):
        im.fetch_since(str(tmp_path / "nope" / "chat.db"), 0)
    with pytest.raises(im.NeedsFullDiskAccess):
        im.max_rowid(str(tmp_path / "nope" / "chat.db"))
    env.bridge.deps.chat_db_path = str(tmp_path / "nope" / "chat.db")

    async def go() -> None:
        await env.bridge.start()
        await _wait(lambda: env.bridge.status()["status"] == "needs_full_disk_access", "the FDA status")
        assert env.bridge.status()["fda_ok"] is False
        await env.bridge.stop()

    run(go())


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads through chmod 000")
def test_unreadable_database_means_full_disk_access(env: Env) -> None:
    os.chmod(env.chat.path, 0)
    try:
        with pytest.raises(im.NeedsFullDiskAccess):
            im.fetch_since(env.chat.path, 0)
    finally:
        os.chmod(env.chat.path, 0o600)


def test_other_database_errors_back_off_instead_of_dying(env: Env, tmp_path: Path) -> None:
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is not a sqlite file " * 100)
    env.bridge.deps.chat_db_path = str(junk)

    async def go() -> None:
        await env.bridge.start()
        await _wait(lambda: env.bridge.status()["status"] == "error", "the error status")
        assert env.bridge.status()["last_error"] and env.bridge.status()["running"]
        await env.bridge.stop()

    run(go())


def test_the_reader_never_writes_or_keeps_the_file_open(env: Env) -> None:
    env.chat.add(ME, "hello")
    rows = im.fetch_since(env.chat.path, 0)
    assert rows[0]["text"] == "hello" and rows[0]["handle"] == ME and rows[0]["group"] is False
    c = sqlite3.connect(env.chat.path)
    c.execute("INSERT INTO handle(id) VALUES('x')")  # would block if the reader still held a lock
    c.commit()
    c.close()
    ro = im._open_ro(env.chat.path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("INSERT INTO handle(id) VALUES('y')")
    finally:
        ro.close()


def test_a_message_whose_chat_join_has_not_landed_yet_is_retried(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        env.chat.add(ME, "joined late", join=False, age=1)
        await env.poll()
        assert env.turns == []
        c = sqlite3.connect(env.chat.path)
        c.execute("INSERT INTO chat_message_join SELECT c.ROWID, 1 FROM chat c LIMIT 1")
        c.commit()
        c.close()
        await env.poll()
        assert [t[1] for t in env.turns] == ["joined late"]

    run(go())


def test_send_test_only_reaches_allowlisted_handles(env: Env) -> None:
    async def go() -> None:
        assert (await env.bridge.send_test(STRANGER)) == {"ok": False, "error": "not_allowlisted"}
        assert env.sent == []
        assert (await env.bridge.send_test()) == {"ok": True, "to": "handle"}
        assert env.sent[-1][2] == im.SCRIPT_PARTICIPANT and env.sent[-1][3] == ME and env.sent[-1][4] == MARK + "Grain is connected ✅"
        assert (await env.bridge.send_test("(555) 123-4567")) == {"ok": True, "to": "handle"}
        env.settings["imessageHandles"] = []
        assert (await env.bridge.send_test()) == {"ok": False, "error": "no_self_chat"}
        assert (await env.bridge.send_test(ME)) == {"ok": False, "error": "not_allowlisted"}

    run(go())


def test_logs_never_carry_bodies_or_full_handles(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    async def go() -> None:
        await env.arm()
        env.chat.add(STRANGER, BODY)
        env.chat.add(ME, None, body=HEAD + bytes([len(BODY)]) + BODY.encode())
        env.chat.add(ME, "status")
        env.chat.add(ME, BODY, group=None)
        await env.poll()
        env.pend("c1", args={"note": BODY})
        await env.awaiting()
        await env.ask("yes")
        env.messages["m1"] = BODY
        env.bridge.on_run_change(FakeRun("run-4", replied=True, message_id="m1"))
        await env.settle()
        await env.bridge.send_test()
        env.bridge.deps.chat_db_path = "/nonexistent/chat.db"
        await env.bridge.start()
        await asyncio.sleep(0.1)
        await env.bridge.stop()

    run(go())
    assert any("imessage" in r.getMessage() for r in caplog.records), "the bridge logged nothing, so this proves nothing"
    blob = "\n".join(r.getMessage() + (r.exc_text or "") for r in caplog.records)
    for secret in ("ZEBRA", "keep this out", "15551234567", "5551234567", "15559998888", "5559998888", "iMessage;-;"):
        assert secret not in blob, secret


# ---------------------------------------------------------------- review fixes

def test_staleness_filter_holds_across_batches_until_caught_up(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(im, "BATCH", 2)
    env.state["cursor"] = 0
    for i in range(3):
        env.chat.add(ME, f"old {i}", age=3600)

    async def go() -> None:
        await env.poll()  # a full batch: still replaying
        assert env.bridge._stale_before is not None
        await env.poll()  # short batch: caught up
        assert env.turns == [] and env.state["cursor"] == 3 and env.bridge._stale_before is None
        env.chat.add(ME, "live")
        await env.poll()
        assert [t[1] for t in env.turns] == ["live"]

    run(go())


def test_a_failed_first_poll_keeps_the_fresh_flag_and_the_age_filter(env: Env, tmp_path: Path) -> None:
    good = env.chat.path
    env.chat.add(ME, "backlog")
    env.state["cursor"] = 0
    env.bridge.deps.chat_db_path = str(tmp_path / "nope" / "chat.db")

    async def go() -> None:
        env.bridge._fresh = True
        with pytest.raises(im.NeedsFullDiskAccess):
            await env.bridge.poll_once()
        assert env.bridge._fresh is True and env.bridge._stale_before is not None and env.state["cursor"] == 0
        env.bridge.deps.chat_db_path = good
        await env.bridge.poll_once()
        assert env.state["cursor"] == 1 and env.turns == []  # the backlog was skipped, not replayed from the old cursor

    run(go())


def test_a_database_that_shrank_resets_the_cursor(env: Env) -> None:
    env.chat.add(ME, "one")
    env.chat.add(ME, "two")
    env.state["cursor"] = 500

    async def go() -> None:
        await env.poll()
        assert env.state["cursor"] == 2 and env.turns == []
        env.chat.add(ME, "three")
        await env.poll()
        assert [t[1] for t in env.turns] == ["three"]

    run(go())


def test_our_own_sends_coming_back_as_inbound_text_are_ignored(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        await env.bridge.send_test()
        env.chat.add(ME, "Grain is connected ✅")
        env.messages["m1"] = "A sentence goes here. " * 90
        await env.ask("go")
        env.bridge.on_run_change(FakeRun("run-1", replied=True, message_id="m1"))
        await env.settle()
        first_part = env.texts()[-2]
        assert first_part.startswith("(1/2) ")
        env.chat.add(ME, first_part)
        before = len(env.turns)
        await env.poll()
        assert len(env.turns) == before == 1  # only "go" ever started a turn
        assert env.state["ignoredCount"] == 2

    run(go())


def test_only_imessage_senders_are_heard_and_blobs_are_decoded_only_for_trusted_rows(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    decoded: list[int] = []
    real = im.decode_attributed_body
    monkeypatch.setattr(im, "decode_attributed_body", lambda b: decoded.append(1) or real(b))
    body = HEAD + bytes([5]) + b"hello"

    async def go() -> None:
        await env.arm()
        env.chat.add(ME, "spoofed", service="SMS")
        env.chat.add(STRANGER, None, body=body)
        env.chat.add(ME, None, body=body, service="SMS")
        await env.poll()
        assert env.turns == [] and env.state["ignoredCount"] == 3 and decoded == []
        env.chat.add(ME, None, body=body, service=None)  # message.service empty: the chat's service_name says iMessage
        await env.poll()
        assert [t[1] for t in env.turns] == ["hello"] and decoded == [1]

    run(go())


def test_lock_is_per_user_per_messages_database() -> None:
    import tempfile
    a, b = im.lock_path("/x/chat.db"), im.lock_path("/y/chat.db")
    assert a != b and a == im.lock_path("/x/chat.db") and a.parent == Path(tempfile.gettempdir()) and a.name.startswith("grain-imessage-")


def test_two_data_dirs_on_one_messages_database_cannot_both_poll(env: Env, tmp_path: Path) -> None:
    other_deps = env.deps()  # same chat_db_path, as a dev backend on a copied data dir would have
    other = im.IMessageBridge(other_deps, poll_seconds=0.02, send_gap=0)

    async def go() -> None:
        await env.arm()
        await other.poll_once()
        assert other.status_code == "locked"
        await env.bridge.stop()

    run(go())


def test_plans_and_questions_are_pointed_at_the_app_once_and_never_decided_by_text(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        await env.ask("go")
        env.pend("p1", tool="propose_plan", args={"title": "Plan"})
        env.pend("q1", tool="ask_user", args={"question": "Which?"})
        await env.awaiting()
        await env.awaiting()
        assert env.texts()[-2:] == ["propose_plan is waiting in Grain — open the app to answer.",
                                    "ask_user is waiting in Grain — open the app to answer."]
        assert sum("waiting in Grain" in t for t in env.texts()) == 2
        await env.ask("yes 1")
        assert env.decisions == [] and env.texts()[-1] == "No pending approval with code 1."
        await env.ask("yes")  # nothing is texted-and-live: an ordinary message
        assert env.decisions == [] and env.turns[-1][1] == "yes"

    run(go())


def test_only_the_changing_runs_live_approvals_are_texted(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        await env.ask("go")
        env.pend("c-other", run_id="run-2")
        env.pend("c-dead")
        env.dead.add("c-dead")
        await env.awaiting("run-1")
        assert not any("Approval needed" in t for t in env.texts())
        env.dead.clear()
        await env.awaiting("run-1")
        assert sum("Approval needed" in t for t in env.texts()) == 1 and "send_email" in env.texts()[-1]

    run(go())


def test_a_decision_for_a_run_that_ended_says_so(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        await env.ask("go")
        env.pend("c1")
        await env.awaiting()
        env.decide_live = False
        await env.ask("yes")
        assert env.texts()[-1] == "That approval is no longer waiting (the run ended)."
        env.pend("c2")
        env.dead.add("c2")
        await env.awaiting()
        assert not any("[2]" in t for t in env.texts())

    run(go())


def test_secrets_are_scrubbed_before_truncation_and_shortened_cards_need_the_code(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        await env.ask("go")
        env.pend("c1", args={"note": "x" * 62 + " sk-abcdefghijklmnop1234 tail", "to": "a@b.co"})
        await env.awaiting()
        msg = env.texts()[-1]
        assert "sk-" not in msg and "abcdefghij" not in msg
        assert "shortened — check Grain for the full action" in msg and msg.endswith("Reply yes 1 or no 1")
        await env.ask("yes")
        assert env.decisions == [] and "yes 1" in env.texts()[-1]
        await env.ask("yes 1")
        assert env.decisions == [("c1", "allow")]

    run(go())


def test_an_approval_is_only_marked_texted_when_the_send_worked(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        await env.ask("go")
        env.pend("c1")
        env.rc.update(chat=1, participant=1)
        await env.awaiting()
        assert env.bridge._texted.seen == {} and env.bridge._code_seq == 0
        env.rc.update(chat=0, participant=0)
        await env.awaiting()
        assert "Approval needed [1]" in env.texts()[-1] and "c1" in env.bridge._texted.seen

    run(go())


def test_codes_start_at_a_random_base_per_process(env: Env) -> None:
    bases = {im.IMessageBridge(env.deps())._code_seq for _ in range(40)}
    assert all(10 <= b <= 80 for b in bases) and len(bases) > 1


def test_a_yes_typed_before_the_request_is_not_an_answer(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        await env.ask("go")
        env.pend("c1")
        await env.awaiting()
        await env.ask("yes", age=120)
        assert env.decisions == [] and env.texts()[-1].startswith("Reply again:")
        await env.ask("yes")
        assert env.decisions == [("c1", "allow")]

    run(go())


def test_an_answer_shaped_message_while_a_card_waits_is_not_steered(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        await env.ask("go")
        env.pend("c1")
        await env.awaiting()
        turns = len(env.turns)
        await env.ask("yes please do that")
        assert len(env.turns) == turns and env.decisions == []
        assert env.texts()[-1] == "Reply yes or no (or yes <code>) to the approval first."
        await env.ask("Nothing else, thanks")  # not an answer word: a normal message
        assert len(env.turns) == turns + 1

    run(go())


def test_home_chat_comes_only_from_one_to_one_chats_and_must_stay_allowlisted(env: Env) -> None:
    env.settings["imessageHandles"] = [ME, "chat123"]
    env.settings["imessageNotifyLongRuns"] = True

    async def go() -> None:
        await env.arm()
        env.chat.add(ME, "from the group", group="chat123")
        await env.poll()
        assert "homeChat" not in env.state
        await env.ask("hello")
        assert env.state["homeChat"] == {"guid": f"iMessage;-;{ME}", "handle": ME}
        env.settings["imessageHandles"] = ["+15550001111"]  # the home handle was removed
        env.bridge.on_run_change(FakeRun("ui-1", started_at=time.time() - 500, ended_at=time.time(), live=False))
        await env.settle()
        assert env.sent[-1][2] == im.SCRIPT_PARTICIPANT and env.sent[-1][3] == "+15550001111"
        env.settings["imessageHandles"] = []
        n = len(env.sent)
        env.bridge.on_run_change(FakeRun("ui-2", started_at=time.time() - 500, ended_at=time.time(), live=False))
        await env.settle()
        assert len(env.sent) == n

    run(go())


def test_rows_without_a_chat_join_are_retried_only_while_fresh(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        env.chat.add(ME, "old and orphaned", join=False, age=100)
        env.chat.add(ME, "no date and orphaned", join=False, date=None)
        env.chat.add(ME, "far future orphan", join=False, age=-3600)
        last = env.chat.add(ME, "after")
        await env.poll()
        assert [t[1] for t in env.turns] == ["after"] and env.state["cursor"] == last
        env.chat.add(ME, "fresh orphan", join=False, age=-30)  # clock a little ahead: still within the retry window
        env.chat.add(ME, "behind it")
        before = env.state["cursor"]
        await env.poll()
        assert env.state["cursor"] == before and len(env.turns) == 1

    run(go())


def test_a_row_that_blows_up_apologises_and_the_cursor_moves_on(env: Env) -> None:
    async def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("boom")

    async def go() -> None:
        await env.arm()
        env.bridge._command = boom  # type: ignore[method-assign]
        env.chat.add(ME, "help")
        last = env.chat.add(STRANGER, "knock")
        env.chat.add(ME, "after")
        await env.poll()
        assert env.texts()[0] == "Grain couldn't take that message — try again." and env.state["cursor"] == last + 1
        assert [t[1] for t in env.turns] == ["after"]

    run(go())


def test_cursor_is_saved_per_row_and_ignored_counts_once_per_poll(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        env.saves.clear()
        a = env.chat.add(STRANGER, "one")
        env.chat.add(STRANGER, "two")
        c = env.chat.add(STRANGER, "three")
        await env.poll()
        cursors = [s["cursor"] for s in env.saves]
        assert [x for i, x in enumerate(cursors) if i == 0 or x != cursors[i - 1]] == [a, a + 1, c]  # every row moved it
        assert sum(1 for s in env.saves if s.get("ignoredCount") == 3) == 1 and not any(s.get("ignoredCount") in (1, 2) for s in env.saves)

    run(go())


def test_stop_and_no_are_never_rate_limited(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        for i in range(12):
            env.chat.add(ME, f"m{i}")
        await env.poll()
        assert len(env.turns) == 10
        env.pend("c1")
        await env.awaiting()
        await env.ask("stop")
        assert env.stopped == ["conv-1"] and env.texts()[-1] == "Stopped."
        await env.ask("no")
        assert env.decisions == [("c1", "deny")]
        await env.ask("status")  # everything else is still limited, silently
        assert env.texts()[-1] == "Denied."

    run(go())


def test_interrupted_runs_text_nothing_and_a_stopped_bridge_is_silent(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        await env.ask("go")
        env.messages["m1"] = "partial"
        env.bridge.on_run_change(FakeRun("run-1", replied=True, message_id="m1", live=False, status="interrupted", ended_at=time.time()))
        await env.settle()
        assert env.sent == []
        await env.ask("again")
        await env.bridge.stop()
        env.bridge.on_run_change(FakeRun("run-2", replied=True, message_id="m1"))
        await env.settle()
        assert env.sent == []

    run(go())


def test_open_full_disk_access_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    def timeout(*a: Any, **k: Any) -> Any:
        raise subprocess.TimeoutExpired("open", 10)

    def missing(*a: Any, **k: Any) -> Any:
        raise FileNotFoundError("open")

    monkeypatch.setattr(im.subprocess, "run", timeout)
    assert im.open_full_disk_access() is False
    monkeypatch.setattr(im.subprocess, "run", missing)
    assert im.open_full_disk_access() is False


# ---------------------------------------------------------------- the note-to-self chat

@pytest.fixture
def senv(tmp_path: Path) -> Env:
    """An allowlisted user whose own number is the confirmed self chat."""
    return Env(tmp_path, imessageSelfChatGuid=SELF_GUID)


def test_a_self_chat_text_from_me_is_a_turn_and_the_reply_goes_back_to_that_chat_with_the_marker(senv: Env) -> None:
    async def go() -> None:
        await senv.arm()
        senv.chat.add(ME, "plan my week", from_me=1)
        await senv.poll()
        assert senv.turns == [("conv-1", "plan my week")]
        assert senv.state["homeChat"] == {"guid": SELF_GUID, "handle": ME}
        senv.messages["m1"] = "Here you go"
        senv.bridge.on_run_change(FakeRun("run-1", replied=True, message_id="m1"))
        await senv.settle()
        assert senv.sent[-1][2] == im.SCRIPT_CHAT and senv.sent[-1][3] == SELF_GUID
        assert senv.sent[-1][4] == MARK + "Here you go"

    run(go())


def test_from_me_rows_in_any_other_chat_are_still_ignored(senv: Env) -> None:
    async def go() -> None:
        await senv.arm()
        senv.chat.add(STRANGER, "do the thing", from_me=1)  # the user texting a friend
        senv.chat.add(ME, "status", from_me=1, group="chat123")  # a group is never the self chat
        await senv.poll()
        assert senv.turns == [] and senv.sent == [] and senv.state.get("ignoredCount", 0) == 0

    run(go())


@pytest.mark.parametrize("guid", [None, "iMessage;-;+19998887777"])
def test_a_self_looking_chat_that_is_not_confirmed_is_not_self(tmp_path: Path, guid: str | None) -> None:
    e = Env(tmp_path, imessageSelfChatGuid=guid)

    async def go() -> None:
        await e.arm()
        e.chat.add(ME, "do the thing", from_me=1)
        await e.poll()
        assert e.turns == [] and e.sent == []
        e.chat.add(ME, "from a received copy")  # the normal allowlisted rules still apply
        await e.poll()
        assert [t[1] for t in e.turns] == ["from a received copy"]

    run(go())


def test_a_received_row_in_the_self_chat_needs_an_allowlisted_handle(senv: Env) -> None:
    async def go() -> None:
        await senv.arm()
        senv.chat.add(ME, "from my own handle", from_me=0)
        senv.chat.add(STRANGER, "from someone else", chat=SELF_GUID)
        await senv.poll()
        assert [t[1] for t in senv.turns] == ["from my own handle"]
        assert senv.state["ignoredCount"] == 1

    run(go())


def test_sms_in_the_self_chat_is_still_ignored(senv: Env) -> None:
    async def go() -> None:
        await senv.arm()
        senv.chat.add(ME, "spoofed", from_me=1, service="SMS")
        await senv.poll()
        assert senv.turns == []

    run(go())


def test_self_chat_rows_that_start_with_the_marker_are_skipped_even_behind_invisible_characters(senv: Env) -> None:
    async def go() -> None:
        await senv.arm()
        for t in (MARK + "hello", "﻿🌾hello", "​⁠ 🌾 hello", "￼🌾"):
            senv.chat.add(ME, t, from_me=1)
        senv.chat.add(ME, "hello 🌾 there", from_me=1)  # the marker mid-text is just a message
        await senv.poll()
        assert [t[1] for t in senv.turns] == ["hello 🌾 there"]

    run(go())


def test_a_changed_marker_is_what_the_self_chat_listens_for(senv: Env) -> None:
    senv.settings["imessageReplyMarker"] = "🤖 "

    async def go() -> None:
        await senv.arm()
        senv.chat.add(ME, "🤖 from grain", from_me=1)
        senv.chat.add(ME, "plain", from_me=1)
        await senv.poll()
        assert [t[1] for t in senv.turns] == ["plain"]
        await senv.bridge._send(im.Target(SELF_GUID, ME), "hi")
        assert senv.sent[-1][4] == "🤖 hi"

    run(go())


@pytest.mark.parametrize("marker", ["", "   ", None])
def test_an_empty_marker_falls_back_to_the_default(senv: Env, marker: Any) -> None:
    senv.settings["imessageReplyMarker"] = marker

    async def go() -> None:
        await senv.arm()
        await senv.bridge._send(im.Target(SELF_GUID, ME), "hi")
        assert senv.sent[-1][4] == MARK + "hi"
        senv.chat.add(ME, MARK + "hi", from_me=1)
        await senv.poll()
        assert senv.turns == []

    run(go())


def test_the_ledger_is_written_before_the_runner_is_called(senv: Env) -> None:
    seen: list[list[dict[str, Any]]] = []
    inner = senv.bridge.deps.runner

    async def spy(argv: list[str]) -> tuple[int, str]:
        seen.append(list(senv.state.get("sent") or []))
        return await inner(argv)

    senv.bridge.deps.runner = spy

    async def go() -> None:
        await senv.arm()
        await senv.bridge._send(im.Target(SELF_GUID, ME), "the answer")
        assert [e["h"] for e in seen[0]] == [im.text_hash("the answer")]
        assert seen[0][0]["chat"] == SELF_GUID and seen[0][0]["rowid"] is None and seen[0][0]["at"] > 0

    run(go())


def test_a_row_matching_the_ledger_by_guid_or_rowid_is_skipped(senv: Env) -> None:
    async def go() -> None:
        await senv.arm()
        by_guid = senv.chat.add(ME, "no marker, never hashed", from_me=1, guid="G-ours")
        by_rowid = senv.chat.add(ME, "another one", from_me=1)
        senv.state["sent"] = [{"h": "x", "chat": SELF_GUID, "at": time.time(), "rowid": None, "guid": "G-ours"},
                              {"h": "y", "chat": SELF_GUID, "at": time.time() - 90000, "rowid": by_rowid, "guid": None}]
        senv.chat.add(ME, "mine, really", from_me=1)
        await senv.poll()
        assert by_guid and [t[1] for t in senv.turns] == ["mine, really"]

    run(go())


def test_the_hash_catches_our_row_before_the_guid_lookup_has_run(senv: Env) -> None:
    senv.bridge.record_delays = ()  # the lookup never runs: only the hash can tell this row is ours

    async def go() -> None:
        await senv.arm()
        await senv.bridge._send(im.Target(SELF_GUID, ME), "Here is the answer")
        assert senv.state["sent"][-1]["rowid"] is None
        senv.chat.add(ME, "﻿Here is the answer  ", from_me=1)  # the marker was dropped on the way
        senv.chat.add(ME, MARK + "Here is the answer", from_me=0)  # and the received copy
        await senv.poll()
        assert senv.turns == []

    run(go())


def test_an_old_ledger_hit_no_longer_hides_a_message(senv: Env) -> None:
    async def go() -> None:
        await senv.arm()
        senv.state["sent"] = [{"h": im.text_hash("Approved."), "chat": SELF_GUID, "at": time.time() - 700, "rowid": None, "guid": None}]
        senv.chat.add(ME, "Approved.", from_me=1)
        await senv.poll()
        assert [t[1] for t in senv.turns] == ["Approved."]

    run(go())


def test_a_split_reply_is_never_ingested_even_when_a_part_loses_its_marker(senv: Env) -> None:
    senv.bridge.record_delays = ()

    async def go() -> None:
        await senv.arm()
        await senv.bridge._send_all(im.Target(SELF_GUID, ME), "A sentence goes here. " * 200)
        parts = [a[4] for a in senv.sent]
        assert len(parts) == 3 and all(p.startswith(MARK) for p in parts)
        senv.chat.add(ME, parts[0].removeprefix(MARK), from_me=1)  # Messages dropped this one's marker
        senv.chat.add(ME, parts[1], from_me=1)
        senv.chat.add(ME, parts[2], from_me=0)
        await senv.poll()
        assert senv.turns == []

    run(go())


def test_our_sent_rows_get_their_rowid_and_guid_in_the_ledger(senv: Env) -> None:
    async def go() -> None:
        await senv.arm()
        await senv.bridge._send(im.Target(SELF_GUID, ME), "first")
        await senv.bridge._send(im.Target(SELF_GUID, ME), "second")
        await senv.settle()  # nothing in the database yet: the lookups find nothing and give up
        assert [e["rowid"] for e in senv.state["sent"]] == [None, None]
        a = senv.chat.add(ME, MARK + "second", from_me=1)
        senv.chat.add(STRANGER, MARK + "first", from_me=1)  # another chat's row is not ours
        await senv.bridge._record_rows(SELF_GUID, time.time() - 1)
        assert [(e["rowid"], e["guid"]) for e in senv.state["sent"]] == [(None, None), (a, f"msg-{a}")]

    run(go())


def test_the_ledger_is_pruned_by_age_and_capped(senv: Env) -> None:
    old = [{"h": "o", "chat": None, "at": time.time() - 90000, "rowid": None, "guid": None}]
    senv.state["sent"] = old + [{"h": f"{i}", "chat": None, "at": time.time(), "rowid": None, "guid": None} for i in range(520)]
    senv.bridge._ledger_add("new", None, time.time())
    sent = senv.state["sent"]
    assert len(sent) == im.LEDGER_CAP and sent[-1]["h"] == "new" and all(e["h"] != "o" for e in sent)


def test_the_same_phone_text_arriving_twice_is_one_turn(senv: Env) -> None:
    async def go() -> None:
        await senv.arm()
        senv.chat.add(ME, "remind me tomorrow", from_me=1)
        senv.chat.add(ME, "remind me tomorrow", from_me=0)  # the received copy
        senv.chat.add(ME, "again", from_me=0)
        senv.chat.add(ME, "again", from_me=1)
        senv.chat.add(ME, "twice", from_me=1)  # a real repeat in the same direction is not a mirror
        senv.chat.add(ME, "twice", from_me=1)
        await senv.poll()
        assert [t[1] for t in senv.turns] == ["remind me tomorrow", "again", "twice", "twice"]

    run(go())


def test_the_loop_guard_pauses_the_bridge_and_a_toggle_clears_it(senv: Env) -> None:
    async def go() -> None:
        await senv.arm()
        for i in range(7):
            senv.chat.add(ME, f"ping {i}", from_me=1)
        last = senv.chat.add(ME, "after", from_me=1)
        await senv.poll()
        assert len(senv.turns) == im.IMessageBridge.LOOP_STREAK == 5
        assert senv.state["loopPaused"] > 0 and senv.state["cursor"] == last  # the cursor still moved
        assert senv.bridge.status()["status"] == "paused_loop_guard"
        assert await senv.bridge._send(im.Target(SELF_GUID, ME), "hi") is False
        assert await senv.bridge.send_test() == {"ok": False, "error": "paused_loop_guard"}
        assert senv.sent == []
        senv.chat.add(ME, "still paused", from_me=1)
        await senv.poll()
        assert len(senv.turns) == 5
        await senv.bridge.start(fresh=True)  # texting switched off and on
        await senv.bridge.stop()
        assert senv.state["loopPaused"] is None and senv.bridge.status()["status"] == "off"
        assert await senv.bridge._send(im.Target(SELF_GUID, ME), "hi") is True

    run(go())


def test_a_slow_back_and_forth_never_trips_the_loop_guard(senv: Env) -> None:
    senv.bridge.LOOP_GAP = 0.0  # every message starts a new streak

    async def go() -> None:
        await senv.arm()
        for i in range(8):
            senv.chat.add(ME, f"ping {i}", from_me=1)
        await senv.poll()
        assert len(senv.turns) == 8 and "loopPaused" not in senv.state

    run(go())


def test_send_test_goes_to_the_self_chat_and_falls_back_when_none_is_set(senv: Env) -> None:
    async def go() -> None:
        assert await senv.bridge.send_test() == {"ok": True, "to": "self_chat"}
        assert senv.sent[-1][2] == im.SCRIPT_CHAT and senv.sent[-1][3] == SELF_GUID
        assert senv.sent[-1][4] == MARK + "Grain is connected ✅"
        senv.settings["imessageSelfChatGuid"] = None
        assert await senv.bridge.send_test() == {"ok": True, "to": "handle"}
        assert senv.sent[-1][2] == im.SCRIPT_PARTICIPANT and senv.sent[-1][3] == ME
        senv.settings["imessageSelfChatGuid"] = SELF_GUID
        senv.rc["chat"] = 1
        senv.rc["participant"] = 1
        assert await senv.bridge.send_test() == {"ok": False, "to": "self_chat", "error": "send_failed"}

    run(go())


def test_proactive_texts_go_to_the_self_chat(senv: Env) -> None:
    senv.settings["imessageNotifyLongRuns"] = True
    senv.state["homeChat"] = {"guid": "iMessage;-;+15550001111", "handle": ME}  # an older 1:1 chat is overridden

    async def go() -> None:
        await senv.arm()
        now = time.time()
        senv.bridge.on_run_change(FakeRun("ui-1", started_at=now - 500, ended_at=now, live=False, status="done"))
        await senv.settle()
        assert senv.sent[-1][2] == im.SCRIPT_CHAT and senv.sent[-1][3] == SELF_GUID and senv.sent[-1][4].startswith(MARK + "Grain finished")

    run(go())


def test_status_reports_the_self_chat_masked(senv: Env, tmp_path: Path) -> None:
    assert senv.bridge.status()["self_chat"] == {"guid": SELF_GUID, "handle": im.mask_handle(ME)}
    (tmp_path / "plain").mkdir()
    assert Env(tmp_path / "plain").bridge.status()["self_chat"] == {"guid": None, "handle": None}


def test_the_marker_is_on_every_part_of_a_split_reply_and_on_approval_prompts(env: Env) -> None:
    async def go() -> None:
        await env.arm()
        await env.ask("go")
        env.pend("c1")
        await env.awaiting()
        assert env.sent[-1][4].startswith(MARK + "Approval needed")
        env.messages["m1"] = "A sentence goes here. " * 90
        before = len(env.sent)
        env.bridge.on_run_change(FakeRun("run-1", replied=True, message_id="m1"))
        await env.settle()
        assert len(env.sent) - before == 2 and all(a[4].startswith(MARK + "(") for a in env.sent[before:])
        await env.ask("help")
        assert env.sent[-1][4] == MARK + im.HELP

    run(go())


def test_no_bodies_or_full_handles_in_the_logs_of_a_self_chat_flow(senv: Env, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    async def go() -> None:
        await senv.arm()
        senv.chat.add(ME, BODY, from_me=1)
        senv.chat.add(ME, BODY, from_me=0)  # mirror
        senv.chat.add(ME, MARK + BODY, from_me=1)
        senv.messages["m1"] = BODY
        await senv.poll()
        senv.bridge.on_run_change(FakeRun("run-1", replied=True, message_id="m1"))
        await senv.settle()
        senv.chat.add(ME, BODY, from_me=1)  # its own reply coming back, bodies and all
        senv.chat.add(ME, BODY + " more", from_me=1)
        senv.chat.add(ME, "x", from_me=1)
        await senv.poll()
        for _ in range(6):
            senv.chat.add(ME, f"{BODY} {_}", from_me=1)
        await senv.poll()  # trips the loop guard
        assert senv.bridge.status()["status"] == "paused_loop_guard"

    run(go())
    assert caplog.records
    for r in caplog.records:
        blob = r.getMessage() + repr(r.args)
        assert "ZEBRA" not in blob and ME not in blob and "5551234567" not in blob


def test_marker_check_never_decodes_untrusted_blobs_in_other_chats(senv: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    decoded: list[int] = []
    real = im.decode_attributed_body
    monkeypatch.setattr(im, "decode_attributed_body", lambda b: decoded.append(1) or real(b))

    async def go() -> None:
        await senv.arm()
        senv.chat.add(STRANGER, None, body=HEAD + bytes([5]) + b"hello")
        await senv.poll()
        assert decoded == []
        senv.chat.add(ME, None, from_me=1, body=HEAD + bytes([5]) + b"hello")
        await senv.poll()
        assert decoded == [1] and [t[1] for t in senv.turns] == ["hello"]

    run(go())


def test_a_self_chat_blob_from_an_unlisted_sender_is_never_decoded(senv: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    decoded: list[int] = []
    real = im.decode_attributed_body
    monkeypatch.setattr(im, "decode_attributed_body", lambda b: decoded.append(1) or real(b))

    async def go() -> None:
        await senv.arm()
        senv.chat.add(STRANGER, None, body=HEAD + bytes([5]) + b"hello", chat=SELF_GUID)
        await senv.poll()
        assert decoded == [] and senv.turns == []

    run(go())


def test_a_self_chat_row_seen_before_its_chat_join_waits_for_it(senv: Env) -> None:
    async def go() -> None:
        senv.chat.add(ME, "earlier", from_me=1)  # makes the self chat; history once armed
        await senv.arm()
        rid = senv.chat.add(ME, "late join", from_me=1, join=False)
        await senv.poll()
        assert senv.turns == [] and senv.state["cursor"] == rid - 1  # held, not skipped
        senv.chat.sql(f"INSERT INTO chat_message_join SELECT ROWID, {rid} FROM chat WHERE guid='{SELF_GUID}'")
        await senv.poll()
        assert [t[1] for t in senv.turns] == ["late join"]

    run(go())


def test_our_reply_coming_back_from_an_unlisted_own_address_is_not_counted_as_a_stranger(tmp_path: Path) -> None:
    own = "nate@icloud.com"  # the self chat's address, never added to the list
    e = Env(tmp_path, imessageSelfChatGuid=f"iMessage;-;{own}")

    async def go() -> None:
        await e.arm()
        e.chat.add(own, MARK + "a reply", from_me=0)
        await e.poll()
        assert e.turns == [] and e.state.get("ignoredCount", 0) == 0
        e.chat.add(STRANGER, "hi", chat=f"iMessage;-;{own}")
        await e.poll()
        assert e.state["ignoredCount"] == 1

    run(go())


# ---------------------------------------------------------------- finding the self chat

def _own_chat_db(tmp_path: Path, accounts: bool = False) -> ChatDB:
    return ChatDB(tmp_path / "chat.db", accounts=accounts)


def test_candidates_from_the_allowlist_without_account_columns(tmp_path: Path) -> None:
    db = _own_chat_db(tmp_path)
    db.add(ME, BODY, from_me=1, age=100)
    db.add(STRANGER, BODY, age=10)  # a friend: not ours
    db.add("+15550001111", BODY, group="chat9")  # a group: never
    got = im.self_chat_candidates(db.path, {ME})
    assert got == [{"guid": SELF_GUID, "handle": im.mask_handle(ME), "last_activity": got[0]["last_activity"],
                    "source": "allowlist", "best": True}]
    assert abs(got[0]["last_activity"] - (time.time() - 100)) < 5
    assert "ZEBRA" not in repr(got) and got[0]["handle"] == "…4567"


def test_candidates_from_account_addresses_with_one_flagged_best(tmp_path: Path) -> None:
    db = _own_chat_db(tmp_path, accounts=True)
    other = "+15557776666"
    db.add(ME, "a", from_me=1, age=500)
    db.add(other, BODY, from_me=1, age=5)  # more recent, but only known from the account columns
    db.add("nate@icloud.com", BODY, from_me=1, age=50)
    db.add(STRANGER, BODY, age=1)
    db.sql("UPDATE message SET destination_caller_id='P:+15557776666' WHERE ROWID=2")
    db.sql("UPDATE chat SET account_login='E:Nate@iCloud.com' WHERE guid='iMessage;-;nate@icloud.com'")
    db.sql("INSERT INTO chat(guid, style, chat_identifier, service_name) VALUES('SMS;-;+15557776666', 45, '+15557776666', 'SMS')")
    got = im.self_chat_candidates(db.path, {ME, other})
    assert sum(c["best"] for c in got) == 1 and got[0]["best"] and got[0]["guid"] == "iMessage;-;+15557776666"  # an account address that is also listed wins
    got = im.self_chat_candidates(db.path, {ME})
    assert got[0]["guid"] == "iMessage;-;+15557776666" and got[0]["source"] == "account"  # a listed non-account chat (maybe a friend) ranks below
    assert {c["guid"] for c in got} == {SELF_GUID, "iMessage;-;+15557776666", "iMessage;-;nate@icloud.com"}  # no SMS twin, no friend
    assert all(set(c) == {"guid", "handle", "last_activity", "source", "best"} for c in got) and "ZEBRA" not in repr(got)
    # with no allowlist hit, the most recent account chat is best
    got = im.self_chat_candidates(db.path, set())
    assert [c["guid"] for c in got if c["best"]] == ["iMessage;-;+15557776666"] and all(c["source"] == "account" for c in got)


def test_candidates_need_full_disk_access_when_the_database_cannot_be_opened(tmp_path: Path) -> None:
    with pytest.raises(im.NeedsFullDiskAccess):
        im.self_chat_candidates(str(tmp_path / "missing" / "chat.db"), {ME})
    env = Env(tmp_path)
    env.bridge.deps.chat_db_path = str(tmp_path / "missing" / "chat.db")
    assert env.bridge.self_chats() == {"error": "needs_full_disk_access"}
