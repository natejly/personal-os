"""The workspace map: a short, ranked list of what exists (memory topics, notes, uploads, projects, skills) that rides in the
system prompt, plus a small live block (sources, todos, next events, pending approvals, running workers) that rides with
the turn. Labels and identifiers only, never contents; the model opens an item with the tool named beside it.

No model call and no persisted copy: every read is a live SQLite query, so nothing here can go stale. The stable half is
deterministic (fully ordered with id tie-breaks, dates as YYYY-MM-DD, bucketed counts, a fixed fence) so it can sit in the
cached prefix; it is memoised on a cheap version stamp, and the stamp covers everything the text depends on. The volatile half
changes within minutes and never enters the prefix.

Scope follows the other readers (repos._scope_clause): a personal chat sees project-less rows, a project chat its own plus
personal ones, an isolated project only its own. Trashed rows are never read.

Every title is untrusted text sitting in a high-trust slot, so it goes through sanitize_title and anything from a file the
user did not type sits inside a fixed fence (working.FENCE_RULE says what the fence means).
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import logging
import re
import secrets
import time
import unicodedata
from collections import Counter, OrderedDict
from typing import Any

from . import context as _ctx, limits, redact
from .repos import _scope_clause, is_isolated, live_mem
from .working import FENCE_RULE, fence_untrusted

log = logging.getLogger(__name__)

SECTIONS = ("memory", "files", "uploads", "projects", "skills", "sources", "today")
# Setting key (llm.DEFAULT_SETTINGS) per section; a chat's own use* switch must be on as well where there is one.
SETTING_KEYS = {"memory": "mapMemory", "files": "mapFiles", "uploads": "mapUploads", "projects": "mapProjects",
                "skills": "mapSkills", "sources": "mapSources", "today": "mapToday"}
CONV_KEYS = {"memory": "useMemory", "files": "useDocuments", "uploads": "useDocuments", "skills": "useSkills"}

HEADER = ("## Workspace map\n"
          "What exists in this workspace, as labels only: it holds no contents, so open an item before relying on it. "
          "Open with doc:ID -> doc_read, upload:ID -> read_document, memory topics -> search_memory; more -> context_map. "
          + FENCE_RULE)
FENCE_ID, FENCE_SOURCE = "map", "upload-names"
MORE = "(%s more: use context_map)"

def _steps() -> Any:
    yield from (1, 2, 3, 5, 10, 25, 50)
    p = 100
    while True:
        yield p
        yield p * 5 // 2
        yield p * 5
        p *= 10


def bucket(n: int) -> int:
    """The largest step (1, 2, 3, 5, 10, 25, 50, 100, 250, 500, 1000, ...) not above n; 0 for 0. Adding one row inside a
    step leaves the text unchanged, so the cached prefix survives it."""
    best = 0
    for st in _steps():
        if st > n:
            return best
        best = st
    return best


def label(n: int) -> str:
    """A bucketed count as text: exact for 0 to 2, else 'N+'."""
    b = bucket(n)
    return str(b) if b <= 2 else f"{b}+"


def _est(text: str) -> int:
    return _ctx.estimate_tokens(text) if text else 0


# ---------------------------------------------------------------- untrusted titles
_INJECT = [re.compile(p, re.I) for p in (
    r"\b(ignore|disregard|forget|override|bypass)\b[^.]{0,60}\b(instruction|prompt|rule|directive|guideline|polic|message|context|above|previous|prior|earlier)",
    r"\b(previous|prior|above|earlier)\s+(instruction|prompt|message|rule|direction)s?\b",
    r"\bnew\s+(instruction|rule|task|directive)s?\b",
    r"^\W*(system|assistant|developer|admin|root|user|human|ai)\W{0,3}\s*[:>\]\)}\-–—]",
    r"\byou\s+(must|should|shall|will|need to|have to|are now|are to)\b",
    r"\byour\s+(new\s+)?(task|job|instructions?|goal)\s+(is|are)\b",
    r"\b(call|run|execute|invoke|use|trigger|send|forward|delete|post|upload|exfiltrate|reveal|leak|share)\b[^.]{0,40}\b(gmail|calendar|shell|tool|function|command|token|password|secret|credential|api key|api_key)",
    r"\b(send|forward|email|mail)\b.{0,50}[\w.+-]+@[\w-]+\.[\w.]+",
    r"\b(gmail|calendar|shell|fs|desk|sandbox|google|browser|doc|todo|memory|mcp|agent|worker|skill|web|fetch|graph|read|write|move|trash|save|search|run|schedule)_[a-z0-9_]+\b",
    r"\b[a-z]+_[a-z_]+\s*\(",
    r"\b[a-z][a-z0-9+.\-]*://[^\s/@]*@",
    r"\b(do not|don't|never)\s+(tell|mention|reveal|inform|show)\b",
    r"\bwithout\s+(telling|asking|informing|notifying)\b",
)]
_TAG = re.compile(r"</?[A-Za-z!?|][^>]*>?")
_DROP_WORDS = frozenset("ignore disregard forget override bypass system assistant developer instruction instructions password secret "
                        "token credential credentials prompt".split())


def _clean(text: str) -> str:
    """One line of plain text: NFKC, no control, format or zero-width characters, credentials scrubbed, no backticks,
    fences or tags, quotes softened. Never longer than the input."""
    t = unicodedata.normalize("NFKC", str(text or ""))
    t = "".join(" " if (ch.isspace() or unicodedata.category(ch) in ("Zl", "Zp")) else ch
                for ch in t if unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co", "Cn") or ch.isspace())
    t = redact.scrub_command_output(t)
    t = t.replace("`", "").replace("<<<", "").replace(">>>", "").replace('"', "'")
    t = _TAG.sub(" ", t).replace("<", "").replace(">", "")
    return " ".join(t.split())


def sanitize_title(text: Any, origin: str = "file", limit: int | None = None) -> str:
    """A label that is safe in a high-trust prompt slot, or `(untitled <origin>)` when it is empty or reads as an instruction.
    The caller keeps the id either way."""
    limit = limit or limits.MAP_TITLE_CHARS
    t = _clean(str(text or ""))
    if not t or any(p.search(t) for p in _INJECT):
        return f"(untitled {origin})"
    return t if len(t) <= limit else t[:limit - 1].rstrip() + "…"


def _day(ts: float | None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else ""


# ---------------------------------------------------------------- switches
def resolve_switches(settings: dict[str, Any] | None, conv_settings: dict[str, Any] | None = None) -> dict[str, bool]:
    """Which sections are on: the section's global switch (default on) and, where there is one, the chat's use* switch."""
    s, cs = settings or {}, conv_settings or {}
    out = {}
    for sec in SECTIONS:
        on = bool(s.get(SETTING_KEYS[sec], True))
        if sec in CONV_KEYS:
            on = on and bool(cs.get(CONV_KEYS[sec], True))
        out[sec] = on
    return out


def map_on(conv_settings: dict[str, Any] | None) -> bool:
    """The whole map: the chat's useMap (default on) and tools on (the map points at tools)."""
    cs = conv_settings or {}
    return bool(cs.get("useMap", True)) and bool(cs.get("useTools", True))


