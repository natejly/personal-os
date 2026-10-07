"""Telegram media: photos and files out (sendPhoto / sendMediaGroup / sendDocument, limits, fallbacks), progress updates,
and photos and files in. Same fake API as test_telegram.py, so nothing here touches the network."""
from __future__ import annotations

import asyncio
import io
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from personal_os import telegram as tg  # noqa: E402
from test_telegram import CONV, OWNER, STRANGER, TOKEN, Env, case, msg, until  # noqa: E402


def png(w: int = 8, h: int = 8) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(b, "PNG")
    return b.getvalue()


class Disk:
    """Stored originals by document id, for Deps.attachment_path."""

    def __init__(self, env: Env, tmp: Path) -> None:
        self.tmp, self.paths = tmp, {}
        env.bridge.deps.attachment_path = lambda i: self.paths.get(i)

    def add(self, name: str, data: bytes = b"x", mime: str = "image/png", doc_id: str | None = None) -> dict[str, Any]:
        doc_id = doc_id or f"doc-{len(self.paths) + 1}"
        p = self.tmp / f"{doc_id}-{name}"
        p.write_bytes(data)
        self.paths[doc_id] = p
        return {"id": doc_id, "name": name, "mime": mime, "size": len(data)}

    def gone(self, name: str = "gone.png") -> dict[str, Any]:
        return {"id": f"missing-{name}", "name": name, "mime": "image/png", "size": 1}


def sleeper(env: Env, hold: asyncio.Event | None = None) -> list[float]:
    """Patch the bridge's sleep: it records the delay and returns at once, or when `hold` is set."""
    slept: list[float] = []

    async def fake(d: float) -> None:
        slept.append(d)
        await (hold.wait() if hold else asyncio.sleep(0))

    env.bridge._sleep = fake
    return slept


# ---------------------------------------------------------------- outbound media

@case
async def test_one_photo_goes_as_sendphoto_with_the_text_as_caption(env: Env) -> None:
    disk = Disk(env, Path(env.bridge.deps.lock_path).parent)
    a = disk.add("shot.png", png())
    await env.bridge._deliver(OWNER, "Here it is", [a])
    assert [m for m, _ in env.calls] == ["sendPhoto"]
    assert env.of("sendPhoto")[0] == {"chat_id": OWNER, "caption": "Here it is"}
    name, data, mime = env.files[0][1]["photo"]
    assert (name, mime, data) == ("shot.png", "image/png", png())


@case
async def test_three_photos_go_as_one_media_group_whose_attach_names_match_the_files(env: Env) -> None:
    disk = Disk(env, Path(env.bridge.deps.lock_path).parent)
    atts = [disk.add(f"s{i}.png", png(8 + i, 8)) for i in range(3)]
    await env.bridge._deliver(OWNER, "three", atts)
    assert [m for m, _ in env.calls] == ["sendMediaGroup"]
    media = env.of("sendMediaGroup")[0]["media"]
    files = env.files[0][1]
    assert [x["media"] for x in media] == [f"attach://{k}" for k in files] and len(media) == 3
    assert media[0]["caption"] == "three" and all("caption" not in x for x in media[1:])
    assert [files[k][0] for k in files] == ["s0.png", "s1.png", "s2.png"]


@case
async def test_twelve_photos_are_a_group_of_ten_and_a_group_of_two(env: Env) -> None:
    disk = Disk(env, Path(env.bridge.deps.lock_path).parent)
    await env.bridge._deliver(OWNER, "", [disk.add(f"s{i}.png", png()) for i in range(12)])
    assert [len(p["media"]) for p in env.of("sendMediaGroup")] == [10, 2]
    assert all("caption" not in x for p in env.of("sendMediaGroup") for x in p["media"])


@case
async def test_a_lone_leftover_photo_is_a_sendphoto(env: Env) -> None:
    disk = Disk(env, Path(env.bridge.deps.lock_path).parent)
    await env.bridge._deliver(OWNER, "", [disk.add(f"s{i}.png", png()) for i in range(11)])
    assert [m for m, _ in env.calls] == ["sendMediaGroup", "sendPhoto"]


