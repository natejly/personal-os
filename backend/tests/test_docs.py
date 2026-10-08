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
check([h["doc_id"] for h in j("GET", "/docs/search?q=intr")] == [did], "search matches a word as it is still being typed")

# ---- autosave records revisions, and a burst of typing coalesces into one ----
j("PUT", f"/docs/{did}", {"content": "# Paper\n\nA better intro with $\\beta$.\n", "summary": "Rewrite intro"})
j("PUT", f"/docs/{did}", {"content": "# Paper\n\nA better intro with $\\beta$, plus more.\n"})
revs = j("GET", f"/docs/{did}/revisions")
check(len(revs) == 1, f"quick successive saves coalesce into one revision, got {len(revs)}")
check(revs[0]["author"] == "user" and revs[0]["status"] == "applied", "a user save is applied immediately")
cut = docs.create("Cut", "x" * 1000)
docs.save(cut["id"], "x" * 1000 + "y")
docs.save(cut["id"], "x" * 100)
cut_revs = docs.revisions(cut["id"])
check(len(cut_revs) == 2 and len(cut_revs[0]["after"]) == 100 and len(cut_revs[1]["after"]) == 1001,
      "a large deletion starts its own revision, so the text before it can be restored")

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
check(names == {"doc_list", "doc_search", "doc_read", "doc_create", "doc_edit", "doc_delete", "doc_comments", "doc_comment_reply"}, f"doc tools registered: {names}")
check(toolbox.specs["doc_edit"].danger == "writes", "doc_edit is a write")
check(toolbox.specs["doc_read"].danger == "safe", "doc_read is read-only")

read = call("doc_read", {"doc": "Paper"})
check("   1| " in read["text"], "doc_read numbers the lines")

# backlinks surface as titles on the first page only, and only when something links in
check("linked_from" not in read, "no linked_from when nothing links in")
src = call("doc_create", {"title": "Linker", "content": "See [[Paper]]\nline2\n"})
read = call("doc_read", {"doc": "Paper"})
check(read.get("linked_from") == ["Linker"], f"doc_read lists the linking title: {read.get('linked_from')}")
_orig_bl = docs.backlinks
_bl_calls = []
docs.backlinks = lambda *a, **k: (_bl_calls.append(a), _orig_bl(*a, **k))[1]
try:
    later = call("doc_read", {"doc": "Paper", "from_line": 2})
    check("text" in later and "linked_from" not in later and not _bl_calls, f"no backlinks scan past the first page: {_bl_calls}")
    call("doc_read", {"doc": "Paper"})
    check(len(_bl_calls) == 1, "first page does scan backlinks (stub counts)")
finally:
    docs.backlinks = _orig_bl
j("DELETE", f"/docs/{src['doc_id']}")

made = call("doc_create", {"title": "Derivation", "content": "$$\\int_0^1 x^2\\,dx = \\tfrac13$$\n"})
check(made["doc_id"] and made["created"] == "Derivation", "doc_create makes a doc")

hits = call("doc_search", {"query": "Derivation"})
check(any(h["title"] == "Derivation" for h in hits["results"]), f"doc_search finds it: {hits}")

pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
secret = call("doc_create", {"title": f"Notes {pat}", "content": f"the key is {pat}\n", "folder": f"vault/{pat}"})
listed = call("doc_list", {"query": "Notes"})
row = next(r for r in listed if r["doc_id"] == secret["doc_id"])
check(pat not in row["title"] and pat not in (row["folder"] or "") and "[github-pat]" in row["title"], "doc_list strips a token")
found = call("doc_search", {"query": "key"})
hit = next(h for h in found["results"] if h["doc_id"] == secret["doc_id"])
check(pat not in hit["title"] and pat not in hit["snippet"] and "[github-pat]" in hit["snippet"], "doc_search strips a token")
stored = docs.get(secret["doc_id"])
check(pat in stored["title"] and pat in stored["content"], "the saved doc stays unchanged")
check(pat not in secret["created"] and pat not in secret["filed_under"] and "[github-pat]" in secret["created"], "doc_create strips a token")
held_edit = call("doc_edit", {"doc": secret["doc_id"], "append": "more\n", "summary": "note"})
check(pat not in held_edit["title"] and "[github-pat]" in held_edit["title"], "doc_edit strips a token in the title")

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
_edit_desc = toolbox.specs["doc_edit"].description
check("Diff review" in _edit_desc and "Full agentic editing" in _edit_desc and "pending_review" in _edit_desc,
      "the tool description names both file-edit modes and the pending status")

# ---- GET /docs/search: snippets, scope, odd characters ----
sd = j("POST", "/docs", {"title": "Searchable", "content": "the quokkafrobnitz lives here"})
hit = j("GET", "/docs/search?q=quokkafrobnitz")
check([h["doc_id"] for h in hit] == [sd["id"]] and "quokkafrobnitz" in hit[0]["snippet"], "search route returns a snippet")
# A project sees its own docs with personal ones layered in, never another project's.
pa, pb = j("POST", "/projects", {"name": "Search A"})["id"], j("POST", "/projects", {"name": "Search B"})["id"]
pd = j("POST", "/docs", {"title": "Scoped", "content": "the zorbleplex lives here", "project_id": pa})
check([h["doc_id"] for h in j("GET", f"/docs/search?q=zorbleplex&project_id={pa}")] == [pd["id"]], "search route: a project finds its own doc")
check(j("GET", f"/docs/search?q=zorbleplex&project_id={pb}") == [], "search route: another project's doc stays out")
check([h["doc_id"] for h in j("GET", f"/docs/search?q=quokkafrobnitz&project_id={pb}")] == [sd["id"]],
      "search route: personal docs are layered into a project")
j("GET", "/docs/search?q=%22%28%2A")
j("DELETE", f"/docs/{sd['id']}")
# ---- pinning: persists, survives a title edit, sorts first, keeps through trash ----
pa = j("POST", "/docs", {"title": "Pin A"})["id"]
pb = j("POST", "/docs", {"title": "Pin B"})["id"]
j("PATCH", f"/docs/{pa}", {"pinned": True})
check(next(r for r in j("GET", "/docs") if r["id"] == pa)["pinned"] == 1, "pinned shows in the list")
j("PATCH", f"/docs/{pa}", {"title": "Pin A2"})
check(j("GET", "/docs")[0]["id"] == pa and j("GET", "/docs")[0]["pinned"] == 1, "a title edit keeps the pin, and pinned sorts first")
j("PATCH", f"/docs/{pa}", {"pinned": False})
check(next(r for r in j("GET", "/docs") if r["id"] == pa)["pinned"] == 0, "unpin clears it")
j("PATCH", f"/docs/{pb}", {"pinned": True})
j("DELETE", f"/docs/{pb}")
check(all(r["id"] != pb for r in j("GET", "/docs")), "a deleted pinned doc is not listed")
j("POST", f"/trash/doc/{pb}/restore")
check(next(r for r in j("GET", "/docs") if r["id"] == pb)["pinned"] == 1, "restoring a trashed doc keeps its pin")

# ---- deletion takes the history with it ----
j("DELETE", f"/docs/{did}")
j("GET", f"/docs/{did}", expect=404)
check(True, "a deleted doc is gone")

# ---- the neighbouring surfaces still answer ----
for path in ("/health", "/todos"):
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