# ---------------------------------------------------------------- reading rows
def _has(c: Any, name: str) -> bool:
    return c.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (name,)).fetchone() is not None


def _collect(db: Any, project_id: str | None, sw: dict[str, bool], file_cap: int = limits.MAP_FILES,
             upload_cap: int = limits.MAP_UPLOADS, project_cap: int = limits.MAP_PROJECTS) -> dict[str, Any]:
    """The cheap, bounded reads the stable text and its version stamp are made of (a handful of LIMITed or aggregate
    queries). Memory topics are not here: they scan every memory and are only computed on a memo miss."""
    out: dict[str, Any] = {}
    where, args = _scope_clause(project_id)
    isolated = bool(project_id) and is_isolated(db, project_id)
    with db.tx() as c:
        if sw.get("memory") and _has(c, "memories"):
            n, newest, pins, size = c.execute(
                f"SELECT COUNT(*), COALESCE(MAX(updated_at),0), TOTAL(pinned), TOTAL(length(content)) FROM memories WHERE {where} AND {live_mem()}", args).fetchone()
            kinds = c.execute(f"SELECT kind, COUNT(*) FROM memories WHERE {where} AND {live_mem()} GROUP BY kind ORDER BY kind", args).fetchall()
            out["memory"] = {"total": n, "newest": newest, "pins": pins, "size": size, "kinds": [(k or "fact", v) for k, v in kinds]}
        if sw.get("files") and _has(c, "docs"):
            total = c.execute(f"SELECT COUNT(*) FROM docs WHERE {where} AND deleted_at IS NULL", args).fetchone()[0]
            rows = c.execute(
                f"SELECT id, title, folder, COALESCE(pinned,0) AS pinned, COALESCE(starred,0) AS starred, updated_at FROM docs "
                f"WHERE {where} AND deleted_at IS NULL ORDER BY (COALESCE(pinned,0)+COALESCE(starred,0) > 0) DESC, updated_at DESC, id LIMIT ?",
                (*args, file_cap)).fetchall()
            out["files"] = {"total": total, "rows": [tuple(r) for r in rows]}
        if sw.get("uploads") and _has(c, "documents"):
            total = c.execute(f"SELECT COUNT(*) FROM documents WHERE {where} AND deleted_at IS NULL", args).fetchone()[0]
            rows = c.execute(f"SELECT id, name, created_at FROM documents WHERE {where} AND deleted_at IS NULL ORDER BY created_at DESC, id LIMIT ?",
                             (*args, upload_cap)).fetchall()
            out["uploads"] = {"total": total, "rows": [tuple(r) for r in rows]}
        if sw.get("projects") and _has(c, "projects"):
            files = ("(SELECT COUNT(*) FROM documents u WHERE u.project_id=p.id AND u.deleted_at IS NULL)"
                     + (" + (SELECT COUNT(*) FROM docs d WHERE d.project_id=p.id AND d.deleted_at IS NULL)" if _has(c, "docs") else ""))
            rows = c.execute(
                f"SELECT p.id, p.name, {files} AS files, (SELECT COUNT(*) FROM conversations v WHERE v.project_id=p.id AND v.deleted_at IS NULL) AS chats, "
                f"COALESCE((SELECT MAX(v.updated_at) FROM conversations v WHERE v.project_id=p.id AND v.deleted_at IS NULL), p.created_at) AS active "
                f"FROM projects p WHERE p.deleted_at IS NULL" + (" AND p.id=?" if isolated else ""), (project_id,) if isolated else ()).fetchall()
            ordered = sorted((tuple(r) for r in rows), key=lambda r: (r[0] != project_id, -float(r[4] or 0), r[0]))
            out["projects"] = {"total": len(ordered), "rows": [r[:4] for r in ordered[:project_cap]], "all": [r[:4] for r in ordered]}
        if sw.get("skills") and _has(c, "skills"):
            out["skills"] = {"total": c.execute(
                f"SELECT COUNT(*) FROM skills WHERE {where} AND status='approved' AND source<>'builtin' AND TRIM(procedure)<>''", args).fetchone()[0]}
    out["project_id"], out["isolated"] = project_id, isolated
    return out


