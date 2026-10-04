"""Commands: the light tier beside workflows. A command is a saved prompt, nothing more.

A command is markdown: a `---` frontmatter block (name, description, subtask, role), then the template. The
template may use `$ARGUMENTS` (everything the user typed after the name) and `$1`..`$n` (the words of it,
quoted phrases kept whole). Nothing else is expanded: no shell, no file reads, no tool calls at template
time. Filling the template is a pure string substitution, so a command cannot do anything the model could not
already do from the same text.

`subtask: true` runs the filled prompt as a child agent (subagents.py) with `role` (default researcher) and
returns its report; otherwise the filled prompt comes back as the instructions to carry out in this reply.
"""
from __future__ import annotations

import re
import shlex
from typing import Any

from .db import new_id, now

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_PLACE = re.compile(r"\$(ARGUMENTS|[1-9][0-9]?)")
MAX_BODY = 20_000


def parse(text: str) -> dict[str, Any]:
    """-> {name, description, subtask, role, body}; raises ValueError with a readable line."""
    lines = (text or "").replace("\r\n", "\n").lstrip("﻿").split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError("a command must start with a --- frontmatter block (name, description)")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("the frontmatter block is never closed with ---")
    fm: dict[str, str] = {}
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        key, sep, val = raw.strip().partition(":")
        if not sep:
            raise ValueError(f"cannot read frontmatter line: {raw.strip()[:60]}")
        v = val.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        fm[key.strip().lower()] = v
    name = fm.get("name", "")
    if not NAME_RE.match(name):
        raise ValueError("name must be lowercase letters, digits, - or _ (max 40)")
    body = "\n".join(lines[end + 1:]).strip()
    if not body:
        raise ValueError("the command needs a template after the frontmatter")
    if len(body) > MAX_BODY:
        raise ValueError(f"the template is over {MAX_BODY} characters")
    sub = fm.get("subtask", "").lower()
    if sub not in ("", "true", "false", "yes", "no", "1", "0"):
        raise ValueError("subtask must be true or false")
    return {"name": name, "description": fm.get("description", "")[:300], "subtask": sub in ("true", "yes", "1"),
            "role": fm.get("role") or None, "body": body}


def words(arguments: str) -> list[str]:
    try:
        return shlex.split(arguments or "")
    except ValueError:
        return (arguments or "").split()  # an unbalanced quote: plain whitespace words


def fill(body: str, arguments: str = "") -> str:
    """One regex pass, so an argument that itself contains `$1` is never expanded again."""
    arguments = (arguments or "").strip()
    ws = words(arguments)
    used = bool(_PLACE.search(body))

    def sub(m: re.Match[str]) -> str:
        if m.group(1) == "ARGUMENTS":
            return arguments
        i = int(m.group(1))
        return ws[i - 1] if i <= len(ws) else ""

    out = _PLACE.sub(sub, body)
    if arguments and not used:
        out += f"\n\nARGUMENTS: {arguments}"
    return out


_SLASH = re.compile(r"^/([a-z0-9][a-z0-9_-]{0,39})(?:\s+([\s\S]*))?$")


def expand(text: str, store: "Commands | None") -> str:
    """A user turn typed as `/name args` gains the filled command under it; the stored row keeps what was typed.
    Unknown names, and text that is not a leading slash command, pass through unchanged. A subtask command is
    not filled here: the model is told to run it with command_run, so the child agent, its approval and its
    taint go through the ordinary tool path."""
    m = _SLASH.match((text or "").strip()) if store is not None and isinstance(text, str) and text.startswith("/") else None
    cmd = store.get(m.group(1)) if m else None
    if not m or not cmd or cmd["name"] != m.group(1):
        return text
    args = (m.group(2) or "").strip()
    if cmd["subtask"]:
        return (f"{text}\n\n[The user ran their saved command /{cmd['name']}, which runs as a subtask. Call command_run "
                f"with name {cmd['name']!r} and arguments {args!r}, then report its result.]")
    return f"{text}\n\n[The user ran their saved command /{cmd['name']}. Its filled-in instructions follow; carry them out.]\n{fill(cmd['body'], args)}"


def expand_history(history: list[dict[str, Any]], store: "Commands | None") -> list[dict[str, Any]]:
    """Every replayed user turn, so a later turn still carries an earlier command's instructions."""
    return [{**m, "content": expand(m["content"], store)} if m.get("role") == "user" and isinstance(m.get("content"), str)
            and m["content"].startswith("/") else m for m in history]


