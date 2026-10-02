"""Working memory that is not the chat: the plan artifact and tool-result handles.

A long serial tool loop loses the thread in two ways, and both are fixed by keeping state out of
the transcript instead of inside it:

- The model forgets the task. `Plans` holds one structured plan per conversation and the chat loop
  re-injects it as the *last* message of every round (see `app._chat_stream`), so the plan always
  sits closer to the model than any tool output it just read. The plan is the model's own artifact:
  it writes it with `todo_write`, the user watches it as a checklist and may tick steps off.
- A large tool result either floods the context or gets truncated into uselessness. `ToolResults`
  keeps the whole blob in SQLite and puts a preview, the total size and a shape summary in the
  context behind a `result_id`; `read_tool_result` pages through the rest on demand. Small results
  stay inline exactly as before, so the common case is unchanged.

A handle is scoped to its conversation: `read` takes the conversation id and will not serve another
chat's blob, which also keeps the taint story intact (taint is sticky per conversation, so content
a tainted tool fetched can only be re-read inside the reply chain that was already marked).
"""
from __future__ import annotations

import json
from typing import Any

from .db import Database, new_id, now, row_to_dict

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_plans (
  conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
  steps TEXT NOT NULL DEFAULT '[]',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_results (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  message_id TEXT,
  tool TEXT NOT NULL,
  content TEXT NOT NULL,
  total_chars INTEGER NOT NULL,
  shape TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_results_conv ON tool_results(conversation_id, created_at DESC);
"""

STATUSES = ("pending", "in_progress", "done")
MAX_STEPS = 40
MAX_STEP_CHARS = 200
MAX_NOTE_CHARS = 300

MARK = {"pending": "[ ]", "in_progress": "[>]", "done": "[x]"}

PLAN_HEADER = (
    "## Your current plan\n"
    "This is your own working memory, re-sent at the end of every round so a long tool run cannot lose it. "
    "It is the plan, not the conversation: trust it over your recollection of earlier rounds."
)
PLAN_FOOTER = (
    "Keep it accurate with todo_write: mark a step done as soon as it is done, keep at most one step "
    "in_progress, and add steps when the work turns out to be bigger than the plan says. When every step "
    "is done, write the final answer instead of calling another tool."
)


def _clean(text: Any, cap: int) -> str:
    return " ".join(str(text or "").split())[:cap]


def normalize_steps(steps: Any) -> list[dict[str, Any]]:
    """Model-supplied steps to the stored shape. Anything unusable is dropped rather than rejected."""
    out: list[dict[str, Any]] = []
    for i, s in enumerate(steps if isinstance(steps, list) else []):
        if isinstance(s, str):
            s = {"text": s}
        if not isinstance(s, dict):
            continue
        text = _clean(s.get("text") or s.get("step") or s.get("title"), MAX_STEP_CHARS)
        if not text:
            continue
        status = str(s.get("status") or "pending").strip().lower()
        if status in ("in progress", "doing", "active"):
            status = "in_progress"
        if status in ("completed", "complete", "finished"):
            status = "done"
        out.append({
            "id": str(s.get("id") or "") or f"s{i + 1}",
            "text": text,
            "status": status if status in STATUSES else "pending",
            "note": _clean(s.get("note"), MAX_NOTE_CHARS),
        })
        if len(out) >= MAX_STEPS:
            break
    return out


def render_plan(steps: list[dict[str, Any]]) -> str:
    """The checklist as the model sees it: one line per step, status first."""
    lines = []
    for i, s in enumerate(steps, 1):
        line = f"{MARK.get(s['status'], '[ ]')} {i}. {s['text']}"
        if s.get("note"):
            line += f" — {s['note']}"
        lines.append(line)
    return "\n".join(lines)


def plan_block(steps: list[dict[str, Any]]) -> str:
    done = sum(1 for s in steps if s["status"] == "done")
    return f"{PLAN_HEADER}\n\n{render_plan(steps)}\n\n{done}/{len(steps)} steps done. {PLAN_FOOTER}"


class Plans:
    """One plan per conversation. Written by `todo_write`, edited by the user, re-injected every round."""

    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            c.executescript(SCHEMA)

    def get(self, conversation_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            row = row_to_dict(c.execute("SELECT * FROM chat_plans WHERE conversation_id=?", (conversation_id,)).fetchone(), ("steps",))
        if not row:
            return None
        row["steps"] = row["steps"] if isinstance(row["steps"], list) else []
        return row

    def set(self, conversation_id: str, steps: Any) -> dict[str, Any]:
        rows = normalize_steps(steps)
        t = now()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO chat_plans(conversation_id, steps, created_at, updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(conversation_id) DO UPDATE SET steps=excluded.steps, updated_at=excluded.updated_at",
                (conversation_id, json.dumps(rows), t, t),
            )
        return self.get(conversation_id)  # type: ignore[return-value]

    def clear(self, conversation_id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM chat_plans WHERE conversation_id=?", (conversation_id,))

    def block(self, conversation_id: str) -> str:
        """The prompt block for this conversation's plan, or '' when there is no plan yet."""
        plan = self.get(conversation_id)
        return plan_block(plan["steps"]) if plan and plan["steps"] else ""


# ---- tool results as handles ----
# A result whose JSON fits in INLINE_CHARS goes into the context whole, as it always did. Anything
# larger is stored and replaced by a handle: a preview of this size, the shape, and the result_id.
INLINE_CHARS = 4000
PREVIEW_CHARS = 2000
MAX_STORED_CHARS = 2_000_000
READ_CHARS = 4000
MAX_READ_CHARS = 20000

HANDLE_NOTE = ("Large result: it is stored outside this conversation's context, not truncated. `preview` is its head. "
               "Call read_tool_result(result_id, offset, limit) to page through the rest; `shape` says what is in there.")


def shape_of(value: Any) -> dict[str, Any]:
    """What the model needs to decide whether to page in more: keys, array lengths, item shape."""
    if isinstance(value, dict):
        out: dict[str, Any] = {"type": "object", "keys": list(value.keys())[:40]}
        lists = {k: len(v) for k, v in value.items() if isinstance(v, list)}
        if lists:
            out["array_lengths"] = dict(list(lists.items())[:20])
        longest = max((k for k in lists), key=lambda k: lists[k], default=None)
        if longest and value[longest] and isinstance(value[longest][0], dict):
            out["item_keys"] = list(value[longest][0].keys())[:20]
        return out
    if isinstance(value, list):
        out = {"type": "array", "length": len(value)}
        if value and isinstance(value[0], dict):
            out["item_keys"] = list(value[0].keys())[:20]
        return out
    if isinstance(value, str):
        return {"type": "string", "chars": len(value)}
    return {"type": type(value).__name__}


def _dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


class ToolResults:
    """Full tool-result blobs, keyed by result_id, so the context can hold a handle instead of a truncation."""

    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            c.executescript(SCHEMA)

    def store(self, conversation_id: str, message_id: str | None, tool: str, content: str, shape: dict[str, Any]) -> dict[str, Any]:
        rid = "tr_" + new_id()
        total = len(content)
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO tool_results(id, conversation_id, message_id, tool, content, total_chars, shape, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (rid, conversation_id, message_id, tool, content[:MAX_STORED_CHARS], total, json.dumps(shape), now()),
            )
        return {"id": rid, "total_chars": total}

    def for_model(self, conversation_id: str, message_id: str | None, tool: str, result: Any, untrusted: bool = False) -> str:
        """The tool message's content: the result itself when small, a handle when not."""
        from .tools import summarize_result  # local: tools.py imports nothing from here

        blob = _dumps(result)
        if len(blob) <= INLINE_CHARS:
            return blob
        shape = shape_of(result)
        if untrusted:
            # Reading this blob later has to taint again. Clearing the chat banner does not delete it.
            shape["untrusted"] = True
        row = self.store(conversation_id, message_id, tool, blob, shape)
        return _dumps({
            "result_id": row["id"], "tool": tool, "total_chars": row["total_chars"], "shape": shape,
            "preview": summarize_result(result, PREVIEW_CHARS), "note": HANDLE_NOTE,
        })

    def get(self, result_id: str, conversation_id: str | None = None) -> dict[str, Any] | None:
        sql = "SELECT * FROM tool_results WHERE id=?"
        args: list[Any] = [result_id]
        if conversation_id is not None:
            sql += " AND conversation_id=?"
            args.append(conversation_id)
        with self.db.tx() as c:
            return row_to_dict(c.execute(sql, args).fetchone(), ("shape",))

    def read(self, conversation_id: str, result_id: str, offset: int = 0, limit: int = READ_CHARS) -> dict[str, Any] | None:
        """A window of a stored blob. An offset past the end returns empty text, not an error."""
        row = self.get(result_id, conversation_id)
        if not row:
            return None
        text, off = row["content"], max(0, int(offset))
        lim = max(1, min(int(limit), MAX_READ_CHARS))
        window = text[off: off + lim]
        end = off + len(window)
        out = {"result_id": row["id"], "tool": row["tool"], "total_chars": row["total_chars"], "shape": row["shape"],
               "offset": off, "chars": len(window), "text": window, "has_more": end < len(text)}
        if out["has_more"]:
            out["next_offset"] = end
        elif not window:
            out["note"] = f"Offset {off} is past the end of this result ({len(text)} characters). Nothing left to read."
        return out

    def list(self, conversation_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute("SELECT id, tool, total_chars, shape, message_id, created_at FROM tool_results "
                             "WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?",
                             (conversation_id, max(1, min(int(limit), 100)))).fetchall()
        return [row_to_dict(r, ("shape",)) for r in rows]  # type: ignore[misc]
