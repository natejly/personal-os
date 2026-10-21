"""`grain ext new`: the files of a working extension to start from."""
from __future__ import annotations

from pathlib import Path
from string import Template

from ..manifest import ID
from .check import BUILTINS

MANIFEST = Template('''{
  "id": "$id",
  "name": "$title",
  "description": "A note tool and a line of context. Replace both.",
  "version": "0.1.0",
  "apiVersion": 1,
  "kind": "extension",
  "enabled": true,
  "activation": ["startup"],
  "contributes": {
    "tools": [
      { "name": "${ident}_note", "danger": "writes", "group": "$id", "description": "Save a short note.", "schema": "tools/${ident}_note.json" }
    ]
  },
  "hooks": { "context.contribute": {} },
  "permissions": ["db:own", "context:volatile"],
  "data": { "tables": ["${ident}_notes"], "migrations": "migrations/" }
}
''')

LOOP_MANIFEST = Template('''{
  "id": "$id",
  "name": "$title",
  "description": "An agent loop that drafts an answer, critiques it and revises it. Replace the steps.",
  "version": "0.1.0",
  "apiVersion": 1,
  "kind": "extension",
  "enabled": true,
  "activation": ["startup"],
  "contributes": {
    "runtime": [
      { "slot": "agent.loop", "id": "draft-critique-revise", "label": "$title: draft, critique, revise" }
    ]
  },
  "permissions": ["runtime:loop"]
}
''')

TOOL_SCHEMA = '''{
  "type": "object",
  "properties": {
    "text": { "type": "string", "description": "The note to save." }
  },
  "required": ["text"]
}
'''

MIGRATION = Template('''CREATE TABLE ${ident}_notes (
  id INTEGER PRIMARY KEY,
  text TEXT NOT NULL
);
''')

BACKEND = Template('''"""$title: saves notes and tells the model how many there are."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from grain_sdk import Contribution, Extension, ToolSpec


def activate(api: Extension) -> None:
    def count() -> int:
        with api.db.tx() as c:
            return c.execute("SELECT COUNT(*) FROM ${ident}_notes").fetchone()[0]

    async def note(ctx: dict[str, Any], text: str) -> dict[str, Any]:
        with api.db.tx() as c:
            c.execute("INSERT INTO ${ident}_notes (text) VALUES (?)", (text,))
        return {"saved": text, "notes": count()}

    schema = json.loads((Path(__file__).parent / "tools" / "${ident}_note.json").read_text("utf-8"))
    api.tool(ToolSpec("${ident}_note", "Save a short note.", schema, note, "$id", danger="writes"))
    api.context(lambda payload: Contribution(f"The user has {count()} saved notes."), slot="volatile")
''')

LOOP_BACKEND = Template('''"""$title: an agent loop that drafts an answer, critiques it and revises it.

A loop reaches the model, the tools and the context only through the harness `run` is handed, so every tool call is judged by
the same permission checks and approval cards as in the chat. The only import from the app is `loop_api` (the import fence checks
the whole file); the introspection modules of the standard library are off limits too.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Any, AsyncIterator

from grain_sdk import Extension
from personal_os.runtime.loop_api import LimitReached, LoopEvent, LoopHarness, TurnInput

CRITIQUE = "Critique the draft answer above for the user's request. List what is wrong, missing or unproven, briefly. Call no tool."
REVISE = "Write the final answer for the user: the draft with the critique applied. Do not mention the draft or the critique."


class DraftCritiqueRevise:
    id = "$id/draft-critique-revise"
    bounds = MappingProxyType({"draft": 6})  # rounds of tools while drafting; the core counts them and caps the turn's model calls

    async def run(self, turn: TurnInput, h: LoopHarness) -> AsyncIterator[LoopEvent]:
        try:
            draft = ""
            yield "status", {"text": "Drafting"}
            messages = h.context.build()  # the context pipeline's messages plus this run's own, kept as the draft grows
            while not h.limits.stopped:
                h.limits.next_round("draft")  # raises LimitReached once the declared bound is spent
                end: dict[str, Any] = {}
                async for event in h.model.stream(messages, tools=True):
                    if event["type"] == "end":
                        end = event
                if h.limits.stopped:
                    return
                if not end.get("tool_calls"):
                    draft = end.get("text", "")
                    break
                await h.tools.run(end["tool_calls"])  # appends the tool messages to `messages`
            yield "status", {"text": "Critiquing"}
            critique = ""
            async for event in h.model.stream(h.context.build([f"Draft answer:\\n{draft}\\n\\n{CRITIQUE}"]), tools=False):
                if event["type"] == "end":
                    critique = event["text"]
            yield "status", {"text": "Revising"}
            async for event in h.model.stream(h.context.build([f"Draft answer:\\n{draft}\\n\\nCritique:\\n{critique}\\n\\n{REVISE}"]), tools=False):
                if event["type"] == "delta":
                    yield "delta", {"text": event["text"]}  # only the revision reaches the reply
        except LimitReached as e:
            yield "delta", {"text": f"I stopped here: {e}."}


def activate(api: Extension) -> None:
    api.provide("agent.loop", DraftCritiqueRevise(), id="draft-critique-revise", label="$title: draft, critique, revise")
''')