class Commands:
    def __init__(self, db: Any) -> None:
        self.db = db

    @staticmethod
    def _row(r: Any) -> dict[str, Any]:
        d = dict(r)
        d["subtask"] = bool(d["subtask"])
        d["text"] = (f"---\nname: {d['name']}\ndescription: {d['description']}\n" + ("subtask: true\n" if d["subtask"] else "")
                     + (f"role: {d['role']}\n" if d.get("role") else "") + f"---\n{d['body']}\n")
        return d

    def list(self) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            return [self._row(r) for r in c.execute("SELECT * FROM commands ORDER BY name").fetchall()]

    def get(self, key: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM commands WHERE id=? OR name=?", (key, key)).fetchone()
        return self._row(r) if r else None

    def save(self, text: str, cmd_id: str | None = None) -> dict[str, Any]:
        f = parse(text)
        t = now()
        with self.db.tx() as c:
            clash = c.execute("SELECT id FROM commands WHERE name=?", (f["name"],)).fetchone()
            if clash and clash["id"] != cmd_id:
                raise ValueError(f"a command named {f['name']!r} already exists")
            if cmd_id and c.execute("SELECT 1 FROM commands WHERE id=?", (cmd_id,)).fetchone():
                c.execute("UPDATE commands SET name=?, description=?, body=?, subtask=?, role=?, updated_at=? WHERE id=?",
                          (f["name"], f["description"], f["body"], int(f["subtask"]), f["role"], t, cmd_id))
                rid = cmd_id
            else:
                rid = "cmd_" + new_id()
                c.execute("INSERT INTO commands(id, name, description, body, subtask, role, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                          (rid, f["name"], f["description"], f["body"], int(f["subtask"]), f["role"], t, t))
        return self.get(rid) or {}

    def delete(self, cmd_id: str) -> bool:
        with self.db.tx() as c:
            return c.execute("DELETE FROM commands WHERE id=?", (cmd_id,)).rowcount > 0


def register(tb: Any) -> None:
    """command_list / command_run. They resolve `tb.commands` at call time (app.py wires it after the toolbox)."""
    from .tools import ToolSpec, _obj, tool_error

    def _cmds() -> Commands | None:
        return getattr(tb, "commands", None)

    async def command_list(ctx: dict[str, Any]) -> Any:
        c = _cmds()
        if c is None:
            return tool_error("Commands are not available.")
        return {"commands": [{"name": x["name"], "description": x["description"], "subtask": x["subtask"]} for x in c.list()]}

    tb.specs["command_list"] = ToolSpec("command_list", "List the user's saved commands (prompt templates they run by name).",
                                        _obj({}, []), command_list, "workflows", "safe", examples=[{}])

    async def command_run(ctx: dict[str, Any], name: str, arguments: str = "") -> Any:
        c = _cmds()
        if c is None:
            return tool_error("Commands are not available.")
        cmd = c.get(str(name).lstrip("/"))
        if cmd is None:
            return tool_error(f"No command named {name!r}.", field="name", expected=", ".join(x["name"] for x in c.list()) or "(none saved)")
        prompt = fill(cmd["body"], str(arguments or ""))
        if not cmd["subtask"]:
            return {"command": cmd["name"], "instructions": prompt,
                    "note": "The user's saved command, filled in. Carry it out in this reply."}
        sub = getattr(tb, "subagents", None)
        if sub is None:
            return tool_error("Subagents are not available, so this command cannot run as a subtask.")
        out = await sub.spawn_tool(ctx, task=prompt, role=cmd["role"] or "researcher")
        ctx["tainted"] = True  # a child's report is untrusted text
        ctx.setdefault("taint_sources", []).append("command_run")  # appended every time: the fence reads growth
        return out

    tb.specs["command_run"] = ToolSpec(
        "command_run", "Run one of the user's saved commands by name (see command_list). The template is filled with the arguments "
        "($ARGUMENTS, $1..$n) and returned as instructions to follow; a command marked as a subtask runs as a child agent and "
        "returns its report.",
        _obj({"name": {"type": "string"}, "arguments": {"type": "string", "description": "Text after the command name"}}, ["name"]),
        command_run, "workflows", "executes", examples=[{"name": "summarize-folder", "arguments": "~/Documents/notes"}])