@case
async def test_other_files_and_svgs_go_as_documents_with_the_caption_on_the_first(env: Env) -> None:
    disk = Disk(env, Path(env.bridge.deps.lock_path).parent)
    pdf = disk.add("a.pdf", b"%PDF-1.4", "application/pdf")
    svg = disk.add("b.svg", b"<svg/>", "image/svg+xml")
    await env.bridge._deliver(OWNER, "files", [pdf, svg])
    assert [m for m, _ in env.calls] == ["sendDocument", "sendDocument"]
    assert env.of("sendDocument")[0]["caption"] == "files" and "caption" not in env.of("sendDocument")[1]
    assert env.files[0][1]["document"][0] == "a.pdf"


@case
async def test_an_image_past_the_photo_limits_goes_as_a_document(env: Env) -> None:
    disk = Disk(env, Path(env.bridge.deps.lock_path).parent)
    tall = disk.add("tall.png", png(1, 20001))
    skinny = disk.add("skinny.png", png(5, 200))  # ratio 40:1
    broken = disk.add("broken.png", b"not an image")
    await env.bridge._deliver(OWNER, "", [tall, skinny, broken])
    assert [m for m, _ in env.calls] == ["sendDocument"] * 3


@case
async def test_a_photo_over_ten_megabytes_goes_as_a_document(env: Env) -> None:
    disk = Disk(env, Path(env.bridge.deps.lock_path).parent)
    big = disk.add("big.png", png())
    with open(disk.paths[big["id"]], "r+b") as f:
        f.truncate(tg.PHOTO_MAX + 1)
    await env.bridge._deliver(OWNER, "", [big])
    assert [m for m, _ in env.calls] == ["sendDocument"]


@case
async def test_a_refused_photo_is_retried_as_a_document(env: Env) -> None:
    disk = Disk(env, Path(env.bridge.deps.lock_path).parent)
    env.fail["sendPhoto"] = tg.TelegramError(400, "Bad Request: PHOTO_INVALID_DIMENSIONS")
    await env.bridge._deliver(OWNER, "cap", [disk.add("s.png", png())])
    assert [m for m, _ in env.calls] == ["sendPhoto", "sendDocument"]
    assert env.of("sendDocument")[0]["caption"] == "cap"
    assert env.sent() == []


@case
async def test_a_refused_group_is_retried_as_one_document_each(env: Env) -> None:
    disk = Disk(env, Path(env.bridge.deps.lock_path).parent)
    env.fail["sendMediaGroup"] = tg.TelegramError(400, "Bad Request")
    await env.bridge._deliver(OWNER, "", [disk.add(f"s{i}.png", png()) for i in range(3)])
    assert [m for m, _ in env.calls] == ["sendMediaGroup"] + ["sendDocument"] * 3


@case
async def test_when_the_document_fails_too_the_user_gets_a_note_naming_the_file(env: Env) -> None:
    disk = Disk(env, Path(env.bridge.deps.lock_path).parent)
    env.fail["sendPhoto"] = tg.TelegramError(400, "Bad Request")
    env.fail["sendDocument"] = tg.TelegramError(400, "Bad Request")
    await env.bridge._deliver(OWNER, "the cap", [disk.add("shot.png", png())])
    assert env.sent() == ["the cap", "Couldn't deliver shot.png."]  # the caption was not lost with the file


@case
async def test_a_missing_or_oversized_file_is_a_note_without_a_call(env: Env) -> None:
    disk = Disk(env, Path(env.bridge.deps.lock_path).parent)
    huge = disk.add("huge.zip", b"", "application/zip")
    with open(disk.paths[huge["id"]], "r+b") as f:
        f.truncate(tg.DOC_MAX + 1)  # sparse
    await env.bridge._deliver(OWNER, "", [huge, disk.gone("lost.png")])
    assert [m for m, _ in env.calls] == ["sendMessage", "sendMessage"]
    assert env.sent() == ["Couldn't deliver huge.zip.", "Couldn't deliver lost.png."]
    assert env.files == []


@case
async def test_text_too_long_for_a_caption_is_sent_first_and_the_photo_has_none(env: Env) -> None:
    disk = Disk(env, Path(env.bridge.deps.lock_path).parent)
    await env.bridge._deliver(OWNER, "w" * 1500, [disk.add("s.png", png())])
    assert [m for m, _ in env.calls] == ["sendMessage", "sendPhoto"]
    assert env.sent() == ["w" * 1500] and "caption" not in env.of("sendPhoto")[0]