LOOP_TEST = Template('''from pathlib import Path

from grain_sdk.testing import Harness

ROOT = Path(__file__).parents[1]


def test_the_loop_drafts_critiques_and_revises() -> None:
    with Harness(ROOT) as h:
        h.select("agent.loop", "$id/draft-critique-revise")
        turn = h.chat("say hi", model_script=["a draft", "the draft is fine", "the final answer"])
        assert turn.error is None
        assert turn.text == "the final answer"  # only the revision is streamed
        assert len(turn.requests) == 3
        assert "a draft" in str(turn.requests[1]) and "the draft is fine" in str(turn.requests[2])


def test_a_tool_round_runs_before_the_critique() -> None:
    call = {"id": "c1", "name": "todo_write", "arguments": {"steps": [{"text": "one", "status": "pending"}]}}
    with Harness(ROOT, settings={"permissionMode": "allow_all"}) as h:
        h.select("agent.loop", "$id/draft-critique-revise")
        turn = h.chat("plan it", model_script=[{"tool_calls": [call]}, "a draft", "fine", "done"])
        assert turn.error is None and turn.text == "done"
''')

FRONTEND = '''// The renderer half. Register views, settings panels and slot items from `activate`.
export function activate(): void {}
'''

TEST = Template('''from pathlib import Path

from grain_sdk.testing import Harness

ROOT = Path(__file__).parents[1]


def test_the_note_count_reaches_the_prompt_and_the_prefix_stays_put() -> None:
    with Harness(ROOT) as h:
        h.chat("hello")
        h.assert_context_contains("saved notes")
        h.chat("hello again")
        h.assert_prefix_stable()


def test_the_tool_saves_a_note() -> None:
    call = {"id": "c1", "name": "${ident}_note", "arguments": {"text": "buy milk"}}
    with Harness(ROOT, settings={"permissionMode": "allow_all"}) as h:
        turn = h.chat("note it", model_script=[{"tool_calls": [call]}, "saved"])
        assert turn.error is None and turn.text == "saved"
        assert "buy milk" in str(turn.requests[1])
''')


def scaffold(ext_id: str, parent: Path, loop: bool = False) -> Path:
    """Write `<parent>/<ext_id>/` and return it. Refuses an id that is not a valid new id or is a built-in's, or a folder that exists."""
    if not ID.fullmatch(ext_id):
        raise ValueError("an extension id starts with a letter and holds lowercase letters, digits and hyphens")
    if (BUILTINS / ext_id / "extension.json").is_file():
        raise ValueError(f"a built-in extension already has the id {ext_id!r}")
    root = parent / ext_id
    if root.exists():
        raise FileExistsError(f"{root} already exists")
    values = {"id": ext_id, "ident": ext_id.replace("-", "_"), "title": ext_id.replace("-", " ").title()}
    files = {
        "extension.json": LOOP_MANIFEST.substitute(values),
        "extension.py": LOOP_BACKEND.substitute(values),
        "tests/test_extension.py": LOOP_TEST.substitute(values),
    } if loop else {
        "extension.json": MANIFEST.substitute(values),
        "extension.py": BACKEND.substitute(values),
        "extension.tsx": FRONTEND,
        f"tools/{values['ident']}_note.json": TOOL_SCHEMA,
        "migrations/0001_init.sql": MIGRATION.substitute(values),
        "tests/test_extension.py": TEST.substitute(values),
    }
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, "utf-8")
    return root
