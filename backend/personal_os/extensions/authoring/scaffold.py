"""`grain ext new`: the files of a working extension (or a profile) to start from."""
from __future__ import annotations

import re
from pathlib import Path
from string import Template

NEW_ID = re.compile(r"[a-z][a-z0-9-]*")

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

PROFILE = Template('''{
  "id": "$id",
  "name": "$title",
  "description": "Theme tokens and defaults; a profile runs no code.",
  "version": "0.1.0",
  "apiVersion": 1,
  "kind": "profile",
  "contributes": { "theme": {}, "defaults": {} }
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

FRONTEND = '''// The renderer half. `ui` is the app's UiApi; register views, settings panels and slot items on it.
export function activate(ui: unknown): void {
  void ui
}
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


def scaffold(ext_id: str, parent: Path, profile: bool = False) -> Path:
    """Write `<parent>/<ext_id>/` and return it. Refuses an id that is not a valid new id or a folder that exists."""
    if not NEW_ID.fullmatch(ext_id):
        raise ValueError("an extension id starts with a letter and holds lowercase letters, digits and dashes")
    root = parent / ext_id
    if root.exists():
        raise FileExistsError(f"{root} already exists")
    values = {"id": ext_id, "ident": ext_id.replace("-", "_"), "title": ext_id.replace("-", " ").title()}
    files = {"extension.json": PROFILE.substitute(values)} if profile else {
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
