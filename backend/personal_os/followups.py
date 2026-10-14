"""Follow-up chips: up to three short questions the user might ask next, written after a reply has ended.

Runs detached from the run (like the chat title), from the last exchange only, and reaches the UI two ways:
saved on the assistant message, and a `followups` event on the app topic for a client that is already looking.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable

from . import llm, redact

log = logging.getLogger(__name__)

MAX_FOLLOWUPS = 3
MAX_LEN = 110
TIMEOUT_S = 30.0
_CLIP = 1500
_HEAD = 400

SYSTEM = (
    "You write the user's most likely next message in a chat, so the box can pre-fill it. Reply with only a JSON "
    "array of at most 3 strings (under 15 words each), written in the user's own voice, best first. No numbering, no markdown.\n"
    "- If the assistant ended with a question, an offer or a choice, the first item answers or accepts it concretely, "
    "using the specifics from the reply. If it ended 'Want me to draft the reply to Sam?', the first item is "
    "'Yes, draft it - keep it short and friendly', not 'Can you help with email?'.\n"
    "- Otherwise the first item digs one level deeper into the most interesting specific point of the reply.\n"
    "- The other items are different directions that also go deeper.\n"
    "- Never generic filler such as 'Tell me more', 'Can you explain?' or 'Thanks'. Name the specifics.")


def tail_clip(text: str) -> str:
    """The reply's end is where the question or offer sits: keep it whole, and the opening too when the reply is long."""
    if len(text) <= _CLIP:
        return text
    return text[:_HEAD] + "\n[...]\n" + text[-(_CLIP - _HEAD):]


def parse(raw: str) -> list[str]:
    """A JSON array of strings out of whatever the model wrapped it in; empty when nothing usable is there."""
    s = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S | re.I)
    m = re.search(r"\[.*\]", s, flags=re.S)
    try:
        items = json.loads(m.group(0)) if m else []
    except ValueError:
        return []
    out: list[str] = []
    for it in items if isinstance(items, list) else []:
        t = " ".join(str(it).split()).strip("\"'`*- ")
        if t and len(t) <= MAX_LEN and t.lower() not in (o.lower() for o in out):
            out.append(t)
    return out[:MAX_FOLLOWUPS]


async def generate(settings: dict[str, Any], model: str, user_text: str, reply: str) -> list[str]:
    body = (f"The user asked:\n{redact.scrub_command_output(user_text)[:_CLIP]}\n\n"
            f"The assistant replied:\n{tail_clip(redact.scrub_command_output(reply))}")
    raw = await asyncio.wait_for(
        llm.complete(settings, settings.get("extractionModel") or model,
                     [{"role": "system", "content": SYSTEM}, {"role": "user", "content": body}],
                     kind="followups", effort="low", deadline=time.monotonic() + TIMEOUT_S),
        TIMEOUT_S + 2)
    return parse(raw)


class FollowupJobs:
    """Detached tasks; `save(message_id, list)` and `publish` are the app's own."""

    def __init__(self, save: Callable[[str, list[str]], Any], publish: Callable[[str, Any], Any]):
        self._save, self._publish = save, publish
        self._tasks: set[asyncio.Task[None]] = set()

    def spawn(self, conv_id: str, message_id: str, project_id: str | None, user_text: str, reply: str,
              model: str, settings: dict[str, Any]) -> None:
        task = asyncio.create_task(self._run(conv_id, message_id, project_id, user_text, reply, model, settings),
                                   name="followups")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, conv_id: str, message_id: str, project_id: str | None, user_text: str, reply: str,
                   model: str, settings: dict[str, Any]) -> None:
        llm.usage_context.set({"conversation_id": conv_id, "project_id": project_id})
        try:
            items = await generate(settings, model, user_text, reply)
            if items:
                self._save(message_id, items)
                self._publish("followups", {"conversation_id": conv_id, "message_id": message_id, "followups": items})
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - chips are a nicety; a failure is silent
            log.warning("follow-ups for %s failed: %s", message_id, e)

    async def stop(self) -> None:
        tasks = list(self._tasks)
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