_STOP = frozenset(_ctx._STOP) | frozenset(
    "about after again also always being could does doing during each every from have having here into just like likes made make "
    "many more most much never only other over prefer prefers preferred really should some such than that their them then there "
    "these they this those user users very want wants were what when where which while will with would your".split())


def _topics(db: Any, project_id: str | None) -> list[tuple[str, int]]:
    """Frequent words across the live memories in scope, as (word, number of memories using it), only words in at least two.
    A pure function of the rows, ranked by bucket then word so the order does not move inside a bucket. Words only, never
    a memory's text."""
    where, args = _scope_clause(project_id)
    count: Counter[str] = Counter()
    with db.tx() as c:
        if not _has(c, "memories"):
            return []
        for (text,) in c.execute(f"SELECT content FROM memories WHERE {where} AND {live_mem()} ORDER BY id LIMIT 5000", args):
            count.update({w for w in re.findall(r"[a-z]{4,20}", unicodedata.normalize("NFKC", text or "").lower())
                          if w not in _STOP and w not in _DROP_WORDS})
    rows = [(w, n) for w, n in count.items() if n >= 2]
    rows.sort(key=lambda r: (-bucket(r[1]), r[0]))
    return rows


# ---------------------------------------------------------------- the stable text
def _file_line(r: tuple[Any, ...]) -> str:
    rid, title, folder, pinned, starred, ts = r
    bits = [b for b in (("pinned" if pinned else ""), ("starred" if starred else ""),
                        sanitize_title(folder, "folder", 40) if (folder or "").strip() else "", f"edited {_day(ts)}") if b]
    return f'doc:{rid} "{sanitize_title(title, "file")}" ({", ".join(bits)})'


