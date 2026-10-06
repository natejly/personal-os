"""Resume an interrupted chat run: what the dead run already did, told to the model as a note.

Pure: no app imports. The tape (run_events) and the idempotency journal (executed_calls) already hold
the truth; this only reads them back into words, and decides whether a resume is allowed. The resume is
always started by the user, never by a timer.
"""
from __future__ import annotations

import json
from typing import Any

from . import redact

RESUME_NOTE = """## Resuming an interrupted reply
{reason}This is what it had already done.
{body}
Continue the task from here. Do not redo completed steps."""

# Why the previous reply ended early, keyed by the tag resumable() returns.
REASONS = {
    "interrupted": "The previous reply was cut off (the app stopped) before it finished. ",
    "error": "The previous reply failed with an error before it finished. ",
    "stopped": "The previous reply was stopped by the user before it finished. ",
    # Replies stored before the limits were removed may still carry these outcomes.
    "rounds": "The previous reply stopped before it finished. ",
    "tokens": "The previous reply stopped before it finished. ",
    "time": "The previous reply stopped before it finished. ",
    "cost": "The previous reply stopped before it finished. ",
    "length": "The previous reply was cut off at the model's output limit. ",
    "incomplete": "The previous reply's stream ended before the model finished. ",
}
# Message outcomes that leave an unfinished reply worth continuing. "loop" is a stuck run: resuming repeats it.
RESUMABLE_OUTCOMES = ("stopped", "rounds", "tokens", "time", "cost", "length", "incomplete")


def reason_tag(run: dict[str, Any], message: dict[str, Any] | None = None) -> str | None:
    """Why a run's reply is unfinished: 'interrupted', 'error', or the message outcome of a finished run; else None."""
    status = run.get("status")
    if status in ("interrupted", "error"):
        return status
    if status == "done" and (message or {}).get("outcome") in RESUMABLE_OUTCOMES:
        return message["outcome"]
    return None


def resumable(run: dict[str, Any] | None, latest_for_conv: dict[str, Any] | None, answering: bool,
              already_resumed: bool = False, *, message: dict[str, Any] | None = None) -> tuple[bool, str]:
    """(ok, reason). Only the newest run of a quiet conversation, once, and never a desk turn. A run that was
    interrupted or errored qualifies; so does a finished one whose reply row says it stopped short (`message`).
    On success the reason is the tag: 'interrupted', 'error', or the message's outcome."""
    if not run:
        return False, "no such run"
    status = run.get("status")
    tag = reason_tag(run, message)
    if tag is None:
        if status == "done":
            if (message or {}).get("outcome") == "loop":
                return False, "the reply stopped because it was repeating itself; a resume would repeat it"
            return False, "the reply finished normally"
        return False, f"the run is {status}, not interrupted"
    if run.get("kind") != "chat":
        return False, "only chat runs can be resumed"
    if run.get("desk_id"):
        return False, "desk runs continue from their desk"
    if answering:
        return False, "the conversation already has a running reply"
    if not latest_for_conv or latest_for_conv.get("run_id") != run.get("run_id"):
        return False, "a newer run exists in this conversation"
    if already_resumed:
        return False, "this run was already resumed"
    return True, tag


def taint_from_tape(events: list[tuple[int, str, Any]]) -> list[str]:
    """Every source the old run marked untrusted, in order. A resume starts tainted when this is non-empty."""
    out: list[str] = []
    for _, event, data in events:
        if event == "taint" and isinstance(data, dict) and data.get("source"):
            out.append(str(data["source"]))
    return out


def _clip(v: Any, n: int) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
    return s if len(s) <= n else s[:n] + "…"


def _args(args: Any, n: int = 300) -> str:
    if not isinstance(args, dict):
        return _clip(args, n)
    short = {k: (v[:n] + "…" if isinstance(v, str) and len(v) > n else v) for k, v in args.items()}
    return json.dumps(short, ensure_ascii=False, sort_keys=True, default=str)


def build_resume_note(run: dict[str, Any], events: list[tuple[int, str, Any]], executed: list[dict[str, Any]],
                      approvals: list[dict[str, Any]], max_text: int = 1500, max_preview: int = 300,
                      reason: str | None = None) -> str:
    mid = run.get("message_id")
    text = "".join((d.get("text") or "") for _, e, d in events
                   if e == "delta" and isinstance(d, dict) and d.get("id") == mid).strip()
    parts: list[str] = []
    if text:
        tail = text if len(text) <= max_text else "…" + text[-max_text:]
        parts.append("Text it had written so far:\n" + tail)
    done = [d for _, e, d in events if e == "tool_result" and isinstance(d, dict) and not d.get("pending")]
    if done:
        lines = []
        for d in done:
            flag = " (error)" if d.get("error") else ""
            lines.append(f"- {d.get('name')} {_args(d.get('arguments'))}{flag}: {_clip(d.get('result_preview') or d.get('error') or '', max_preview)}")
        parts.append("Tool calls that finished:\n" + "\n".join(lines))
    started = [r for r in executed if r.get("status") == "started"]
    if started:
        parts.append("Tool calls that started, outcome unknown: do NOT repeat them; tell the user to check:\n"
                     + "\n".join(f"- {r.get('tool')}" for r in started))
    waiting = [a for a in approvals if a.get("status") in ("pending", "approved")]
    if waiting:
        parts.append("Calls that were waiting on the user's approval; they were NOT run; ask again if still needed:\n"
                     + "\n".join(f"- {a.get('tool')} {_args(a.get('args'))}" for a in waiting))
    if not parts:
        parts.append("It had not done anything yet.")
    tag = reason or reason_tag(run) or "interrupted"
    return redact.scrub_command_output(
        RESUME_NOTE.format(reason=REASONS.get(tag, REASONS["interrupted"]), body="\n\n".join(parts)))


REGEN_NOTE = """## Regenerating a reply
The reply being replaced already made these changes, and they still stand:
{body}
Do NOT repeat them. Refer to what already exists; do it again only if the user asks for another."""


def build_regen_note(done: list[dict[str, Any]], max_preview: int = 300) -> str:
    """What the superseded answer already changed; `done` is its finished, error-free write calls."""
    return REGEN_NOTE.format(body="\n".join(
        f"- {d.get('name')} {_args(d.get('arguments'))}: {_clip(d.get('result_preview') or '', max_preview)}" for d in done))