@case
async def test_a_429_on_sendphoto_waits_retry_after_then_succeeds(env: Env) -> None:
    disk = Disk(env, Path(env.bridge.deps.lock_path).parent)
    slept = sleeper(env)
    n = {"n": 0}
    real = env.api

    async def flaky(token: str, method: str, params: dict[str, Any], timeout: Any = None, files: Any = None) -> Any:
        if method == "sendPhoto":
            n["n"] += 1
            if n["n"] == 1:
                raise tg.TelegramError(429, "Too Many Requests", retry_after=5)
        return await real(token, method, params, timeout, files)

    env.bridge.deps.api = flaky
    await env.bridge._deliver(OWNER, "", [disk.add("s.png", png())])
    assert slept == [5] and n["n"] == 2 and len(env.of("sendPhoto")) == 1 and env.of("sendDocument") == []  # the 429 never reached the recorder


# ---------------------------------------------------------------- owner only

@case
async def test_no_outbound_call_reaches_a_chat_that_is_not_the_owner(env: Env) -> None:
    disk = Disk(env, Path(env.bridge.deps.lock_path).parent)
    assert await env.bridge._send(STRANGER, "hi") is False
    await env.bridge._deliver(STRANGER, "cap", [disk.add("s.png", png()), disk.add("t.png", png())])
    assert await env.bridge._call("sendChatAction", {"chat_id": STRANGER, "action": "typing"}) is False
    assert await env.bridge._call("editMessageText", {"chat_id": STRANGER, "message_id": 1, "text": "x"}) is False
    assert env.calls == []
    assert await env.bridge._send(OWNER, "hi") is True


# ---------------------------------------------------------------- push and the final reply

@case
async def test_push_carries_attachments(env: Env) -> None:
    disk = Disk(env, Path(env.bridge.deps.lock_path).parent)
    await env.feed()  # takes the poller lock
    env.bridge.push("**done**", [disk.add("r.pdf", b"%PDF", "application/pdf")])
    await until(lambda: env.of("sendDocument"))
    assert env.of("sendDocument")[0]["caption"] == "done"
    env.bridge.push("just text")
    await until(lambda: env.sent())
    assert env.sent() == ["just text"]


@case
async def test_the_final_reply_sends_its_attachments_after_the_text(env: Env) -> None:
    disk = Disk(env, Path(env.bridge.deps.lock_path).parent)
    a = disk.add("s.png", png())
    env.texts["m1"] = "All done."
    env.bridge.deps.message_attachments = lambda mid: [a] if mid == "m1" else []
    await env.bridge._reply_final(env.run(message_id="m1", live=False), OWNER)
    assert [m for m, _ in env.calls] == ["sendMessage", "sendPhoto"]
    assert "caption" not in env.of("sendPhoto")[0]


# ---------------------------------------------------------------- progress updates

@case
async def test_send_update_only_for_the_texts_conversation_while_polling(env: Env) -> None:
    assert env.bridge.send_update(CONV, "early") is False  # not polling yet: this process sends nothing
    await env.feed()
    assert env.bridge.send_update("other-conv", "x") is False
    assert env.bridge.send_update(None, "x") is False
    assert env.bridge.send_update(CONV, "") is False
    assert env.bridge.is_texts_conversation(CONV) and not env.bridge.is_texts_conversation("other-conv")
    assert env.bridge.send_update(CONV, "**halfway**") is True
    await until(lambda: env.sent())
    assert env.sent() == ["<b>halfway</b>"]
    env.state["ownerChatId"] = None
    assert env.bridge.send_update(CONV, "x") is False


@case
async def test_updates_inside_the_window_are_coalesced_into_one_message(env: Env) -> None:
    disk = Disk(env, Path(env.bridge.deps.lock_path).parent)
    slept = sleeper(env)
    await env.feed()
    a1, a2 = disk.add("one.png", png()), disk.add("two.png", png())
    assert env.bridge.send_update(CONV, "first", [a1])
    await until(lambda: env.of("sendPhoto"))
    assert env.bridge.send_update(CONV, "second", [a2]) and env.bridge.send_update(CONV, "third", [a2])
    await until(lambda: len(env.calls) >= 3 and not env.bridge._updates[CONV].timer)
    assert 0 < slept[0] <= tg.PROGRESS_MIN_SECONDS
    assert [m for m, _ in env.calls if m != "getUpdates"] == ["sendPhoto", "sendPhoto"]  # one flush for second + third
    assert env.of("sendPhoto")[1]["caption"] == "second\n\nthird"
    assert [f[1]["photo"][0] for f in env.files] == ["one.png", "two.png"]  # a2 once, though it was sent twice