def _upload_line(r: tuple[Any, ...]) -> str:
    return f'upload:{r[0]} "{sanitize_title(r[1], "upload")}" (added {_day(r[2])})'


def _project_line(r: tuple[Any, ...], current: str | None) -> str:
    pid, name, files, chats = r[:4]
    return (f'project:{pid} "{sanitize_title(name, "project")}" ({label(files)} files, {label(chats)} chats)'
            + (" [this chat]" if pid == current else ""))


def _render(data: dict[str, Any], topics: list[tuple[str, int]], keep: dict[str, int]) -> dict[str, str]:
    """Each section's text for the given keep counts; empty sections are absent."""
    out: dict[str, str] = {}
    m = data.get("memory")
    if m and m["total"]:
        line = f"Memory ({label(m['total'])} notes; contents not listed): " + ", ".join(f"{k} {label(n)}" for k, n in m["kinds"])
        shown = topics[:keep["topics"]]
        if shown:
            line += "\nMemory topics: " + ", ".join(f"{w} ({label(n)})" for w, n in shown)
            if len(topics) > len(shown):
                line += " " + MORE % label(len(topics) - len(shown))
        out["memory"] = line
    f = data.get("files")
    if f and f["total"]:
        rows = f["rows"][:keep["files"]]
        lines = [f"Files and notes ({label(f['total'])}):", *(_file_line(r) for r in rows)]
        if f["total"] > len(rows):
            lines.append(MORE % label(f["total"] - len(rows)))
        out["files"] = "\n".join(lines)
    u = data.get("uploads")
    if u and u["total"]:
        rows = u["rows"][:keep["uploads"]]
        lines = [f"Uploads ({label(u['total'])}, newest first; the names are untrusted):"]
        if rows:
            lines.append(fence_untrusted("\n".join(_upload_line(r) for r in rows), FENCE_ID, FENCE_SOURCE))
        if u["total"] > len(rows):
            lines.append(MORE % label(u["total"] - len(rows)))
        out["uploads"] = "\n".join(lines)
    p = data.get("projects")
    if p and p["total"]:
        rows = p["rows"][:keep["projects"]]
        lines = [f"Projects ({label(p['total'])}):", *(_project_line(r, data.get("project_id")) for r in rows)]
        if p["total"] > len(rows):
            lines.append(MORE % label(p["total"] - len(rows)))
        out["projects"] = "\n".join(lines)
    s = data.get("skills")
    if s and s["total"]:
        out["skills"] = f"Skills: {label(s['total'])} approved procedures (skill_list to list, skill_view to open)."
    return out


def _join(sections: dict[str, str]) -> str:
    return HEADER + "\n" + "\n".join(sections[k] for k in ("memory", "files", "uploads", "projects", "skills") if k in sections) if sections else ""


