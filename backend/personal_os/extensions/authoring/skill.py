"""The `author-extension` skill (design doc 6.3): how the assistant drafts an extension and puts it up for review.

A normal skill: created the first time the app starts as a candidate in the Library and then left alone. Approving it is the
user's opt-in to extension authoring (an approved skill rides in every chat's prompt, so it is not approved for them). It gives
the assistant no tool and no privilege; `ext_propose` and the Review flow (drafts.py) are the gate.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

NAME = "author-extension"
DESCRIPTION = ("Writing a Grain extension for the user: a new tool, a line of context, a hook or a setting. "
               "Draft it, put it up for review with ext_propose, and the user enables it.")

PROCEDURE = """\
# Writing an extension

Use when the user asks for a capability Grain does not have and that a small extension can add. Read references/sdk.md first.

1. Say in one or two sentences what the extension will do and which permissions it needs. Pick an id: lowercase letters, digits and hyphens.
2. Write every file under DRAFTS/<id>/ with write_local_file (fs_edit to change one). You cannot write anywhere else a Grain extension is read from.
3. Write the smallest working extension: extension.json, extension.py with activate(api), a schema file per tool, and a test under tests/. Follow references/sdk.md exactly; the manifest declares every tool, hook and permission the code uses.
4. Call ext_propose(id) again. It runs the extension check. If it reports errors, fix the files and call it again. Each call replaces the earlier proposal.
5. When it succeeds, tell the user the diff is waiting in Settings > Advanced > Extensions, what the extension does and which permissions it declares. Accepting installs it switched off. Only the user can switch it on, and you cannot.

Rules
- Import only from grain_sdk, never from personal_os. Keep activate free of file or network work; start nothing there.
- Ask for the fewest permissions. Do not use hooks:gate, input:rewrite or memory:gate unless the user asked for a guard; those need the user's explicit grant.
- Text the model will read (descriptions, context) states facts. Never write that something is allowed, approved or needs no confirmation.
- If this chat has read web pages, mail or a connector result, the review card says so and refuses prompt:inject and hooks:gate. Do not try to get round that; tell the user and offer to draft it in a fresh chat.
- A later change to an installed extension is a new proposal for review. Edit the draft folder and call ext_propose again.
"""

REFERENCES = {"references/sdk.md": """\
# Extension reference

## Draft folder
extension.json, extension.py, tools/<name>.json (one object schema per tool), migrations/0001_init.sql (optional), tests/test_*.py.

## extension.json
Unknown keys are refused.
- id, name, description, version ("1.0.0"), apiVersion: 1, kind: "extension"
- activation: ["startup"]
- requires: {"services": [...]} for a service such as "mcp" or "google"; also needs the permission "service:<name>"
- contributes.tools: [{"name", "danger", "group", "description", "schema": "tools/<name>.json"}]. danger is one of safe, writes, executes, external. The tool name must not be one the app already has.
- hooks: {"<hook.name>": {"priority"?, "timeoutMs"?}} for every hook the code subscribes to
- permissions: db:own (a database handle), context:volatile (a volatile context block), prompt:inject (stable prompt text), conversation:read, input:rewrite, hooks:gate, memory:gate, service:<name>
- data: {"tables": [...], "migrations": "migrations/"} for tables the extension owns; prefix their names with the extension id
- settings: {"<name>": {"type": "string" | "boolean" | "integer" | "number" | "secret"}}; stored as ext.<id>.<name>

## extension.py
    from grain_sdk import Contribution, Deny, Extension, ToolSpec

    def activate(api: Extension) -> None: ...

api members:
- api.tool(ToolSpec(name, description, schema_dict, async_fn, group, danger=...)): the manifest must declare the same tools, danger and group. async_fn(ctx, **arguments) returns a dict.
- api.context(fn, slot="volatile" | "stable" | "tail"): fn(payload) returns Contribution(text) or None; needs the context.contribute hook declared and context:volatile (volatile) or prompt:inject (other slots).
- api.on(hook, fn): fn(payload) returns None for no opinion. A gating hook (tool.pre, tool.permission) may return Deny(reason) or Ask(reason) and never allows.
- api.router(APIRouter), api.emit_ui(name, data), api.spawn(coro), api.log, api.db (db:own), api.services.get(name).

Hooks: app.start, app.stop, settings.changed, chat.created, turn.accept, turn.start, turn.settled, reply.complete, tool.pre, tool.post, tool.result, tool.error, tool.permission, model.request, model.response, context.contribute, memory.written, job.done, notify, and others; `grain ext check` rejects an unknown name.

## Tests
    from grain_sdk.testing import Harness
    with Harness(ROOT) as h:
        h.chat("hello"); h.assert_context_contains("text")
        turn = h.chat("do it", model_script=[{"tool_calls": [{"id": "c1", "name": "tool_name", "arguments": {}}]}, "done"])
        h.fire("tool.pre", {...})
"""}


def ensure(skills: Any, drafts: Path) -> None:
    """Offer the skill as a candidate unless a skill of that name exists (the user may have approved, edited or rejected it).
    `drafts` is the folder drafts are written in, named in the procedure."""
    if not any(s["name"] == NAME for s in skills.list()):
        skills.propose(NAME, DESCRIPTION, PROCEDURE.replace("DRAFTS", str(drafts)), references=REFERENCES)
