"""Per-section window shares, priority trimming, and pinned documents in build_context.

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

from personal_os import limits  # noqa: E402
from personal_os.context import build_context, estimate_tokens  # noqa: E402
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
check(estimate_tokens(block) <= limits.context_shares(limits.CONTEXT_WINDOW_FALLBACK)["memories"], "memories block fits its window share")
n = used["trimmed"]["memories"]
check(n > 0 and len(used["memories"]) + n == 200, "omitted count recorded")
check([m["id"] for m in used["memories"]] == [str(i) for i in range(len(used["memories"]))], "order preserved, tail dropped")
check(f"({n} more omitted)" in sys_, "omitted line shown")

# the share scales with the window: a big window keeps every note, a small one fewer
big_w, used_big = build(memory_hits=mems, window=1_000_000)
check(len(used_big["memories"]) == 200 and "memories" not in used_big["trimmed"], "a 1M window keeps everything")
small, used_small = build(memory_hits=mems, window=8000)
check(estimate_tokens("## What you remember about the user" + mem_block(small)) <= limits.context_shares(8000)["memories"], "small window share honoured")
check(len(used_small["memories"]) < len(used["memories"]), "a small window keeps fewer notes than the default")

# ---- chunks
hits = [{"chunk_id": f"c{i}", "document_id": f"d{i}", "name": f"f{i}", "idx": 0, "text": "y" * 1000, "source": "file"} for i in range(30)]
sys_, used = build(doc_hits=hits)
check(0 < len(used["chunks"]) < 30 and used["trimmed"]["chunks"] == 30 - len(used["chunks"]), "chunks trimmed and counted")
check(used["chunks"][0]["chunk_id"] == "c0", "best chunk kept")

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
check("## Pinned files" in sys_ and "pinned body" in sys_, "pinned text in prompt")
check([p["document_id"] for p in used["pinned"]] == [mine["id"]], "used['pinned'] lists the doc")
check("SECRET" not in sys_, "other project's pin out of scope")
check("…(truncated)" in sys_ and "w" * 4000 not in sys_, "clipped to the limit")
documents.set_pinned(glob["id"], True)
sys_, _ = build(project_id=proj["id"])
check("global pinned text" in sys_, "global pin visible in a project")
sys_, _ = build(project_id=proj["id"], conv={"useDocuments": False})
check("Pinned files" not in sys_, "useDocuments off drops pins")
# a retrieval hit for a pinned document is not repeated
dup = [{"chunk_id": "k", "document_id": mine["id"], "name": "mine.txt", "idx": 0, "text": "DUPLICATE", "source": "file"}]
sys_, used = build(project_id=proj["id"], doc_hits=dup)
check("DUPLICATE" not in sys_ and all(c.get("kind") == "range" for c in used["chunks"]), "pinned doc not retrieved twice")
check([(c["n"], c["document_id"]) for c in used["chunks"]] == [(i, p["document_id"]) for i, p in enumerate(used["pinned"], 1)]
      and "### [1] " in sys_, "each pinned doc is citable, numbered first")
documents.set_pinned(mine["id"], False)
documents.set_pinned(glob["id"], False)
sys_, used = build(project_id=proj["id"])
check("Pinned files" not in sys_ and used["pinned"] == [], "unpin removes it")

# ---- hidden views are named, so the model does not send the user to a page they cannot see
sys_, _ = build(settings={"hiddenViews": ["library", "health", "docs"]})
check("Library, Health, Files" in sys_ and "Settings → Sidebar" in sys_, "hidden views named with where to turn them on")
check("Hidden in this app" not in build()[0], "nothing hidden, no line")

pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
secret = documents.create(proj["id"], f"notes-{pat}.txt", "text/plain", 5, "/x", f"the key is {pat}")
documents.set_pinned(secret["id"], True)
sys_, used = build(project_id=proj["id"])
check(pat not in sys_ and sys_.count("[github-pat]") == 2, "a token in a pinned file is stripped")
check(any(pat in p["name"] for p in used["pinned"]), "the recorded pin keeps the stored name")
documents.set_pinned(secret["id"], False)

print(f"{passed} checks passed")
