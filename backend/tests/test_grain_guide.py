"""The built-in "Using Grain" skill (guide.py): seeded approved at startup, refreshed, undeletable, readable.

Run: PERSONAL_OS_DATA_DIR=/tmp/ggtest python backend/tests/test_grain_guide.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="ggtest-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import guide  # noqa: E402
from personal_os.commands import expand_skill  # noqa: E402
from personal_os.context import build_context  # noqa: E402
from personal_os.learn import MAX_SKILL_PROCEDURE, skills_seen  # noqa: E402

passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


sk = appmod.skills
rows = [s for s in sk.list() if s["source"] == "builtin"]
check(len(rows) == 1 and rows[0]["name"] == "grain-guide", "seeded once at startup")
row = rows[0]
check(row["status"] == "approved", "approved from first run")
check(len(guide.PROCEDURE) < MAX_SKILL_PROCEDURE - 2000, "text fits the cap with room")
check(row["procedure"] == guide.PROCEDURE.strip(), "stored text is the bundled text")

# a second startup neither duplicates nor touches the row
again = guide.ensure(sk)
check(again["id"] == row["id"] and len([s for s in sk.list() if s["source"] == "builtin"]) == 1, "idempotent")
check(again["updated_at"] == row["updated_at"], "unchanged text is not rewritten")

# stale text is refreshed; a revoked skill stays revoked
sk.update(row["id"], {"procedure": "edited by hand", "status": "candidate"})
fresh = guide.ensure(sk)
check(fresh["procedure"] == guide.PROCEDURE.strip(), "changed text is overwritten")
check(fresh["status"] == "candidate", "refresh keeps a revoke")
sk.update(row["id"], {"status": "approved"})

# not deletable, by route or by the store
try:
    appmod.delete_skill(row["id"])
    check(False, "route should refuse")
except HTTPException as e:
    check(e.status_code == 409 and "built into Grain" in e.detail, "delete route says it is built in")
sk.delete(row["id"])
check(sk.get(row["id"]) is not None, "store refuses too")

# /skill grain-guide carries the procedure
check("Mental model" in expand_skill("how do I?", "grain-guide how do I add a list", sk), "/skill expands it")

# the skill tool returns it
out = asyncio.run(appmod.toolbox.specs["skill_view"].fn({}, "grain-guide"))
check("Mental model" in out.get("procedure", ""), "skill_view returns the procedure")

# routing: one hint line in the stable prefix, body never inlined or indexed
common = dict(memories=appmod.memories, graph=appmod.graph, documents=appmod.documents, project=None, project_id=None,
              query="how do I add a list in Grain?", settings=dict(appmod.db.get_settings()), global_system_prompt="You are a test.",
              skills=sk)
system, used = build_context(**common, conv_settings={})
check(guide.PROMPT_HINT in used["stable_system"], "hint is in the stable prefix")
check("Mental model" not in system and not used["skills"], "body is not injected")
sk.update(row["id"], {"status": "candidate"})
system, used = build_context(**common, conv_settings={})
check(guide.PROMPT_HINT not in system, "no hint while the guide is revoked")
sk.update(row["id"], {"status": "approved"})

# auto-learn never sees it as a skill that led a reply
seen = skills_seen([], [{"name": "skill_view", "arguments": {"skill": "grain-guide"}}], sk, None)
check(seen == [], "skills_seen skips the built-in")

print(f"test_grain_guide: {passed} checks passed")
