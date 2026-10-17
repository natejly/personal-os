"""The event stream a swapped model client returns, held to the shape `llm.stream_chat` has always yielded (design doc
section 10.7.2): delta and reasoning text, retry notes, and exactly one `end` that carries the tool calls. The core
wrapper repairs what a client sends (`checked`); the contract suite refuses a client that needs repairing (`problems`)."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator, Iterable

TEXT_EVENTS = ("delta", "reasoning")


def _call(raw: Any) -> dict[str, str] | None:
    """A tool call as the loop reads it ({id, name, arguments: JSON text}), or None when it is not one."""
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not raw["name"]:
        return None
    args = raw.get("arguments")
    if isinstance(args, dict):
        args = json.dumps(args, ensure_ascii=False)
    if not isinstance(args, str) or not isinstance(raw.get("id"), str) or not raw["id"]:
        return None
    return {"id": raw["id"], "name": raw["name"], "arguments": args}


def problems(events: Iterable[Any]) -> list[str]:
    """What is wrong with a finished stream: empty means a loop could consume it as it is."""
    found: list[str] = []
    ends = 0
    for ev in events:
        kind = ev.get("type") if isinstance(ev, dict) else None
        if ends:
            found.append("an event came after the end")
        if kind in TEXT_EVENTS:
            if not isinstance(ev.get("text"), str):
                found.append(f"a {kind} event has no text")
        elif kind == "retry":
            continue
        elif kind == "end":
            ends += 1
            if not isinstance(ev.get("tool_calls"), list) or any(_call(c) != c for c in ev["tool_calls"]):
                found.append("the end event's tool_calls are not a list of {id, name, arguments}")
        else:
            found.append(f"unknown event type {kind!r}")
    if ends != 1:
        found.append(f"the stream has {ends} end events, it needs exactly one")
    return found


def _end(ev: dict[str, Any], sent_chars: int, prompt_chars: int, incomplete: bool) -> dict[str, Any]:
    calls = [c for raw in ev.get("tool_calls") or () if (c := _call(raw))] if isinstance(ev.get("tool_calls"), list) else []
    out: dict[str, Any] = {
        "type": "end", "finish_reason": ev["finish_reason"] if isinstance(ev.get("finish_reason"), str) else None,
        "tool_calls": calls, "usage": ev["usage"] if isinstance(ev.get("usage"), dict) else None,
        "usage_est": {"prompt_tokens": prompt_chars // 4, "completion_tokens": (sent_chars + sum(len(c["arguments"]) for c in calls)) // 4},
        "incomplete": incomplete or bool(ev.get("incomplete"))}
    if isinstance(ev.get("reasoning"), str) and ev["reasoning"]:
        out["reasoning"] = ev["reasoning"]
    return out


async def checked(events: AsyncIterator[Any], prompt_chars: int) -> AsyncIterator[dict[str, Any]]:
    """`events` with every malformed one dropped and exactly one `end`: a second end and anything after it is ignored, a
    stream that stops without one gets an `incomplete` end (the loop then clears its tool calls)."""
    sent = 0
    async for ev in events:
        kind = ev.get("type") if isinstance(ev, dict) else None
        if kind in TEXT_EVENTS and isinstance(ev.get("text"), str):
            sent += len(ev["text"])
            yield {"type": kind, "text": ev["text"]}
        elif kind == "retry":
            yield ev
        elif kind == "end":
            yield _end(ev, sent, prompt_chars, incomplete=False)
            return
    yield _end({}, sent, prompt_chars, incomplete=True)
