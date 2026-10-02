"""Docs: long-form markdown notes with a revision history and reviewable assistant edits.

Distinct from two neighbours that sound similar:
  * `documents` — files the user uploads, chunked for retrieval. Read-only knowledge.
  * `notes` — canvas mode's sticky notes: a body, a colour, no history.

A doc is something the user writes. Every change lands as a revision, so the editor can show a diff
and walk backwards. Assistant edits never touch `docs.content`: they land as a *pending* revision the
user accepts or rejects, which is what makes an LLM safe to point at prose someone cares about.
"""
from __future__ import annotations

import datetime
import difflib
import hashlib
import logging
import re
import threading
from typing import Any

from .chunker import chunk_blocks
from .db import Database, new_id, now, row_to_dict
from .extract_text import markdown_blocks
from .repos import ALL, _scope_clause, fts_query

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,  -- demote to personal, like todos and notes
  title TEXT NOT NULL DEFAULT 'Untitled',
  content TEXT NOT NULL DEFAULT '',
  folder TEXT NOT NULL DEFAULT '',
  starred INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_docs_updated ON docs(updated_at DESC);

CREATE TABLE IF NOT EXISTS doc_revisions (
  id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
  before TEXT NOT NULL DEFAULT '',
  after TEXT NOT NULL DEFAULT '',
  title_before TEXT,
  title_after TEXT,
  summary TEXT NOT NULL DEFAULT '',
  author TEXT NOT NULL DEFAULT 'user',      -- 'user' | 'assistant'
  tool TEXT,                                 -- tool that proposed it, when author='assistant'
  status TEXT NOT NULL DEFAULT 'applied',    -- 'applied' | 'pending' | 'rejected'
  created_at REAL NOT NULL,
  resolved_at REAL,
  append TEXT                                -- an append proposal: a section added to whatever the doc says at accept time
);
CREATE INDEX IF NOT EXISTS idx_rev_doc ON doc_revisions(doc_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rev_pending ON doc_revisions(status, created_at DESC);

-- Folders are rows, not just a string on each doc: a folder you made and have not filled yet has
-- to survive a reload, and nesting needs a name for "Work/Research" even when only its children hold
-- docs. `docs.folder` stays the path, so every existing doc keeps working untouched.
--
-- `scope` is which tree the folder belongs to: '' is personal, otherwise a project id. Two projects
-- can each hold a "Research" without colliding, and a doc's scope is simply its own `project_id`, so
-- "which project is this filed under" has exactly one home and cannot drift.
CREATE TABLE IF NOT EXISTS doc_folders (
  scope TEXT NOT NULL DEFAULT '',
  path TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY (scope, path)
);

CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
  title, content, doc_id UNINDEXED, tokenize='porter unicode61'
);