def cap_tokens(shares: dict[str, int], retrieved: dict[str, int] | None = None) -> tuple[int, bool]:
    """(cap, shrunk): the map's share of the window, held to MAP_SHRUNK_TOKENS once the memory, excerpt and graph blocks already
    in this turn (`retrieved`: estimated tokens by 'memories' / 'chunks' / 'graph') fill MAP_SHRINK_AT of their shares."""
    cap = min(int(shares.get("map", 0)), limits.MAP_HARD_CEILING_TOKENS)
    pool = sum(int(shares.get(k, 0)) for k in ("memories", "chunks", "graph"))
    used = sum(int(v) for v in (retrieved or {}).values())
    if pool and used >= limits.MAP_SHRINK_AT * pool and cap > limits.MAP_SHRUNK_TOKENS:
        return limits.MAP_SHRUNK_TOKENS, True
    return cap, False


_MEMO: "OrderedDict[tuple[Any, ...], tuple[str, dict[str, Any]]]" = OrderedDict()
MEMO_SIZE = 32


def version_stamp(db: Any, project_id: str | None, sw: dict[str, bool]) -> tuple[Any, ...]:
    """Everything the stable text is a function of, from cheap aggregate and LIMITed reads: the scope, the switches, memory
    count / newest edit / pins / total size / kinds, the listed notes, uploads and projects with their ranks, and the
    approved-skill count. Any change to a listed row, or to how many rows there are, changes the stamp."""
    return _stamp(_collect(db, project_id, sw), sw)


def _stamp(data: dict[str, Any], sw: dict[str, bool]) -> tuple[Any, ...]:
    def freeze(v: Any) -> Any:
        if isinstance(v, dict):
            return tuple((k, freeze(x)) for k, x in sorted(v.items()) if k != "all")
        if isinstance(v, (list, tuple)):
            return tuple(freeze(x) for x in v)
        return v
    return (tuple(sorted(sw.items())), freeze(data))


def build_stable(db: Any, *, project_id: str | None, switches: dict[str, bool], cap: int) -> tuple[str, dict[str, Any]]:
    """(text, info) for the prompt prefix; ("", info) when there is nothing to list or the cap cannot hold even the header.
    Over the cap, sections are cut from the tail in this order: notes, projects, memory topics, uploads (then notes and
    projects down to nothing); each cut section ends with an "(N more: use context_map)" line."""
    data = _collect(db, project_id, switches)
    key = (str(getattr(db, "path", id(db))), _stamp(data, switches), cap)
    hit = _MEMO.get(key)
    if hit is not None:
        _MEMO.move_to_end(key)
        return hit[0], copy.deepcopy(hit[1])
    topics = _topics(db, project_id) if "memory" in data else []
    keep = {"files": len((data.get("files") or {}).get("rows") or []), "projects": len((data.get("projects") or {}).get("rows") or []),
            "uploads": len((data.get("uploads") or {}).get("rows") or []), "topics": min(len(topics), limits.MAP_TOPICS)}
    full = dict(keep)
    secs = _render(data, topics, keep)
    text = _join(secs)
    for name, floor in (("files", 3), ("projects", 2), ("topics", 0), ("uploads", 0), ("files", 0), ("projects", 0)):
        while text and _est(text) > cap and keep[name] > floor:
            keep[name] -= 1
            secs = _render(data, topics, keep)
            text = _join(secs)
    if _est(text) > cap:
        secs, text = {}, ""
    info = {"tokens": _est(text), "sections": {k: _est(v) for k, v in secs.items()}, "hash": hashlib.sha256(text.encode()).hexdigest()[:12] if text else "",
            "cap": cap, "trimmed": {k: full[k] - keep[k] for k in full if keep[k] < full[k]} if text else {}}
    if text:
        info["sections"] = {"header": _est(HEADER), **info["sections"]}
    _MEMO[key] = (text, copy.deepcopy(info))
    while len(_MEMO) > MEMO_SIZE:
        _MEMO.popitem(last=False)
    return text, info


def used_stable(text: str, info: dict[str, Any], shrunk: bool) -> dict[str, Any] | None:
    """The context_used["map"] value for the Context drawer (JSON-safe); None when there is no map."""
    if not text:
        return None
    return {"tokens": info["tokens"], "shrunk": bool(shrunk), "hash": info["hash"], "sections": dict(info["sections"]),
            "volatile": None, "text": text}


