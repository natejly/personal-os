"""Conversation titles: an instant placeholder from the first message, then a short model-written one.

The placeholder is what the sidebar shows while the reply streams. The model title is generated after the
reply has ended, off the run, from the user's own typed text only (never assistant or tool output, so
nothing fetched from the outside can steer it). A title the user typed is never overwritten: every write is a
compare-and-set against the title the job started from, and `titleSource == "user"` stops it outright.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Callable

from . import llm

log = logging.getLogger(__name__)

PLACEHOLDER_LIMIT = 48
TITLE_LIMIT = 60
TIMEOUT_S = 30.0
RETITLE_AT = 6  # user turns after which an auto-titled chat is titled once more, from its first and latest messages
_CLIP = 600

SYSTEM = ("You write titles for chat conversations. Reply with only a title of at most 6 words that says what the "
          "conversation is about. No quotes, no markdown, no punctuation at the end.")


def _clamp(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp >= limit // 3 else cut).rstrip()


def placeholder(text: str, limit: int = PLACEHOLDER_LIMIT) -> str:
    """The first message cut at a word boundary. One long unbroken token falls back to a hard cut."""
    t = " ".join((text or "").split())
    if not t:
        return "New chat"
    if len(t) <= limit:
        return t
    cut = t[:limit + 1]
    sp = cut.rfind(" ")
    head = cut[:sp] if sp >= 20 else t[:limit]
    return head.rstrip(" ,.;:-–—!?") + "…"


def clean(raw: str, limit: int = TITLE_LIMIT) -> str:
    """What a model returned, reduced to a title. Empty when nothing usable is left."""
    s = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S | re.I)
    s = re.sub(r"<[^>]*>", "", s)
    line = next((ln.strip() for ln in s.splitlines() if ln.strip()), "")
    line = line.strip("\"'`*#_ ")
    line = re.sub(r"^title\s*:\s*", "", line, flags=re.I).strip("\"'`*#_ ")
    line = " ".join(line.split()).rstrip(".")
    return _clamp(line, limit)


def pick_texts(user_texts: list[str]) -> list[str]:
    """The first user message plus the last three, without repeats, in order."""
    if len(user_texts) <= 4:
        return list(user_texts)
    return [user_texts[0], *user_texts[-3:]]


async def generate(settings: dict[str, Any], model: str, user_texts: list[str], cancel: asyncio.Event | None = None) -> str:
    texts = [" ".join(t.split())[:_CLIP] for t in user_texts if t and t.strip()]
    if not texts:
        return ""
    body = "\n\n".join(f"Message {i + 1}: {t}" for i, t in enumerate(texts))
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": body}]
    raw = await asyncio.wait_for(
        llm.complete(settings, settings.get("extractionModel") or model, messages, kind="title",
                     effort="low", cancel=cancel, deadline=time.monotonic() + TIMEOUT_S),
        TIMEOUT_S + 2)
    return clean(raw)


class TitleJobs:
    """Detached title tasks, one per conversation. `get`/`update`/`publish` are the app's own."""

    def __init__(self, get: Callable[..., Any], update: Callable[..., Any], publish: Callable[[str, Any], Any]):
        self._get, self._update, self._publish = get, update, publish
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancels: dict[str, asyncio.Event] = {}

    def spawn(self, conv_id: str, project_id: str | None, expect: str, user_texts: list[str], model: str,
              settings: dict[str, Any], turns: int) -> None:
        if conv_id in self._tasks and not self._tasks[conv_id].done():
            return
        cancel = asyncio.Event()
        self._cancels[conv_id] = cancel
        task = asyncio.create_task(self._run(conv_id, project_id, expect, user_texts, model, settings, turns, cancel),
                                   name="auto-title")
        self._tasks[conv_id] = task
        task.add_done_callback(lambda t, c=conv_id: self._forget(c, t))

    def _forget(self, conv_id: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(conv_id) is task:
            self._tasks.pop(conv_id, None)
            self._cancels.pop(conv_id, None)

    def cancel(self, conv_id: str) -> None:
        ev = self._cancels.get(conv_id)
        if ev:
            ev.set()

    def apply(self, conv_id: str, expect: str | None, new: str, turns: int) -> dict[str, Any] | None:
        """Write `new` only if the row still exists, still carries `expect` (None skips that check) and was
        not retitled by the user. No await between the read and the write."""
        cur = self._get(conv_id, with_messages=False)
        if not cur or (cur.get("settings") or {}).get("titleSource") == "user":
            return None
        if expect is not None and cur["title"] != expect:
            return None
        out = self._update(conv_id, {"title": new, "settings": {"titleSource": "auto", "titleTurns": turns}})
        self._publish("conversation_changed", {"id": conv_id, "title": new})
        return out

    async def _run(self, conv_id: str, project_id: str | None, expect: str, user_texts: list[str], model: str,
                   settings: dict[str, Any], turns: int, cancel: asyncio.Event) -> None:
        # A task does not inherit the run's context var, so the usage row is attributed here.
        llm.usage_context.set({"conversation_id": conv_id, "project_id": project_id})
        try:
            new = await generate(settings, model, pick_texts(user_texts), cancel)
            if new and new != expect:
                self.apply(conv_id, expect, new, turns)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("title job for %s failed: %s", conv_id, e)

    async def stop(self) -> None:
        for ev in self._cancels.values():
            ev.set()
        tasks = list(self._tasks.values())
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
