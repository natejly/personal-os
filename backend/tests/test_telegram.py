"""The Telegram bridge on its own: owner-only filtering, pairing, commands, approvals, splitting, token hygiene, backoff.

Every Telegram call goes through a fake API caller installed on the bridge, so nothing here touches the network.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import re
import sqlite3
import sys
import time
import types
from pathlib import Path
from typing import Any, Callable

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import migrations  # noqa: E402
from personal_os import telegram as tg  # noqa: E402

TOKEN = "123456789:" + "AbC-dEf_GhI" * 3 + "xyz"
OWNER, STRANGER, BOT = 4242, 777, "grain_test_bot"
CONV = "conv-1"
_ids = itertools.count(1)


def msg(text: str | None, chat: int = OWNER, user: int | None = None, ctype: str = "private", date: float | None = None) -> dict[str, Any]:
    m: dict[str, Any] = {"message_id": next(_ids), "chat": {"id": chat, "type": ctype},
                         "from": {"id": chat if user is None else user, "first_name": "Nate", "username": "nate"},
                         "date": int(date if date is not None else time.time()) + 2}
    if text is not None:
        m["text"] = text
    return {"update_id": next(_ids), "message": m}


def tap(data: str, user: int = OWNER, chat: int = OWNER, text: str = "Approval needed [1]: send_email") -> dict[str, Any]:
    return {"update_id": next(_ids), "callback_query": {"id": f"cq{next(_ids)}", "from": {"id": user}, "data": data,
                                                         "message": {"message_id": 55, "text": text, "chat": {"id": chat}}}}


class Env:
    """The bridge with an in-memory settings/state store and a recording fake API."""

    def __init__(self, tmp: Path, paired: bool = True) -> None:
        self.settings: dict[str, Any] = {"telegramEnabled": True, "telegramNotifyLongRuns": False, "telegramLongRunMinutes": 3}
        self.state: dict[str, Any] = {"botId": 1, "botUsername": BOT, "textsConversationId": CONV}
        if paired:
            self.state.update(ownerChatId=OWNER, ownerUserId=OWNER, ownerName="Nate")
        self.token: str | None = TOKEN
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.updates: list[dict[str, Any]] = []
        self.fail: dict[str, Exception] = {}
        self.files: list[tuple[str, dict[str, Any]]] = []
        self.turns: list[tuple[str, str]] = []
        self.decisions: list[tuple[str, str]] = []
        self.stopped: list[str] = []
        self.pending: list[dict[str, Any]] = []
        self.live: set[str] = set()
        self.active: list[dict[str, Any]] = []
        self.texts: dict[str, str] = {}
        self.new_convs = 0
        self.bridge = tg.TelegramBridge(tg.Deps(
            get_settings=lambda: self.settings, load_state=lambda: self.state, save_state=self._save,
            get_token=lambda: self.token, start_turn=self._turn, stop=self._stop, decide=self._decide,
            pending_approvals=lambda: list(self.pending), is_live=lambda c: c in self.live, active_runs=lambda: self.active,
            create_texts_conversation=self._new_conv, conversation_exists=lambda c: True, message_text=lambda mid: self.texts.get(mid),
            conversation_title=lambda c: "Chat", api=self.api, lock_path=tmp / "t.lock"))

    def _save(self, st: dict[str, Any]) -> None:
        self.state = dict(st)

    async def _turn(self, conv: str, text: str) -> dict[str, Any]:
        self.turns.append((conv, text))
        return {"run_id": f"run-{len(self.turns)}"}

    def _stop(self, conv: str) -> bool:
        self.stopped.append(conv)
        return True

    async def _decide(self, call_id: str, decision: str) -> dict[str, Any]:
        self.decisions.append((call_id, decision))
        self.pending = [a for a in self.pending if a["call_id"] != call_id]
        self.live.discard(call_id)
        return {"live": True}

    def _new_conv(self) -> str:
        self.new_convs += 1
        return f"conv-new-{self.new_convs}"

    async def api(self, token: str, method: str, params: dict[str, Any], timeout: float | None = None, files: Any = None) -> Any:
        assert token == TOKEN
        self.calls.append((method, params))
        if files:
            self.files.append((method, files))
        if method in self.fail:
            raise self.fail[method]
        if method == "getUpdates":
            out, self.updates = self.updates, []
            return out
        if method == "getFile":
            return {"file_path": "photos/x.jpg", "file_size": 1234}
        return True

    # ---- helpers
    def sent(self) -> list[str]:
        return [p["text"] for m, p in self.calls if m == "sendMessage"]

    def of(self, method: str) -> list[dict[str, Any]]:
        return [p for m, p in self.calls if m == method]

    async def feed(self, *updates: dict[str, Any]) -> None:
        self.updates = list(updates)
        await self.bridge.poll_once()

    def approval(self, call_id: str, tool: str = "send_email", args: Any = None, forced: bool = False, live: bool = True) -> None:
        self.pending.append({"call_id": call_id, "run_id": "r1", "conversation_id": CONV, "tool": tool,
                             "args": args or {"to": "a@b.co"}, "danger": "external", "forced": forced})
        if live:
            self.live.add(call_id)

    def run(self, status: str = "awaiting_approval", live: bool = True, replied: bool = False, run_id: str = "r1", **kw: Any) -> Any:
        base = dict(run_id=run_id, conversation_id=CONV, kind="chat", started_at=time.time(), ended_at=None, live=live,
                    replied=replied, status=status, message_id=None, error=None)
        return types.SimpleNamespace(**{**base, **kw})


async def until(pred: Callable[[], Any], timeout: float = 3.0) -> Any:
    end = time.time() + timeout
    while time.time() < end:
        if v := pred():
            return v
        await asyncio.sleep(0.005)
    raise AssertionError("timed out")


def case(fn: Callable[..., Any]) -> Callable[[Path], None]:
    def wrapper(tmp_path: Path) -> None:
        async def go() -> None:
            env = Env(tmp_path)
            try:
                await fn(env)
            finally:
                await env.bridge.stop()
        asyncio.run(go())
    wrapper.__name__ = fn.__name__
    return wrapper


def case_unpaired(fn: Callable[..., Any]) -> Callable[[Path], None]:
    def wrapper(tmp_path: Path) -> None:
        async def go() -> None:
            env = Env(tmp_path, paired=False)
            try:
                await fn(env)
            finally:
                await env.bridge.stop()
        asyncio.run(go())
    wrapper.__name__ = fn.__name__
    return wrapper


# ---------------------------------------------------------------- owner-only filtering

@case
async def test_only_the_paired_private_chat_is_heard(env: Env) -> None:
    group = msg("hi from a group", chat=-100, user=OWNER, ctype="supergroup")
    other_user_same_chat = msg("not the owner", chat=OWNER, user=STRANGER)
    other_chat = msg("other chat", chat=STRANGER)
    channel = {"update_id": next(_ids), "channel_post": {"chat": {"id": OWNER, "type": "channel"}, "text": "post"}}
    edited = {"update_id": next(_ids), "edited_message": msg("edited")["message"]}
    stranger_tap = tap("ap:11", user=STRANGER)
    wrong_chat_tap = tap("ap:11", chat=STRANGER)
    env.approval("call-1")
    await env.feed(group, other_user_same_chat, other_chat, channel, edited, stranger_tap, wrong_chat_tap)
    assert env.turns == [] and env.decisions == []
    assert [m for m, _ in env.calls if m != "getUpdates"] == []  # not one reply, not one callback answer
    assert env.state["offset"] == wrong_chat_tap["update_id"] + 1  # but every update was consumed


@case
async def test_the_owner_is_heard(env: Env) -> None:
    await env.feed(msg("hello there"))
    assert env.turns == [(CONV, "hello there")]
    assert env.bridge._runs.seen.keys() == {"run-1"}


@case
async def test_old_messages_are_history_not_instructions(env: Env) -> None:
    await env.feed(msg("from before", date=time.time() - 3600))
    assert env.turns == []
    assert env.state["offset"] > 0


@case
async def test_attachment_only_messages_get_a_polite_no(env: Env) -> None:
    await env.feed(msg(None))
    assert env.turns == [] and env.sent() == [tg.UNSUPPORTED]


# ---------------------------------------------------------------- pairing

@case_unpaired
async def test_pairing_valid_code_pairs_once(env: Env) -> None:
    code = env.bridge.issue_pairing()["code"]
    assert len(code) == 8 and code.isalnum()
    await env.feed(msg(f"/start {code}", chat=OWNER, user=OWNER))
    assert (env.state["ownerChatId"], env.state["ownerUserId"], env.state["ownerName"]) == (OWNER, OWNER, "Nate")
    assert env.state["pairing"] is None
    assert env.sent() == ["Paired. Text me anything, or /help."]
    # Single use: the same code from someone else does nothing, and the owner stays the owner.
    await env.feed(msg(f"/start {code}", chat=STRANGER))
    assert env.state["ownerChatId"] == OWNER and env.sent() == ["Paired. Text me anything, or /help."]


@case_unpaired
async def test_pairing_accepts_the_botname_suffix(env: Env) -> None:
    code = env.bridge.issue_pairing()["code"]
    await env.feed(msg(f"/start@{BOT} {code}"))
    assert env.state["ownerChatId"] == OWNER


@case_unpaired
async def test_pairing_wrong_code_is_ignored_silently(env: Env) -> None:
    code = env.bridge.issue_pairing()["code"]
    await env.feed(msg("/start WRONGCODE"), msg("/start"), msg(code), msg("hello"), msg(f"/start {code} extra"))
    assert env.state.get("ownerChatId") is None and env.sent() == [] and env.turns == []
    assert env.state["pairing"]["code"] == code  # still usable


@case_unpaired
async def test_pairing_expired_code_is_ignored(env: Env) -> None:
    code = env.bridge.issue_pairing()["code"]
    env.state["pairing"]["expiresAt"] = time.time() - 1
    await env.feed(msg(f"/start {code}"))
    assert env.state.get("ownerChatId") is None and env.sent() == []
    assert env.bridge.status()["pairing"] is None


@case_unpaired
async def test_pairing_only_from_a_private_chat(env: Env) -> None:
    code = env.bridge.issue_pairing()["code"]
    await env.feed(msg(f"/start {code}", chat=-100, user=OWNER, ctype="group"))
    assert env.state.get("ownerChatId") is None and env.sent() == []


@case_unpaired
async def test_pairing_name_falls_back_to_the_username(env: Env) -> None:
    code = env.bridge.issue_pairing()["code"]
    u = msg(f"/start {code}")
    u["message"]["from"] = {"id": OWNER, "username": "nate_j"}
    await env.feed(u)
    assert env.state["ownerName"] == "@nate_j"


@case_unpaired
async def test_unpaired_bridge_never_starts_a_turn_or_answers_a_tap(env: Env) -> None:
    env.bridge.issue_pairing()
    await env.feed(msg("hello"), tap("ap:11"))
    assert env.turns == [] and env.decisions == [] and env.sent() == []


@case
async def test_adopting_another_bot_clears_the_owner_and_issues_a_code(env: Env) -> None:
    env.state["offset"] = 99
    env.bridge.adopt({"id": 1, "username": BOT})  # the same bot: nothing is lost
    assert env.state["ownerChatId"] == OWNER and env.state["offset"] == 99
    env.bridge.adopt({"id": 2, "username": "other_bot"})
    assert env.state.get("ownerChatId") is None and "offset" not in env.state
    assert env.state["botUsername"] == "other_bot" and len(env.state["pairing"]["code"]) == 8
    assert env.state["textsConversationId"] == CONV


# ---------------------------------------------------------------- commands

@case
async def test_commands_including_the_botname_suffix(env: Env) -> None:
    env.active = [{"run_id": "x", "conversation_id": CONV, "status": "running", "started_at": time.time() - 65}]
    await env.feed(msg("/help"), msg("/status"), msg(f"/status@{BOT}"), msg("/stop"), msg(f"/stop@{BOT}"), msg("/new"), msg(f"/new@{BOT}"),
                   msg("/start"), msg(f"/start@{BOT}"), msg("help"))
    sent = env.sent()
    assert sent[0] == tg.HELP and sent[-3:] == [tg.HELP] * 3  # /help, then /start, /start@bot and plain "help" at the end
    assert sent[1].startswith("1 run active. This chat: running for 1m ") and sent[1].endswith("0 approvals waiting.")
    assert sent[3:5] == ["Stopped.", "Stopped."] and env.stopped == [CONV, CONV]
    assert sent[5:7] == ["Started a new conversation."] * 2 and env.new_convs == 2
    assert env.state["textsConversationId"] == "conv-new-2"
    assert env.turns == []  # none of them reached the model


@case
async def test_stop_with_nothing_to_stop_still_answers(env: Env) -> None:
    env.state.pop("textsConversationId")
    await env.feed(msg("/stop"))
    assert env.sent() == ["Nothing is running."] and env.stopped == []


@case
async def test_a_stopped_run_sends_no_reply(env: Env) -> None:
    await env.feed(msg("work on it"))
    env.active = [{"run_id": "run-1", "conversation_id": CONV}]
    await env.feed(msg("/stop"))
    env.texts["m1"] = "late reply"
    env.bridge.on_run_change(env.run(status="stopped", live=False, message_id="m1", run_id="run-1"))
    await asyncio.sleep(0.05)
    assert env.sent() == ["Stopped."]
    assert env.bridge._typing == set()


@case
async def test_other_slash_text_is_a_normal_message(env: Env) -> None:
    await env.feed(msg("/status please"), msg("what is the status"))
    assert [t for _, t in env.turns] == ["/status please", "what is the status"]


# ---------------------------------------------------------------- approvals

async def card(env: Env, call_id: str = "call-1", **kw: Any) -> tuple[int, dict[str, Any]]:
    """Open an approval, announce it, and return (code, the sent message's params)."""
    if env.bridge._lock_fd is None:
        await env.feed()  # the poll takes the lock; only the lock holder sends
    n = len(env.of("sendMessage"))
    env.approval(call_id, **kw)
    env.bridge.on_run_change(env.run())
    await until(lambda: len(env.of("sendMessage")) > n)
    params = env.of("sendMessage")[-1]
    return int(re.search(r"\[(\d+)\]", params["text"]).group(1)), params  # type: ignore[union-attr]


@case
async def test_approval_card_has_buttons_and_no_secrets(env: Env) -> None:
    code, p = await card(env, args={"to": "a@b.co", "api_key": "sk-abcdefghijklmnopqrstuvwxyz123456"})
    assert p["chat_id"] == OWNER and p["text"].startswith(f"Approval needed [{code}]: send_email — external action\n")
    assert "to=a@b.co" in p["text"] and "sk-abcdefghijklmnopqrstuvwxyz123456" not in p["text"]
    assert p["reply_markup"] == {"inline_keyboard": [[{"text": "Approve", "callback_data": f"ap:{code}"},
                                                      {"text": "Deny", "callback_data": f"dn:{code}"}]]}
    assert "parse_mode" not in p


@case
async def test_approve_button_answers_the_query_and_edits_the_card(env: Env) -> None:
    code, p = await card(env)
    await env.feed(tap(f"ap:{code}", text=p["text"]))
    assert env.decisions == [("call-1", "allow")]
    ans = env.of("answerCallbackQuery")
    assert len(ans) == 1 and ans[0]["text"] == "Approved." and ans[0]["callback_query_id"].startswith("cq")
    edit = env.of("editMessageText")[0]
    assert edit["chat_id"] == OWNER and edit["message_id"] == 55 and edit["text"] == p["text"] + "\n\nApproved."
    assert "reply_markup" not in edit
    # a second tap on the same card: nothing is pending any more
    await env.feed(tap(f"ap:{code}", text=p["text"]))
    assert env.decisions == [("call-1", "allow")] and env.of("answerCallbackQuery")[1]["text"] == "No longer pending."
    assert env.of("editMessageText")[1]["text"].endswith("\n\nNo longer pending.")


@case
async def test_deny_button(env: Env) -> None:
    code, p = await card(env)
    await env.feed(tap(f"dn:{code}", text=p["text"]))
    assert env.decisions == [("call-1", "deny")]
    assert env.of("answerCallbackQuery")[0]["text"] == "Denied." and env.of("editMessageText")[0]["text"].endswith("\n\nDenied.")


@case
async def test_a_button_approve_needs_no_code_even_when_the_card_is_forced(env: Env) -> None:
    code, p = await card(env, forced=True)
    await env.feed(msg("yes"))
    assert env.decisions == [] and "needs its code" in env.sent()[-1]
    await env.feed(tap(f"ap:{code}", text=p["text"]))
    assert env.decisions == [("call-1", "allow")]


@case
async def test_garbage_and_unknown_callbacks_do_nothing_harmful(env: Env) -> None:
    code, _ = await card(env)
    await env.feed(tap("zz:1"), tap("ap:"), tap("ap:abc"), tap(f"ap:{code + 50}"))
    assert env.decisions == []
    assert [a["text"] for a in env.of("answerCallbackQuery")] == ["No longer pending."]  # only the well-formed unknown code


@case
async def test_yes_and_no_texts(env: Env) -> None:
    code, _ = await card(env)
    await env.feed(msg("yes"))
    assert env.decisions == [("call-1", "allow")] and env.sent()[-1] == "Approved."
    await card(env, "call-2")
    await env.feed(msg("No."))
    assert env.decisions[-1] == ("call-2", "deny") and env.sent()[-1] == "Denied."
    assert env.turns == []


@case
async def test_a_bare_yes_with_nothing_pending_is_a_normal_message(env: Env) -> None:
    await env.feed(msg("yes"))
    assert env.turns == [(CONV, "yes")] and env.decisions == []


@case
async def test_several_pending_need_codes(env: Env) -> None:
    c1, _ = await card(env, "call-1")
    c2, _ = await card(env, "call-2")
    assert c1 != c2
    await env.feed(msg("yes"))
    assert env.decisions == [] and f"{c1}, {c2}" in env.sent()[-1]
    await env.feed(msg(f"yes {c2}"))
    assert env.decisions == [("call-2", "allow")]
    await env.feed(msg(f"no {c1 + 100}"))
    assert env.sent()[-1] == f"No pending approval with code {c1 + 100}."
    await env.feed(msg("deny"))  # one left: a bare deny is fine
    assert env.decisions[-1] == ("call-1", "deny")


@case
async def test_an_answer_typed_before_the_card_existed_is_not_taken(env: Env) -> None:
    await card(env)
    await env.feed(msg("yes", date=time.time() - 120))
    assert env.decisions == [] and "before the approval request" in env.sent()[-1]


@case
async def test_text_while_a_card_waits_is_refused_not_steered(env: Env) -> None:
    await card(env)
    await env.feed(msg("yes please do that"))
    assert env.turns == [] and "Reply yes or no" in env.sent()[-1]


@case
async def test_cards_nobody_waits_on_and_other_chats_are_not_announced(env: Env) -> None:
    env.approval("orphan", live=False)
    env.pending[-1]["conversation_id"] = "elsewhere"
    env.approval("elsewhere-live")
    env.pending[-1]["conversation_id"] = "elsewhere"
    await env.feed()  # takes the poll lock, as the poller process does
    env.bridge.on_run_change(env.run())
    await asyncio.sleep(0.05)
    assert env.of("sendMessage") == []
    env.settings["telegramNotifyLongRuns"] = True  # everything is announced now, but the orphan still is not
    env.bridge.on_run_change(env.run())
    await until(lambda: env.of("sendMessage"))
    assert len(env.of("sendMessage")) == 1 and "send_email" in env.sent()[0]


@case
async def test_app_only_tools_point_at_the_app(env: Env) -> None:
    env.approval("plan-1", tool="propose_plan")
    await env.feed()
    env.bridge.on_run_change(env.run())
    await until(lambda: env.sent())
    assert env.sent() == ["propose_plan is waiting in Grain. Open the app to answer."]
    assert "reply_markup" not in env.of("sendMessage")[0]
    env.bridge.on_run_change(env.run())
    await asyncio.sleep(0.05)
    assert len(env.sent()) == 1  # told once


@case
async def test_a_process_without_the_poll_lock_sends_nothing(env: Env) -> None:
    env.approval("call-1")
    env.bridge.on_run_change(env.run())  # no poll yet: this process holds no lock
    await asyncio.sleep(0.05)
    assert env.sent() == []


# ---------------------------------------------------------------- replies

@case
async def test_the_reply_comes_back_as_plain_text(env: Env) -> None:
    await env.feed(msg("hello"))
    env.texts["m1"] = "## Title\n\nHello **there**, see [docs](https://x.co)."
    env.bridge.on_run_change(env.run(status="done", live=False, replied=True, message_id="m1", run_id="run-1"))
    await until(lambda: env.sent())
    assert env.sent() == ["Title\n\nHello there, see docs (https://x.co)."]
    env.bridge.on_run_change(env.run(status="done", live=False, replied=True, message_id="m1", run_id="run-1"))
    await asyncio.sleep(0.05)
    assert len(env.sent()) == 1  # once per run


@case
async def test_errors_and_interrupts(env: Env) -> None:
    await env.feed(msg("one"), msg("two"))
    env.bridge.on_run_change(env.run(status="error", live=False, error="secret stack trace", run_id="run-1"))
    env.bridge.on_run_change(env.run(status="interrupted", live=False, run_id="run-2"))
    await until(lambda: env.sent())
    await asyncio.sleep(0.05)
    assert env.sent() == [tg.RUN_ERROR]


@case
async def test_long_runs_notify_only_when_asked(env: Env) -> None:
    await env.feed()
    long_run = env.run(status="done", live=False, run_id="other", started_at=time.time() - 400, ended_at=time.time())
    env.bridge.on_run_change(long_run)
    await asyncio.sleep(0.05)
    assert env.sent() == []
    env.settings["telegramNotifyLongRuns"] = True
    env.bridge.on_run_change(env.run(status="done", live=False, run_id="other2", started_at=time.time() - 400, ended_at=time.time()))
    env.bridge.on_run_change(env.run(status="done", live=False, run_id="short", started_at=time.time() - 30, ended_at=time.time()))
    await until(lambda: env.sent())
    await asyncio.sleep(0.05)
    assert env.sent() == ["Grain finished: Chat (7 min)"]


@case
async def test_typing_indicator_runs_until_the_run_ends(env: Env) -> None:
    real = asyncio.sleep
    env.bridge._sleep = lambda d: real(0.005)
    await env.feed(msg("hello"))
    await until(lambda: len(env.of("sendChatAction")) >= 2)
    assert env.of("sendChatAction")[0] == {"chat_id": OWNER, "action": "typing"}
    env.texts["m1"] = "done"
    env.bridge.on_run_change(env.run(status="done", live=False, replied=True, message_id="m1", run_id="run-1"))
    await until(lambda: env.sent() == ["done"])
    n = len(env.of("sendChatAction"))
    await real(0.05)
    assert len(env.of("sendChatAction")) <= n + 1 and env.bridge._typing == set()


@case
async def test_rate_limit_warns_once_and_never_blocks_stop_or_deny(env: Env) -> None:
    await env.feed(*[msg(f"m{i}") for i in range(20)])
    assert len(env.turns) == 20 and env.sent() == []
    await env.feed(msg("one too many"), msg("and another"), msg("and again"))
    assert len(env.turns) == 20 and env.sent() == [tg.SLOW_DOWN]
    await env.feed(msg("/stop"))
    assert env.sent()[-1] in ("Stopped.", "Nothing is running.")
    env.approval("call-1")
    env.bridge._codes[5] = "call-1"
    env.bridge._texted.add("call-1", tg._Texted(5, False, time.time() - 10))
    await env.feed(msg("no"))
    assert env.decisions == [("call-1", "deny")]


# ---------------------------------------------------------------- formatting

def test_split_prefers_paragraphs_then_lines_then_sentences_then_words() -> None:
    a, b = "a" * 3000, "b" * 3000
    assert tg.split_reply(f"{a}\n\n{b}") == [a, b]
    assert tg.split_reply(f"{a}\n{b}") == [a, b]
    assert tg.split_reply(f"{a}. {b}") == [f"{a}.", b]
    assert tg.split_reply(f"{a} {b}") == [a, b]
    para = "word " * 1000
    parts = tg.split_reply(para)
    assert len(parts) == 2 and all(len(p) <= 4096 for p in parts) and " ".join(" ".join(parts).split()) == para.strip()


def test_split_hard_cuts_when_there_is_no_break_and_leaves_short_text_alone() -> None:
    assert tg.split_reply("hello") == ["hello"] and tg.split_reply("   ") == []
    parts = tg.split_reply("x" * 5000)
    assert [len(p) for p in parts] == [4096, 904]
    assert tg.split_reply("y" * 4096) == ["y" * 4096]


def test_split_caps_the_part_count_with_a_pointer_to_the_app() -> None:
    parts = tg.split_reply("para one\n\n" * 20000)
    assert len(parts) == 8 and parts[-1].endswith(tg.TRUNCATED) and all(len(p) <= 4096 for p in parts)
    full = tg.split_reply("z" * 4096 * 9)
    assert len(full) == 8 and len(full[-1]) <= 4096 and full[-1].endswith(tg.TRUNCATED)


def test_to_plain() -> None:
    assert tg.to_plain("# H\n- one\n- **two** and `code`\n```py\nx = 1\n```\n> quote") == "H\n• one\n• two and code\nx = 1\nquote"


def test_parse_command() -> None:
    assert tg.parse_command("/status@Bot") == ("status", None) and tg.parse_command("Yes 3.") == ("approve", "3")
    assert tg.parse_command("/deny 12") == ("deny", "12") and tg.parse_command("n") == ("deny", None)
    assert tg.parse_command("stop 3") is None and tg.parse_command("yes please") is None and tg.parse_command("") is None


# ---------------------------------------------------------------- status

@case
async def test_status_precedence(env: Env) -> None:
    b = env.bridge
    env.state.pop("ownerChatId")
    env.bridge.issue_pairing()
    s = b.status()
    assert s["status"] == "not_paired" and s["pairing"]["link"] == f"https://t.me/{BOT}?start={s['pairing']['code']}"
    assert set(s) == {"enabled", "has_token", "bot_username", "paired", "owner_name", "status", "last_error", "last_poll_at", "pairing"}
    env.state.update(ownerChatId=OWNER, ownerName="Nate")
    s = b.status()
    assert (s["status"], s["paired"], s["owner_name"], s["pairing"]) == ("connected", True, "Nate", None)
    for health in ("error", "locked", "conflict", "bad_token"):
        b.health = health
        assert b.status()["status"] == health
    env.settings["telegramEnabled"] = False
    assert b.status()["status"] == "disabled" and b.status()["last_error"] is None
    env.token = None
    assert b.status()["status"] == "no_token" and b.status()["has_token"] is False and b.status()["pairing"] is None


# ---------------------------------------------------------------- token hygiene, errors and backoff

def test_the_api_caller_never_leaks_the_token(caplog) -> None:  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if req.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 1, "username": BOT}})
        if req.url.path.endswith("/getUpdates"):
            return httpx.Response(429, json={"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 7}})
        if req.url.path.endswith("/sendMessage"):
            return httpx.Response(401, json={"ok": False, "error_code": 401, "description": f"Unauthorized {TOKEN}"})
        if req.url.path.endswith("/boom"):
            return httpx.Response(502, text="<html>bad gateway</html>")
        raise httpx.ConnectError(f"connection failed for {req.url}")

    async def go() -> None:
        api = tg.HttpApi()
        api._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        assert await api(TOKEN, "getMe", {}) == {"id": 1, "username": BOT}
        assert seen == [f"https://api.telegram.org/bot{TOKEN}/getMe"]
        for method, code in (("getUpdates", 429), ("sendMessage", 401), ("boom", 502), ("deleteWebhook", 0)):
            try:
                await api(TOKEN, method, {})
            except tg.TelegramError as e:
                assert e.code == code and TOKEN not in str(e) and TOKEN not in e.description and e.__cause__ is None
                if code == 429:
                    assert e.retry_after == 7.0
            else:
                raise AssertionError("expected a TelegramError")
        await api.aclose()

    asyncio.run(go())
    assert TOKEN not in caplog.text
    assert logging.getLogger("httpx").level >= logging.WARNING and logging.getLogger("httpcore").level >= logging.WARNING


def test_sanitize_replaces_the_token_and_anything_shaped_like_one() -> None:
    assert tg.sanitize(f"GET https://api.telegram.org/bot{TOKEN}/getMe failed", TOKEN) == "GET https://api.telegram.org/bot<token>/getMe failed"
    assert TOKEN not in tg.sanitize(f"oops {TOKEN}")
    assert tg.sanitize("987654321:" + "Z" * 35).count("<token>") == 1
    assert tg.sanitize("plain text 12:30") == "plain text 12:30"


async def drive(env: Env, failures: list[Exception | None], stop_after: int | None = None) -> list[float]:
    """Run the poll loop with `failures` as the getUpdates outcomes (None = a good poll); returns the sleeps."""
    outcomes = iter(failures)
    sleeps: list[float] = []

    async def api(token: str, method: str, params: dict[str, Any], timeout: float | None = None) -> Any:
        out = next(outcomes)
        if out is not None:
            raise out
        return []

    async def sleep(d: float) -> None:
        sleeps.append(d)
        if len(sleeps) >= len(failures):  # one sleep per outcome: the last one ends the loop
            env.settings["telegramEnabled"] = False

    env.bridge.deps.api = api
    env.bridge._sleep = sleep
    await asyncio.wait_for(env.bridge._run(), 5)
    return sleeps


@case
async def test_backoff_doubles_to_the_cap_and_resets_on_success(env: Env) -> None:
    boom = tg.TelegramError(502, "bad gateway")
    sleeps = await drive(env, [boom] * 9)
    assert [s for s in sleeps] == [1, 2, 4, 8, 16, 32, 60, 60, 60]
    env.settings["telegramEnabled"] = True
    assert env.bridge.status()["status"] == "error"
    sleeps = await drive(env, [boom, boom, None, boom])
    assert sleeps == [1, 2, 0, 1]
    env.settings["telegramEnabled"] = True
    assert await drive(env, [None]) == [0]
    env.settings["telegramEnabled"] = True
    assert env.bridge.status()["status"] == "connected" and env.bridge.last_error is None and env.bridge.last_poll_at


@case
async def test_network_errors_back_off_too(env: Env) -> None:
    sleeps = await drive(env, [httpx.ConnectError("down"), OSError("x")])
    assert sleeps == [1, 2]


@case
async def test_401_is_bad_token_and_polling_stops(env: Env) -> None:
    sleeps = await drive(env, [tg.TelegramError(401, "Unauthorized"), None, None])
    env.settings["telegramEnabled"] = True
    assert sleeps == [] and env.bridge.status()["status"] == "bad_token"
    assert "401" in env.bridge.last_error and env.bridge._lock_fd is None  # the lock is released


@case
async def test_a_changed_token_restarts_a_bad_token_poller_but_the_same_one_does_not(env: Env) -> None:
    env.bridge.health = "bad_token"
    env.bridge._token_fp = env.bridge._fp(env.token)
    await env.bridge.reconcile()
    assert env.bridge._task is None  # same token: still dead
    env.token = TOKEN  # (the fake API asserts this exact token, so the changed one is only simulated by the fingerprint)
    env.bridge._token_fp = "something else"
    await env.bridge.reconcile()
    assert env.bridge._task is not None and env.bridge.health == ""


@case
async def test_409_is_conflict_and_retries_every_30_seconds(env: Env) -> None:
    sleeps = await drive(env, [tg.TelegramError(409, "terminated by other getUpdates request")] * 2)
    env.settings["telegramEnabled"] = True
    assert sleeps == [30, 30] and env.bridge.status()["status"] == "conflict"


@case
async def test_429_honours_retry_after(env: Env) -> None:
    sleeps = await drive(env, [tg.TelegramError(429, "Too Many Requests", retry_after=7)])
    assert sleeps == [7]


@case
async def test_a_second_poller_on_the_same_token_is_locked_out(env: Env) -> None:
    other = Env(env.bridge.deps.lock_path.parent)  # same lock file
    await env.bridge.poll_once()
    await other.bridge.poll_once()
    assert other.bridge.health == "locked" and other.bridge._lock_fd is None
    sleeps: list[float] = []

    async def sleep(d: float) -> None:
        sleeps.append(d)
        other.settings["telegramEnabled"] = False

    other.bridge._sleep = sleep
    await other.bridge._run()
    assert sleeps == [30.0] and other.bridge.health == "locked"
    other.settings["telegramEnabled"] = True
    assert other.bridge.status()["status"] == "locked"
    other.bridge.on_run_change(other.run())  # the lock holder is the only one that sends
    await asyncio.sleep(0.02)
    assert other.sent() == []


@case
async def test_the_token_never_reaches_logs_status_or_last_error(env: Env) -> None:
    logs: list[logging.LogRecord] = []

    class H(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            logs.append(record)

    h = H(level=logging.DEBUG)
    root = logging.getLogger()
    old = root.level
    root.addHandler(h)
    root.setLevel(logging.DEBUG)
    try:
        leaky = httpx.ConnectError(f"All connection attempts failed for https://api.telegram.org/bot{TOKEN}/getUpdates")
        await drive(env, [leaky, tg.TelegramError(500, f"upstream said {TOKEN}"), RuntimeError(f"bot{TOKEN} exploded")])
        env.settings["telegramEnabled"] = True
        env.bridge.deps.api = env.api
        env.fail["sendMessage"] = leaky
        await env.feed(msg("secret message body text"))
        await env.feed(msg("/help"))
        assert not await env.bridge.send_test() == {"ok": True}
    finally:
        root.removeHandler(h)
        root.setLevel(old)
    blob = "\n".join(r.getMessage() for r in logs) + repr(env.bridge.status()) + str(env.bridge.last_error)
    assert TOKEN not in blob and "secret message body text" not in blob
    assert env.bridge.last_error and "<token>" in env.bridge.last_error


@case
async def test_a_failing_send_is_retried_once_on_429_and_never_raises(env: Env) -> None:
    sleeps: list[float] = []

    async def sleep(d: float) -> None:
        sleeps.append(d)

    env.bridge._sleep = sleep
    calls = {"n": 0}

    async def api(token: str, method: str, params: dict[str, Any], timeout: float | None = None) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise tg.TelegramError(429, "slow", retry_after=3)
        return True

    env.bridge.deps.api = api
    assert await env.bridge._send(OWNER, "x") is True and sleeps == [3.0] and calls["n"] == 2
    calls["n"] = 0
    env.bridge.deps.api = env.api
    env.fail["sendMessage"] = tg.TelegramError(403, "Forbidden: bot was blocked by the user")
    assert await env.bridge._send(OWNER, "x") is False


@case
async def test_one_bad_update_does_not_stall_the_offset(env: Env) -> None:
    async def boom(conv: str, text: str) -> Any:
        raise RuntimeError("model down")

    env.bridge.deps.start_turn = boom
    u1, u2 = msg("one"), msg("/help")
    await env.feed(u1, u2)
    assert env.state["offset"] == u2["update_id"] + 1
    assert env.sent() == ["Grain couldn't take that message right now. Try again in a moment.", tg.HELP]


# ---------------------------------------------------------------- legacy settings

def test_the_migration_drops_the_old_texting_settings_and_nothing_else() -> None:
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    legacy = migrations._LEGACY_TEXTING_KEYS
    assert len(legacy) == 8
    for k in (*legacy, "model", "telegramEnabled"):
        c.execute("INSERT INTO settings VALUES (?, '1')", (k,))
    migrations._drop_legacy_texting_keys(c)
    assert sorted(r[0] for r in c.execute("SELECT key FROM settings")) == ["model", "telegramEnabled"]
    assert any(n == "drop_legacy_texting_keys" for _, n, _ in migrations.MIGRATIONS)