# ---------------------------------------------------------------- the volatile block
def _safe(fn: Any) -> Any:
    try:
        return fn()
    except Exception:  # noqa: BLE001 - a failed read drops its own line only
        log.debug("workspace status read failed", exc_info=True)
        return None


def _stamp_time(ts: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _event_start(ev: dict[str, Any]) -> dt.datetime | None:
    raw = str(ev.get("start") or "")
    try:
        d = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.astimezone()


def build_volatile(db: Any, *, project_id: str | None, switches: dict[str, bool], google: Any = None, mcp_store: Any = None,
                   nonce: str | None = None) -> tuple[str, dict[str, Any]]:
    """(text, info) for the per-turn block: source freshness, open todos, the next calendar events, what waits on the user, running
    workers. Times are absolute here (the block is not in the cached prefix). Calendar titles are untrusted and sit in a fence
    keyed by `nonce`."""
    nonce = nonce or secrets.token_hex(8)
    secs: dict[str, list[str]] = {}
    isolated = bool(project_id) and bool(_safe(lambda: is_isolated(db, project_id)))
    if switches.get("sources"):
        lines: list[str] = []
        if google is not None:
            def g() -> str | None:
                st = google.status()
                if st.get("connected") and not st.get("needs_reauth"):
                    synced = _safe(lambda: google.calendar_synced_at())
                    return "Google (mail, calendar, drive): connected" + (f", calendar synced {_stamp_time(synced)}" if synced else "")
                if st.get("connected") or st.get("connected_at"):
                    return "Google: sign-in needs attention"
                return None
            if line := _safe(g):
                lines.append(line)
        if mcp_store is not None:
            def mc() -> str | None:
                rows = [s for s in mcp_store.servers() if s.get("enabled")]
                rows.sort(key=lambda s: (str(s.get("name") or ""), str(s.get("id") or "")))
                bits = [f"{sanitize_title(s.get('name'), 'connector', 40)} ({s.get('status') or 'idle'}"
                        + (f", connected {_stamp_time(s['last_connected_at'])}" if s.get("last_connected_at") else "") + ")" for s in rows[:6]]
                return "Connectors: " + ", ".join(bits) + (f" (+{len(rows) - 6} more)" if len(rows) > 6 else "") if bits else None
            if line := _safe(mc):
                lines.append(line)
        if lines:
            secs["sources"] = lines
    if switches.get("today"):
        lines = []
        where, args = _scope_clause(project_id)

        def count(sql: str, a: Any = ()) -> int:
            with db.tx() as c:
                return int(c.execute(sql, a).fetchone()[0])
        todos = _safe(lambda: count(f"SELECT COUNT(*) FROM todos WHERE done=0 AND deleted_at IS NULL AND {where}", args))
        if todos is not None:
            lines.append(f"Open todos: {todos}")
        if google is not None and not isolated:
            def ev() -> str | None:
                now = dt.datetime.now().astimezone()
                evs = [(s, e) for e in (google.calendar_saved(7) or []) if (s := _event_start(e)) is not None and s >= now]
                evs.sort(key=lambda p: (p[0], str(p[1].get("id") or "")))
                rows = [f"{s:%Y-%m-%d %H:%M} \"{sanitize_title(e.get('summary'), 'event')}\"" for s, e in evs[:limits.MAP_UPCOMING_EVENTS]]
                return fence_untrusted("Next events: " + "; ".join(rows), nonce, "calendar-titles") if rows else None
            if line := _safe(ev):
                lines.append(line)
        waiting = []
        for name, sql in (("approvals", "SELECT COUNT(*) FROM approvals WHERE status='pending'"),
                          ("proposals", "SELECT COUNT(*) FROM proposals WHERE status='pending'"),
                          ("document edits", "SELECT COUNT(*) FROM doc_revisions r JOIN docs d ON d.id=r.doc_id WHERE r.status='pending' AND d.deleted_at IS NULL")):
            n = _safe(lambda sql=sql: count(sql))
            if n:
                waiting.append(f"{n} {name}")
        if waiting:
            lines.append("Waiting on you: " + ", ".join(waiting))
        running = _safe(lambda: count("SELECT COUNT(*) FROM agent_runs WHERE kind='worker' AND status IN ('running','queued')"))
        if running:
            lines.append(f"Background workers running: {running}")
        if lines:
            secs["today"] = lines
    if not secs:
        return "", {"tokens": 0, "sections": {}}
    texts = {k: "\n".join(v) for k, v in secs.items()}
    text = "## Workspace status\nLive labels and counts; open the item with its tool before relying on it.\n" + "\n".join(texts[k] for k in ("sources", "today") if k in texts)
    return text, {"tokens": _est(text), "sections": {k: _est(v) for k, v in texts.items()}}


# ---------------------------------------------------------------- the context_map tool
def list_section(db: Any, section: str, query: str = "", offset: int = 0, *, project_id: str | None, switches: dict[str, bool],
                 limit: int = limits.MAP_PAGE, google: Any = None, mcp_store: Any = None) -> tuple[list[str], int]:
    """One page of the map's lines, unbounded: (lines, total matching). `section` is a SECTIONS name or 'all'; `query` filters
    case-insensitively over the lines. For memory a query matches topic labels only (counts, never a note); read the notes
    with search_memory."""
    want = SECTIONS if section in ("", "all", None) else (section,)
    q = (query or "").strip().casefold()
    out: list[str] = []
    big = {"files": 100000, "uploads": 100000, "projects": 100000}
    data = _collect(db, project_id, {k: switches.get(k, False) and k in want for k in SECTIONS}, big["files"], big["uploads"], big["projects"])
    for sec in want:
        if not switches.get(sec):
            continue
        lines: list[str] = []
        if sec == "memory" and "memory" in data and data["memory"]["total"]:
            m = data["memory"]
            if not q:
                lines.append(f"Memory ({label(m['total'])} notes): " + ", ".join(f"{k} {label(n)}" for k, n in m["kinds"]))
            lines += [f"memory topic: {w} ({label(n)})" for w, n in _topics(db, project_id)[:200]]
        elif sec == "files" and "files" in data:
            lines = [_file_line(r) for r in data["files"]["rows"]]
        elif sec == "uploads" and "uploads" in data:
            lines = [_upload_line(r) for r in data["uploads"]["rows"]]
        elif sec == "projects" and "projects" in data:
            lines = [_project_line(r, project_id) for r in data["projects"]["all"]]
        elif sec == "skills":
            where, args = _scope_clause(project_id)
            with db.tx() as c:
                rows = c.execute(f"SELECT id, name FROM skills WHERE {where} AND status='approved' AND source<>'builtin' AND TRIM(procedure)<>'' "
                                 f"ORDER BY name, id", args).fetchall() if _has(c, "skills") else []
            lines = [f'skill:{r[0]} "{sanitize_title(r[1], "skill")}"' for r in rows]
        elif sec in ("sources", "today"):
            text, _ = build_volatile(db, project_id=project_id, switches={sec: True}, google=google, mcp_store=mcp_store)
            lines = [ln for ln in text.splitlines()[2:] if ln]
        if q:
            lines = [ln for ln in lines if q in ln.casefold()]
        out += lines
    off = max(0, int(offset or 0))
    return out[off:off + limit], len(out)


# ---------------------------------------------------------------- agents that do not build a chat context
def agent_block(db: Any, project_id: str | None, settings: dict[str, Any]) -> str:
    """The stable map for a background worker or subagent prompt (built once when the child starts, so it cannot shift
    under its run). Empty unless the mapWorkers setting is on; held to the shrunk cap to keep those prompts small."""
    if not settings.get("mapWorkers", False):
        return ""
    sw = resolve_switches(settings)
    sw["sources"] = sw["today"] = False
    try:
        win = limits.context_window(settings.get("contextWindow"))
        cap = min(limits.context_shares(win)["map"], limits.MAP_SHRUNK_TOKENS)
        return build_stable(db, project_id=project_id, switches=sw, cap=cap)[0]
    except Exception:  # noqa: BLE001 - a prompt addition must never stop a worker from starting
        log.debug("worker map failed", exc_info=True)
        return ""
