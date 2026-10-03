"""Docs routes, revisions and the doc_* tools. Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_docs.py"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="docstest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os.app import AUTH_TOKEN, app, docs, toolbox  # noqa: E402
from personal_os.docs import diff_stat, unified_diff, word_count  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


def j(method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    r = client.request(method, path, json=body)
    assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text[:300]} (wanted {expect})"
    return r.json()


def call(name: str, args: dict[str, Any], ctx: dict[str, Any] | None = None) -> Any:
    base: dict[str, Any] = {"project_id": None}
    if ctx:
        base.update(ctx)
    return asyncio.get_event_loop().run_until_complete(toolbox.call(name, args, base))


# ---- helpers ----
check(word_count("one two  three\nfour") == 4, "word_count counts across whitespace")
check(diff_stat("a\nb", "a\nc") == {"added": 1, "removed": 1}, "diff_stat counts a swap")
check("+c" in unified_diff("a\nb\n", "a\nc\n"), "unified_diff emits the new line")

# ---- the feature's prefix must not collide with FastAPI's own docs ----
paths = {getattr(r, "path", "") for r in app.routes}
check("/docs" in paths and "/api-docs" in paths, "swagger moved aside so /docs is the feature's")
check(not any(p.startswith("/docs/oauth2") for p in paths), "no swagger cruft under /docs")

# ---- CRUD ----
check(j("GET", "/docs") == [], "no docs to begin with")
d = j("POST", "/docs", {"title": "Paper", "content": "# Paper\n\nIntro with $\\alpha$.\n"})
did = d["id"]
check(d["title"] == "Paper" and d["words"] == word_count(d["content"]), "create returns the doc with a word count")
check(d["pending"] == [], "a new doc has nothing pending")

rows = j("GET", "/docs")
check(len(rows) == 1, "the doc is listed")
check("content" not in rows[0], "list rows carry a preview, not the whole body")
check(rows[0]["preview"].startswith("# Paper"), "list rows carry a preview")
check(rows[0]["pending"] == 0, "list rows carry a pending count")

# ---- autosave records revisions, and a burst of typing coalesces into one ----
j("PUT", f"/docs/{did}", {"content": "# Paper\n\nA better intro with $\\beta$.\n", "summary": "Rewrite intro"})
j("PUT", f"/docs/{did}", {"content": "# Paper\n\nA better intro with $\\beta$, plus more.\n"})
revs = j("GET", f"/docs/{did}/revisions")
check(len(revs) == 1, f"quick successive saves coalesce into one revision, got {len(revs)}")
check(revs[0]["author"] == "user" and revs[0]["status"] == "applied", "a user save is applied immediately")

# ---- metadata changes stay out of the history ----
before = len(j("GET", f"/docs/{did}/revisions"))
m = j("PATCH", f"/docs/{did}", {"starred": True, "folder": "Research"})
check(m["starred"] == 1 and m["folder"] == "Research", "metadata patch applies")
check(len(j("GET", f"/docs/{did}/revisions")) == before, "a metadata change is not a revision")

# ---- an assistant edit is proposed, not applied ----
body_now = j("GET", f"/docs/{did}")["content"]
out = call("doc_edit", {"doc": "Paper", "edits": [{"find": "A better intro", "replace": "A much better intro"}], "summary": "Tighten"})
check(out["status"] == "pending_review", f"doc_edit proposes rather than writes: {out}")
check(j("GET", f"/docs/{did}")["content"] == body_now, "the doc is untouched until the edit is accepted")
check(j("GET", "/docs/pending") == {"pending": 1}, "the pending badge counts it")
rev_id = out["revision_id"]

one = j("GET", f"/docs/revisions/{rev_id}")
check(one["patch"].startswith("---"), "a single revision carries a unified patch")
check(one["stale"] is False, "nothing has moved, so the proposal is not stale")

# ---- a user edit under a pending proposal marks it stale ----
j("PUT", f"/docs/{did}", {"content": body_now + "\nA paragraph the user added.\n"})
full = j("GET", f"/docs/{did}")
check(full["pending"][0]["stale"] is True, "editing under a proposal marks it stale")
check(full["pending"][0]["stat_vs_current"] is not None, "a pending revision is also diffed against the current body")

# ---- accept applies it and records what was really replaced ----
replaced = full["content"]
after = j("POST", f"/docs/revisions/{rev_id}/accept")
check("A much better intro" in after["content"], "accept applies the proposal")
check(j("GET", f"/docs/revisions/{rev_id}")["before"] == replaced, "accept records the text it actually replaced")
check(j("GET", "/docs/pending") == {"pending": 0}, "the badge clears")
j("POST", f"/docs/revisions/{rev_id}/accept", expect=404)
check(True, "a revision cannot be accepted twice")

# ---- reject leaves the doc alone ----
rej = docs.propose(did, "obliterated", "Replace everything")
j("POST", f"/docs/revisions/{rej['id']}/reject")
check(j("GET", f"/docs/{did}")["content"] != "obliterated", "reject leaves the doc alone")
check(j("GET", f"/docs/revisions/{rej['id']}")["status"] == "rejected", "reject is recorded")

# ---- restore walks backwards, and is itself a revision ----
# Coalescing rewrites a folded revision's timestamp, so "oldest row" is not necessarily an older
# *version*; pick a revision that genuinely differs from what the doc says now.
history = j("GET", f"/docs/{did}/revisions")
current = j("GET", f"/docs/{did}")["content"]
target = next(r for r in history if r["after"] != current and r["status"] == "applied")
n_before = len(history)
back = j("POST", f"/docs/revisions/{target['id']}/restore")
check(back["content"] == target["after"], "restore reinstates that version")
check(len(j("GET", f"/docs/{did}/revisions")) == n_before + 1, "restore is itself recorded, so it can be undone")
check(j("GET", f"/docs/{did}/revisions")[0]["summary"].startswith("Restored"), "the restore is labelled in the history")

# restoring to the version already on screen changes nothing and adds nothing
same = j("GET", f"/docs/{did}/revisions")[0]
n = len(j("GET", f"/docs/{did}/revisions"))
j("POST", f"/docs/revisions/{same['id']}/restore")
check(len(j("GET", f"/docs/{did}/revisions")) == n, "restoring the current version is a no-op")

# ---- tools ----
names = {n for n, s in toolbox.specs.items() if s.group == "docs"}  # by group: doc_guide shares the prefix, not the feature
check(names == {"doc_list", "doc_search", "doc_read", "doc_create", "doc_edit"}, f"doc tools registered: {names}")
check(toolbox.specs["doc_edit"].danger == "writes", "doc_edit is a write")
check(toolbox.specs["doc_read"].danger == "safe", "doc_read is read-only")

read = call("doc_read", {"doc": "Paper"})
check("   1| " in read["text"], "doc_read numbers the lines")

made = call("doc_create", {"title": "Derivation", "content": "$$\\int_0^1 x^2\\,dx = \\tfrac13$$\n"})
check(made["doc_id"] and made["created"] == "Derivation", "doc_create makes a doc")

hits = call("doc_search", {"query": "Derivation"})
check(any(h["title"] == "Derivation" for h in hits["results"]), f"doc_search finds it: {hits}")

# a find that matches nothing, or twice, is refused with a usable hint rather than guessed at
docs.save(made["doc_id"], content="dup\ndup\n", coalesce=False)
miss = call("doc_edit", {"doc": "Derivation", "edits": [{"find": "nowhere", "replace": "x"}]})
check("error" in miss and "0 times" in miss["error"] and miss["hint"], f"a missing find is refused: {miss}")
amb = call("doc_edit", {"doc": "Derivation", "edits": [{"find": "dup", "replace": "x"}]})
check("error" in amb and "2 times" in amb["error"], f"an ambiguous find is refused: {amb}")
none = call("doc_edit", {"doc": "Derivation"})
check("error" in none and "Nothing to change" in none["error"], "an empty edit is refused")
ghost = call("doc_edit", {"doc": "no such doc"})
check("error" in ghost and ghost["docs"], "an unknown doc is refused with the titles that do exist")

app_out = call("doc_edit", {"doc": "Derivation", "append": "\n## Next\n\nMore.\n", "summary": "Add a section"})
check(app_out["status"] == "pending_review" and app_out["lines_added"] >= 3, f"append proposes: {app_out}")

# ---- accept-all writes the doc and still returns a revision to diff ----
made2 = call("doc_create", {"title": "Apply me", "content": "alpha\n"})
auto = call("doc_edit", {"doc": made2["doc_id"], "edits": [{"find": "alpha", "replace": "beta"}], "summary": "Swap"},
            {"settings": {"docEditMode": "apply"}})
check(auto["status"] == "applied" and auto["revision_id"], f"accept-all writes: {auto}")
check(j("GET", f"/docs/{made2['doc_id']}")["content"] == "beta\n", "accept-all changes the body")
check(j("GET", f"/docs/revisions/{auto['revision_id']}")["status"] == "applied", "the revision is applied, not pending")
held = call("doc_edit", {"doc": made2["doc_id"], "edits": [{"find": "beta", "replace": "gamma"}]},
            {"settings": {"docEditMode": "nope"}})
check(held["status"] == "pending_review", "an unknown mode still asks")
check(j("GET", f"/docs/{made2['doc_id']}")["content"] == "beta\n", "asking leaves the body alone")
job = call("doc_edit", {"doc": made2["doc_id"], "edits": [{"find": "beta", "replace": "delta"}], "summary": "Swap"},
           {"settings": {"docEditMode": "apply"}, "proposal_only": True})
check(job["status"] == "pending_review", "a scheduled run does not accept-all")
check(j("GET", f"/docs/{made2['doc_id']}")["content"] == "beta\n", "a scheduled run leaves the body alone")

# ---- GET /docs/search: snippets, scope, odd characters ----
sd = j("POST", "/docs", {"title": "Searchable", "content": "the quokkafrobnitz lives here"})
hit = j("GET", "/docs/search?q=quokkafrobnitz")
check([h["doc_id"] for h in hit] == [sd["id"]] and "quokkafrobnitz" in hit[0]["snippet"], "search route returns a snippet")
check(j("GET", "/docs/search?q=quokkafrobnitz&project_id=nope") == [], "search route honours the scope")
j("GET", "/docs/search?q=%22%28%2A")
j("DELETE", f"/docs/{sd['id']}")

# ---- deletion takes the history with it ----
j("DELETE", f"/docs/{did}")
j("GET", f"/docs/{did}", expect=404)
check(True, "a deleted doc is gone")

# ---- the neighbouring surfaces still answer ----
for path in ("/health", "/notes", "/todos", "/boards"):
    j("GET", path)
check(True, "the routes that were already there still work")

# Autosave conflict: a stale base_updated_at is refused and leaves the body alone.
_d = j("POST", "/docs", {"title": "Conflict", "content": "v1"})
_a = j("PUT", f"/docs/{_d['id']}", {"content": "v2", "base_updated_at": _d["updated_at"]})
_b = j("PUT", f"/docs/{_d['id']}", {"content": "v3", "base_updated_at": _a["updated_at"]})
check(_b["content"] == "v3", "successive saves refresh the base and do not self-conflict")
j("PUT", f"/docs/{_d['id']}", {"content": "stale", "base_updated_at": _d["updated_at"]}, expect=409)
check(j("GET", f"/docs/{_d['id']}")["content"] == "v3", "409 leaves content unchanged")
check(j("PUT", f"/docs/{_d['id']}", {"content": "v4"})["content"] == "v4", "no base keeps unconditional write")
# A metadata PATCH bumps updated_at: the PATCH response is the base for the next save.
_p = j("PATCH", f"/docs/{_d['id']}", {"starred": True})
check(_p["updated_at"] > _d["updated_at"], "PATCH bumps updated_at")
check(j("PUT", f"/docs/{_d['id']}", {"content": "v5", "base_updated_at": _p["updated_at"]})["content"] == "v5",
      "a save based on the PATCH response is accepted")
j("PUT", f"/docs/{_d['id']}", {"content": "x", "base_updated_at": _d["updated_at"]}, expect=409)

print(f"test_docs: {passed} checks passed")