@case
async def test_the_final_reply_flushes_waiting_progress_and_skips_files_already_sent(env: Env) -> None:
    disk = Disk(env, Path(env.bridge.deps.lock_path).parent)
    sleeper(env, asyncio.Event())  # the window never ends on its own
    await env.feed()
    a1, a2, a3 = disk.add("one.png", png()), disk.add("two.png", png()), disk.add("three.png", png())
    env.bridge.send_update(CONV, "first", [a1])
    await until(lambda: env.of("sendPhoto"))
    env.bridge.send_update(CONV, "waiting", [a2])
    await until(lambda: env.bridge._updates[CONV].texts)
    env.texts["m1"] = "Finished."
    env.bridge.deps.message_attachments = lambda mid: [a1, a2, a3]
    await env.bridge._reply_final(env.run(message_id="m1", live=False), OWNER)
    sent = [f[1]["photo"][0] for f in env.files]
    assert sent == ["one.png", "two.png", "three.png"]  # each exactly once, the waiting one before the answer
    assert env.sent() == ["Finished."]
    assert env.bridge._sent_atts.seen == {}  # consumed: the same file may be sent again in a later run


# ---------------------------------------------------------------- inbound photos and files

class Inbound:
    def __init__(self, env: Env) -> None:
        self.stored: list[tuple[str, str, bytes]] = []
        self.downloads: list[str] = []
        self.starts: list[tuple[Any, ...]] = []
        self.fail_download = False
        env.bridge.deps.download = self._download
        env.bridge.deps.store_upload = self._store

        async def start(*a: Any) -> dict[str, Any]:
            self.starts.append(a)
            return {"run_id": f"run-{len(self.starts)}"}

        env.bridge.deps.start_turn = start

    async def _download(self, token: str, file_path: str) -> bytes:
        assert token == TOKEN
        self.downloads.append(file_path)
        if self.fail_download:
            raise tg.TelegramError(0, "network")
        return b"IMG"

    def _store(self, name: str, mime: str, data: bytes) -> dict[str, Any]:
        self.stored.append((name, mime, data))
        return {"id": "doc-in-1", "name": name, "mime": mime, "size": len(data)}


def media_msg(extra: dict[str, Any], text: str | None = None, **kw: Any) -> dict[str, Any]:
    u = msg(None, **kw)
    u["message"].update(extra)
    if text is not None:
        u["message"]["caption"] = text
    return u


@case
async def test_an_inbound_photo_is_downloaded_stored_and_started_with_its_caption(env: Env) -> None:
    inb = Inbound(env)
    photo = [{"file_id": "small", "file_size": 10}, {"file_id": "large", "file_size": 2000}]
    await env.feed(media_msg({"photo": photo}, "what is this?"))
    assert [p["file_id"] for p in env.of("getFile")] == ["large"]  # the largest size
    assert inb.downloads == ["photos/x.jpg"]
    assert len(inb.stored) == 1 and inb.stored[0][1:] == ("image/jpeg", b"IMG") and inb.stored[0][0].startswith("photo-")
    assert inb.starts == [(CONV, "what is this?", ["doc-in-1"])]
    assert env.bridge._runs.seen.keys() == {"run-1"}


@case
async def test_a_document_keeps_its_name_and_type_and_may_have_no_caption(env: Env) -> None:
    inb = Inbound(env)
    doc = {"file_id": "d1", "file_name": "../../notes.pdf", "mime_type": "application/pdf", "file_size": 99}
    await env.feed(media_msg({"document": doc}))
    assert inb.stored == [("notes.pdf", "application/pdf", b"IMG")]
    assert inb.starts == [(CONV, "", ["doc-in-1"])]


@case
async def test_a_caption_that_looks_like_a_command_is_a_message(env: Env) -> None:
    inb = Inbound(env)
    await env.feed(media_msg({"photo": [{"file_id": "p"}]}, "yes"))
    assert inb.starts == [(CONV, "yes", ["doc-in-1"])] and env.decisions == []


