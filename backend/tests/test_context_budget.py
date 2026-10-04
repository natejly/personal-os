"""Per-section token budgets, priority trimming, and pinned documents in build_context.

Run: backend/.venv/bin/python backend/tests/test_context_budget.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="ctxbud-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.context import PINNED_LIMIT, build_context, estimate_tokens  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.repos import Documents, Graph, Memories, Projects  # noqa: E402

db = Database(Path(tempfile.mkdtemp(prefix="ctxbud-db-")))
memories, graph, documents, projects = Memories(db), Graph(db), Documents(db), Projects(db)
passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def build(settings: dict[str, Any] | None = None, project_id: str | None = None, conv: dict[str, Any] | None = None,
          doc_hits: list[dict[str, Any]] | None = None, memory_hits: list[dict[str, Any]] | None = None, **kw: Any):
    return build_context(memories=memories, graph=graph, documents=documents, project=None, project_id=project_id, query="alpha",
                         settings=settings or {}, conv_settings=conv or {}, global_system_prompt="", doc_hits=doc_hits or [],
                         memory_hits=memory_hits or [], **kw)


def mem_block(system: str) -> str:
    return system.split("## What you remember about the user")[1].split("\n\n##")[0]


# ---- memories: 200 long notes
mems = [{"id": str(i), "content": f"note {i} " + "x" * 200, "project_id": None} for i in range(200)]
sys_, used = build(memory_hits=mems)
block = "## What you remember about the user" + mem_block(sys_)
check(estimate_tokens(block) <= 1500, "memories block fits its budget")
n = used["trimmed"]["memories"]
check(n > 0 and len(used["memories"]) + n == 200, "omitted count recorded")
check([m["id"] for m in used["memories"]] == [str(i) for i in range(len(used["memories"]))], "order preserved, tail dropped")
check(f"({n} more omitted)" in sys_, "omitted line shown")

# budget 0 = unlimited, identical to an uncapped build
unl, used0 = build(settings={"contextBudget": {"memories": 0}}, memory_hits=mems)
check(len(used0["memories"]) == 200 and "memories" not in used0["trimmed"] and "omitted" not in unl, "budget 0 keeps everything")
small, _ = build(settings={"contextBudget": {"memories": 100}}, memory_hits=mems)
check(estimate_tokens("## What you remember about the user" + mem_block(small)) <= 100, "custom budget honoured")

# ---- chunks
hits = [{"chunk_id": f"c{i}", "document_id": f"d{i}", "name": f"f{i}", "idx": 0, "text": "y" * 1000, "source": "file"} for i in range(30)]
sys_, used = build(doc_hits=hits)
check(0 < len(used["chunks"]) < 30 and used["trimmed"]["chunks"] == 30 - len(used["chunks"]), "chunks trimmed and counted")
check(used["chunks"][0]["chunk_id"] == "c0", "best chunk kept")

# ---- activity / meetings (pre-built text blocks)
class Block:
    def __init__(self, text: str) -> None:
        self.text = text

    def context_block(self) -> str:
        return self.text


big = "## Recent activity\n" + "\n".join(f"- line {i} " + "z" * 100 for i in range(100))
_, used = build(activity=Block(big), meetings=Block(big.replace("activity", "meetings")))
check(used["trimmed"]["activity"] > 0 and estimate_tokens(used["activity"]) <= 800 + 20, "activity trimmed")
check(used["trimmed"]["meetings"] > 0 and used["meetings"].startswith("## Recent meetings"), "meetings trimmed, heading kept")

# ---- pinned documents
proj = projects.create("P")
other = projects.create("Q")
mine = documents.create(proj["id"], "mine.txt", "text/plain", 5, "/x", "pinned body " + "w" * 9000)
theirs = documents.create(other["id"], "theirs.txt", "text/plain", 5, "/x", "SECRET other project")
glob = documents.create(None, "global.txt", "text/plain", 5, "/x", "global pinned text")
check(documents.list(proj["id"])[0]["pinned"] == 0, "unpinned by default")
for d in (mine, theirs):
    documents.set_pinned(d["id"], True)
sys_, used = build(project_id=proj["id"])
check("## Pinned documents" in sys_ and "pinned body" in sys_, "pinned text in prompt")
check([p["document_id"] for p in used["pinned"]] == [mine["id"]], "used['pinned'] lists the doc")
check("SECRET" not in sys_, "other project's pin out of scope")
check("…(truncated)" in sys_ and "w" * (PINNED_LIMIT + 50) not in sys_, "clipped to the limit")
documents.set_pinned(glob["id"], True)
sys_, _ = build(project_id=proj["id"])
check("global pinned text" in sys_, "global pin visible in a project")
sys_, _ = build(project_id=proj["id"], conv={"useDocuments": False})
check("Pinned documents" not in sys_, "useDocuments off drops pins")
# a retrieval hit for a pinned document is not repeated
dup = [{"chunk_id": "k", "document_id": mine["id"], "name": "mine.txt", "idx": 0, "text": "DUPLICATE", "source": "file"}]
sys_, used = build(project_id=proj["id"], doc_hits=dup)
check("DUPLICATE" not in sys_ and used["chunks"] == [], "pinned doc not retrieved twice")
documents.set_pinned(mine["id"], False)
documents.set_pinned(glob["id"], False)
sys_, used = build(project_id=proj["id"])
check("Pinned documents" not in sys_ and used["pinned"] == [], "unpin removes it")

# ---- hidden views are named, so the model does not send the user to a page they cannot see
sys_, _ = build(settings={"hiddenViews": ["library", "activity"]})
check("Library, Activity" in sys_ and "Settings → Modules" in sys_, "hidden views named with where to turn them on")
check("Hidden in this app" not in build()[0], "nothing hidden, no line")

print(f"{passed} checks passed")