-- The same retrieval pipeline the uploaded files use, over the user's own writing: chunks by heading,
-- an FTS row per chunk holding the contextualised string, vectors in a parallel table (a chunk id can
-- only reference one table). doc_chunk_state is the "already indexed this exact title+body" marker, so
-- autosave rebuilds nothing when the text did not change, and a doc with no chunks is not retried forever.
CREATE TABLE IF NOT EXISTS doc_chunks (
  id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  heading TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_doc ON doc_chunks(doc_id, idx);
CREATE TABLE IF NOT EXISTS doc_chunk_state (
  doc_id TEXT PRIMARY KEY REFERENCES docs(id) ON DELETE CASCADE,
  hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS doc_chunk_embeddings (
  chunk_id TEXT PRIMARY KEY REFERENCES doc_chunks(id) ON DELETE CASCADE,
  doc_id TEXT NOT NULL,
  model TEXT NOT NULL,
  dim INTEGER NOT NULL,
  vec BLOB NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS doc_chunks_fts USING fts5(
  text, chunk_id UNINDEXED, doc_id UNINDEXED, tokenize='porter unicode61'
);
"""

def doc_hit(r: Any) -> dict[str, Any]:
    """A doc chunk row as a retrieval hit. `document_id`/`name` mirror the file hits so ranking and the
    context block treat both stores alike; `source`, `doc_id` and `title` say which one it came from."""
    return {"chunk_id": r["chunk_id"], "document_id": r["doc_id"], "name": r["title"], "idx": r["idx"], "text": r["text"],
            "heading": r["heading"], "page": None, "score": r["score"] if "score" in r.keys() else 0.0,
            "source": "doc", "doc_id": r["doc_id"], "title": r["title"]}


# A burst of keystrokes is one edit, not forty. Consecutive user revisions inside this window are
# folded into the newest one, so the history reads as sessions rather than as a keylogger.
COALESCE_SECONDS = 180.0


# The folder daily notes live in (personal tree), and the shape of a wiki link: [[Title]] or [[Title|alias]].
DAILY_FOLDER = "Daily"
_WIKI_LINK = re.compile(r"\[\[([^\[\]|]*?)(?:\|[^\[\]]*)?\]\]")


def _link_line(content: str, want: str) -> str | None:
    """The first line outside a fenced code block that links to `want` (casefolded title), clipped
    later by the caller. A link in a code sample is an example, not a reference."""
    fence = ""
    for line in content.split("\n"):
        stripped = line.lstrip()
        marker = stripped[:3] if stripped[:3] in ("```", "~~~") else ""
        if marker:
            if not fence:
                fence = marker
            elif marker == fence:
                fence = ""
            continue
        if fence:
            continue
        for m in _WIKI_LINK.finditer(line):
            if m.group(1).strip().casefold() == want:
                return line.strip()
    return None


# A folder path is "Work/Research/2026": segments joined by a slash. Kept in the doc's own `folder`
# column rather than a foreign key, so a doc is never orphaned by a folder row going missing.
MAX_FOLDER_DEPTH = 8
MAX_SEGMENT = 60


def folder_path(raw: str | None) -> str:
    """Normalise whatever the UI or a model passed into a storable path. '' is the root."""
    segs = []
    for seg in str(raw or "").replace("\\", "/").split("/"):
        seg = seg.strip().strip(".")[:MAX_SEGMENT].strip()
        if seg:
            segs.append(seg)
    return "/".join(segs[:MAX_FOLDER_DEPTH])


def ancestors(path: str) -> list[str]:
    """['Work', 'Work/Research'] for 'Work/Research' — every folder a path implies, itself included."""
    segs = [s for s in path.split("/") if s]
    return ["/".join(segs[: i + 1]) for i in range(len(segs))]


def scope_key(project_id: str | None) -> str:
    """Which tree a path lives in. A doc's scope is its own project; '' is the personal tree."""
    return (project_id or "").strip()


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def diff_stat(before: str, after: str) -> dict[str, int]:
    """Lines added/removed between two versions — cheap enough to compute on every list row."""
    added = removed = 0
    for line in difflib.ndiff(before.splitlines(), after.splitlines()):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return {"added": added, "removed": removed}


def unified_diff(before: str, after: str, context: int = 3) -> str:
    return "".join(
        difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                             fromfile="before", tofile="after", n=context)
    )


log = logging.getLogger("personal_os.docs")


class Docs:
    def __init__(self, db: Database):
        self.db = db
        # Called with a doc id whenever its chunks were rebuilt (app.py wires background embedding to it).
        self.on_chunks: Any = None
        # Called with a doc id just before its row is hard-deleted (app.py wires linked recordings to it).
        self.on_delete: Any = None
        # Called with (doc id, new project id or None) after a doc changed project (app.py keeps its recordings in step).
        self.on_move: Any = None
        # Serialises `daily`, so a double click cannot find nothing twice and create two notes.
        self._daily_lock = threading.Lock()
        with db.tx() as c:
            c.executescript(SCHEMA)
            self._migrate_folder_scope(c)
            # Soft delete (trash.py). docs is created here, not in db.py, so its columns are added here too.
            have = {r["name"] for r in c.execute("PRAGMA table_info(docs)").fetchall()}
            for col, ddl in {"deleted_at": "REAL", "deleted_with": "TEXT"}.items():
                if col not in have:
                    c.execute(f"ALTER TABLE docs ADD COLUMN {col} {ddl}")
            # doc_revisions.append arrived after the first release (recording summaries), so an existing DB needs it added.
            have_rev = {r["name"] for r in c.execute("PRAGMA table_info(doc_revisions)").fetchall()}
            if "append" not in have_rev:
                c.execute("ALTER TABLE doc_revisions ADD COLUMN append TEXT")
        self.backfill_chunks()

    @staticmethod
    def _migrate_folder_scope(c: Any) -> None:
        """Give an existing install's folders a scope. They were global, so they are all personal.

        The old table keyed on `path` alone, which would stop two projects ever holding a folder of
        the same name — so this rebuilds rather than bolting a column on the side.
        """
        cols = {r["name"] for r in c.execute("PRAGMA table_info(doc_folders)").fetchall()}
        if not cols or "scope" in cols:
            return
        c.execute("ALTER TABLE doc_folders RENAME TO doc_folders_unscoped")
        c.execute(
            "CREATE TABLE doc_folders (scope TEXT NOT NULL DEFAULT '', path TEXT NOT NULL,"
            " created_at REAL NOT NULL, PRIMARY KEY (scope, path))")
        c.execute("INSERT OR IGNORE INTO doc_folders(scope, path, created_at)"
                  " SELECT '', path, created_at FROM doc_folders_unscoped")
        c.execute("DROP TABLE doc_folders_unscoped")

    # ---- indexing ----
    def _reindex(self, c: Any, doc_id: str, title: str, content: str) -> None:
        c.execute("DELETE FROM docs_fts WHERE doc_id=?", (doc_id,))
        c.execute("INSERT INTO docs_fts(title, content, doc_id) VALUES(?,?,?)", (title, content, doc_id))
        self._index_chunks(c, doc_id, title, content)

    def _index_chunks(self, c: Any, doc_id: str, title: str, content: str) -> bool:
        """Rebuild this doc's retrieval chunks if title+body changed. Pure SQL inside the caller's
        transaction: no network here, vectors are made later from the unembedded rows."""
        digest = hashlib.sha256(f"{title}\x00{content}".encode("utf-8", "replace")).hexdigest()
        st = c.execute("SELECT hash FROM doc_chunk_state WHERE doc_id=?", (doc_id,)).fetchone()
        if st and st["hash"] == digest:
            return False
        c.execute("DELETE FROM doc_chunks_fts WHERE doc_id=?", (doc_id,))
        c.execute("DELETE FROM doc_chunks WHERE doc_id=?", (doc_id,))  # cascades the vectors
        try:
            chunks = chunk_blocks(markdown_blocks(content), title=title)
        except Exception:  # noqa: BLE001 - a chunker bug must not fail a save
            chunks = []
        for i, ch in enumerate(chunks):
            cid = new_id()
            c.execute("INSERT INTO doc_chunks(id,doc_id,idx,heading,text) VALUES(?,?,?,?,?)", (cid, doc_id, i, ch.heading_str, ch.text))
            c.execute("INSERT INTO doc_chunks_fts(text, chunk_id, doc_id) VALUES(?,?,?)", (ch.ctx, cid, doc_id))
        c.execute("INSERT OR REPLACE INTO doc_chunk_state(doc_id, hash) VALUES(?,?)", (doc_id, digest))
        if self.on_chunks and chunks:
            try:
                self.on_chunks(doc_id)
            except Exception:  # noqa: BLE001
                pass
        return True

    def backfill_chunks(self) -> int:
        """Chunk every doc that has never been indexed (existing installs, rows inserted behind our back). Idempotent."""
        with self.db.tx() as c:
            rows = c.execute("SELECT id, title, content FROM docs WHERE id NOT IN (SELECT doc_id FROM doc_chunk_state)").fetchall()
            for r in rows:
                self._index_chunks(c, r["id"], r["title"], r["content"])
        return len(rows)

    def chunk_search(self, query: str, project_id: str | None = ALL, limit: int = 6) -> list[dict[str, Any]]:
        """BM25 over doc chunks, in the same hit shape as Documents.search plus source='doc'.
        Scope matches files: a project sees its own docs plus personal ones."""
        fq = fts_query(query)
        if not fq:
            return []
        where, args = _scope_clause(project_id)
        with self.db.tx() as c:
            rows = c.execute(
                f"""SELECT f.chunk_id, f.doc_id, d.title, ch.idx, ch.text, ch.heading, bm25(doc_chunks_fts) AS score
                    FROM doc_chunks_fts f JOIN docs d ON d.id=f.doc_id JOIN doc_chunks ch ON ch.id=f.chunk_id
                    WHERE doc_chunks_fts MATCH ? AND d.deleted_at IS NULL AND {where.replace('project_id', 'd.project_id')}
                    ORDER BY score LIMIT ?""", (fq, *args, limit)).fetchall()
        return [doc_hit(r) for r in rows]

    # ---- reads ----
    def list(self, project_id: str | None = "__all__", q: str = "") -> list[dict[str, Any]]:
        """Docs without their bodies: a preview is enough for a list, and bodies get long."""
        where, args = ["d.deleted_at IS NULL"], []
        if project_id != "__all__":
            if project_id is None:
                where.append("d.project_id IS NULL")
            else:
                where.append("d.project_id = ?")
                args.append(project_id)
        if q.strip():
            where.append("(d.title LIKE ? OR d.content LIKE ?)")
            args += [f"%{q}%", f"%{q}%"]
        sql = (
            "SELECT d.id, d.project_id, d.title, d.folder, d.starred, d.created_at, d.updated_at, d.content, "
            "  length(d.content) AS size, "
            "  (SELECT COUNT(*) FROM doc_revisions r WHERE r.doc_id=d.id AND r.status='pending') AS pending "
            "FROM docs d" + (" WHERE " + " AND ".join(where) if where else "") +
            " ORDER BY d.starred DESC, d.updated_at DESC"
        )
        with self.db.tx() as c:
            rows = c.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            body = d.pop("content") or ""  # the list shows a preview; bodies stay out of the payload
            out.append({**d, "preview": body[:240], "words": word_count(body)})
        return out

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            d = row_to_dict(c.execute("SELECT * FROM docs WHERE id=? AND deleted_at IS NULL", (id,)).fetchone())
            if not d:
                return None
            pending = [row_to_dict(r) for r in c.execute(
                "SELECT * FROM doc_revisions WHERE doc_id=? AND status='pending' ORDER BY created_at", (id,)).fetchall()]
        d["words"] = word_count(d["content"])
        d["pending"] = [self._rev_view(r, d["content"]) for r in pending]  # type: ignore[arg-type]
        return d

    def find(self, name_or_id: str) -> dict[str, Any] | None:
        """Resolve what a model passed: an id, or a title (exact, then unique prefix/substring)."""
        key = (name_or_id or "").strip()
        if not key:
            return None
        with self.db.tx() as c:
            r = c.execute("SELECT id FROM docs WHERE deleted_at IS NULL AND (id=? OR lower(title)=lower(?))", (key, key)).fetchone()
            if not r:
                hits = c.execute("SELECT id FROM docs WHERE deleted_at IS NULL AND title LIKE ? COLLATE NOCASE LIMIT 2", (f"%{key}%",)).fetchall()
                if len(hits) != 1:
                    return None
                r = hits[0]
        return self.get(r["id"])

    def search(self, query: str, project_id: str | None = "__all__", limit: int = 10) -> list[dict[str, Any]]:
        """FTS over titles and bodies, with a snippet around the hit."""
        q = (query or "").strip()
        if not q:
            return []
        match = " OR ".join(f'"{w}"' for w in re.findall(r"\w+", q)) or f'"{q}"'
        with self.db.tx() as c:
            try:
                rows = c.execute(
                    "SELECT f.doc_id, snippet(docs_fts, 1, '', '', ' … ', 24) AS snippet, bm25(docs_fts) AS score "
                    "FROM docs_fts f WHERE docs_fts MATCH ? ORDER BY score LIMIT ?", (match, max(1, limit) * 3)).fetchall()
            except Exception:  # malformed FTS expression — fall back to LIKE
                rows = c.execute(
                    "SELECT id AS doc_id, substr(content,1,200) AS snippet, 0 AS score FROM docs "
                    "WHERE deleted_at IS NULL AND (content LIKE ? OR title LIKE ?) LIMIT ?", (f"%{q}%", f"%{q}%", max(1, limit) * 3)).fetchall()
            out = []
            for r in rows:
                d = c.execute("SELECT id, title, project_id FROM docs WHERE id=? AND deleted_at IS NULL", (r["doc_id"],)).fetchone()
                if not d:
                    continue
                if project_id != "__all__" and d["project_id"] != project_id:
                    continue
                out.append({"doc_id": d["id"], "title": d["title"], "snippet": (r["snippet"] or "").strip()})
                if len(out) >= limit:
                    break
        return out

    # ---- writes ----
    def create(self, title: str = "Untitled", content: str = "", project_id: str | None = None,
               folder: str = "", author: str = "user") -> dict[str, Any]:
        did = new_id()
        t = now()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO docs(id,project_id,title,content,folder,starred,created_at,updated_at) VALUES(?,?,?,?,?,0,?,?)",
                (did, project_id, (title or "Untitled").strip()[:200], content, folder_path(folder), t, t))
            self._reindex(c, did, title, content)
            if content:
                c.execute(
                    "INSERT INTO doc_revisions(id,doc_id,before,after,title_before,title_after,summary,author,tool,status,created_at,resolved_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,'applied',?,?)",
                    (new_id(), did, "", content, None, title, "Created", author, None, t, t))
        return self.get(did)  # type: ignore[return-value]

    def save(self, id: str, content: str | None = None, title: str | None = None, summary: str = "",
             author: str = "user", coalesce: bool = True) -> dict[str, Any] | None:
        """Apply an edit straight to the doc and record it. Used by the editor's autosave."""
        cur = self.get(id)
        if not cur:
            return None
        new_content = cur["content"] if content is None else content
        new_title = cur["title"] if title is None else (title.strip()[:200] or "Untitled")
        if new_content == cur["content"] and new_title == cur["title"]:
            return cur
        t = now()
        with self.db.tx() as c:
            c.execute("UPDATE docs SET content=?, title=?, updated_at=? WHERE id=?", (new_content, new_title, t, id))
            self._reindex(c, id, new_title, new_content)
            last = c.execute(
                "SELECT * FROM doc_revisions WHERE doc_id=? AND status='applied' ORDER BY created_at DESC LIMIT 1", (id,)).fetchone()
            fold = (coalesce and last is not None and last["author"] == author
                    and t - last["created_at"] < COALESCE_SECONDS and not last["summary"].startswith("Restored"))
            if fold:
                c.execute("UPDATE doc_revisions SET after=?, title_after=?, created_at=?, resolved_at=?, summary=? WHERE id=?",
                          (new_content, new_title, t, t, summary or last["summary"], last["id"]))
            else:
                c.execute(
                    "INSERT INTO doc_revisions(id,doc_id,before,after,title_before,title_after,summary,author,tool,status,created_at,resolved_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,'applied',?,?)",
                    (new_id(), id, cur["content"], new_content, cur["title"], new_title, summary or "Edited", author, None, t, t))
        return self.get(id)

    def update_meta(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        """Title/folder/star/project moves that are not content edits, so they skip the history."""
        fields = {k: v for k, v in patch.items() if k in {"title", "folder", "starred", "project_id"}}
        if not fields:
            return self.get(id)
        if "title" in fields:
            fields["title"] = (str(fields["title"]).strip()[:200]) or "Untitled"
        if "starred" in fields:
            fields["starred"] = 1 if fields["starred"] else 0
        if "folder" in fields:
            fields["folder"] = folder_path(fields["folder"])
        fields["updated_at"] = now()
        with self.db.tx() as c:
            c.execute(f"UPDATE docs SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), id))
            d = c.execute("SELECT title, content FROM docs WHERE id=?", (id,)).fetchone()
            if d:
                self._reindex(c, id, d["title"], d["content"])
        if "project_id" in fields and d and self.on_move:
            try:
                self.on_move(id, fields["project_id"])
            except Exception as e:  # noqa: BLE001 - the doc already moved; a stale recording scope is not worth failing it
                log.warning("docs: could not move recordings of doc %s: %s", id, e)
        return self.get(id)

    def move(self, id: str, scope: str | None, folder: str = "") -> dict[str, Any] | None:
        """File a doc somewhere in the tree: which project ('' is personal) and which folder in it.

        One call, because the two halves are one gesture — dragging a doc from Personal/Notes into
        Work/Research has to land both or the doc ends up in a folder its project does not have.
        """
        sc = scope_key(scope)
        return self.update_meta(id, {"project_id": sc or None, "folder": folder})

    def delete(self, id: str) -> None:
        if self.on_delete:
            # Before the row goes: a linked recording's FTS row and audio dir are not covered by any FK cascade.
            try:
                self.on_delete(id)
            except Exception as e:  # noqa: BLE001 - a failing hook must not leave the doc half-purged
                log.warning("docs: could not purge recordings of doc %s: %s", id, e)
        with self.db.tx() as c:
            c.execute("DELETE FROM docs WHERE id=?", (id,))
            c.execute("DELETE FROM docs_fts WHERE doc_id=?", (id,))
            c.execute("DELETE FROM doc_chunks_fts WHERE doc_id=?", (id,))

    # ---- folders ----
    def folders(self) -> list[dict[str, Any]]:
        """Every folder in every tree, with the doc counts the UI renders. Ancestors implied by a path
        are included even if nothing ever created them, so a tree never has a hole in the middle, and
        a folder a doc claims to be in is listed even if its own row went missing."""
        with self.db.tx() as c:
            named = [(r["scope"], r["path"]) for r in c.execute("SELECT scope, path FROM doc_folders").fetchall()]
            counts = {(r["scope"], r["folder"]): int(r["n"]) for r in c.execute(
                "SELECT COALESCE(project_id,'') AS scope, folder, COUNT(*) AS n FROM docs"
                " WHERE folder<>'' AND deleted_at IS NULL GROUP BY scope, folder").fetchall()}
        keys: set[tuple[str, str]] = set()
        for scope, p in [*named, *counts.keys()]:
            for anc in ancestors(folder_path(p)):
                keys.add((scope_key(scope), anc))
        out = []
        for scope, path in sorted(keys, key=lambda k: (k[0], [seg.lower() for seg in k[1].split("/")])):
            prefix = path + "/"
            out.append({
                "scope": scope,
                "path": path,
                "name": path.rsplit("/", 1)[-1],
                "parent": path.rsplit("/", 1)[0] if "/" in path else "",
                "docs": counts.get((scope, path), 0),
                # What the badge shows on a collapsed folder: everything filed anywhere beneath it.
                "docs_deep": sum(n for (s, f), n in counts.items()
                                 if s == scope and (f == path or f.startswith(prefix))),
            })
        return out

    def create_folder(self, path: str, scope: str | None = "") -> list[dict[str, Any]]:
        norm = folder_path(path)
        if not norm:
            raise ValueError("A folder needs a name")
        sc = scope_key(scope)
        t = now()
        with self.db.tx() as c:
            for anc in ancestors(norm):
                c.execute("INSERT OR IGNORE INTO doc_folders(scope, path, created_at) VALUES(?,?,?)", (sc, anc, t))
        return self.folders()

    # ---- daily note + backlinks ----
    def daily(self, date_str: str | None = None) -> tuple[dict[str, Any], bool]:
        """Find or create the personal daily note for a date ("YYYY-MM-DD", default: today here).

        The title is the date itself, so `[[2026-10-02]]` links to it. A trashed note with that title
        does not count: `get`-style reads already hide it, so a fresh one is made rather than reviving
        something the user threw away. Raises ValueError on a malformed date."""
        if date_str:
            try:
                day = datetime.date.fromisoformat(date_str.strip())
            except ValueError as e:
                raise ValueError("Date must be YYYY-MM-DD") from e
            if day.isoformat() != date_str.strip():
                raise ValueError("Date must be YYYY-MM-DD")
        else:
            day = datetime.date.today()
        title = day.isoformat()
        with self._daily_lock:
            with self.db.tx() as c:
                row = c.execute(
                    "SELECT id FROM docs WHERE deleted_at IS NULL AND project_id IS NULL AND folder=? AND title=?"
                    " ORDER BY created_at LIMIT 1", (DAILY_FOLDER, title)).fetchone()
            if row:
                return self.get(row["id"]), False  # type: ignore[return-value]
            self.create_folder(DAILY_FOLDER, "")
            body = f"# {day.strftime('%A, %B')} {day.day}, {day.year}\n\n"
            return self.create(title, body, None, DAILY_FOLDER), True

    def backlinks(self, id: str) -> list[dict[str, Any]] | None:
        """Live docs that link to this one with `[[Title]]` or `[[Title|alias]]`, newest first.

        A plain scan: SQL narrows to docs containing `[[` at all, then the links are parsed in Python so
        fenced code and case/space differences are handled in one place. None if the doc is missing."""
        me = self.get(id)
        if not me:
            return None
        want = (me["title"] or "").strip().casefold()
        if not want:
            return []
        with self.db.tx() as c:
            rows = c.execute(
                "SELECT id, title, folder, project_id, content, updated_at FROM docs"
                " WHERE deleted_at IS NULL AND id<>? AND content LIKE ? ESCAPE '\\' ORDER BY updated_at DESC",
                (id, "%\\[[%")).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            line = _link_line(r["content"], want)
            if line is not None:
                out.append({"id": r["id"], "title": r["title"], "folder": r["folder"], "project_id": r["project_id"],
                            "snippet": line[:160], "updated_at": r["updated_at"]})
        return out

    def rename_folder(self, path: str, new_path: str, scope: str | None = "") -> list[dict[str, Any]]:
        """Rename or move a folder within its own tree, taking its subtree and its docs along.

        Moving a folder to a *different* project is deliberately not this operation: the docs' own
        `project_id` is what decides which tree they are in, so that is a per-doc move.
        """
        src, dst = folder_path(path), folder_path(new_path)
        sc = scope_key(scope)
        if not src:
            raise ValueError("A folder needs a name")
        if not dst:
            raise ValueError("A folder needs a name")
        if src == dst:
            return self.folders()
        # Moving a folder inside itself would orphan the subtree it is carrying.
        if dst.startswith(src + "/"):
            raise ValueError("A folder cannot be moved inside itself")
        proj_match = "project_id IS NULL" if sc == "" else "project_id = ?"
        proj_args: tuple[Any, ...] = () if sc == "" else (sc,)
        with self.db.tx() as c:
            if c.execute("SELECT 1 FROM doc_folders WHERE scope=? AND path=?", (sc, dst)).fetchone():
                raise ValueError(f'"{dst}" already exists')
            rows = c.execute("SELECT path FROM doc_folders WHERE scope=? AND (path=? OR substr(path, 1, ?) = ?)",
                             (sc, src, len(src) + 1, src + "/")).fetchall()
            t = now()
            for anc in ancestors(dst):
                c.execute("INSERT OR IGNORE INTO doc_folders(scope, path, created_at) VALUES(?,?,?)", (sc, anc, t))
            for r in rows:
                moved = dst + r["path"][len(src):]
                c.execute("DELETE FROM doc_folders WHERE scope=? AND path=?", (sc, r["path"]))
                c.execute("INSERT OR IGNORE INTO doc_folders(scope, path, created_at) VALUES(?,?,?)", (sc, moved, t))
            c.execute(f"UPDATE docs SET folder=? WHERE deleted_at IS NULL AND folder=? AND {proj_match}", (dst, src, *proj_args))
            c.execute(f"UPDATE docs SET folder=? || substr(folder, ?) WHERE deleted_at IS NULL AND substr(folder, 1, ?) = ? AND {proj_match}",
                      (dst, len(src) + 1, len(src) + 1, src + "/", *proj_args))
        return self.folders()

    def delete_folder(self, path: str, delete_docs: bool = False, scope: str | None = "") -> list[dict[str, Any]]:
        """Remove a folder and its subfolders. Its docs move up to the parent unless asked otherwise:
        losing a folder must not silently lose what was written in it. With delete_docs they go to the
        trash (each one restorable on its own) rather than being erased."""
        src = folder_path(path)
        sc = scope_key(scope)
        if not src:
            raise ValueError("A folder needs a name")
        parent = src.rsplit("/", 1)[0] if "/" in src else ""
        proj_match = "project_id IS NULL" if sc == "" else "project_id = ?"
        proj_args: tuple[Any, ...] = () if sc == "" else (sc,)
        with self.db.tx() as c:
            if delete_docs:
                c.execute(
                    f"UPDATE docs SET deleted_at=?, deleted_with=NULL WHERE deleted_at IS NULL"
                    f" AND (folder=? OR substr(folder, 1, ?) = ?) AND {proj_match}", (now(), src, len(src) + 1, src + "/", *proj_args))
            else:
                c.execute(f"UPDATE docs SET folder=? WHERE deleted_at IS NULL AND (folder=? OR substr(folder, 1, ?) = ?) AND {proj_match}",
                          (parent, src, len(src) + 1, src + "/", *proj_args))
            c.execute("DELETE FROM doc_folders WHERE scope=? AND (path=? OR substr(path, 1, ?) = ?)",
                      (sc, src, len(src) + 1, src + "/"))
        return self.folders()

    def forget_scope(self, project_id: str) -> None:
        """Drop a deleted project's folder rows. Its docs are demoted to personal by the schema's
        ON DELETE SET NULL, so the folders they still name resurface in the personal tree — which is
        the point: the project is gone, the writing is not."""
        with self.db.tx() as c:
            c.execute("DELETE FROM doc_folders WHERE scope=?", (scope_key(project_id),))

    # ---- revisions ----
    @staticmethod
    def append_after(content: str, section: str) -> str:
        """What a doc reads like once an append proposal's section is added to `content`."""
        body = (content or "").rstrip()
        return (body + "\n\n" if body else "") + (section or "").strip("\n") + "\n"

    def _rev_view(self, r: dict[str, Any], current: str | None = None) -> dict[str, Any]:
        """A revision plus what the UI needs to render it without fetching the bodies twice."""
        if r.get("append") is not None and r["status"] == "pending":
            # An append proposal is "this section, added to the doc as it stands", so a pending one is
            # resolved against the live content: the diff shows only the addition and typing after the
            # proposal does not make it stale. Applied/rejected rows keep what was stored.
            if current is None:
                with self.db.tx() as c:
                    row = c.execute("SELECT content FROM docs WHERE id=?", (r["doc_id"],)).fetchone()
                current = row["content"] if row else r["before"]
            r = {**r, "before": current, "after": self.append_after(current, r["append"])}
        base = r["before"] if current is None else current
        return {**r, "stat": diff_stat(r["before"], r["after"]),
                # A pending edit is reviewed against the doc as it stands now, not as it stood when
                # proposed — otherwise the diff lies whenever the user typed in between.
                "stale": current is not None and r["status"] == "pending" and current != r["before"],
                "stat_vs_current": diff_stat(base, r["after"]) if current is not None else None}

    def revisions(self, doc_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = [row_to_dict(r) for r in c.execute(
                "SELECT * FROM doc_revisions WHERE doc_id=? ORDER BY created_at DESC LIMIT ?", (doc_id, max(1, min(int(limit), 200)))).fetchall()]
        return [self._rev_view(r) for r in rows]  # type: ignore[arg-type]

    def revision(self, rev_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = row_to_dict(c.execute("SELECT * FROM doc_revisions WHERE id=?", (rev_id,)).fetchone())
        return self._rev_view(r) if r else None  # type: ignore[arg-type]

    def pending_count(self) -> int:
        with self.db.tx() as c:
            return int(c.execute(
                "SELECT COUNT(*) FROM doc_revisions r JOIN docs d ON d.id=r.doc_id"
                " WHERE r.status='pending' AND d.deleted_at IS NULL").fetchone()[0])

    def propose(self, doc_id: str, after: str, summary: str = "", tool: str | None = None,
                title_after: str | None = None) -> dict[str, Any] | None:
        """Record an assistant edit for review. The doc itself is untouched until `accept`."""
        cur = self.get(doc_id)
        if cur is None:
            return None
        rid = new_id()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO doc_revisions(id,doc_id,before,after,title_before,title_after,summary,author,tool,status,created_at)"
                " VALUES(?,?,?,?,?,?,?,'assistant',?,'pending',?)",
                (rid, doc_id, cur["content"], after, cur["title"], title_after, summary or "Assistant edit", tool, now()))
        return self.revision(rid)

    def propose_append(self, doc_id: str, section: str, summary: str = "",
                       tool: str | None = None) -> dict[str, Any] | None:
        """Propose adding `section` to the end of a doc, resolved against the doc at accept time.

        `propose` freezes `after` as the whole new body, so anything typed between the proposal and
        the click is overwritten. A section that is only ever added does not need the whole body:
        the revision stores the section, and both the pending view and `accept` build the result
        from the doc as it stands. None when the doc is missing or in the trash.
        """
        cur = self.get(doc_id)
        if cur is None or not (section or "").strip():
            return None
        rid = new_id()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO doc_revisions(id,doc_id,before,after,title_before,title_after,summary,author,tool,status,created_at,append)"
                " VALUES(?,?,?,?,?,?,?,'assistant',?,'pending',?,?)",
                (rid, doc_id, cur["content"], self.append_after(cur["content"], section), cur["title"], None,
                 summary or "Assistant edit", tool, now(), section))
        return self.revision(rid)

    def accept(self, rev_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM doc_revisions WHERE id=? AND status='pending'", (rev_id,)).fetchone()
            if not r:
                return None
            d = c.execute("SELECT * FROM docs WHERE id=?", (r["doc_id"],)).fetchone()
            if not d:
                return None
            t = now()
            title = r["title_after"] or d["title"]
            after = self.append_after(d["content"], r["append"]) if r["append"] is not None else r["after"]
            # `before` is rewritten to the content actually replaced, so "undo" after a stale accept
            # restores what the user had rather than what the model saw. An append proposal also
            # stores the `after` it really produced.
            c.execute("UPDATE doc_revisions SET status='applied', before=?, after=?, title_before=?, resolved_at=? WHERE id=?",
                      (d["content"], after, d["title"], t, rev_id))
            c.execute("UPDATE docs SET content=?, title=?, updated_at=? WHERE id=?", (after, title, t, r["doc_id"]))
            self._reindex(c, r["doc_id"], title, after)
            doc_id = r["doc_id"]
        return self.get(doc_id)

    def reject(self, rev_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT doc_id FROM doc_revisions WHERE id=? AND status='pending'", (rev_id,)).fetchone()
            if not r:
                return None
            c.execute("UPDATE doc_revisions SET status='rejected', resolved_at=? WHERE id=?", (now(), rev_id))
            doc_id = r["doc_id"]
        return self.get(doc_id)

    def restore(self, rev_id: str) -> dict[str, Any] | None:
        """Roll the doc back to how a revision left it — itself recorded as a new revision."""
        r = self.revision(rev_id)
        if not r:
            return None
        return self.save(r["doc_id"], content=r["after"], title=r["title_after"],
                         summary=f"Restored revision from {r['created_at']:.0f}", author="user", coalesce=False)
