"""Summarised thinking: a reasoning model's raw chain-of-thought never reaches the client. While it accumulates,
a cheap model writes one short status line per chunk ("Checking calendar for conflicts"); those lines are what
the UI shows and what is stored as the message's reasoning.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from . import llm, redact

log = logging.getLogger(__name__)

MIN_CHARS = 600
MIN_GAP_S = 2.5
TIMEOUT_S = 8.0
_CLIP = 2500

SYSTEM = ("You write a live status line for an assistant that is thinking. Given its latest private reasoning, reply "
          "with ONE present-tense line of at most 8 words saying what it is working out right now, e.g. "
          "'Checking calendar for conflicts'. No trailing period, no quotes, no markdown.")


def clean(raw: str) -> str:
    s = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S | re.I)
    s = next((l for l in s.splitlines() if l.strip()), "")
    return s.strip("\"'`*#- ").rstrip(".")[:80]


def low_model(cfg: dict[str, Any], model: str) -> str:
    # One line to swap for the low-tier lookup.
    return str(cfg.get("modelLow") or cfg.get("extractionModel") or model)


async def summarise(cfg: dict[str, Any], model: str, chunk: str) -> str:
    raw = await asyncio.wait_for(
        llm.complete(cfg, low_model(cfg, model),
                     [{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": redact.scrub_command_output(chunk[-_CLIP:])}],
                     "thinking_summary", effort="low", deadline=time.monotonic() + TIMEOUT_S),
        TIMEOUT_S + 1)
    return clean(raw)


class ThinkingSummary:
    """Feed raw reasoning with `add`; `take` returns the lines finished since the last call. One call in flight at most."""

    def __init__(self, cfg: dict[str, Any], model: str, summarise_fn: Any = summarise):
        self._cfg, self._model, self._fn = cfg, model, summarise_fn
        self.lines: list[str] = []
        self._pending = ""
        self._sent = 0
        self._last = float("-inf")
        self._task: asyncio.Task[str] | None = None
        self._ready: list[str] = []

    def add(self, text: str) -> None:
        self._pending += text
        if self._task and self._task.done():
            self._harvest()
        if self._task is None and len(self._pending) >= MIN_CHARS and time.monotonic() - self._last >= MIN_GAP_S:
            chunk, self._pending = self._pending, ""
            self._last = time.monotonic()
            self._task = asyncio.ensure_future(self._fn(self._cfg, self._model, chunk))

    def _harvest(self) -> None:
        t, self._task = self._task, None
        try:
            line = t.result() if t and not t.cancelled() else ""
        except Exception as e:  # a summary must never break the run
            log.debug("thinking summary failed: %s", e)
            line = ""
        if line and (not self.lines or self.lines[-1] != line):
            self.lines.append(line)
            self._ready.append(line)

    def take(self) -> list[str]:
        if self._task and self._task.done():
            self._harvest()
        out, self._ready = self._ready, []
        return out

    def stored(self) -> str | None:
        if self._task:
            if self._task.done():
                self._harvest()
            else:
                self._task.cancel()
                self._task = None
        return "\n".join(self.lines) or None

    def reset(self) -> None:
        self.stored()
        self.lines, self._pending, self._ready = [], "", []
