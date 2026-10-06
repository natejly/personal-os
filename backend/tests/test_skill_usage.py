"""Skill use counters and $name force-inject."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="skillusage-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.context import build_context  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.learn import Skills  # noqa: E402

passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


class _Repo:
    def for_context(self, *_a: Any, **_k: Any) -> list[Any]:
        return []

    def profile(self, *_a: Any, **_k: Any) -> list[Any]:
        return []

    def matching(self, *_a: Any, **_k: Any) -> list[Any]:
        return []

    def neighborhood(self, *_a: Any, **_k: Any) -> dict[str, list[Any]]:
        return {"nodes": [], "edges": []}

    def search(self, *_a: Any, **_k: Any) -> list[Any]:
        return []

    def pinned(self, *_a: Any, **_k: Any) -> list[Any]:
        return []


db = Database(Path(tempfile.mkdtemp(prefix="skillusage-db-")))
skills = Skills(db)
ok = skills.propose("weekly-review", "Run the review", "STEP-ONE-OK")
skills.update(ok["id"], {"status": "approved"})
skills.propose("secret-plan", "Not reviewed", "STEP-CANDIDATE")
other = skills.propose("other-skill", "Another", "STEP-OTHER")
skills.update(other["id"], {"status": "approved"})
check(ok["use_count"] == 0 and ok["last_used_at"] is None, "new skills start unused")

skills.bump_use([ok["id"], ok["id"]])
row = skills.get(ok["id"])
check(row["use_count"] == 2 and row["last_used_at"], "bump_use counts and stamps")


def build(query: str) -> tuple[str, dict[str, Any]]:
    r = _Repo()
    return build_context(memories=r, graph=r, documents=r, project=None, project_id=None, query=query, settings={},
                         conv_settings={"skillsDisclosure": "manifest"}, global_system_prompt="", skills=skills)


system, used = build("please do the $weekly-review now")
check("STEP-ONE-OK" in system, "$name injects the approved body under manifest mode")
check("STEP-OTHER" not in system, "an unnamed skill stays out")
check(any(s["id"] == ok["id"] and s["disclosure"] == "forced" for s in used["skills"]), "forced use is reported")
system, _ = build("run $secret-plan")
check("STEP-CANDIDATE" not in system, "a candidate name never injects")
system, _ = build("weekly-review without the sigil")
check("STEP-ONE-OK" not in system, "no sigil, no body")
import asyncio  # noqa: E402

from personal_os import app as appmod  # noqa: E402

live = appmod.skills.propose("viewed-skill", "d", "VIEW-BODY")
appmod.skills.update(live["id"], {"status": "approved"})
cand = appmod.skills.propose("viewed-candidate", "d", "VIEW-CAND")
out = asyncio.run(appmod.toolbox.call("skill_view", {"skill": "viewed-skill"}, {"project_id": None}))
row = appmod.skills.get(live["id"])
check(not out.get("error") and row["use_count"] == 1 and row["last_used_at"], "skill_view bumps use_count and last_used_at")
out = asyncio.run(appmod.toolbox.call("skill_view", {"skill": "viewed-candidate"}, {"project_id": None}))
check(out.get("error") and appmod.skills.get(cand["id"])["use_count"] == 0, "a candidate is refused and not bumped")
print(f"test_skill_usage: {passed} checks passed")