@case
async def test_an_inbound_file_over_twenty_megabytes_is_refused_without_a_download(env: Env) -> None:
    inb = Inbound(env)
    await env.feed(media_msg({"document": {"file_id": "d", "file_name": "big.zip", "file_size": 21 * 1024 * 1024}}))
    assert env.sent() == [tg.TOO_BIG] and "20 MB" in tg.TOO_BIG
    assert env.of("getFile") == [] and inb.downloads == [] and inb.starts == []


@case
async def test_a_download_or_store_failure_is_one_polite_line(env: Env) -> None:
    inb = Inbound(env)
    inb.fail_download = True
    await env.feed(media_msg({"photo": [{"file_id": "p"}]}, "look"))
    assert env.sent() == [tg.FETCH_FAILED] and inb.starts == [] and inb.stored == []
    inb.fail_download = False

    def boom(*a: Any) -> Any:
        raise RuntimeError("disk full")

    env.bridge.deps.store_upload = boom
    await env.feed(media_msg({"photo": [{"file_id": "p"}]}))
    assert env.sent() == [tg.FETCH_FAILED] * 2 and inb.starts == []


@case
async def test_a_strangers_photo_is_ignored(env: Env) -> None:
    inb = Inbound(env)
    await env.feed(media_msg({"photo": [{"file_id": "p"}]}, "hi", chat=STRANGER),
                   media_msg({"photo": [{"file_id": "p"}]}, "hi", chat=OWNER, user=STRANGER))
    assert [m for m, _ in env.calls] == ["getUpdates"] and inb.starts == [] and inb.downloads == []


@case
async def test_voice_and_video_say_what_is_supported(env: Env) -> None:
    inb = Inbound(env)
    await env.feed(media_msg({"voice": {"file_id": "v"}}, "caption does not rescue it"), media_msg({"sticker": {"file_id": "s"}}))
    assert env.sent() == [tg.UNSUPPORTED] * 2 and inb.starts == []
    assert tg.UNSUPPORTED == "Send text, a photo or a file; voice and video aren't supported yet."


def test_media_before_pairing_is_ignored_for_real(tmp_path: Path) -> None:
    async def go() -> None:
        env = Env(tmp_path, paired=False)
        inb = Inbound(env)
        try:
            await env.feed(media_msg({"photo": [{"file_id": "p"}]}, "hi"))
            assert [m for m, _ in env.calls] == ["getUpdates"] and inb.starts == []
        finally:
            await env.bridge.stop()

    asyncio.run(go())


@case
async def test_media_counts_toward_the_rate_limit(env: Env) -> None:
    inb = Inbound(env)
    env.bridge.limiter.limit = 2
    await env.feed(*[media_msg({"photo": [{"file_id": "p"}]}) for _ in range(3)])
    assert len(inb.starts) == 2 and env.sent() == [tg.SLOW_DOWN]


@case
async def test_a_two_argument_start_turn_still_works_for_text(env: Env) -> None:
    await env.feed(msg("plain text"))
    assert env.turns == [(CONV, "plain text")]


# ---------------------------------------------------------------- the real HTTP caller

def test_httpapi_posts_multipart_and_downloads_without_leaking_the_token() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path.endswith("/sendPhoto"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        if "/file/bot" in req.url.path and req.url.path.endswith("good.jpg"):
            return httpx.Response(200, content=b"JPEGBYTES")
        return httpx.Response(404, text=f"missing {req.url}")

    async def go() -> None:
        api = tg.HttpApi()
        api._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        res = await api(TOKEN, "sendPhoto", {"chat_id": OWNER, "media": [{"a": 1}], "caption": "c"}, 60.0,
                        {"photo": ("s.png", b"PNGDATA", "image/png")})
        assert res == {"message_id": 1}
        body = seen[0].content
        assert seen[0].headers["content-type"].startswith("multipart/form-data") and b"PNGDATA" in body
        assert b'name="chat_id"' in body and b"4242" in body and b'[{"a": 1}]' in body and b'filename="s.png"' in body
        assert await api.download(TOKEN, "photos/good.jpg") == b"JPEGBYTES"
        assert str(seen[1].url) == f"https://api.telegram.org/file/bot{TOKEN}/photos/good.jpg"
        try:
            await api.download(TOKEN, "photos/nope.jpg")
        except tg.TelegramError as e:
            assert e.code == 404 and TOKEN not in str(e)
        else:
            raise AssertionError("expected a TelegramError")
        await api.aclose()

    asyncio.run(go())
