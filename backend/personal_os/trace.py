"""Per-reply execution trace.

A flat list of timed spans covering everything that happened to produce one assistant
message: context assembly, each LLM round, each tool call and the auto-learn pass.
Spans are streamed to the UI as they start/finish and persisted on the message.
"""
from __future__ import annotations

import time
from typing import Any

from .db import new_id


def now_ms() -> int:
    return int(time.time() * 1000)


class Tracer:
    def __init__(self, spans: list[dict[str, Any]] | None = None) -> None:
        # `spans` continues a trace the reply already wrote: auto-learn appends its span to the
        # finished message's trace from a worker, long after the run's own tracer is gone.
        self.spans: list[dict[str, Any]] = list(spans or [])

    def start(self, kind: str, name: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
        span = {"id": new_id(), "kind": kind, "name": name, "start": now_ms(), "end": None, "meta": dict(meta or {}), "error": None}
        self.spans.append(span)
        return span

    def end(self, span: dict[str, Any], meta: dict[str, Any] | None = None, error: str | None = None) -> dict[str, Any]:
        span["end"] = now_ms()
        if meta:
            span["meta"].update(meta)
        if error:
            span["error"] = error
        return span

    def fail_open(self, error: str) -> list[dict[str, Any]]:
        """Close every still-running span with an error (stream aborted / exception)."""
        closed = [self.end(s, error=error) for s in self.spans if s["end"] is None]
        return closed

    def summary(self) -> dict[str, Any]:
        if not self.spans:
            return {"total_ms": 0, "llm_calls": 0, "tool_calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        t0 = min(s["start"] for s in self.spans)
        t1 = max(s["end"] or now_ms() for s in self.spans)
        pt = sum(int((s["meta"].get("usage") or {}).get("prompt_tokens") or 0) for s in self.spans if s["kind"] == "llm")
        ct = sum(int((s["meta"].get("usage") or {}).get("completion_tokens") or 0) for s in self.spans if s["kind"] == "llm")
        return {
            "total_ms": t1 - t0,
            "llm_calls": sum(1 for s in self.spans if s["kind"] == "llm"),
            "tool_calls": sum(1 for s in self.spans if s["kind"] == "tool"),
            "prompt_tokens": pt,
            "completion_tokens": ct,
        }
