"""How replies reach the phone: HTML formatting, the plain-text fallback, long replies as reply.md, tables as .csv, and
owner-only delivery. Same fake API as test_telegram.py, so nothing here touches the network."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from personal_os import telegram as tg  # noqa: E402
from personal_os import telegram_format as tf  # noqa: E402
from test_telegram import OWNER, STRANGER, Env, case, until  # noqa: E402
from test_telegram_media import Disk, png  # noqa: E402

BAD = tg.TelegramError(400, "Bad Request: can't parse entities: Unsupported start tag")


def refuse(env: Env, when: Callable[[str, dict[str, Any]], bool], error: Exception = BAD) -> None:
    """Make the fake API raise `error` for every call `when` picks (the call is still recorded)."""
    inner = env.api

    async def api(token: str, method: str, params: dict[str, Any], timeout: float | None = None, files: Any = None) -> Any:
        if when(method, params):
            env.calls.append((method, params))
            raise error
        return await inner(token, method, params, timeout, files)

    env.bridge.deps.api = api


def is_html(method: str, params: dict[str, Any]) -> bool:
    return method == "sendMessage" and params.get("parse_mode") == "HTML"


def docs(env: Env) -> list[tuple[str, str, bytes]]:
    return [(name, mime, data) for m, f in env.files if m == "sendDocument" for name, data, mime in [f["document"]]]


# ---------------------------------------------------------------- formatting on the wire

@case
async def test_text_is_escaped_and_sent_as_html(env: Env) -> None:
    await env.bridge._send_all(OWNER, "a < b & c")
    [p] = env.of("sendMessage")
    assert p["text"] == "a &lt; b &amp; c" and p["parse_mode"] == "HTML" and p["chat_id"] == OWNER
    assert p["link_preview_options"] == {"is_disabled": True}


@case
async def test_text_without_markup_is_sent_as_is(env: Env) -> None:
    await env.bridge._send_all(OWNER, "just words.")
    assert env.of("sendMessage") == [{"chat_id": OWNER, "text": "just words."}]


@case
async def test_code_bold_italic_and_links(env: Env) -> None:
    await env.bridge._send_all(OWNER, "**Done** with `x < y`, *really*, see [docs](https://x.co)\n\n```py\nprint('<hi>')\n```")
    assert env.sent() == ['<b>Done</b> with <code>x &lt; y</code>, <i>really</i>, see <a href="https://x.co">docs</a>\n\n'
                          '<pre><code class="language-py">print(\'&lt;hi&gt;\')</code></pre>']


@case
async def test_a_small_table_is_a_pre_block(env: Env) -> None:
    await env.bridge._send_all(OWNER, "| a | b |\n|---|---|\n| 1 | 2 |")
    assert env.sent() == ["<pre>a | b\n--+--\n1 | 2</pre>"] and docs(env) == []


@case
async def test_a_wide_table_goes_as_a_csv_document_after_the_text(env: Env) -> None:
    wide = "| " + " | ".join(f"column{i}" for i in range(9)) + " |\n|" + "---|" * 9 + "\n| " + " | ".join("v" * 7 for _ in range(9)) + " |"
    await env.bridge._send_all(OWNER, f"Here:\n\n{wide}\n\nBye.")
    assert [m for m, _ in env.calls] == ["sendMessage", "sendDocument"]
    assert env.sent() == [f"Here:\n\n{tf.TABLE_PLACEHOLDER}\n\nBye."]
    [(name, data_mime, data)] = docs(env)
    assert (name, data_mime) == ("table.csv", "text/csv") and data.decode().startswith("column0,column1")
    assert "caption" not in env.of("sendDocument")[0] and env.of("sendDocument")[0]["chat_id"] == OWNER


@case
async def test_a_long_message_splits_with_the_code_block_reopened(env: Env) -> None:
    body = "\n".join(f"row {i:04d} <ok>" for i in range(330))  # ~5 kB of code: two messages
    await env.bridge._send_all(OWNER, f"```txt\n{body}\n```")
    sent = env.sent()
    assert len(sent) == 2 and all(len(t) <= 4096 for t in sent)
    assert sent[0].endswith("</code></pre>") and sent[1].startswith('<pre><code class="language-txt">row ')
    assert all(p["parse_mode"] == "HTML" for p in env.of("sendMessage"))


# ---------------------------------------------------------------- long replies

@case
async def test_a_long_reply_is_a_summary_and_reply_md(env: Env) -> None:
    md = "# Report\n\nThe short version is that it worked. " + "Details follow. " * 30 + "\n\n" + "More **detail** here. " * 400
    await env.bridge._send_all(OWNER, md)
    assert [m for m, _ in env.calls] == ["sendMessage", "sendDocument"]
    summary = env.sent()[0]
    assert summary.endswith("Full reply attached.") and "parse_mode" not in env.of("sendMessage")[0]
    assert len(summary) <= tf.SUMMARY_MAX + len("\n\n" + tf.SUMMARY_TAIL) + 1
    assert summary.startswith("Report")
    assert docs(env) == [("reply.md", "text/markdown", md.strip().encode())]
    assert "caption" not in env.of("sendDocument")[0]


@case
async def test_no_message_is_ever_truncated(env: Env) -> None:
    await env.bridge._send_all(OWNER, "x " * 20000)
    assert env.sent()[-1].endswith("Full reply attached.") and not any("full reply in Grain" in t for t in env.sent())


# ---------------------------------------------------------------- fallbacks

@case
async def test_a_parse_error_resends_the_part_as_plain_text(env: Env) -> None:
    refuse(env, is_html)
    await env.bridge._send_all(OWNER, "**bold** and a < b, [l](https://x.co)")
    first, second = env.of("sendMessage")
    assert first["parse_mode"] == "HTML" and first["text"].startswith("<b>bold</b>")
    assert second == {"chat_id": OWNER, "text": "bold and a < b, l (https://x.co)"}
    assert docs(env) == []


@case
async def test_only_the_refused_part_falls_back(env: Env) -> None:
    body = "\n".join(f"row {i:04d} padding padding" for i in range(190))
    n = {"html": 0}

    def second_html(method: str, params: dict[str, Any]) -> bool:
        if is_html(method, params):
            n["html"] += 1
            return n["html"] == 2
        return False

    refuse(env, second_html)
    await env.bridge._send_all(OWNER, f"```\n{body}\n```")
    texts = env.of("sendMessage")
    assert len(texts) == 3 and texts[0]["parse_mode"] == "HTML" and texts[1]["parse_mode"] == "HTML" and "parse_mode" not in texts[2]
    assert texts[2]["text"].startswith("row ") and "<" not in texts[2]["text"] and docs(env) == []


@case
async def test_when_plain_text_is_refused_too_the_whole_reply_is_a_document(env: Env) -> None:
    refuse(env, lambda m, p: m == "sendMessage")
    md = "**bold** text\n\n" + "para " * 500 + "\n\n" + "tail " * 500
    await env.bridge._send_all(OWNER, md)
    assert [m for m, _ in env.calls] == ["sendMessage", "sendMessage", "sendDocument"]  # html, plain, then reply.md; the rest is not tried
    assert docs(env) == [("reply.md", "text/markdown", md.strip().encode())]
    assert env.of("sendDocument")[0]["chat_id"] == OWNER


@case
async def test_a_failure_that_is_not_a_parse_error_is_not_retried(env: Env) -> None:
    refuse(env, lambda m, p: m == "sendMessage", tg.TelegramError(403, "Forbidden: bot was blocked by the user"))
    await env.bridge._send_all(OWNER, "**hi**")
    assert [m for m, _ in env.calls] == ["sendMessage"]


@case
async def test_a_429_is_still_waited_out_for_formatted_text(env: Env) -> None:
    hits = {"n": 0}

    def once(method: str, params: dict[str, Any]) -> bool:
        hits["n"] += method == "sendMessage"
        return method == "sendMessage" and hits["n"] == 1

    refuse(env, once, tg.TelegramError(429, "Too Many Requests", 0.0))
    env.bridge._sleep = lambda d: _noop()
    await env.bridge._send_all(OWNER, "**hi**")
    assert [p["text"] for p in env.of("sendMessage")] == ["<b>hi</b>", "<b>hi</b>"]


async def _noop() -> None:
    return None


# ---------------------------------------------------------------- final replies, updates, push, captions

@case
async def test_the_final_reply_is_formatted(env: Env) -> None:
    env.texts["m1"] = "## Title\n\nBody with `code`."
    await env.bridge._reply_final(env.run(message_id="m1", live=False), OWNER)
    assert env.sent() == ["<b>Title</b>\n\nBody with <code>code</code>."]


@case
async def test_an_empty_reply_is_the_stopped_note_and_an_error_hides_its_text(env: Env) -> None:
    await env.bridge._reply_final(env.run(live=False), OWNER)
    await env.bridge._reply_final(env.run(live=False, error="boom secret"), OWNER)
    assert env.sent() == ["Stopped.", tg.RUN_ERROR]


@case
async def test_push_and_progress_updates_are_formatted(env: Env) -> None:
    await env.feed()  # takes the poller lock
    env.bridge.push("**done** & dusted")
    await until(lambda: env.sent())
    assert env.sent() == ["<b>done</b> &amp; dusted"]
    assert env.bridge.send_update("conv-1", "step *two*") is True
    await until(lambda: len(env.sent()) == 2)
    assert env.sent()[1] == "step <i>two</i>"


@case
async def test_a_caption_is_plain_text(env: Env) -> None:
    disk = Disk(env, Path(env.bridge.deps.lock_path).parent)
    await env.bridge._deliver(OWNER, "**saved** a < b", [disk.add("s.png", png())])
    [p] = env.of("sendPhoto")
    assert p["caption"] == "saved a < b" and "parse_mode" not in p


@case
async def test_text_with_a_csv_table_goes_before_the_files(env: Env) -> None:
    disk = Disk(env, Path(env.bridge.deps.lock_path).parent)
    wide = "| " + " | ".join(f"column{i}" for i in range(9)) + " |\n|" + "---|" * 9 + "\n| " + " | ".join("v" * 7 for _ in range(9)) + " |"
    await env.bridge._deliver(OWNER, wide, [disk.add("s.png", png())])
    assert [m for m, _ in env.calls] == ["sendMessage", "sendDocument", "sendPhoto"]
    assert "caption" not in env.of("sendPhoto")[0]


# ---------------------------------------------------------------- owner only

@case
async def test_every_outbound_call_goes_to_the_owner(env: Env) -> None:
    disk = Disk(env, Path(env.bridge.deps.lock_path).parent)
    wide = "| " + " | ".join(f"column{i}" for i in range(9)) + " |\n|" + "---|" * 9 + "\n| " + " | ".join("v" * 7 for _ in range(9)) + " |"
    env.texts["m1"] = f"**hi** < there\n\n{wide}"
    env.texts["m2"] = "long " * 3000
    env.bridge.deps.message_attachments = lambda mid: [disk.add("s.png", png())] if mid == "m1" else []
    await env.bridge._reply_final(env.run(message_id="m1", live=False), OWNER)
    await env.bridge._reply_final(env.run(message_id="m2", live=False, run_id="r2"), OWNER)
    refuse(env, is_html)
    await env.bridge._send_all(OWNER, "**x**")  # the plain resend
    refuse(env, lambda m, p: m == "sendMessage")
    await env.bridge._send_all(OWNER, "**y**")  # the reply.md fallback
    methods = {m for m, _ in env.calls}
    assert {"sendMessage", "sendDocument", "sendPhoto"} <= methods
    assert {p["chat_id"] for _, p in env.calls} == {OWNER}


@case
async def test_a_chat_that_is_not_the_owner_is_refused_for_text_and_documents(env: Env) -> None:
    await env.bridge._send_all(STRANGER, "**hi**")
    await env.bridge._send_all(STRANGER, "long " * 3000)
    assert await env.bridge._send_doc(STRANGER, "reply.md", "text/markdown", b"x") is False
    wide = "| " + " | ".join(f"column{i}" for i in range(9)) + " |\n|" + "---|" * 9 + "\n| " + " | ".join("v" * 7 for _ in range(9)) + " |"
    await env.bridge._send_all(STRANGER, wide)
    assert env.calls == []
    env.state["ownerChatId"] = None  # unpaired: nobody is the owner
    await env.bridge._send_all(OWNER, "hi")
    assert env.calls == []


def test_to_plain_is_still_exported() -> None:
    assert tg.to_plain("# H\n- **one**\n[a](https://x.co)") == "H\n• one\na (https://x.co)"
    assert tg.split_reply("hello") == ["hello"]
